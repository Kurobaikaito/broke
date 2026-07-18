import argparse
import unittest
from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db import Base
from backend.app.models import DailyBarAdj, DimStock
from backend.app.research.factors import (
    FACTOR_COLUMNS,
    factor_frames_to_long,
    standardize_cross_section,
)
from backend.app.research.modeling import WalkForwardConfig, walk_forward_predict
from backend.app.research.storage import resolve_recent_start_date, save_factors
from scripts.run_research import (
    baseline_is_compatible,
    create_publication_version,
    research_fingerprint,
    resolve_run_mode,
)


def research_args(**overrides):
    values = {
        "industry_neutral": False,
        "model_type": "logistic",
        "max_features": None,
        "half_life_days": None,
        "train_window_days": 756,
        "min_train_days": 252,
        "min_train_rows": 1000,
        "min_amount": 20_000_000.0,
        "top_n": 20,
        "initial_capital": 50_000.0,
        "commission_bps": 2.5,
        "stamp_duty_bps": 5.0,
        "slippage_bps": 2.5,
        "start_date": "20180101",
        "end_date": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class IncrementalFactorMaterializationTestCase(unittest.TestCase):
    def test_wide_only_path_materializes_no_history_and_latest_slice_is_exact(self):
        dates = pd.to_datetime(["2026-07-15", "2026-07-16"])
        rows = []
        for trade_date in dates:
            for stock_index in range(10):
                row = {"code": f"{stock_index:06d}", "trade_date": trade_date}
                row.update(
                    {
                        factor: float(stock_index + factor_index)
                        for factor_index, factor in enumerate(FACTOR_COLUMNS)
                    }
                )
                rows.append(row)
        raw = pd.DataFrame(rows)

        wide, long_history = standardize_cross_section(raw, materialize_long=False)
        latest_mask = raw["trade_date"].eq(dates[-1])
        latest_long = factor_frames_to_long(raw.loc[latest_mask], wide.loc[latest_mask])

        self.assertTrue(long_history.empty)
        self.assertEqual(len(latest_long), 10 * len(FACTOR_COLUMNS))
        first = latest_long[
            latest_long["code"].eq("000000")
            & latest_long["factor_name"].eq("momentum_20d")
        ].iloc[0]
        self.assertEqual(first["trade_date"], dates[-1])
        self.assertEqual(first["factor_value"], 0.0)


class LatestPredictionScopeTestCase(unittest.TestCase):
    @staticmethod
    def panel() -> pd.DataFrame:
        rng = np.random.default_rng(20260716)
        rows = []
        for trade_date in pd.bdate_range("2024-01-02", periods=70):
            for stock_index in range(8):
                features = rng.normal(size=len(FACTOR_COLUMNS))
                forward_return = 0.025 * features[0] - 0.01 * features[1] + rng.normal(scale=0.02)
                row = {
                    "code": f"{stock_index:06d}",
                    "trade_date": trade_date,
                    "amount": 100_000_000.0,
                    "eligible": True,
                    "label": float(forward_return > 0),
                    "forward_return": forward_return,
                }
                row.update(dict(zip(FACTOR_COLUMNS, features)))
                rows.append(row)
        return pd.DataFrame(rows)

    def test_latest_scope_matches_full_walk_forward_latest_snapshot(self):
        config = WalkForwardConfig(
            horizon=5,
            train_window_days=40,
            min_train_days=20,
            min_train_rows=100,
            top_n=3,
            explanation_scope="latest",
        )
        panel = self.panel()

        full = walk_forward_predict(panel, config)
        latest = walk_forward_predict(panel, replace(config, prediction_scope="latest"))
        expected = full[full["is_latest_snapshot"]].sort_values("code").reset_index(drop=True)
        actual = latest.sort_values("code").reset_index(drop=True)

        self.assertEqual(actual["trade_date"].nunique(), 1)
        assert_frame_equal(
            actual[["code", "score", "probability", "rank_no", "train_start", "train_end"]],
            expected[["code", "score", "probability", "rank_no", "train_start", "train_end"]],
            check_exact=False,
            rtol=1e-12,
            atol=1e-12,
        )
        self.assertEqual(actual["selected_factors"].tolist(), expected["selected_factors"].tolist())
        self.assertEqual(actual["factor_snapshot"].tolist(), expected["factor_snapshot"].tolist())


class ResearchModeFingerprintTestCase(unittest.TestCase):
    @staticmethod
    def baseline(fingerprint):
        metrics = {
            "top_group_return": 0.1,
            "benchmark_return": 0.05,
            "win_rate": 0.55,
            "max_drawdown": -0.1,
            "sharpe": 1.1,
            "rank_ic": 0.03,
            "turnover": 1.0,
            "periods": 20,
        }
        return {"config": {"config_fingerprint": fingerprint}, "metrics": metrics}

    def test_auto_uses_latest_only_for_a_matching_validated_baseline(self):
        fingerprint = research_fingerprint(research_args(), 20, "strategy-v1")
        baseline = self.baseline(fingerprint)

        self.assertTrue(baseline_is_compatible(baseline, fingerprint))
        self.assertEqual(resolve_run_mode("auto", [20], {20: baseline}, {20: fingerprint}), "latest")
        self.assertEqual(resolve_run_mode("auto", [20], {}, {20: fingerprint}), "full")
        with self.assertRaisesRegex(ValueError, "compatible"):
            resolve_run_mode("latest", [20], {}, {20: fingerprint})

    def test_capital_change_invalidates_the_backtest_fingerprint(self):
        baseline = research_fingerprint(research_args(initial_capital=50_000), 20, "strategy-v1")
        changed = research_fingerprint(research_args(initial_capital=80_000), 20, "strategy-v1")
        self.assertNotEqual(baseline, changed)

    def test_publication_versions_are_unique_bounded_immutable_namespaces(self):
        first = create_publication_version("x" * 64)
        second = create_publication_version("x" * 64)
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 64)
        self.assertTrue(first.startswith("x"))


