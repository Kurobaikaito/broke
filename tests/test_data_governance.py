import unittest
from dataclasses import replace
from datetime import date

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.db import Base
from backend.app.models import (
    DailyBar,
    DataMaintenanceRun,
    DimStock,
    FactorDaily,
    ModelPrediction,
    ResearchModelRun,
)
from backend.app.services.data_governance import (
    DataGovernanceService,
    DataPolicy,
    build_data_policies,
)


class DataGovernanceTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        settings = replace(
            get_settings(),
            data_raw_retention_days=0,
            data_factor_retention_days=30,
            data_prediction_retention_days=30,
            data_maintenance_audit_retention_days=10,
            data_maintenance_batch_size=2,
        )
        self.service = DataGovernanceService(
            self.engine,
            policies=build_data_policies(settings),
            batch_size=2,
        )

    def tearDown(self):
        self.engine.dispose()

    def test_preview_is_read_only_and_latest_prediction_run_is_protected(self):
        with Session(self.engine) as session:
            session.add(DimStock(code="000001", name="平安银行", exchange="SZSE"))
            session.add_all(
                [
                    FactorDaily(
                        code="000001",
                        trade_date=date(2024, 1, 1),
                        factor_name="momentum",
                        factor_value=1,
                    ),
                    FactorDaily(
                        code="000001",
                        trade_date=date(2024, 3, 20),
                        factor_name="momentum",
                        factor_value=2,
                    ),
                    ModelPrediction(
                        code="000001",
                        trade_date=date(2024, 1, 1),
                        horizon="20d",
                        model_version="model-v1",
                        score=1,
                        probability=0.5,
                    ),
                    ModelPrediction(
                        code="000001",
                        trade_date=date(2024, 2, 1),
                        horizon="20d",
                        model_version="model-v1",
                        score=2,
                        probability=0.6,
                    ),
                ]
            )
            session.commit()

        preview = self.service.maintain(dry_run=True, as_of=date(2024, 4, 1))

        self.assertEqual(preview["retention"]["factor_daily"]["rows"], 1)
        self.assertEqual(preview["retention"]["model_prediction"]["rows"], 1)
        with Session(self.engine) as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(FactorDaily)), 2)
            self.assertEqual(session.scalar(select(func.count()).select_from(ModelPrediction)), 2)
            self.assertEqual(session.scalar(select(func.count()).select_from(DataMaintenanceRun)), 0)

        applied = self.service.maintain(
            dry_run=False,
            compact=True,
            as_of=date(2024, 4, 1),
        )

        self.assertEqual(applied["status"], "completed")
        self.assertEqual(applied["compaction"]["method"], "VACUUM")
        with Session(self.engine) as session:
            factors = session.scalars(select(FactorDaily).order_by(FactorDaily.trade_date)).all()
            predictions = session.scalars(
                select(ModelPrediction).order_by(ModelPrediction.trade_date)
            ).all()
            audits = session.scalars(select(DataMaintenanceRun)).all()
        self.assertEqual([row.trade_date for row in factors], [date(2024, 3, 20)])
        self.assertEqual([row.trade_date for row in predictions], [date(2024, 2, 1)])
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].status, "completed")

    def test_orphans_are_reported_and_only_removed_when_explicit(self):
        with Session(self.engine) as session:
            session.add(
                DailyBar(code="999999", trade_date=date(2024, 3, 1), close=10)
            )
            session.commit()

        preview = self.service.maintain(dry_run=True, as_of=date(2024, 4, 1))
        self.assertEqual(preview["orphans"]["daily_bar"], {"detected": 1, "removed": 0})

        self.service.maintain(
            dry_run=False,
            purge_orphans=True,
            as_of=date(2024, 4, 1),
        )
        with Session(self.engine) as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(DailyBar)), 0)

    def test_only_exact_serving_prediction_snapshot_bypasses_retention(self):
        with Session(self.engine) as session:
            session.add(DimStock(code="000001", name="平安银行", exchange="SZSE"))
            for version in ("old-publication", "serving-publication"):
                session.add(
                    ModelPrediction(
                        code="000001",
                        trade_date=date(2024, 1, 1),
                        horizon="20d",
                        model_version=version,
                        score=1,
                        probability=0.5,
                    )
                )
            session.add(
                ResearchModelRun(
                    run_id="serving-run",
                    horizon="20d",
                    model_version="serving-publication",
                    prediction_date=date(2024, 1, 1),
                    status="completed",
                    is_serving=1,
                    prediction_count=1,
                    latest_prediction_count=1,
                    start_date=date(2023, 1, 1),
                    end_date=date(2024, 1, 1),
                    config_json="{}",
                    metrics_json="{}",
                )
            )
            session.commit()

        report = self.service.maintain(dry_run=False, as_of=date(2024, 4, 1))

        self.assertEqual(report["retention"]["model_prediction"]["rows"], 1)
        with Session(self.engine) as session:
            versions = session.scalars(select(ModelPrediction.model_version)).all()
        self.assertEqual(versions, ["serving-publication"])

    def test_inventory_is_sqlite_compatible_and_exposes_storage_decisions(self):
        with Session(self.engine) as session:
            session.add(DimStock(code="600000", name="浦发银行", exchange="SSE"))
            session.commit()

        inventory = self.service.inventory(include_quality=True)

        stock_table = next(row for row in inventory["tables"] if row["table"] == "dim_stock")
        self.assertEqual(stock_table["estimated_rows"], 1)
        self.assertTrue(stock_table["row_count_is_exact"])
        self.assertEqual(inventory["summary"]["database"], "sqlite")
        log_policy = next(
            row for row in inventory["policies"] if row["name"] == "sync_job_logs"
        )
        self.assertEqual(log_policy["storage"], "memory")

    def test_derived_retention_is_not_longer_than_explicit_raw_retention(self):
        settings = replace(
            get_settings(),
            data_raw_retention_days=365,
            data_factor_retention_days=1095,
            data_prediction_retention_days=730,
        )
        policies = {policy.name: policy for policy in build_data_policies(settings)}

        self.assertEqual(policies["raw_daily_bar"].retention_days, 365)
        self.assertEqual(policies["adjusted_daily_bar"].retention_days, 365)
        self.assertEqual(policies["factor_values"].retention_days, 365)
        self.assertEqual(policies["model_predictions"].retention_days, 365)


