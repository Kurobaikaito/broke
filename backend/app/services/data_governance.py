from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import and_, delete, exists, func, inspect, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import (
    AdjFactor,
    BacktestSummary,
    DailyBar,
    DailyBarAdj,
    DailyBasic,
    DataMaintenanceRun,
    DataSyncState,
    DimStock,
    FactorDaily,
    ModelPrediction,
    ResearchModelRun,
    TradeCalendar,
)


@dataclass(frozen=True)
class DataPolicy:
    """One explicit storage/lifecycle decision for a database-backed dataset."""

    name: str
    model: type[Any]
    category: str
    storage: str
    rebuildable: bool
    description: str
    retention_days: int | None = None
    date_column: str | None = None
    date_kind: str = "date"
    key_column: str | None = None
    dedupe_key: tuple[str, ...] = ()
    preserve_latest_by: tuple[str, ...] = ()
    code_column: str | None = None

    @property
    def table_name(self) -> str:
        return self.model.__table__.name

    def manifest(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("model")
        payload["table"] = self.table_name
        payload["retention"] = (
            "permanent" if self.retention_days is None else f"{self.retention_days}d"
        )
        payload["payload_compression"] = "none_portable_typed_columns"
        payload["space_reclamation"] = "optional_sqlite_vacuum_or_mysql_optimize"
        return payload


NON_TABLE_POLICIES: tuple[dict[str, Any], ...] = (
    {
        "name": "provider_response_frames",
        "table": None,
        "category": "transient_ingestion",
        "storage": "memory",
        "rebuildable": True,
        "retention": "request_lifetime",
        "payload_compression": "not_applicable",
        "description": "Tushare response frames are normalized and discarded after a date is committed.",
    },
    {
        "name": "sync_job_logs",
        "table": None,
        "category": "ephemeral_log",
        "storage": "memory",
        "rebuildable": False,
        "retention": "latest_200_entries_per_process",
        "payload_compression": "not_applicable",
        "description": "Progress logs are UI telemetry; durable checkpoints live in data_sync_state.",
    },
    {
        "name": "trained_model_objects",
        "table": None,
        "category": "transient_model_cache",
        "storage": "memory",
        "rebuildable": True,
        "retention": "research_run_lifetime",
        "payload_compression": "not_applicable",
        "description": "Estimator objects are reproducible from raw data and are not persisted.",
    },
)


def _days_or_permanent(days: int) -> int | None:
    return days or None


def _bounded_by_raw(derived_days: int | None, raw_days: int | None) -> int | None:
    if raw_days is None:
        return derived_days
    if derived_days is None:
        return raw_days
    return min(derived_days, raw_days)


def build_data_policies(settings: Settings | None = None) -> tuple[DataPolicy, ...]:
    settings = settings or get_settings()
    raw_days = _days_or_permanent(settings.data_raw_retention_days)
    factor_days = _bounded_by_raw(
        _days_or_permanent(settings.data_factor_retention_days), raw_days
    )
    prediction_days = _bounded_by_raw(
        _days_or_permanent(settings.data_prediction_retention_days), raw_days
    )
    return (
        DataPolicy(
            "stock_master",
            DimStock,
            "reference",
            "database",
            False,
            "Canonical security identity and listing status; retained permanently.",
        ),
        DataPolicy(
            "trade_calendar",
            TradeCalendar,
            "reference",
            "database",
            False,
            "Small exchange calendar used to detect gaps; retained permanently.",
        ),
        DataPolicy(
            "raw_daily_bar",
            DailyBar,
            "raw_market_data",
            "database",
            False,
            "Provider OHLCV source of truth; permanent unless raw retention is explicitly set.",
            raw_days,
            "trade_date",
            key_column="id",
            dedupe_key=("code", "trade_date"),
            code_column="code",
        ),
        DataPolicy(
            "raw_adjustment_factor",
            AdjFactor,
            "raw_market_data",
            "database",
            False,
            "Provider adjustment factor required to reproduce research prices.",
            raw_days,
            "trade_date",
            key_column="id",
            dedupe_key=("code", "trade_date"),
            code_column="code",
        ),
        DataPolicy(
            "raw_daily_basic",
            DailyBasic,
            "raw_market_data",
            "database",
            False,
            "Point-in-time valuation/liquidity inputs used by selection and risk filters.",
            raw_days,
            "trade_date",
            key_column="id",
            dedupe_key=("code", "trade_date"),
            code_column="code",
        ),
        DataPolicy(
            "adjusted_daily_bar",
            DailyBarAdj,
            "materialized_derived_data",
            "database",
            True,
            "Rebuildable adjusted OHLCV kept aligned with the configured raw-data horizon.",
            raw_days,
            "trade_date",
            key_column="id",
            dedupe_key=("code", "trade_date"),
            code_column="code",
        ),
        DataPolicy(
            "factor_values",
            FactorDaily,
            "rebuildable_derived_data",
            "database",
            True,
            "Recent diagnostic cross-sections only; model training uses rebuildable in-memory wide factors.",
            factor_days,
            "trade_date",
            key_column="id",
            dedupe_key=("code", "trade_date", "factor_name"),
            code_column="code",
        ),
        DataPolicy(
            "model_predictions",
            ModelPrediction,
            "derived_serving_data",
            "database",
            True,
            "Recent immutable publication snapshots power the UI; only active serving snapshots are protected.",
            prediction_days,
            "trade_date",
            key_column="id",
            dedupe_key=("code", "trade_date", "horizon", "model_version"),
            preserve_latest_by=("horizon", "model_version"),
            code_column="code",
        ),
        DataPolicy(
            "research_model_runs",
            ResearchModelRun,
            "research_audit",
            "database",
            False,
            "Append-only model configuration/metrics audit; serving flag publishes only completed runs.",
        ),
        DataPolicy(
            "backtest_summaries",
            BacktestSummary,
            "research_audit",
            "database",
            True,
            "Low-volume aggregate evidence is retained permanently for model comparison.",
            key_column="id",
            dedupe_key=("horizon", "model_version", "start_date", "end_date"),
        ),
        DataPolicy(
            "sync_checkpoints",
            DataSyncState,
            "operational_state",
            "database",
            False,
            "One upserted row per provider/dataset/scope; needed for safe incremental resume.",
        ),
        DataPolicy(
            "maintenance_audit",
            DataMaintenanceRun,
            "operational_audit",
            "database",
            False,
            "Compact audit records for destructive maintenance; old runs expire automatically.",
            _days_or_permanent(settings.data_maintenance_audit_retention_days),
            "started_at",
            date_kind="datetime",
            key_column="run_id",
        ),
    )


class DataGovernanceService:
    """Cross-database inventory, retention, de-duplication and space reclamation."""

    def __init__(
        self,
        engine: Engine,
        policies: Iterable[DataPolicy] | None = None,
        batch_size: int | None = None,
    ) -> None:
        settings = get_settings()
        self.engine = engine
        self.policies = tuple(policies or build_data_policies(settings))
        self.batch_size = batch_size or settings.data_maintenance_batch_size
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")

    def policy_manifest(self) -> list[dict[str, Any]]:
        return [policy.manifest() for policy in self.policies] + [
            dict(policy) for policy in NON_TABLE_POLICIES
        ]

    def inventory(
        self,
        session: Session | None = None,
        *,
        include_quality: bool = False,
    ) -> dict[str, Any]:
        owns_session = session is None
        active_session = session or Session(self.engine, future=True)
        try:
            connection = active_session.connection()
            existing = set(inspect(connection).get_table_names())
            database_policies = [p for p in self.policies if p.table_name in existing]
            estimates, estimates_are_exact = self._row_estimates(active_session, database_policies)
            tables: list[dict[str, Any]] = []
            for policy in database_policies:
                table = policy.model.__table__
                start_value = end_value = None
                if policy.date_column:
                    date_column = table.c[policy.date_column]
                    start_value, end_value = active_session.execute(
                        select(func.min(date_column), func.max(date_column))
                    ).one()
                item = {
                    "table": policy.table_name,
                    "estimated_rows": estimates.get(policy.table_name, 0),
                    "row_count_is_exact": estimates_are_exact,
                    "start_date": self._iso(start_value),
                    "end_date": self._iso(end_value),
                    "category": policy.category,
                    "rebuildable": policy.rebuildable,
                    "retention_days": policy.retention_days,
                    "retention": (
                        "permanent"
                        if policy.retention_days is None
                        else f"{policy.retention_days}d"
                    ),
                }
                if include_quality:
                    item["duplicate_rows"] = self._duplicate_excess(active_session, policy)
                    item["orphan_rows"] = self._orphan_count(active_session, policy, existing)
                tables.append(item)

            states = self._sync_states(active_session, existing)
            maintenance_runs = self._maintenance_runs(active_session, existing)
            return {
                "tables": tables,
                "states": states,
                "maintenance_runs": maintenance_runs,
                "policies": self.policy_manifest(),
                "summary": {
                    "database": connection.dialect.name,
                    "table_count": len(tables),
                    "estimated_total_rows": sum(item["estimated_rows"] for item in tables),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
            }
        finally:
            if owns_session:
                active_session.close()

    def maintain(
        self,
        *,
        dry_run: bool = True,
        deduplicate: bool = True,
        purge_orphans: bool = False,
        compact: bool = False,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        as_of = as_of or date.today()
        run_id = str(uuid.uuid4())
        report: dict[str, Any] = {
            "run_id": run_id,
            "mode": "dry-run" if dry_run else "apply",
            "as_of": as_of.isoformat(),
            "retention": {},
            "duplicates": {},
            "orphans": {},
            "compaction": {"requested": compact, "status": "not-run"},
        }

        if not dry_run:
            DataMaintenanceRun.__table__.create(bind=self.engine, checkfirst=True)
            self._start_audit(run_id)

        try:
            with Session(self.engine, future=True) as session:
                existing = set(inspect(session.connection()).get_table_names())
                for policy in self.policies:
                    if policy.table_name not in existing or policy.retention_days is None:
                        continue
                    cutoff_date = as_of - timedelta(days=policy.retention_days)
                    cutoff: date | datetime
                    if policy.date_kind == "datetime":
                        cutoff = datetime.combine(cutoff_date, time.min)
                    else:
                        cutoff = cutoff_date
                    affected = self._prune_expired(session, policy, cutoff, dry_run=dry_run)
                    report["retention"][policy.table_name] = {
                        "cutoff": cutoff.isoformat(),
                        "rows": affected,
                    }

                if deduplicate:
                    for policy in self.policies:
                        if policy.table_name not in existing or not policy.dedupe_key:
                            continue
                        report["duplicates"][policy.table_name] = self._deduplicate(
                            session, policy, dry_run=dry_run
                        )

                for policy in self.policies:
                    if policy.table_name not in existing or not policy.code_column:
                        continue
                    count = self._orphan_count(session, policy, existing)
                    item: dict[str, Any] = {"detected": count, "removed": 0}
                    if purge_orphans and count:
                        item["removed"] = self._purge_orphans(session, policy, dry_run=dry_run)
                    report["orphans"][policy.table_name] = item

            affected_tables = sorted(
                {
                    table_name
                    for section in ("retention", "duplicates")
                    for table_name, value in report[section].items()
                    if (value.get("rows", 0) if isinstance(value, dict) else value) > 0
                }
                | {
                    table_name
                    for table_name, value in report["orphans"].items()
                    if value.get("removed", 0) > 0
                }
            )
            report["affected_tables"] = affected_tables
            if compact:
                if dry_run:
                    report["compaction"] = {
                        "requested": True,
                        "status": "planned",
                        "tables": affected_tables,
                    }
                elif affected_tables:
                    report["compaction"] = self._compact(affected_tables)
                else:
                    report["compaction"] = {"requested": True, "status": "no-changes", "tables": []}

            report["status"] = "preview" if dry_run else "completed"
            if not dry_run:
                self._finish_audit(run_id, "completed", report)
            return report
        except Exception as exc:
            report["status"] = "failed"
            report["error"] = f"{type(exc).__name__}: {exc}"
            if not dry_run:
                self._finish_audit(run_id, "failed", report, str(exc))
            raise

    def _row_estimates(
        self, session: Session, policies: list[DataPolicy]
    ) -> tuple[dict[str, int], bool]:
        if session.get_bind().dialect.name == "mysql":
            wanted = {policy.table_name for policy in policies}
            rows = session.execute(
                text(
                    "SELECT table_name, table_rows FROM information_schema.tables "
                    "WHERE table_schema = DATABASE()"
                )
            ).all()
            return {
                str(table_name): int(table_rows or 0)
                for table_name, table_rows in rows
                if str(table_name) in wanted
            }, False
        return {
            policy.table_name: int(
                session.execute(select(func.count()).select_from(policy.model.__table__)).scalar_one()
            )
            for policy in policies
        }, True

    @staticmethod
    def _iso(value: date | datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _sync_states(session: Session, existing: set[str]) -> list[dict[str, Any]]:
        if DataSyncState.__table__.name not in existing:
            return []
        states = session.execute(
            select(DataSyncState).order_by(DataSyncState.updated_at.desc())
        ).scalars()
        return [
            {
                "provider": state.provider,
                "dataset": state.dataset,
                "scope": state.scope,
                "last_trade_date": (
                    state.last_trade_date.isoformat() if state.last_trade_date else None
                ),
                "last_row_count": state.last_row_count,
                "status": state.status,
                "error_message": state.error_message,
                "updated_at": state.updated_at.isoformat() if state.updated_at else None,
            }
            for state in states
        ]

    @staticmethod
    def _maintenance_runs(session: Session, existing: set[str]) -> list[dict[str, Any]]:
        if DataMaintenanceRun.__table__.name not in existing:
            return []
        runs = session.execute(
            select(DataMaintenanceRun).order_by(DataMaintenanceRun.started_at.desc()).limit(20)
        ).scalars()
        return [
            {
                "run_id": run.run_id,
                "mode": run.mode,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "error_message": run.error_message,
            }
            for run in runs
        ]

    def _protected_latest_values(
        self, session: Session, policy: DataPolicy
    ) -> set[tuple[Any, ...]]:
        if not policy.preserve_latest_by or not policy.date_column:
            return set()
        table = policy.model.__table__
        group_columns = [table.c[name] for name in policy.preserve_latest_by]
        date_column = table.c[policy.date_column]
        if policy.model is ModelPrediction and inspect(session.get_bind()).has_table(
            ResearchModelRun.__tablename__
        ):
            serving_rows = session.execute(
                select(
                    ResearchModelRun.horizon,
                    ResearchModelRun.model_version,
                    ResearchModelRun.prediction_date,
                ).where(
                    ResearchModelRun.status == "completed",
                    ResearchModelRun.is_serving == 1,
                )
            ).all()
            # Legacy databases without a publication pointer retain the old
            # group-wise protection fallback below.  Once pointers exist, only
            # exact active snapshots bypass retention; immutable old versions
            # remain reproducible from the permanent run audit.
            if serving_rows:
                return {tuple(row) for row in serving_rows}
        rows = session.execute(
            select(*group_columns, func.max(date_column)).group_by(*group_columns)
        ).all()
        return {tuple(row) for row in rows}

    def _prune_expired(
        self,
        session: Session,
        policy: DataPolicy,
        cutoff: date | datetime,
        *,
        dry_run: bool,
    ) -> int:
        if not policy.key_column or not policy.date_column:
            return 0
        table = policy.model.__table__
        key_column = table.c[policy.key_column]
        date_column = table.c[policy.date_column]
        group_columns = [table.c[name] for name in policy.preserve_latest_by]
        protected = self._protected_latest_values(session, policy)
        affected = 0
        cursor: Any = None
        while True:
            statement = select(key_column, date_column, *group_columns).where(date_column < cutoff)
            if cursor is not None:
                statement = statement.where(key_column > cursor)
            rows = session.execute(
                statement.order_by(key_column).limit(self.batch_size)
            ).all()
            if not rows:
                break
            cursor = rows[-1][0]
            keys: list[Any] = []
            for row in rows:
                protected_value = tuple(row[2:]) + (row[1],)
                if protected and protected_value in protected:
                    continue
                keys.append(row[0])
            affected += len(keys)
            if not dry_run and keys:
                session.execute(delete(table).where(key_column.in_(keys)))
                session.commit()
        return affected

    @staticmethod
    def _group_conditions(columns: list[Any], values: tuple[Any, ...]) -> list[Any]:
        return [
            column.is_(None) if value is None else column == value
            for column, value in zip(columns, values)
        ]

    def _duplicate_excess(self, session: Session, policy: DataPolicy) -> int:
        if not policy.dedupe_key:
            return 0
        if self._has_unique_business_key(session, policy):
            return 0
        table = policy.model.__table__
        columns = [table.c[name] for name in policy.dedupe_key]
        counts = session.execute(
            select(func.count()).select_from(table).group_by(*columns).having(func.count() > 1)
        ).scalars()
        return sum(int(count) - 1 for count in counts)

    @staticmethod
    def _has_unique_business_key(session: Session, policy: DataPolicy) -> bool:
        inspector = inspect(session.connection())
        expected = tuple(policy.dedupe_key)

        def matches(columns: Any) -> bool:
            values = tuple(columns or ())
            return len(values) == len(expected) and set(values) == set(expected)

        unique_constraints = inspector.get_unique_constraints(policy.table_name)
        if any(matches(item.get("column_names")) for item in unique_constraints):
            return True
        indexes = inspector.get_indexes(policy.table_name)
        return any(
            item.get("unique") and matches(item.get("column_names")) for item in indexes
        )

    def _deduplicate(self, session: Session, policy: DataPolicy, *, dry_run: bool) -> int:
        if not policy.key_column:
            return 0
        if self._has_unique_business_key(session, policy):
            return 0
        if dry_run:
            return self._duplicate_excess(session, policy)
        table = policy.model.__table__
        key_column = table.c[policy.key_column]
        group_columns = [table.c[name] for name in policy.dedupe_key]
        removed = 0
        group_query = (
            select(*group_columns, func.count())
            .select_from(table)
            .group_by(*group_columns)
            .having(func.count() > 1)
            .limit(self.batch_size)
        )
        while True:
            duplicate_groups = session.execute(group_query).all()
            if not duplicate_groups:
                break
            for row in duplicate_groups:
                values = tuple(row[:-1])
                keys = session.execute(
                    select(key_column)
                    .where(and_(*self._group_conditions(group_columns, values)))
                    .order_by(key_column.desc())
                ).scalars().all()
                stale_keys = keys[1:]
                for start in range(0, len(stale_keys), self.batch_size):
                    batch = stale_keys[start : start + self.batch_size]
                    session.execute(delete(table).where(key_column.in_(batch)))
                removed += len(stale_keys)
            session.commit()
        return removed

    @staticmethod
    def _orphan_predicate(policy: DataPolicy) -> Any:
        table = policy.model.__table__
        stock_table = DimStock.__table__
        return ~exists(
            select(1)
            .select_from(stock_table)
            .where(stock_table.c.code == table.c[policy.code_column])
        )

    def _orphan_count(
        self, session: Session, policy: DataPolicy, existing: set[str]
    ) -> int | None:
        if not policy.code_column or DimStock.__table__.name not in existing:
            return None
        table = policy.model.__table__
        return int(
            session.execute(
                select(func.count()).select_from(table).where(self._orphan_predicate(policy))
            ).scalar_one()
        )

    def _purge_orphans(self, session: Session, policy: DataPolicy, *, dry_run: bool) -> int:
        count = self._orphan_count(
            session, policy, {DimStock.__table__.name, policy.table_name}
        )
        if dry_run or not count or not policy.key_column:
            return int(count or 0)
        table = policy.model.__table__
        key_column = table.c[policy.key_column]
        removed = 0
        while True:
            keys = session.execute(
                select(key_column)
                .where(self._orphan_predicate(policy))
                .order_by(key_column)
                .limit(self.batch_size)
            ).scalars().all()
            if not keys:
                break
            session.execute(delete(table).where(key_column.in_(keys)))
            session.commit()
            removed += len(keys)
        return removed

    def _start_audit(self, run_id: str) -> None:
        with Session(self.engine, future=True) as session:
            session.add(
                DataMaintenanceRun(
                    run_id=run_id,
                    mode="apply",
                    status="running",
                    started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
            session.commit()

    def _finish_audit(
        self,
        run_id: str,
        status: str,
        report: dict[str, Any],
        error_message: str | None = None,
    ) -> None:
        encoded = json.dumps(report, ensure_ascii=False, separators=(",", ":"), default=str)
        with Session(self.engine, future=True) as session:
            session.execute(
                update(DataMaintenanceRun)
                .where(DataMaintenanceRun.run_id == run_id)
                .values(
                    status=status,
                    finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    report_json=encoded,
                    error_message=error_message[:4000] if error_message else None,
                )
            )
            session.commit()

    def _compact(self, table_names: list[str]) -> dict[str, Any]:
        dialect = self.engine.dialect.name
        if dialect == "sqlite":
            with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.exec_driver_sql("VACUUM")
            return {"requested": True, "status": "completed", "method": "VACUUM", "tables": table_names}
        if dialect == "mysql":
            preparer = self.engine.dialect.identifier_preparer
            with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                for table_name in table_names:
                    connection.exec_driver_sql(
                        f"OPTIMIZE TABLE {preparer.quote_identifier(table_name)}"
                    )
            return {
                "requested": True,
                "status": "completed",
                "method": "OPTIMIZE TABLE",
                "tables": table_names,
            }
        return {
            "requested": True,
            "status": "unsupported",
            "database": dialect,
            "tables": table_names,
        }
