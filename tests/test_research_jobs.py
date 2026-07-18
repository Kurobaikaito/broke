import threading
import time
import unittest
from unittest.mock import patch

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


class ResearchJobManagerTestCase(unittest.TestCase):
    def test_completed_job_reports_horizons_and_bounded_logs(self):
        def runner(options, on_progress, stop_event):
            self.assertEqual(options.horizons, (5, 20, 60))
            self.assertFalse(stop_event.is_set())
            for index, horizon in enumerate(options.horizons, start=1):
                on_progress(
                    {
                        "stage": "modeling",
                        "message": f"horizon={horizon}d completed",
                        "current_horizon": horizon,
                        "completed_horizons": index,
                        "progress_pct": 25 + index * 25,
                    }
                )
            return {"status": "completed", "completed_horizons": 3}

        manager = ResearchJobManager(runner=runner)
        started = manager.start(ResearchRunOptions())
        self.assertEqual(started["status"], "running")
        for _ in range(100):
            state = manager.snapshot()
            if state["status"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["progress_pct"], 100.0)
        self.assertEqual(state["completed_horizons"], 3)
        self.assertLessEqual(len(state["logs"]), 200)

    def test_stop_is_cooperative(self):
        entered = threading.Event()

        def runner(options, on_progress, stop_event):
            entered.set()
            self.assertTrue(stop_event.wait(timeout=2))
            return {"status": "stopped", "completed_horizons": 0}

        manager = ResearchJobManager(runner=runner)
        manager.start(ResearchRunOptions())
        self.assertTrue(entered.wait(timeout=1))
        stopping = manager.stop()
        self.assertEqual(stopping["status"], "stopping")
        for _ in range(100):
            state = manager.snapshot()
            if state["status"] == "stopped":
                break
            time.sleep(0.01)
        self.assertEqual(state["status"], "stopped")

    def test_options_validate_supported_capital(self):
        with self.assertRaises(ValueError):
            ResearchRunOptions(initial_capital=9_999).validate()
        with self.assertRaises(ValueError):
            ResearchRunOptions(run_mode="incremental").validate()

    def test_subprocess_is_unbuffered_and_all_skipped_is_failure(self):
        process = FakeProcess(
            [
                "horizon=5d skipped=no_valid_training_window\n",
                "horizon=20d skipped=no_valid_training_window\n",
                "horizon=60d skipped=no_valid_training_window\n",
            ]
        )
        with patch("backend.app.services.research_jobs.subprocess.Popen", return_value=process) as popen:
            with self.assertRaisesRegex(RuntimeError, "没有生成有效预测"):
                run_research_subprocess(ResearchRunOptions(), lambda _payload: None, threading.Event())
        command = popen.call_args.args[0]
        self.assertIn("-u", command)
        self.assertEqual(command[command.index("--run-mode") + 1], "auto")
        self.assertEqual(command[command.index("--factor-storage") + 1], "latest")

    def test_subprocess_reports_effective_mode_and_accepts_up_to_date_horizons(self):
        process = FakeProcess(
            [
                "run_mode=latest requested=auto\n",
                "bars_loaded=100 start=2022-01-01 end=2026-07-16\n",
                "horizon=5d status=up_to_date run_id=run-5\n",
                "horizon=20d mode=latest predictions=100 backtest={}\n",
            ]
        )
        progress = []
        with patch("backend.app.services.research_jobs.subprocess.Popen", return_value=process):
            result = run_research_subprocess(
                ResearchRunOptions(horizons=(5, 20)),
                progress.append,
                threading.Event(),
            )
        self.assertEqual(result["completed_horizons"], 2)
        self.assertEqual(result["actual_run_mode"], "latest")
        self.assertTrue(any(item.get("actual_run_mode") == "latest" for item in progress))

    def test_subprocess_distinguishes_completed_and_skipped_horizons(self):
        process = FakeProcess(
            [
                "horizon=5d model=test predictions=100 backtest={}\n",
                "horizon=20d skipped=no_valid_training_window\n",
            ]
        )
        with patch("backend.app.services.research_jobs.subprocess.Popen", return_value=process):
            result = run_research_subprocess(
                ResearchRunOptions(horizons=(5, 20)),
                lambda _payload: None,
                threading.Event(),
            )
        self.assertEqual(result["completed_horizons"], 1)
        self.assertEqual(result["skipped_horizons"], [20])

    def test_shutdown_waits_for_cooperative_runner(self):
        entered = threading.Event()

        def runner(options, on_progress, stop_event):
            entered.set()
            stop_event.wait(timeout=2)
            return {"status": "stopped", "completed_horizons": 0}

        manager = ResearchJobManager(runner=runner)
        manager.start(ResearchRunOptions())
        self.assertTrue(entered.wait(timeout=1))
        manager.shutdown(timeout=2)
        self.assertEqual(manager.snapshot()["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
