from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, func, select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from .models import (
    AdjFactor,
    BacktestSummary,
    DailyBar,
    DailyBarAdj,
    DailyBasic,
    DataSyncState,
    DimStock,
    ModelPrediction,
    TradeCalendar,
)
from .services.demo_data import demo_backtest_summary, demo_explanation, demo_recommendations


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _factor_highlights(value: str | None) -> list[dict[str, Any]]:
    parsed = _json_loads(value, [])
    if isinstance(parsed, list):
        return parsed[:4]
    if isinstance(parsed, dict):
        rows = [
            {"name": key, "value": _as_float(item), "weight": None, "contribution": _as_float(item)}
            for key, item in parsed.items()
        ]
        return rows[:4]
    return []


class DemoRepository:
    def health(self) -> dict[str, Any]:
        return {"database": "demo", "ok": True}

    def recommendations(self, horizon: str, limit: int, min_score: float | None = None) -> list[dict[str, Any]]:
        return demo_recommendations(horizon=horizon, limit=limit, min_score=min_score)

    def stock_explanation(self, code: str, horizon: str) -> dict[str, Any] | None:
        return demo_explanation(code=code, horizon=horizon)

    def backtest_summary(self, horizon: str) -> dict[str, Any]:
        return demo_backtest_summary(horizon=horizon)


