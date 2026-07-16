import time
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from backend.app.services.sync_jobs import SyncJobManager
from backend.app.services.data_sources import tushare_daily_bundle_records
from backend.app.services.tushare_sync import SyncOptions, resolve_start_date, run_tushare_sync


class SyncJobManagerTestCase(unittest.TestCase):
    def test_empty_database_starts_with_full_history(self):
        self.assertEqual(resolve_start_date(None, None), "20180101")

    def test_checkpoint_resumes_on_the_next_calendar_date(self):
        self.assertEqual(resolve_start_date(None, date(2026, 7, 14)), "20260715")

    def test_unfetched_status_fields_are_unknown_not_false(self):
        daily = pd.DataFrame(
            [{"ts_code": "000001.SZ", "trade_date": "20240102", "open": 10, "high": 11, "low": 9, "close": 10.5, "vol": 100, "amount": 1000}]
        )
        factors = pd.DataFrame(
            [{"ts_code": "000001.SZ", "trade_date": "20240102", "adj_factor": 2.0}]
        )
        basics = pd.DataFrame(
            [{"ts_code": "000001.SZ", "trade_date": "20240102", "turnover_rate": 1.0, "limit_status": 0}]
        )

        record = tushare_daily_bundle_records(daily, factors, basics)["basics"][0]

        self.assertIsNone(record["is_st"])
        self.assertIsNone(record["is_suspended"])
        self.assertEqual(record["limit_status"], 0)

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

    def test_daily_bundle_and_checkpoint_share_one_transaction(self):
        class FakeSession:
            def __init__(self):
                self.commits = 0
                self.rollbacks = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def commit(self):
                self.commits += 1

            def rollback(self):
                self.rollbacks += 1

        session = FakeSession()

        class FakeRepository:
            def __init__(self):
                self.commit_flags = []
                self.states = []

            def get_sync_state(self, *args):
                return None

            def upsert_stocks(self, records):
                return len(records)

            def upsert_trade_calendar(self, records):
                return len(records)

            def _write(self, records, commit):
                self.commit_flags.append(commit)
                return len(records)

            def upsert_daily_bars(self, records, commit=True):
                return self._write(records, commit)

            def upsert_adj_factors(self, records, commit=True):
                return self._write(records, commit)

            def upsert_daily_basics(self, records, commit=True):
                return self._write(records, commit)

            def upsert_adjusted_bars(self, records, commit=True):
                return self._write(records, commit)

            def set_sync_state(self, *args, **kwargs):
                self.states.append((args, kwargs))
                if kwargs.get("commit", True):
                    session.commit()

        repository = FakeRepository()

        class FakeClient:
            def stock_list_records(self):
                return []

            def trade_calendar_records(self, start_date, end_date):
                return [{"trade_date": date(2024, 1, 2), "exchange": "SSE", "is_open": 1}]

            def daily_frames(self, trade_date):
                daily = pd.DataFrame(
                    [{"ts_code": "000001.SZ", "trade_date": trade_date, "open": 10, "high": 11, "low": 9, "close": 10.5, "vol": 100, "amount": 1000}]
                )
                factors = pd.DataFrame(
                    [{"ts_code": "000001.SZ", "trade_date": trade_date, "adj_factor": 2.0}]
                )
                basics = pd.DataFrame(
                    [{"ts_code": "000001.SZ", "trade_date": trade_date, "turnover_rate": 1.0}]
                )
                return daily, factors, basics

        with (
            patch("backend.app.services.tushare_sync.TushareClient", return_value=FakeClient()),
            patch("backend.app.services.tushare_sync.get_session_factory", return_value=lambda: session),
            patch("backend.app.services.tushare_sync.MysqlRepository", return_value=repository),
        ):
            result = run_tushare_sync(
                SyncOptions(
                    start_date="20240102",
                    end_date="20240102",
                    sleep_seconds=0,
                    use_checkpoint=False,
                ),
                token="configured-token-value",
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(repository.commit_flags, [False, False, False, False])
        self.assertFalse(repository.states[0][1]["commit"])
        self.assertEqual(session.commits, 1)
        self.assertEqual(session.rollbacks, 0)


if __name__ == "__main__":
    unittest.main()