class LegacyDuplicateCleanupTestCase(unittest.TestCase):
    def test_business_key_duplicates_are_removed_without_mysql_specific_sql(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE TABLE daily_bar ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, code VARCHAR(16) NOT NULL, "
                        "trade_date DATE NOT NULL, open NUMERIC, high NUMERIC, low NUMERIC, "
                        "close NUMERIC, volume NUMERIC, amount NUMERIC, pct_chg NUMERIC, "
                        "turnover_rate NUMERIC, created_at DATETIME)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO daily_bar (code, trade_date, close) VALUES "
                        "('000001', '2024-01-02', 10), ('000001', '2024-01-02', 11)"
                    )
                )
            policy = DataPolicy(
                name="raw_daily_bar",
                model=DailyBar,
                category="raw_market_data",
                storage="database",
                rebuildable=False,
                description="test",
                key_column="id",
                dedupe_key=("code", "trade_date"),
            )
            service = DataGovernanceService(engine, policies=(policy,), batch_size=1)

            preview = service.maintain(dry_run=True)
            applied = service.maintain(dry_run=False)

            self.assertEqual(preview["duplicates"]["daily_bar"], 1)
            self.assertEqual(applied["duplicates"]["daily_bar"], 1)
            with engine.connect() as connection:
                rows = connection.execute(text("SELECT id, close FROM daily_bar")).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].close, 11)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
