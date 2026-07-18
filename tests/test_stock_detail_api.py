import unittest
from dataclasses import replace
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app import main
from backend.app.db import Base
from backend.app.models import DailyBar, DailyBasic, DimStock
from backend.app.repositories import MysqlRepository
from backend.app.services.stock_detail import get_stock_detail


class StockDetailApiTestCase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_demo_detail_has_chronological_ohlcv_bars(self):
        with patch.object(main, "settings", replace(main.settings, demo_mode=True)):
            response = self.client.get("/api/stocks/600519/detail?limit=60")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["mode"], "demo")
        self.assertEqual(payload["stock"]["code"], "600519")
        self.assertEqual(len(payload["bars"]), 60)
        self.assertEqual(payload["bars"], sorted(payload["bars"], key=lambda row: row["trade_date"]))
        self.assertEqual(
            set(payload["bars"][0]),
            {"trade_date", "open", "high", "low", "close", "volume", "amount"},
        )

    def test_detail_accepts_tushare_style_code_and_rejects_unknown_limit(self):
        with patch.object(main, "settings", replace(main.settings, demo_mode=True)):
            ok_response = self.client.get("/api/stocks/600519.SH/detail?limit=120")
            invalid_response = self.client.get("/api/stocks/600519/detail?limit=61")
        self.assertEqual(ok_response.status_code, 200)
        self.assertEqual(ok_response.json()["stock"]["code"], "600519")
        self.assertEqual(invalid_response.status_code, 422)

    def test_unknown_stock_returns_404(self):
        with patch.object(main, "settings", replace(main.settings, demo_mode=True)):
            response = self.client.get("/api/stocks/999999/detail?limit=60")
        self.assertEqual(response.status_code, 404)

    def test_mysql_empty_fallback_recommendation_still_has_a_detail_page(self):
        real_get_stock_detail = get_stock_detail

        def fallback_only(repository, code, limit):
            if isinstance(repository, MysqlRepository):
                return None
            return real_get_stock_detail(repository, code, limit)

        def repository_override():
            yield MysqlRepository(None)

        main.app.dependency_overrides[main.get_repository] = repository_override
        try:
            with (
                patch.object(main, "settings", replace(main.settings, demo_mode=False)),
                patch.object(main, "get_stock_detail", side_effect=fallback_only),
            ):
                response = self.client.get("/api/stocks/600036/detail?limit=60")
        finally:
            main.app.dependency_overrides.pop(main.get_repository, None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "mysql-empty-demo-fallback")

    def test_mysql_detail_preserves_cny_market_value_and_normalizes_percent(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as session:
                session.add(DimStock(code="000001", name="平安银行", exchange="SZSE"))
                session.add_all(
                    [
                        DailyBar(code="000001", trade_date=date(2026, 7, 15), close=20, volume=100),
                        DailyBar(
                            code="000001",
                            trade_date=date(2026, 7, 16),
                            close=9.5,
                            volume=200,
                            pct_chg=-5,
                        ),
                        DailyBasic(
                            code="000001",
                            trade_date=date(2026, 7, 16),
                            total_mv=123_000_000,
                            float_mv=98_000_000,
                            turnover_rate=2.5,
                        ),
                    ]
                )
                session.commit()
                payload = get_stock_detail(MysqlRepository(session), "000001", 60)
            self.assertEqual(payload["stock"]["total_market_value"], 123_000_000)
            self.assertEqual(payload["stock"]["circ_market_value"], 98_000_000)
            self.assertAlmostEqual(payload["stock"]["turnover_rate"], 0.025)
            self.assertAlmostEqual(payload["stock"]["change_pct"], -0.05)
            self.assertAlmostEqual(payload["stock"]["change"], -0.5)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
