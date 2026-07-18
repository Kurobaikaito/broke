import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from sqlalchemy.dialects import mysql

from backend.app.research import storage as research_storage
from backend.app.research.backtest import evaluate_predictions
from backend.app.research.diagnostics import select_stable_factors, summarize_factor_performance
from backend.app.research.factors import FACTOR_COLUMNS, calculate_raw_factors, standardize_cross_section
from backend.app.research.modeling import WalkForwardConfig, build_model_panel, walk_forward_predict


class EnhancedFactorTestCase(unittest.TestCase):
    @staticmethod
    def _bars(periods=150, stocks=8):
        rng = np.random.default_rng(2026)
        dates = pd.bdate_range("2023-01-02", periods=periods)
        rows = []
        for stock_index in range(stocks):
            returns = rng.normal(0.0004 + stock_index * 0.00002, 0.012, size=periods)
            closes = 20.0 * np.cumprod(1.0 + returns)
            for date_index, trade_date in enumerate(dates):
                rows.append(
                    {
                        "code": f"{stock_index:06d}",
                        "trade_date": trade_date,
                        "close": closes[date_index],
                        "amount": 50_000_000.0 * (1.0 + 0.01 * stock_index),
                        "turnover_rate": 1.0 + 0.1 * stock_index,
                        "industry": "A" if stock_index < stocks / 2 else "B",
                    }
                )
        return pd.DataFrame(rows)

    def test_factor_values_do_not_change_when_future_bars_are_appended(self):
        bars = self._bars()
        cutoff = pd.Timestamp(sorted(bars["trade_date"].unique())[130])
        full = calculate_raw_factors(bars)
        truncated = calculate_raw_factors(bars[bars["trade_date"].le(cutoff)])
        full_cutoff = full[full["trade_date"].eq(cutoff)].sort_values("code")
        truncated_cutoff = truncated[truncated["trade_date"].eq(cutoff)].sort_values("code")
        np.testing.assert_allclose(
            full_cutoff[FACTOR_COLUMNS].to_numpy(),
            truncated_cutoff[FACTOR_COLUMNS].to_numpy(),
            equal_nan=True,
        )

    def test_gap_invalidates_only_factors_whose_window_crosses_it(self):
        bars = self._bars(periods=130, stocks=2)
        dates = sorted(bars["trade_date"].unique())
        gap_date = dates[-10]
        bars = bars[~((bars["code"] == "000000") & (bars["trade_date"] == gap_date))]
        factors = calculate_raw_factors(bars)
        latest = factors[(factors["code"] == "000000") & (factors["trade_date"] == dates[-1])].iloc[0]
        self.assertTrue(pd.isna(latest["momentum_20d"]))
        self.assertFalse(pd.isna(latest["reversal_5d"]))

    def test_optional_industry_neutralization_centers_each_group(self):
        raw = self._bars(periods=1, stocks=12)[["code", "trade_date", "industry"]]
        for factor_index, factor in enumerate(FACTOR_COLUMNS):
            raw[factor] = np.arange(len(raw), dtype=float) + factor_index
        wide, _ = standardize_cross_section(raw, neutralize_by="industry")
        group_means = wide.groupby("industry")["momentum_20d"].mean()
        np.testing.assert_allclose(group_means.to_numpy(), np.zeros(len(group_means)), atol=1e-12)


