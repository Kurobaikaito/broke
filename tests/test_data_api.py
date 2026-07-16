import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.services.sync_jobs import SyncJobManager


class DataApiTestCase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_config_exposes_policy_without_token_information(self):
        response = self.client.get("/api/data/config")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("token", payload)
        self.assertNotIn("token_configured", payload)
        self.assertNotIn("token_suffix", payload)
        self.assertEqual(payload["history_start"], "2018-01-01")
        self.assertEqual(payload["markets"], ["SSE", "SZSE"])
        self.assertTrue(payload["resume_automatically"])
        self.assertEqual(payload["sync_dataset"], "a_share_daily_full_v2")

    def test_frontend_shell_is_not_cached(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_start_endpoint_returns_background_job_state(self):
        captured = {}

        def runner(options, token, on_progress, stop_event):
            captured["options"] = options
            on_progress({"stage": "syncing", "total_dates": 1, "completed_dates": 1})
            return {"status": "completed", "totals": {"daily": 1}, "failures": []}

        manager = SyncJobManager(runner=runner)
        with patch.object(main, "sync_manager", manager):
            response = self.client.post("/api/data/sync")
            self.assertEqual(response.status_code, 202)
            for _ in range(100):
                payload = self.client.get("/api/data/sync/status").json()
                if payload["status"] == "completed":
                    break
                time.sleep(0.01)
            self.assertEqual(payload["status"], "completed")
            self.assertIsNone(captured["options"].start_date)
            self.assertEqual(captured["options"].sleep_seconds, 0.0)
            self.assertTrue(captured["options"].use_checkpoint)
            self.assertFalse(captured["options"].continue_on_error)

    def test_recommendations_are_sized_for_small_capital(self):
        with patch.object(main, "settings", replace(main.settings, demo_mode=True)):
            response = self.client.get("/api/recommendations?horizon=20d&capital=10000&limit=100")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["target_position_count"], 3)
        self.assertLessEqual(len(payload["items"]), 3)
        for item in payload["items"]:
            self.assertEqual(item["target_shares"] % 100, 0)

    def test_recommendations_reject_capital_outside_supported_range(self):
        response = self.client.get("/api/recommendations?horizon=20d&capital=9999")
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
