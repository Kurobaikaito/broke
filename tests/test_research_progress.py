import json
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from backend.app.research.factors import (
    FACTOR_COLUMNS,
    calculate_raw_factors,
    standardize_cross_section,
)
from backend.app.research.modeling import (
    WalkForwardConfig,
    prediction_window_count,
    walk_forward_predict,
)
from backend.app.services.research_jobs import (
    ResearchJobManager,
    ResearchRunOptions,
    run_research_subprocess,
)


class FakeStdout:
    def __init__(self, lines):
        self.lines = lines

    def __iter__(self):
        return iter(self.lines)

    def close(self):
        return None


class FakeProcess:
    def __init__(self, lines, return_code=0):
        self.stdout = FakeStdout(lines)
        self.return_code = return_code

    def poll(self):
        return self.return_code

    def wait(self, timeout=None):
        return self.return_code

    def terminate(self):
        self.return_code = -15

    def kill(self):
        self.return_code = -9


class PredictionWindowCountTestCase(unittest.TestCase):
    def test_full_scope_counts_scheduled_windows_and_unscheduled_latest_snapshot(self):
        config = WalkForwardConfig(
            horizon=5,
            min_train_days=10,
            train_window_days=20,
            min_train_rows=1,
            rebalance_step=5,
        )

        # first prediction index is 10 + 5 + 1 = 16.  Scheduled indices for
        # 30 dates are 16, 21 and 26; index 29 is appended as the live snapshot.
        self.assertEqual(prediction_window_count(30, config), 4)
        # When the latest index is already scheduled it must not be counted twice.
        self.assertEqual(prediction_window_count(27, config), 3)

    def test_latest_scope_has_one_window_and_short_history_has_none(self):
        config = WalkForwardConfig(
            horizon=5,
            min_train_days=10,
            train_window_days=20,
            min_train_rows=1,
            prediction_scope="latest",
        )

        self.assertEqual(prediction_window_count(30, config), 1)
        self.assertEqual(prediction_window_count(16, config), 0)


class FactorProgressCallbackTestCase(unittest.TestCase):
    def test_callbacks_report_all_phases_without_changing_factor_values(self):
        dates = pd.bdate_range("2025-01-02", periods=130)
        rows = []
        for stock_index in range(6):
            for date_index, trade_date in enumerate(dates):
                rows.append(
                    {
                        "code": f"{stock_index:06d}",
                        "trade_date": trade_date,
                        "close": 10 + stock_index + date_index * 0.01,
                        "amount": 100_000_000 + stock_index * 1_000_000,
                        "turnover_rate": 1 + stock_index * 0.01,
                    }
                )
        bars = pd.DataFrame(rows)
        raw_events = []
        standardization_events = []

        expected_raw = calculate_raw_factors(bars)
        actual_raw = calculate_raw_factors(
            bars, progress_callback=lambda *event: raw_events.append(event)
        )
        expected_wide, _ = standardize_cross_section(
            expected_raw, materialize_long=False
        )
        actual_wide, _ = standardize_cross_section(
            actual_raw,
            materialize_long=False,
            progress_callback=lambda *event: standardization_events.append(event),
        )

        pd.testing.assert_frame_equal(actual_raw, expected_raw)
        pd.testing.assert_frame_equal(actual_wide, expected_wide)
        self.assertEqual([event[0] for event in raw_events], list(range(6)))
        self.assertTrue(all(event[1] == 5 for event in raw_events))
        self.assertEqual(
            [event[0] for event in standardization_events],
            list(range(len(FACTOR_COLUMNS) + 1)),
        )
        self.assertTrue(
            all(event[1] == len(FACTOR_COLUMNS) for event in standardization_events)
        )


class WalkForwardProgressCallbackTestCase(unittest.TestCase):
    @staticmethod
    def panel() -> pd.DataFrame:
        rows = []
        dates = pd.bdate_range("2026-01-05", periods=12)
        for date_index, trade_date in enumerate(dates):
            for stock_index in range(4):
                row = {
                    "code": f"{stock_index:06d}",
                    "trade_date": trade_date,
                    "amount": 100_000_000.0,
                    # Force one otherwise-valid prediction window to be skipped.
                    "eligible": date_index != 8,
                    "label": float(stock_index % 2),
                    "forward_return": 0.01 if stock_index % 2 else -0.01,
                }
                row.update(
                    {
                        factor: float(stock_index + factor_index / 100)
                        for factor_index, factor in enumerate(FACTOR_COLUMNS)
                    }
                )
                rows.append(row)
        return pd.DataFrame(rows)

    def test_callback_advances_for_every_attempt_including_skipped_windows(self):
        config = WalkForwardConfig(
            horizon=2,
            min_train_days=3,
            train_window_days=6,
            min_train_rows=8,
            rebalance_step=2,
            top_n=2,
            min_amount=0,
            explanation_scope="latest",
        )
        events = []

        predictions = walk_forward_predict(
            self.panel(),
            config,
            progress_callback=events.append,
        )

        expected_total = prediction_window_count(12, config)
        self.assertFalse(predictions.empty)
        self.assertEqual(len(events), expected_total)
        self.assertEqual([event["completed_windows"] for event in events], list(range(1, expected_total + 1)))
        self.assertTrue(all(event["total_windows"] == expected_total for event in events))
        self.assertIn("skipped", {event["status"] for event in events})
        self.assertIn("fitted", {event["status"] for event in events})
        self.assertTrue(all(event["fitted_windows"] <= event["completed_windows"] for event in events))
        self.assertTrue(all(len(event["prediction_date"]) == 10 for event in events))
        self.assertEqual(events[-1]["completed_windows"], events[-1]["total_windows"])


