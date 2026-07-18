import time
import threading
import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi import HTTPException

from backend.app import main
from backend.app.services.research_jobs import ResearchJobManager


class ResearchApiTestCase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_demo_mode_rejects_research_run(self):
        with patch.object(main, "settings", replace(main.settings, demo_mode=True)):
            response = self.client.post("/api/research/run?capital=50000")
        self.assertEqual(response.status_code, 409)

    def test_mysql_mode_starts_background_research(self):
        captured = {}

        def runner(options, on_progress, stop_event):
            captured["capital"] = options.initial_capital
            captured["run_mode"] = options.run_mode
            on_progress({"stage": "modeling", "completed_horizons": 3, "progress_pct": 100})
            return {"status": "completed", "completed_horizons": 3}

        manager = ResearchJobManager(runner=runner)
        with (
            patch.object(main, "settings", replace(main.settings, demo_mode=False)),
            patch.object(main, "research_manager", manager),
        ):
            response = self.client.post("/api/research/run?capital=80000&mode=full")
            self.assertEqual(response.status_code, 202)
            for _ in range(100):
                payload = self.client.get("/api/research/status").json()
                if payload["status"] == "completed":
                    break
                time.sleep(0.01)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(captured["capital"], 80_000)
        self.assertEqual(captured["run_mode"], "full")

    def test_research_capital_is_validated(self):
        response = self.client.post("/api/research/run?capital=9999")
        self.assertEqual(response.status_code, 422)
        response = self.client.post("/api/research/run?capital=50000&mode=incremental")
        self.assertEqual(response.status_code, 422)

    def test_sync_and_research_cannot_start_together(self):
        class StubManager:
            def __init__(self):
                self.status = "idle"

            def snapshot(self):
                return {"status": self.status}

            def start(self, *_args, **_kwargs):
                self.status = "running"
                return {"status": "running"}

        sync = StubManager()
        research = StubManager()
        barrier = threading.Barrier(2)
        outcomes = []

        def invoke(name, action):
            barrier.wait(timeout=2)
            try:
                action()
                outcomes.append((name, "running"))
            except HTTPException as exc:
                outcomes.append((name, f"blocked-{exc.status_code}"))

        configured = replace(main.settings, demo_mode=False, tushare_token="configured-for-test")
        with (
            patch.object(main, "settings", configured),
            patch.object(main, "get_settings", return_value=configured),
            patch.object(main, "sync_manager", sync),
            patch.object(main, "research_manager", research),
        ):
            threads = [
                threading.Thread(target=invoke, args=("sync", main.start_data_sync)),
                threading.Thread(target=invoke, args=("research", lambda: main.start_research(50_000))),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

        self.assertEqual(sum(status == "running" for _, status in outcomes), 1)
        self.assertEqual(sum(status == "blocked-409" for _, status in outcomes), 1)


if __name__ == "__main__":
    unittest.main()
