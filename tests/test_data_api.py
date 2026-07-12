import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.services.sync_jobs import SyncJobManager


class DataApiTestCase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_config_masks_token_and_uses_2026_default(self):
        response = self.client.get("/api/data/config")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("token", payload)
        self.assertEqual(payload["defaults"]["start_date"], "2026-01-01")
        self.assertEqual(payload["defaults"]["sleep_seconds"], 0.8)

    def test_start_endpoint_returns_background_job_state(self):
        def runner(options, token, on_progress, stop_event):
            on_progress({"stage": "syncing", "total_dates": 1, "completed_dates": 1})
            return {"status": "completed", "totals": {"daily": 1}, "failures": []}

        manager = SyncJobManager(runner=runner)
        with patch.object(main, "sync_manager", manager):
            response = self.client.post(
                "/api/data/sync",
                json={
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-02",
                    "sleep_seconds": 0.8,
                    "retry": 3,
                    "continue_on_error": False,
                },
            )
            self.assertEqual(response.status_code, 202)
            for _ in range(100):
                payload = self.client.get("/api/data/sync/status").json()
                if payload["status"] == "completed":
                    break
                time.sleep(0.01)
            self.assertEqual(payload["status"], "completed")

    def test_reversed_date_range_is_rejected(self):
        response = self.client.post(
            "/api/data/sync",
            json={"start_date": "2026-07-01", "end_date": "2026-01-01", "sleep_seconds": 0.8, "retry": 3},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