class ResearchProgressParserTestCase(unittest.TestCase):
    def test_window_progress_is_monotonic_and_a_skipped_window_still_advances(self):
        lines = [
            "run_mode=full requested=auto\n",
            "factors_completed=100\n",
            "model_plan=" + json.dumps({"5": 4, "20": 2}) + "\n",
            "model_progress="
            + json.dumps(
                {
                    "horizon": 5,
                    "completed_windows": 1,
                    "total_windows": 4,
                    "fitted_windows": 1,
                    "prediction_date": "2025-01-02",
                    "status": "fitted",
                }
            )
            + "\n",
            "model_progress="
            + json.dumps(
                {
                    "horizon": 5,
                    "completed_windows": 2,
                    "total_windows": 4,
                    "fitted_windows": 1,
                    "prediction_date": "2025-01-09",
                    "status": "skipped",
                }
            )
            + "\n",
            "model_progress="
            + json.dumps(
                {
                    "horizon": 5,
                    "completed_windows": 4,
                    "total_windows": 4,
                    "fitted_windows": 3,
                    "prediction_date": "2025-01-23",
                    "status": "fitted",
                }
            )
            + "\n",
            "horizon=5d mode=full predictions=20 backtest={}\n",
            "model_progress="
            + json.dumps(
                {
                    "horizon": 20,
                    "completed_windows": 1,
                    "total_windows": 2,
                    "fitted_windows": 1,
                    "prediction_date": "2025-02-03",
                    "status": "fitted",
                }
            )
            + "\n",
            "model_progress="
            + json.dumps(
                {
                    "horizon": 20,
                    "completed_windows": 2,
                    "total_windows": 2,
                    "fitted_windows": 2,
                    "prediction_date": "2025-02-24",
                    "status": "fitted",
                }
            )
            + "\n",
            "horizon=20d mode=full predictions=20 backtest={}\n",
        ]
        process = FakeProcess(lines)
        progress = []

        with patch("backend.app.services.research_jobs.subprocess.Popen", return_value=process):
            result = run_research_subprocess(
                ResearchRunOptions(horizons=(5, 20)),
                progress.append,
                threading.Event(),
            )

        percentages = [item["progress_pct"] for item in progress if "progress_pct" in item]
        window_events = [item for item in progress if item.get("window_status")]
        self.assertEqual(result["completed_horizons"], 2)
        self.assertEqual(percentages, sorted(percentages))
        self.assertEqual(
            [item["step_completed"] for item in window_events if item.get("current_horizon") == 5],
            [1, 2, 4],
        )
        skipped = next(item for item in window_events if item["window_status"] == "skipped")
        self.assertGreater(skipped["progress_pct"], window_events[0]["progress_pct"])

    def test_high_frequency_window_updates_do_not_flood_job_logs(self):
        def runner(options, on_progress, stop_event):
            for completed in range(1, 501):
                on_progress(
                    {
                        "stage": "modeling",
                        "message": f"horizon=20d window={completed}/500 status=fitted",
                        "completed_windows": completed,
                        "total_windows": 500,
                        "progress_pct": 25 + 75 * completed / 500,
                        "log": False,
                    }
                )
            return {"status": "completed", "completed_horizons": 1}

        manager = ResearchJobManager(runner=runner)
        manager.start(ResearchRunOptions(horizons=(20,)))
        for _ in range(100):
            state = manager.snapshot()
            if state["status"] == "completed":
                break
            time.sleep(0.01)

        self.assertEqual(state["status"], "completed")
        self.assertNotIn("log", state)
        self.assertLessEqual(len(state["logs"]), 3)
        self.assertEqual(state["completed_windows"], 500)


if __name__ == "__main__":
    unittest.main()
