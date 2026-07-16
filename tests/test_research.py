import unittest
from threading import Barrier

import numpy as np
import pandas as pd

from backend.app.research.backtest import evaluate_predictions, max_drawdown
from backend.app.research.factors import FACTOR_COLUMNS, calculate_raw_factors, standardize_cross_section
from backend.app.research.modeling import WalkForwardConfig, build_model_panel, walk_forward_predict
from backend.app.services.data_sources import (
    TushareClient,
    filter_supported_market,
    normalize_ts_code,
    tushare_daily_bundle_records,
    validate_tushare_frames,
)


class FactorEngineTestCase(unittest.TestCase):
    def test_momentum_uses_exact_trailing_observations(self):
        dates = pd.bdate_range("2024-01-01", periods=80)
        bars = pd.DataFrame(
            {
                "code": "000001",
                "trade_date": dates,
                "close": np.arange(1.0, 81.0),
                "amount": 100_000_000.0,
                "turnover_rate": 1.0,
            }
        )
        factors = calculate_raw_factors(bars)
        self.assertAlmostEqual(factors.iloc[60]["momentum_20d"], 61.0 / 41.0 - 1.0)
        self.assertAlmostEqual(factors.iloc[60]["momentum_60d"], 60.0)

    def test_cross_section_zscore_has_zero_mean_and_unit_variance(self):
        rows = []
        for index in range(10):
            row = {"code": f"{index:06d}", "trade_date": pd.Timestamp("2024-01-02")}
            row.update({name: float(index) for name in FACTOR_COLUMNS})
            rows.append(row)
        wide, _ = standardize_cross_section(pd.DataFrame(rows))
        self.assertAlmostEqual(float(wide["momentum_20d"].mean()), 0.0, places=12)
        self.assertAlmostEqual(float(wide["momentum_20d"].std(ddof=0)), 1.0, places=12)


class LabelAndBacktestTestCase(unittest.TestCase):
    def test_label_is_next_open_to_horizon_exit_open(self):
        dates = pd.bdate_range("2024-01-01", periods=8)
        bars = pd.DataFrame(
            {
                "code": "000001",
                "trade_date": dates,
                "open": np.arange(10.0, 18.0),
                "close": np.arange(10.5, 18.5),
                "amount": 100_000_000.0,
            }
        )
        factors = bars[["code", "trade_date"]].copy()
        for name in FACTOR_COLUMNS:
            factors[name] = 0.0
        panel = build_model_panel(bars, factors, horizon=2)
        self.assertAlmostEqual(panel.iloc[0]["entry_open"], 11.0)
        self.assertAlmostEqual(panel.iloc[0]["exit_open"], 13.0)
        self.assertAlmostEqual(panel.iloc[0]["forward_return"], 13.0 / 11.0 - 1.0)

    def test_missing_next_market_day_is_not_skipped(self):
        dates = pd.bdate_range("2024-01-01", periods=5)
        bars = pd.DataFrame(
            {
                "code": ["A"] * 4 + ["B"] * 5,
                "trade_date": list(dates[[0, 2, 3, 4]]) + list(dates),
                "open": [10.0] * 9,
                "close": [10.0] * 9,
                "amount": [100_000_000.0] * 9,
            }
        )
        factors = bars[["code", "trade_date"]].copy()
        for name in FACTOR_COLUMNS:
            factors[name] = 0.0
        panel = build_model_panel(bars, factors, horizon=1)
        first_a = panel[(panel["code"] == "A") & (panel["trade_date"] == dates[0])].iloc[0]
        self.assertTrue(pd.isna(first_a["entry_open"]))

    def test_costs_and_drawdown(self):
        rows = []
        for date in pd.to_datetime(["2024-01-02", "2024-01-09"]):
            rows.extend(
                [
                    {"trade_date": date, "code": "A", "score": 3.0, "forward_return": 0.10},
                    {"trade_date": date, "code": "B", "score": 2.0, "forward_return": 0.05},
                    {"trade_date": date, "code": "C", "score": 1.0, "forward_return": 0.00},
                ]
            )
        metrics, periods = evaluate_predictions(pd.DataFrame(rows), horizon=5, top_n=1, transaction_cost_bps=10)
        self.assertAlmostEqual(periods.iloc[0]["net_return"], 0.099)
        self.assertAlmostEqual(periods.iloc[1]["net_return"], 0.10)
        self.assertAlmostEqual(metrics["turnover"], 0.5)
        self.assertAlmostEqual(metrics["rank_ic"], 1.0)
        self.assertAlmostEqual(max_drawdown(pd.Series([-0.10, 0.05])), -0.10)