class MysqlRepository:
    def __init__(self, session: Session):
        self.session = session

    def health(self) -> dict[str, Any]:
        self.session.execute(text("SELECT 1"))
        return {"database": "mysql", "ok": True}

    def data_inventory(self) -> dict[str, Any]:
        table_names = ("daily_bar", "daily_bar_adj", "adj_factor", "daily_basic")
        estimates = {
            table_name: int(table_rows or 0)
            for table_name, table_rows in self.session.execute(
                text(
                    """
                    SELECT table_name, table_rows
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                      AND table_name IN ('daily_bar', 'daily_bar_adj', 'adj_factor', 'daily_basic')
                    """
                )
            ).all()
        }
        inventory = []
        for table_name in table_names:
            bounds = self.session.execute(
                text(f"SELECT MIN(trade_date) AS min_date, MAX(trade_date) AS max_date FROM {table_name}")
            ).one()
            inventory.append(
                {
                    "table": table_name,
                    "estimated_rows": estimates.get(table_name, 0),
                    "start_date": bounds.min_date.isoformat() if bounds.min_date else None,
                    "end_date": bounds.max_date.isoformat() if bounds.max_date else None,
                }
            )
        states = self.session.execute(
            select(DataSyncState).order_by(desc(DataSyncState.updated_at))
        ).scalars()
        return {
            "tables": inventory,
            "states": [
                {
                    "provider": state.provider,
                    "dataset": state.dataset,
                    "scope": state.scope,
                    "last_trade_date": state.last_trade_date.isoformat() if state.last_trade_date else None,
                    "last_row_count": state.last_row_count,
                    "status": state.status,
                    "error_message": state.error_message,
                    "updated_at": state.updated_at.isoformat() if state.updated_at else None,
                }
                for state in states
            ],
        }

    def latest_prediction_run(self, horizon: str) -> tuple[date, str] | None:
        stmt = (
            select(ModelPrediction.trade_date, ModelPrediction.model_version)
            .where(ModelPrediction.horizon == horizon)
            .order_by(desc(ModelPrediction.trade_date), desc(ModelPrediction.created_at))
            .limit(1)
        )
        return self.session.execute(stmt).one_or_none()

    def recommendations(self, horizon: str, limit: int, min_score: float | None = None) -> list[dict[str, Any]]:
        latest_run = self.latest_prediction_run(horizon)
        if latest_run is None:
            return []
        latest_date, model_version = latest_run

        stmt = (
            select(ModelPrediction, DimStock, DailyBar.close)
            .join(DimStock, DimStock.code == ModelPrediction.code, isouter=True)
            .join(
                DailyBar,
                (DailyBar.code == ModelPrediction.code) & (DailyBar.trade_date == ModelPrediction.trade_date),
                isouter=True,
            )
            .where(
                ModelPrediction.horizon == horizon,
                ModelPrediction.trade_date == latest_date,
                ModelPrediction.model_version == model_version,
            )
            .order_by(desc(ModelPrediction.score))
            .limit(limit)
        )
        if min_score is not None:
            stmt = stmt.where(ModelPrediction.score >= min_score)

        rows = []
        for prediction, stock, close in self.session.execute(stmt).all():
            rows.append(
                {
                    "code": prediction.code,
                    "name": stock.name if stock else prediction.code,
                    "industry": stock.industry if stock else "未分类",
                    "trade_date": prediction.trade_date.isoformat(),
                    "last_close": _as_float(close),
                    "horizon": prediction.horizon,
                    "score": _as_float(prediction.score),
                    "probability": _as_float(prediction.probability),
                    "rank": prediction.rank_no,
                    "factor_highlights": _factor_highlights(prediction.factor_snapshot),
                    "factor_snapshot": _json_loads(prediction.factor_snapshot, {}),
                    "risk_flags": _json_loads(prediction.risk_flags, []),
                }
            )
        return rows

    def stock_explanation(self, code: str, horizon: str) -> dict[str, Any] | None:
        stmt = (
            select(ModelPrediction, DimStock)
            .join(DimStock, DimStock.code == ModelPrediction.code, isouter=True)
            .where(ModelPrediction.code == code, ModelPrediction.horizon == horizon)
            .order_by(desc(ModelPrediction.trade_date))
            .limit(1)
        )
        row = self.session.execute(stmt).first()
        if not row:
            return None

        prediction, stock = row
        recommendation = {
            "code": prediction.code,
            "name": stock.name if stock else prediction.code,
            "industry": stock.industry if stock else "未分类",
            "trade_date": prediction.trade_date.isoformat(),
            "horizon": prediction.horizon,
            "score": _as_float(prediction.score),
            "probability": _as_float(prediction.probability),
            "rank": prediction.rank_no,
            "factor_highlights": _factor_highlights(prediction.factor_snapshot),
            "factor_snapshot": _json_loads(prediction.factor_snapshot, {}),
            "risk_flags": _json_loads(prediction.risk_flags, []),
        }
        return {
            "prediction": recommendation,
            "method": prediction.model_version,
            "notes": ["读取自 MySQL model_prediction 表。"],
        }

    def backtest_summary(self, horizon: str) -> dict[str, Any] | None:
        stmt = (
            select(BacktestSummary)
            .where(BacktestSummary.horizon == horizon)
            .order_by(desc(BacktestSummary.end_date), desc(BacktestSummary.created_at))
            .limit(1)
        )
        summary = self.session.execute(stmt).scalar_one_or_none()
        if summary is None:
            return None
        return {
            "horizon": summary.horizon,
            "model_version": summary.model_version,
            "start_date": summary.start_date.isoformat(),
            "end_date": summary.end_date.isoformat(),
            "top_group_return": _as_float(summary.top_group_return),
            "benchmark_return": _as_float(summary.benchmark_return),
            "win_rate": _as_float(summary.win_rate),
            "max_drawdown": _as_float(summary.max_drawdown),
            "sharpe": _as_float(summary.sharpe),
            "rank_ic": _as_float(summary.rank_ic),
            "turnover": _as_float(summary.turnover),
            "notes": summary.notes,
        }

    def upsert_stocks(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        table = DimStock.__table__
        for start in range(0, len(records), 1000):
            stmt = mysql_insert(table).values(records[start : start + 1000])
            stmt = stmt.on_duplicate_key_update(
                name=stmt.inserted.name,
                exchange=stmt.inserted.exchange,
                industry=func.coalesce(stmt.inserted.industry, table.c.industry),
                status=stmt.inserted.status,
            )
            self.session.execute(stmt)
        self.session.commit()
        return len(records)

    def upsert_daily_bars(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        table = DailyBar.__table__
        update_columns = ("open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover_rate")
        for start in range(0, len(records), 1000):
            stmt = mysql_insert(table).values(records[start : start + 1000])
            stmt = stmt.on_duplicate_key_update(**{name: getattr(stmt.inserted, name) for name in update_columns})
            self.session.execute(stmt)
        self.session.commit()
        return len(records)

    def get_sync_state(self, provider: str, dataset: str, scope: str) -> DataSyncState | None:
        return self.session.get(DataSyncState, (provider, dataset, scope))

    def set_sync_state(
        self,
        provider: str,
        dataset: str,
        scope: str,
        last_trade_date: date | None,
        last_row_count: int | None,
        status: str,
        error_message: str | None = None,
    ) -> None:
        record = {
            "provider": provider,
            "dataset": dataset,
            "scope": scope,
            "last_trade_date": last_trade_date,
            "last_row_count": last_row_count,
            "status": status,
            "error_message": error_message,
        }
        table = DataSyncState.__table__
        stmt = mysql_insert(table).values(record)
        stmt = stmt.on_duplicate_key_update(
            last_trade_date=stmt.inserted.last_trade_date,
            last_row_count=stmt.inserted.last_row_count,
            status=stmt.inserted.status,
            error_message=stmt.inserted.error_message,
            updated_at=func.now(),
        )
        self.session.execute(stmt)
        self.session.commit()

    def upsert_trade_calendar(self, records: list[dict[str, Any]]) -> int:
        return self._bulk_upsert(TradeCalendar, records, ("is_open",))

    def upsert_adj_factors(self, records: list[dict[str, Any]]) -> int:
        return self._bulk_upsert(AdjFactor, records, ("adj_factor",))

    def upsert_daily_basics(self, records: list[dict[str, Any]]) -> int:
        columns = (
            "pe_ttm",
            "pb",
            "ps_ttm",
            "total_mv",
            "float_mv",
            "turnover_rate",
            "is_st",
            "is_suspended",
            "limit_status",
        )
        return self._bulk_upsert(DailyBasic, records, columns)

    def upsert_adjusted_bars(self, records: list[dict[str, Any]]) -> int:
        columns = ("open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover_rate")
        return self._bulk_upsert(DailyBarAdj, records, columns)

    def _bulk_upsert(self, model, records: list[dict[str, Any]], update_columns: tuple[str, ...]) -> int:
        if not records:
            return 0
        table = model.__table__
        for start in range(0, len(records), 1000):
            stmt = mysql_insert(table).values(records[start : start + 1000])
            stmt = stmt.on_duplicate_key_update(
                **{name: getattr(stmt.inserted, name) for name in update_columns}
            )
            self.session.execute(stmt)
        self.session.commit()
        return len(records)
