import time
import unittest

from backend.app.services.sync_jobs import SyncJobManager
from backend.app.services.tushare_sync import SyncOptions, resolve_start_date


class SyncJobManagerTestCase(unittest.TestCase):
    def test_empty_database_default_starts_in_2026(self):
        self.assertEqual(resolve_start_date(None, None, 7), "20260101")

    def test_completed_job_exposes_progress_without_token(self):
        def runner(options, token, on_progress, stop_event):
            self.assertEqual(token, "secret-token-value-12345")
            on_progress({"stage": "syncing", "total_dates": 2, "completed_dates": 1, "message": "first"})
            on_progress({"stage": "syncing", "total_dates": 2, "completed_dates": 2, "message": "second"})
            return {"status": "completed", "totals": {"daily": 10}, "failures": []}

        manager = SyncJobManager(runner=runner)
        manager.start(SyncOptions(start_date="20260101", end_date="20260102"), "secret-token-value-12345")
        for _ in range(100):
            snapshot = manager.snapshot()
            if snapshot["status"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["progress_pct"], 100.0)
        self.assertNotIn("token", snapshot)
        self.assertEqual(len(snapshot["logs"]), 3)

    def test_stop_sets_cooperative_event(self):
        def runner(options, token, on_progress, stop_event):
            on_progress({"stage": "syncing", "total_dates": 10, "completed_dates": 0})
            stop_event.wait(2)
            return {"status": "stopped", "totals": {}, "failures": []}

        manager = SyncJobManager(runner=runner)
        manager.start(SyncOptions(start_date="20260101", end_date="20260131"), "secret-token-value-12345")
        manager.stop()
        for _ in range(100):
            snapshot = manager.snapshot()
            if snapshot["status"] == "stopped":
                break
            time.sleep(0.01)
        self.assertEqual(snapshot["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
