from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, func, inspect, select, text
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
    ResearchModelRun,
    TradeCalendar,
)
from .services.data_governance import DataGovernanceService
from .services.demo_data import demo_backtest_summary, demo_explanation, demo_recommendations


DRIVER_UPSERT_BATCH_SIZE = 6000


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
        service = DataGovernanceService(self.session.get_bind())
        return service.inventory(self.session)

    def _has_published_runs(self) -> bool:
        return inspect(self.session.get_bind()).has_table(ResearchModelRun.__tablename__)

    def serving_model_run(self, horizon: str) -> ResearchModelRun | None:
        if not self._has_published_runs():
            return None
        return self.session.execute(
            select(ResearchModelRun)
            .where(
                ResearchModelRun.horizon == horizon,
                ResearchModelRun.status == "completed",
                ResearchModelRun.is_serving == 1,
            )
            .order_by(desc(ResearchModelRun.completed_at), desc(ResearchModelRun.created_at))
            .limit(1)
        ).scalar_one_or_none()

    def latest_prediction_run(self, horizon: str) -> tuple[date, str] | None:
        if self._has_published_runs():
            published_run = self.serving_model_run(horizon)
            # Once the publication table exists, never infer serving from raw
            # prediction timestamps: an empty pointer means no completed run.
            if published_run is None:
                return None
            return published_run.prediction_date, published_run.model_version
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
        published_run = self.serving_model_run(horizon)
        published_config = _json_loads(published_run.config_json, {}) if published_run else {}
        strategy_version = (
            published_config.get("strategy_version", model_version)
            if isinstance(published_config, dict)
            else model_version
        )

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
                DimStock.exchange.in_(("SSE", "SZSE")),
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
                    "run_id": published_run.run_id if published_run else None,
                    "model_version": strategy_version,
                    "publication_version": model_version,
                }
            )
        return rows

    def stock_explanation(self, code: str, horizon: str) -> dict[str, Any] | None:
        latest_run = self.latest_prediction_run(horizon)
        if latest_run is None:
            return None
        latest_date, model_version = latest_run
        published_run = self.serving_model_run(horizon)
        published_config = _json_loads(published_run.config_json, {}) if published_run else {}
        strategy_version = (
            published_config.get("strategy_version", model_version)
            if isinstance(published_config, dict)
            else model_version
        )
        stmt = (
            select(ModelPrediction, DimStock)
            .join(DimStock, DimStock.code == ModelPrediction.code, isouter=True)
            .where(
                ModelPrediction.code == code,
                ModelPrediction.horizon == horizon,
                ModelPrediction.trade_date == latest_date,
                ModelPrediction.model_version == model_version,
                DimStock.exchange.in_(("SSE", "SZSE")),
            )
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
            "method": strategy_version,
            "publication_version": prediction.model_version,
            "run_id": published_run.run_id if published_run else None,
            "notes": ["读取自 MySQL model_prediction 表。"],
        }

    def backtest_summary(self, horizon: str) -> dict[str, Any] | None:
        published_run = None
        publication_table_exists = self._has_published_runs()
        if publication_table_exists:
            published_run = self.serving_model_run(horizon)
            if published_run is None:
                return None
        if published_run is not None:
            metrics = _json_loads(published_run.metrics_json, {})
            config = _json_loads(published_run.config_json, {})
            strategy_version = (
                config.get("strategy_version", published_run.model_version)
                if isinstance(config, dict)
                else published_run.model_version
            )
            return {
                "horizon": published_run.horizon,
                "model_version": strategy_version,
                "publication_version": published_run.model_version,
                "run_id": published_run.run_id,
                "start_date": published_run.start_date.isoformat(),
                "end_date": published_run.end_date.isoformat(),
                "top_group_return": metrics.get("top_group_return"),
                "benchmark_return": metrics.get("benchmark_return"),
                "win_rate": metrics.get("win_rate"),
                "max_drawdown": metrics.get("max_drawdown"),
                "sharpe": metrics.get("sharpe"),
                "rank_ic": metrics.get("rank_ic"),
                "turnover": metrics.get("turnover"),
                "initial_capital": config.get("initial_capital"),
                "ending_capital": metrics.get("ending_capital"),
                "notes": config,
            }
        stmt = (
            select(BacktestSummary)
            .where(BacktestSummary.horizon == horizon)
            .order_by(desc(BacktestSummary.end_date), desc(BacktestSummary.created_at))
            .limit(1)
        )
        summary = self.session.execute(stmt).scalar_one_or_none()
        if summary is None:
            return None
        parsed_notes = _json_loads(summary.notes, {})
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
            "initial_capital": parsed_notes.get("initial_capital") if isinstance(parsed_notes, dict) else None,
            "ending_capital": parsed_notes.get("ending_capital") if isinstance(parsed_notes, dict) else None,
            "notes": parsed_notes,
        }

    def upsert_stocks(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        table = DimStock.__table__
        stock_batch_size = 1000
        for start in range(0, len(records), stock_batch_size):
            stmt = mysql_insert(table).values(records[start : start + stock_batch_size])
            stmt = stmt.on_duplicate_key_update(
                name=stmt.inserted.name,
                exchange=stmt.inserted.exchange,
                industry=func.coalesce(stmt.inserted.industry, table.c.industry),
                status=stmt.inserted.status,
            )
            self.session.execute(stmt)
        self.session.commit()
        return len(records)

    def upsert_daily_bars(self, records: list[dict[str, Any]], commit: bool = True) -> int:
        update_columns = ("open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover_rate")
        return self._driver_multi_upsert(DailyBar, records, update_columns, commit=commit)

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
        commit: bool = True,
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
        if commit:
            self.session.commit()

    def upsert_trade_calendar(self, records: list[dict[str, Any]]) -> int:
        return self._bulk_upsert(TradeCalendar, records, ("is_open",))

    def upsert_adj_factors(self, records: list[dict[str, Any]], commit: bool = True) -> int:
        return self._bulk_upsert(AdjFactor, records, ("adj_factor",), commit=commit)

    def upsert_daily_basics(self, records: list[dict[str, Any]], commit: bool = True) -> int:
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
        return self._bulk_upsert(DailyBasic, records, columns, commit=commit)

    def upsert_adjusted_bars(self, records: list[dict[str, Any]], commit: bool = True) -> int:
        columns = ("open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover_rate")
        return self._bulk_upsert(DailyBarAdj, records, columns, commit=commit)

    def _bulk_upsert(
        self,
        model,
        records: list[dict[str, Any]],
        update_columns: tuple[str, ...],
        commit: bool = True,
    ) -> int:
        return self._driver_multi_upsert(
            model,
            records,
            update_columns,
            commit=commit,
        )

    def _driver_multi_upsert(
        self,
        model,
        records: list[dict[str, Any]],
        update_columns: tuple[str, ...],
        commit: bool = True,
    ) -> int:
        """Use one safely-bound MySQL multi-row statement without SQLAlchemy's giant bind graph."""
        if not records:
            return 0
        table = model.__table__
        bind = self.session.get_bind()
        quote = bind.dialect.identifier_preparer.quote
        table_columns = {column.name for column in table.columns}
        record_keys = set(records[0])
        if not record_keys or any(set(record) != record_keys for record in records):
            raise ValueError("bulk records must have one consistent non-empty shape")
        unknown_columns = record_keys.difference(table_columns)
        if unknown_columns:
            raise ValueError(f"unknown bulk columns: {sorted(unknown_columns)}")
        if set(update_columns).difference(record_keys):
            raise ValueError("bulk update columns must exist in every record")

        columns = tuple(column.name for column in table.columns if column.name in record_keys)
        row_sql = f"({','.join(['%s'] * len(columns))})"
        column_sql = ",".join(quote(column) for column in columns)
        update_sql = ",".join(
            f"{quote(column)}=new.{quote(column)}" for column in update_columns
        )
        target = bind.dialect.identifier_preparer.format_table(table)
        connection = self.session.connection()
        for start in range(0, len(records), DRIVER_UPSERT_BATCH_SIZE):
            batch = records[start : start + DRIVER_UPSERT_BATCH_SIZE]
            values_sql = ",".join([row_sql] * len(batch))
            statement = (
                f"INSERT INTO {target} ({column_sql}) VALUES {values_sql} AS new "
                f"ON DUPLICATE KEY UPDATE {update_sql}"
            )
            parameters = tuple(record[column] for record in batch for column in columns)
            connection.exec_driver_sql(statement, parameters)
        if commit:
            self.session.commit()
        return len(records)