class RecentWindowAndFactorTransactionTestCase(unittest.TestCase):
    def test_recent_start_date_counts_only_supported_market_sessions(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        supported_dates = pd.bdate_range("2026-07-06", periods=5).date
        with Session(engine) as session:
            session.add_all(
                [
                    DimStock(code="000001", name="深市", exchange="SZSE"),
                    DimStock(code="920001", name="北交", exchange="BSE"),
                ]
            )
            session.add_all(
                [DailyBarAdj(code="000001", trade_date=trade_date) for trade_date in supported_dates]
            )
            session.add(DailyBarAdj(code="920001", trade_date=date(2026, 7, 20)))
            session.commit()

        self.assertEqual(
            resolve_recent_start_date(engine, 3),
            pd.Timestamp(supported_dates[2]).strftime("%Y%m%d"),
        )
        self.assertEqual(
            resolve_recent_start_date(engine, 2, pd.Timestamp(supported_dates[3]).strftime("%Y%m%d")),
            pd.Timestamp(supported_dates[2]).strftime("%Y%m%d"),
        )
        engine.dispose()

    def test_factor_batches_commit_once_and_rollback_as_one_slice(self):
        class RecordingSession:
            def __init__(self, fail_on_execute=None):
                self.fail_on_execute = fail_on_execute
                self.executes = 0
                self.commits = 0
                self.rollbacks = 0

            def execute(self, _statement):
                self.executes += 1
                if self.executes == self.fail_on_execute:
                    raise RuntimeError("simulated batch failure")

            def commit(self):
                self.commits += 1

            def rollback(self):
                self.rollbacks += 1

        frame = pd.DataFrame(
            {
                "code": ["000001", "000002"],
                "trade_date": pd.to_datetime(["2026-07-16", "2026-07-16"]),
                "factor_name": ["momentum_20d", "momentum_20d"],
                "factor_value": [0.1, 0.2],
                "factor_zscore": [-1.0, 1.0],
            }
        )
        success = RecordingSession()
        self.assertEqual(save_factors(success, frame, batch_size=1), 2)
        self.assertEqual((success.commits, success.rollbacks), (1, 0))

        failed = RecordingSession(fail_on_execute=2)
        with self.assertRaisesRegex(RuntimeError, "batch failure"):
            save_factors(failed, frame, batch_size=1)
        self.assertEqual((failed.commits, failed.rollbacks), (0, 1))


if __name__ == "__main__":
    unittest.main()
