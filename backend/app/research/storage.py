from __future__ import annotations

import json
from datetime import date
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..models import BacktestSummary, FactorDaily, ModelPrediction


def load_bars(engine: Engine, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    clauses: list[str] = []
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


def _chunks(records: list[dict[str, Any]], size: int = 2000) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(records), size):
        yield records[start : start + size]


def save_factors(session: Session, factors: pd.DataFrame) -> int:
    clean = factors.replace([np.inf, -np.inf], np.nan).dropna(subset=["factor_value"])
    table = FactorDaily.__table__
    saved = 0
    for start in range(0, len(clean), 2000):
        batch_frame = clean.iloc[start : start + 2000]
        batch = [
            {
                "code": str(row.code),
                "trade_date": pd.Timestamp(row.trade_date).date(),
                "factor_name": row.factor_name,
                "factor_value": None if pd.isna(row.factor_value) else float(row.factor_value),
                "factor_zscore": None if pd.isna(row.factor_zscore) else float(row.factor_zscore),
            }
            for row in batch_frame.itertuples(index=False)
        ]
        stmt = mysql_insert(table).values(batch)
        stmt = stmt.on_duplicate_key_update(
            factor_value=stmt.inserted.factor_value,
            factor_zscore=stmt.inserted.factor_zscore,
        )
        session.execute(stmt)
        session.commit()
        saved += len(batch)
    return saved


def save_predictions(session: Session, predictions: pd.DataFrame, horizon: int, model_version: str) -> int:
    records = []
    for row in predictions.itertuples(index=False):
        records.append(
            {
                "code": str(row.code),
                "trade_date": pd.Timestamp(row.trade_date).date(),
                "horizon": f"{horizon}d",
                "model_version": model_version,
                "score": float(row.score),
                "probability": float(row.probability),
                "rank_no": int(row.rank_no),
                "factor_snapshot": json.dumps(row.factor_snapshot, ensure_ascii=False, allow_nan=False),
                "risk_flags": "[]",
            }
        )
    table = ModelPrediction.__table__
    for batch in _chunks(records):
        stmt = mysql_insert(table).values(batch)
        stmt = stmt.on_duplicate_key_update(
            score=stmt.inserted.score,
            probability=stmt.inserted.probability,
            rank_no=stmt.inserted.rank_no,
            factor_snapshot=stmt.inserted.factor_snapshot,
            risk_flags=stmt.inserted.risk_flags,
        )
        session.execute(stmt)
        session.commit()
    return len(records)


def save_backtest_summary(
    session: Session,
    horizon: int,
    model_version: str,
    start_date: date,
    end_date: date,
    metrics: dict[str, float | int],
    notes: dict[str, Any],
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
    )
    session.execute(stmt)
    session.commit()