class DiagnosticsAndModelTestCase(unittest.TestCase):
    def test_non_positive_execution_price_never_creates_a_label(self):
        dates = pd.bdate_range("2024-01-02", periods=6)
        bars = pd.DataFrame(
            {
                "code": "000001",
                "trade_date": dates,
                "open": [10.0, 0.0, 11.0, 12.0, 13.0, 14.0],
                "close": [10.0, 10.5, 11.0, 12.0, 13.0, 14.0],
                "amount": 100_000_000.0,
            }
        )
        factors = bars[["code", "trade_date"]].copy()
        for factor in FACTOR_COLUMNS:
            factors[factor] = 0.0
        panel = build_model_panel(bars, factors, horizon=1)
        self.assertTrue(pd.isna(panel.iloc[0]["forward_return"]))
        self.assertTrue(pd.isna(panel.iloc[0]["label"]))

    def test_training_rank_ic_selects_planted_factor(self):
        rng = np.random.default_rng(7)
        rows = []
        for trade_date in pd.bdate_range("2024-01-02", periods=30):
            planted = rng.normal(size=20)
            target = planted + rng.normal(scale=0.05, size=20)
            for stock_index in range(20):
                row = {
                    "code": f"{stock_index:06d}",
                    "trade_date": trade_date,
                    "forward_return": target[stock_index],
                }
                row.update({factor: rng.normal() for factor in FACTOR_COLUMNS})
                row["momentum_20d"] = planted[stock_index]
                rows.append(row)
        panel = pd.DataFrame(rows)
        selected = select_stable_factors(panel, max_features=1, min_dates=20)
        summary = summarize_factor_performance(panel)
        planted_ic = summary.loc[summary["factor_name"].eq("momentum_20d"), "mean_rank_ic"].iloc[0]
        self.assertEqual(selected, ["momentum_20d"])
        self.assertGreater(planted_ic, 0.95)

    def test_ridge_challenger_is_oos_and_latest_snapshot_is_marked(self):
        rng = np.random.default_rng(42)
        dates = pd.bdate_range("2023-01-02", periods=90)
        rows = []
        for trade_date in dates:
            for stock_index in range(10):
                features = rng.normal(size=len(FACTOR_COLUMNS))
                forward_return = 0.02 * features[0] - 0.01 * features[1] + rng.normal(scale=0.01)
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
        config = WalkForwardConfig(
            horizon=5,
            train_window_days=50,
            min_train_days=20,
            min_train_rows=100,
            top_n=3,
            model_type="ridge",
        )
        predictions = walk_forward_predict(pd.DataFrame(rows), config)
        self.assertFalse(predictions.empty)
        self.assertTrue(predictions["probability"].between(0.0, 1.0).all())
        self.assertTrue((predictions["train_end"] < predictions["trade_date"]).all())
        latest = predictions[predictions["is_latest_snapshot"]]
        self.assertFalse(latest.empty)
        self.assertTrue((~latest["is_scheduled_rebalance"]).all())

    def test_future_labels_cannot_change_earlier_feature_selection_or_scores(self):
        rng = np.random.default_rng(88)
        dates = pd.bdate_range("2023-01-02", periods=45)
        rows = []
        for trade_date in dates:
            for stock_index in range(8):
                features = rng.normal(size=len(FACTOR_COLUMNS))
                forward_return = 0.08 * features[0] + rng.normal(scale=0.003)
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
        original = pd.DataFrame(rows)
        changed_future = original.copy()
        future_mask = changed_future["trade_date"].gt(dates[32])
        changed_future.loc[future_mask, "forward_return"] = (
            -10.0 * changed_future.loc[future_mask, "market_beta_60d"]
        )
        changed_future.loc[future_mask, "label"] = (
            changed_future.loc[future_mask, "forward_return"].gt(0).astype(float)
        )
        config = WalkForwardConfig(
            horizon=5,
            train_window_days=40,
            min_train_days=20,
            min_train_rows=100,
            top_n=2,
            max_features=1,
            min_ic_dates=5,
            include_latest=False,
        )
        baseline = walk_forward_predict(original, config)
        perturbed = walk_forward_predict(changed_future, config)
        baseline_early = baseline[baseline["trade_date"].le(dates[32])].sort_values(["trade_date", "code"])
        perturbed_early = perturbed[perturbed["trade_date"].le(dates[32])].sort_values(["trade_date", "code"])
        self.assertFalse(baseline_early.empty)
        self.assertEqual(
            baseline_early["selected_factors"].tolist(),
            perturbed_early["selected_factors"].tolist(),
        )
        np.testing.assert_allclose(baseline_early["score"], perturbed_early["score"], atol=1e-12)
        np.testing.assert_allclose(baseline_early["probability"], perturbed_early["probability"], atol=1e-12)

    def test_backtest_excludes_unscheduled_live_snapshot(self):
        rows = []
        for trade_date, scheduled in [
            (pd.Timestamp("2024-01-02"), True),
            (pd.Timestamp("2024-01-09"), True),
            (pd.Timestamp("2024-01-10"), False),
        ]:
            rows.extend(
                {
                    "trade_date": trade_date,
                    "code": code,
                    "score": score,
                    "forward_return": realized,
                    "is_scheduled_rebalance": scheduled,
                }
                for code, score, realized in [("A", 2.0, 0.02), ("B", 1.0, -0.01)]
            )
        metrics, periods = evaluate_predictions(pd.DataFrame(rows), horizon=5, top_n=1)
        self.assertEqual(metrics["periods"], 2)
        self.assertEqual(len(periods), 2)


class PredictionStorageAtomicityTestCase(unittest.TestCase):
    class RecordingSession:
        def __init__(self, fail_on_execute=None):
            self.fail_on_execute = fail_on_execute
            self.statements = []
            self.commits = 0
            self.rollbacks = 0

        def execute(self, statement):
            self.statements.append(statement)
            if len(self.statements) == self.fail_on_execute:
                raise RuntimeError("simulated second-chunk failure")

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    @staticmethod
    def _predictions(dates):
        return pd.DataFrame(
            [
                {
                    "code": f"{index:06d}",
                    "trade_date": trade_date,
                    "score": 1.0 - index * 0.1,
                    "probability": 0.6,
                    "rank_no": index + 1,
                    "factor_snapshot": [],
                    "risk_flags": [],
                }
                for index, trade_date in enumerate(dates)
            ]
        )

    def test_second_chunk_failure_rolls_back_the_whole_trade_date(self):
        def two_row_chunks(records, size=2000):
            del size
            for start in range(0, len(records), 2):
                yield records[start : start + 2]

        session = self.RecordingSession(fail_on_execute=2)
        predictions = self._predictions([pd.Timestamp("2024-01-02")] * 3)
        with patch.object(research_storage, "_chunks", two_row_chunks):
            with self.assertRaisesRegex(RuntimeError, "second-chunk"):
                research_storage.save_predictions(session, predictions, 20, "model-v2")
        self.assertEqual(len(session.statements), 2)
        self.assertEqual(session.commits, 0)
        self.assertEqual(session.rollbacks, 1)

    def test_each_trade_date_commits_once_and_rerun_refreshes_created_at(self):
        session = self.RecordingSession()
        predictions = self._predictions(
            [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
        )
        saved = research_storage.save_predictions(session, predictions, 20, "model-v2")
        self.assertEqual(saved, 2)
        self.assertEqual(session.commits, 2)
        self.assertEqual(session.rollbacks, 0)
        for statement in session.statements:
            compiled = str(statement.compile(dialect=mysql.dialect())).lower()
            self.assertIn("created_at = now()", compiled)


if __name__ == "__main__":
    unittest.main()
