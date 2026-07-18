import unittest
from datetime import date

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from backend.app.db import Base
from backend.app.models import DailyBar, DimStock, ModelPrediction, ResearchModelRun
from backend.app.repositories import MysqlRepository
from backend.app.research.storage import publish_model_run


class PublishedModelServingTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    @staticmethod
    def frame(score: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "code": ["000001"],
                "trade_date": [pd.Timestamp("2026-07-16")],
                "score": [score],
            }
        )

    def test_repository_uses_one_completed_serving_run_for_all_views(self):
        with Session(self.engine) as session:
            session.add(DimStock(code="000001", name="平安银行", exchange="SZSE", industry="银行"))
            session.add(DailyBar(code="000001", trade_date=date(2026, 7, 16), close=12))
            for version, score in (("logistic-v2", 0.3), ("ridge-v1", 0.8)):
                session.add(
                    ModelPrediction(
                        code="000001",
                        trade_date=date(2026, 7, 16),
                        horizon="20d",
                        model_version=version,
                        score=score,
                        probability=0.6,
                        rank_no=1,
                        factor_snapshot="[]",
                        risk_flags="[]",
                    )
                )
            session.commit()
            self.assertEqual(MysqlRepository(session).recommendations("20d", 10), [])
            first_run = publish_model_run(
                session,
                horizon=20,
                model_version="logistic-v2",
                predictions=self.frame(0.3),
                start_date=date(2024, 1, 1),
                end_date=date(2026, 7, 16),
                metrics={"top_group_return": 0.1, "rank_ic": 0.03},
                config={"initial_capital": 50_000},
            )
            second_run = publish_model_run(
                session,
                horizon=20,
                model_version="ridge-v1",
                predictions=self.frame(0.8),
                start_date=date(2024, 1, 1),
                end_date=date(2026, 7, 16),
                metrics={"top_group_return": 0.2, "rank_ic": 0.05, "ending_capital": 60_000},
                config={"initial_capital": 50_000, "strategy_version": "ridge-return-pv15-v1"},
            )

            repository = MysqlRepository(session)
            recommendations = repository.recommendations("20d", 10)
            explanation = repository.stock_explanation("000001", "20d")
            backtest = repository.backtest_summary("20d")
            runs = session.scalars(select(ResearchModelRun).order_by(ResearchModelRun.created_at)).all()

        self.assertNotEqual(first_run, second_run)
        self.assertEqual(len(runs), 2)
        self.assertEqual(sum(run.is_serving for run in runs), 1)
        self.assertEqual(recommendations[0]["score"], 0.8)
        self.assertEqual(recommendations[0]["model_version"], "ridge-return-pv15-v1")
        self.assertEqual(recommendations[0]["publication_version"], "ridge-v1")
        self.assertEqual(explanation["method"], "ridge-return-pv15-v1")
        self.assertEqual(explanation["publication_version"], "ridge-v1")
        self.assertEqual(backtest["run_id"], second_run)
        self.assertEqual(backtest["model_version"], "ridge-return-pv15-v1")
        self.assertEqual(backtest["publication_version"], "ridge-v1")
        self.assertEqual(backtest["top_group_return"], 0.2)
        self.assertEqual(backtest["initial_capital"], 50_000)

    def test_each_publish_keeps_an_append_only_configuration_audit(self):
        with Session(self.engine) as session:
            session.add_all(
                [
                    ModelPrediction(
                        code="000001",
                        trade_date=date(2026, 7, 16),
                        horizon="5d",
                        model_version=version,
                        score=0.1,
                        probability=0.5,
                        rank_no=1,
                    )
                    for version in ("model-v1-a", "model-v1-b")
                ]
            )
            session.commit()
            publish_model_run(
                session,
                horizon=5,
                model_version="model-v1-a",
                predictions=self.frame(0.1),
                start_date=date(2025, 1, 1),
                end_date=date(2026, 7, 16),
                metrics={"top_group_return": 0.01},
                config={"initial_capital": 10_000},
            )
            publish_model_run(
                session,
                horizon=5,
                model_version="model-v1-b",
                predictions=self.frame(0.1),
                start_date=date(2025, 1, 1),
                end_date=date(2026, 7, 16),
                metrics={"top_group_return": 0.02},
                config={"initial_capital": 80_000},
            )
            count = session.scalar(select(func.count()).select_from(ResearchModelRun))
            capitals = [run.config_json for run in session.scalars(select(ResearchModelRun)).all()]
        self.assertEqual(count, 2)
        self.assertTrue(any("10000" in value for value in capitals))
        self.assertTrue(any("80000" in value for value in capitals))

    def test_publication_rejects_a_snapshot_that_was_not_fully_persisted(self):
        with Session(self.engine) as session:
            with self.assertRaisesRegex(ValueError, "persisted prediction snapshot count"):
                publish_model_run(
                    session,
                    horizon=60,
                    model_version="missing-publication",
                    predictions=self.frame(0.1),
                    start_date=date(2025, 1, 1),
                    end_date=date(2026, 7, 16),
                    metrics={"top_group_return": 0.01},
                    config={"initial_capital": 50_000},
                )


if __name__ == "__main__":
    unittest.main()