class WalkForwardModelTestCase(unittest.TestCase):
    def test_predictions_are_out_of_sample_and_probabilities_are_bounded(self):
        rng = np.random.default_rng(42)
        dates = pd.bdate_range("2023-01-02", periods=70)
        rows = []
        for date_index, trade_date in enumerate(dates):
            for stock_index in range(8):
                features = rng.normal(size=len(FACTOR_COLUMNS))
                forward_return = 0.03 * features[0] + rng.normal(scale=0.02)
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
            train_window_days=40,
            min_train_days=20,
            min_train_rows=100,
            top_n=2,
        )
        predictions = walk_forward_predict(pd.DataFrame(rows), config)
        self.assertFalse(predictions.empty)
        self.assertTrue(predictions["probability"].between(0.0, 1.0).all())
        self.assertTrue((predictions["train_end"] < predictions["trade_date"]).all())
        self.assertEqual(len(predictions.iloc[0]["factor_snapshot"]), len(FACTOR_COLUMNS))


class TushareConversionTestCase(unittest.TestCase):
    def test_daily_endpoints_are_requested_concurrently(self):
        barrier = Barrier(3)

        class ConcurrentPro:
            def query(self, api_name, fields="", **kwargs):
                barrier.wait(timeout=2)
                records = {
                    "daily": {
                        "ts_code": "000001.SZ",
                        "trade_date": "20240102",
                        "open": 10,
                    },
                    "adj_factor": {
                        "ts_code": "000001.SZ",
                        "trade_date": "20240102",
                        "adj_factor": 2,
                    },
                    "daily_basic": {
                        "ts_code": "000001.SZ",
                        "trade_date": "20240102",
                        "turnover_rate": 1,
                    },
                }
                return pd.DataFrame([records[api_name]])

        client = TushareClient.__new__(TushareClient)
        client.pro = ConcurrentPro()

        daily, factors, basics = client.daily_frames("20240102")

        self.assertEqual((len(daily), len(factors), len(basics)), (1, 1, 1))

    def test_default_page_size_uses_official_six_thousand_row_limit(self):
        class EmptyPro:
            def __init__(self):
                self.limits = []

            def query(self, api_name, fields="", **kwargs):
                self.limits.append(kwargs["limit"])
                return pd.DataFrame()

        client = TushareClient.__new__(TushareClient)
        client.pro = EmptyPro()

        client.query_all("daily", "ts_code")

        self.assertEqual(client.pro.limits, [6000])

    def test_market_filter_excludes_beijing_rows(self):
        frame = pd.DataFrame(
            {
                "ts_code": ["600000.SH", "000001.SZ", "920000.BJ"],
                "value": [1, 2, 3],
            }
        )

        filtered = filter_supported_market(frame)

        self.assertEqual(filtered["ts_code"].tolist(), ["600000.SH", "000001.SZ"])

    def test_units_and_static_adjustment_are_correct(self):
        daily = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240102",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "pct_chg": 5.0,
                    "vol": 123.0,
                    "amount": 456.0,
                }
            ]
        )
        factors = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20240102", "adj_factor": 2.5}])
        basics = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240102",
                    "turnover_rate": 1.2,
                    "pe_ttm": 8.0,
                    "pb": 1.0,
                    "ps_ttm": 2.0,
                    "total_mv": 100.0,
                    "circ_mv": 80.0,
                }
            ]
        )
        bundle = tushare_daily_bundle_records(daily, factors, basics)
        self.assertEqual(normalize_ts_code("000001.SZ"), "000001")
        self.assertEqual(float(bundle["daily"][0]["volume"]), 12_300.0)
        self.assertEqual(float(bundle["daily"][0]["amount"]), 456_000.0)
        self.assertEqual(float(bundle["adjusted"][0]["close"]), 26.25)
        self.assertEqual(float(bundle["basics"][0]["total_mv"]), 1_000_000.0)

    def test_validation_accepts_consistent_bundle(self):
        daily = pd.DataFrame(
            [{"ts_code": "000001.SZ", "trade_date": "20240102", "open": 10, "high": 11, "low": 9, "close": 10.5, "vol": 100, "amount": 1000}]
        )
        factors = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20240102", "adj_factor": 2.0}])
        basics = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20240102", "turnover_rate": 1.0}])
        report = validate_tushare_frames(daily, factors, basics, "20240102")
        self.assertEqual(report["daily_rows"], 1)
        self.assertEqual(report["basic_coverage"], 1.0)

    def test_pagination_reads_until_short_page(self):
        class FakePro:
            def __init__(self):
                self.offsets = []

            def query(self, api_name, fields="", **kwargs):
                self.offsets.append(kwargs["offset"])
                size = 3 if kwargs["offset"] == 0 else 1
                return pd.DataFrame({"value": range(kwargs["offset"], kwargs["offset"] + size)})

        client = TushareClient.__new__(TushareClient)
        client.pro = FakePro()
        result = client.query_all("daily", "value", page_size=3)
        self.assertEqual(len(result), 4)
        self.assertEqual(client.pro.offsets, [0, 3])


if __name__ == "__main__":
    unittest.main()
