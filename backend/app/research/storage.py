from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sqlalchemy import delete, desc, func, select, text, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..models import BacktestSummary, FactorDaily, ModelPrediction, ResearchModelRun


def load_bars(engine: Engine, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    clauses: list[str] = ["s.exchange IN ('SSE', 'SZSE')"]
    params: dict[str, Any] = {}
    if start_date:
        clauses.append("b.trade_date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        clauses.append("b.trade_date <= :end_date")
        params["end_date"] = end_date
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = text(
        f"""
        SELECT b.code, b.trade_date, b.open, b.high, b.low, b.close,
               b.volume, b.amount, b.pct_chg, b.turnover_rate,
               s.name, s.industry
        FROM daily_bar_adj b
        LEFT JOIN dim_stock s ON s.code = b.code
        {where}
        ORDER BY b.code, b.trade_date
        """
    )
    return pd.read_sql(query, engine, params=params, parse_dates=["trade_date"])


def resolve_recent_start_date(
    engine: Engine,
    sessions: int,
    end_date: str | None = None,
) -> str | None:
    """Return the first date in the latest ``sessions`` supported-market dates."""
    if sessions <= 0:
        raise ValueError("sessions must be positive")
    end_clause = "AND b.trade_date <= :end_date" if end_date else ""
    params: dict[str, Any] = {"sessions": int(sessions)}
    if end_date:
        parsed_end = pd.to_datetime(end_date, format="%Y%m%d", errors="coerce")
        if pd.isna(parsed_end):
            raise ValueError("end_date must use YYYYMMDD")
        params["end_date"] = pd.Timestamp(parsed_end).date()
    query = text(
        f"""
        SELECT MIN(recent.trade_date)
        FROM (
            SELECT DISTINCT b.trade_date
            FROM daily_bar_adj b
            INNER JOIN dim_stock s ON s.code = b.code
            WHERE s.exchange IN ('SSE', 'SZSE')
              {end_clause}
            ORDER BY b.trade_date DESC
            LIMIT :sessions
        ) recent
        """
    )
    with engine.connect() as connection:
        value = connection.execute(query, params).scalar_one_or_none()
    if value is None:
        return None
    return pd.Timestamp(value).strftime("%Y%m%d")


def _chunks(records: list[dict[str, Any]], size: int = 2000) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(records), size):
        yield records[start : start + size]


def save_factors(session: Session, factors: pd.DataFrame, batch_size: int = 6000) -> int:
    """Replace intentionally materialized factor dates as one transaction."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if factors.empty:
        return 0
    required = {"code", "trade_date", "factor_name", "factor_value", "factor_zscore"}
    missing = required.difference(factors.columns)
    if missing:
        raise ValueError(f"Missing factor storage columns: {sorted(missing)}")
    values = pd.to_numeric(factors["factor_value"], errors="coerce")
    clean = factors.loc[values.notna() & np.isfinite(values)].copy()
    table = FactorDaily.__table__
    trade_dates = sorted(
        {pd.Timestamp(value).date() for value in factors["trade_date"].dropna().tolist()}
    )
    if not trade_dates:
        raise ValueError("factors contain no valid trade_date values")
    saved = 0
    try:
        # A rerun can legitimately lose a security or an individual factor.
        # Replacing complete dates removes those stale rows, while the single
        # transaction restores the previous snapshot if any insert batch fails.
        session.execute(delete(table).where(table.c.trade_date.in_(trade_dates)))
        for start in range(0, len(clean), batch_size):
            batch_frame = clean.iloc[start : start + batch_size]
            batch = [
                {
                    "code": str(row.code),
                    "trade_date": pd.Timestamp(row.trade_date).date(),
                    "factor_name": row.factor_name,
                    "factor_value": float(row.factor_value),
                    "factor_zscore": (
                        None
                        if pd.isna(row.factor_zscore) or not np.isfinite(float(row.factor_zscore))
                        else float(row.factor_zscore)
                    ),
                }
                for row in batch_frame.itertuples(index=False)
            ]
            stmt = mysql_insert(table).values(batch)
            stmt = stmt.on_duplicate_key_update(
                factor_value=stmt.inserted.factor_value,
                factor_zscore=stmt.inserted.factor_zscore,
            )
            session.execute(stmt)
            saved += len(batch)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return saved


def save_predictions(session: Session, predictions: pd.DataFrame, horizon: int, model_version: str) -> int:
    records_by_date: dict[date, list[dict[str, Any]]] = {}
    for row in predictions.itertuples(index=False):
        risk_flags = getattr(row, "risk_flags", [])
        if not isinstance(risk_flags, list):
            risk_flags = []
        trade_date = pd.Timestamp(row.trade_date).date()
        records_by_date.setdefault(trade_date, []).append(
            {
                "code": str(row.code),
                "trade_date": trade_date,
                "horizon": f"{horizon}d",
                "model_version": model_version,
                "score": float(row.score),
                "probability": float(row.probability),
                "rank_no": int(row.rank_no),
                "factor_snapshot": json.dumps(row.factor_snapshot, ensure_ascii=False, allow_nan=False),
                "risk_flags": json.dumps(risk_flags, ensure_ascii=False, allow_nan=False),
            }
        )
    table = ModelPrediction.__table__
    saved = 0
    for trade_date in sorted(records_by_date):
        date_records = records_by_date[trade_date]
        try:
            for batch in _chunks(date_records):
                stmt = mysql_insert(table).values(batch)
                stmt = stmt.on_duplicate_key_update(
                    score=stmt.inserted.score,
                    probability=stmt.inserted.probability,
                    rank_no=stmt.inserted.rank_no,
                    factor_snapshot=stmt.inserted.factor_snapshot,
                    risk_flags=stmt.inserted.risk_flags,
                    created_at=func.now(),
                )
                session.execute(stmt)
            session.commit()
        except Exception:
            session.rollback()
            raise
        saved += len(date_records)
    return saved


def save_backtest_summary(
    session: Session,
    horizon: int,
    model_version: str,
    start_date: date,
    end_date: date,
    metrics: dict[str, float | int],
    notes: dict[str, Any],
    *,
    commit: bool = True,
) -> None:
    table = BacktestSummary.__table__
    record = {
        "horizon": f"{horizon}d",
        "model_version": model_version,
        "start_date": start_date,
        "end_date": end_date,
        "top_group_return": metrics["top_group_return"],
        "benchmark_return": metrics["benchmark_return"],
        "win_rate": metrics["win_rate"],
        "max_drawdown": metrics["max_drawdown"],
        "sharpe": metrics["sharpe"],
        "rank_ic": metrics["rank_ic"],
        "turnover": metrics["turnover"],
        "notes": json.dumps(notes, ensure_ascii=False, allow_nan=False),
    }
    stmt = mysql_insert(table).values(record)
    stmt = stmt.on_duplicate_key_update(
        top_group_return=stmt.inserted.top_group_return,
        benchmark_return=stmt.inserted.benchmark_return,
        win_rate=stmt.inserted.win_rate,
        max_drawdown=stmt.inserted.max_drawdown,
        sharpe=stmt.inserted.sharpe,
        rank_ic=stmt.inserted.rank_ic,
        turnover=stmt.inserted.turnover,
        notes=stmt.inserted.notes,
        created_at=func.now(),
    )
    session.execute(stmt)
    if commit:
        session.commit()


def ensure_research_model_run_table(session: Session) -> None:
    ResearchModelRun.__table__.create(bind=session.get_bind(), checkfirst=True)


def get_serving_model_run(session: Session, horizon: int | str) -> ResearchModelRun | None:
    """Read the completed serving baseline for a horizon without inferring raw rows."""
    horizon_name = f"{horizon}d" if isinstance(horizon, int) else horizon
    return session.execute(
        select(ResearchModelRun)
        .where(
            ResearchModelRun.horizon == horizon_name,
            ResearchModelRun.status == "completed",
            ResearchModelRun.is_serving == 1,
        )
        .order_by(desc(ResearchModelRun.completed_at), desc(ResearchModelRun.created_at))
        .limit(1)
    ).scalar_one_or_none()


def publish_model_run(
    session: Session,
    *,
    horizon: int,
    model_version: str,
    predictions: pd.DataFrame,
    start_date: date,
    end_date: date,
    metrics: dict[str, float | int],
    config: dict[str, Any],
) -> str:
    """Append an immutable audit record and atomically switch the serving run."""
    # Existing installations may predate the serving/audit table. Creating this
    # one additive table here keeps the first post-upgrade research run usable
    # even when AUTO_CREATE_TABLES is intentionally disabled.
    ensure_research_model_run_table(session)
    if predictions.empty:
        raise ValueError("cannot publish an empty prediction run")
    trade_dates = pd.to_datetime(predictions["trade_date"], errors="coerce")
    if trade_dates.isna().any():
        raise ValueError("predictions contain invalid trade_date values")
    if trade_dates.dt.date.nunique() != 1:
        raise ValueError("publish_model_run requires exactly one prediction snapshot date")
    prediction_date = pd.Timestamp(trade_dates.max()).date()
    latest_count = int(trade_dates.dt.date.eq(prediction_date).sum())
    horizon_name = f"{horizon}d"
    persisted_count = session.scalar(
        select(func.count())
        .select_from(ModelPrediction)
        .where(
            ModelPrediction.horizon == horizon_name,
            ModelPrediction.model_version == model_version,
            ModelPrediction.trade_date == prediction_date,
        )
    )
    if int(persisted_count or 0) != latest_count:
        raise ValueError(
            "persisted prediction snapshot count does not match the publication payload"
        )
    run_id = str(uuid.uuid4())
    record = ResearchModelRun(
        run_id=run_id,
        horizon=horizon_name,
        model_version=model_version,
        prediction_date=prediction_date,
        status="completed",
        is_serving=1,
        prediction_count=len(predictions),
        latest_prediction_count=latest_count,
        start_date=start_date,
        end_date=end_date,
        config_json=json.dumps(config, ensure_ascii=False, allow_nan=False),
        metrics_json=json.dumps(metrics, ensure_ascii=False, allow_nan=False),
    )
    try:
        session.execute(
            update(ResearchModelRun)
            .where(
                ResearchModelRun.horizon == horizon_name,
                ResearchModelRun.is_serving == 1,
            )
            .values(is_serving=0)
        )
        session.add(record)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return run_id
