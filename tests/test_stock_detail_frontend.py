import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "backend" / "app" / "static"


class StockDetailFrontendContractTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.javascript = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.styles = (STATIC / "styles.css").read_text(encoding="utf-8")

    def test_recommendations_offer_an_explicit_detail_entry(self):
        self.assertIn('class="table-detail-btn"', self.javascript)
        self.assertIn('data-detail-code=', self.javascript)
        self.assertIn("openStockDetail(button.dataset.detailCode)", self.javascript)

    def test_detail_view_has_navigation_metrics_chart_and_states(self):
        required_ids = {
            "stockDetailView",
            "backToAnalysisBtn",
            "detailStockName",
            "detailLastClose",
            "detailMetrics",
            "stockChart",
            "detailChartState",
            "retryDetailBtn",
        }
        html_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', self.html))
        self.assertEqual(required_ids.difference(html_ids), set())
        self.assertIn('data-detail-limit="60"', self.html)
        self.assertIn('data-detail-limit="120"', self.html)
        self.assertIn('data-detail-limit="250"', self.html)
        self.assertIn('role="status"', self.html)
        self.assertIn("setDetailChartState(\"error\"", self.javascript)
        self.assertIn("setDetailChartState(\"empty\"", self.javascript)

    def test_detail_api_contract_is_used(self):
        self.assertIn(
            "/api/stocks/${encodeURIComponent(code)}/detail?limit=${state.detailLimit}",
            self.javascript,
        )
        self.assertIn("normalizeBars(payload.bars)", self.javascript)

    def test_financial_chart_uses_candles_volume_and_moving_averages(self):
        self.assertIn("library.CandlestickSeries", self.javascript)
        self.assertIn("library.HistogramSeries", self.javascript)
        self.assertRegex(
            self.javascript,
            r"chart\.addSeries\(library\.HistogramSeries,[\s\S]+?\}, 1\);",
        )
        self.assertIn("movingAverage(bars, period)", self.javascript)
        self.assertIn("chart.subscribeCrosshairMove", self.javascript)

    def test_chart_library_is_pinned_and_served_locally(self):
        vendor_path = STATIC / "vendor" / "lightweight-charts.standalone.production.js"
        notice_path = STATIC / "vendor" / "NOTICE.lightweight-charts.txt"
        license_path = STATIC / "vendor" / "LICENSE.lightweight-charts.txt"
        self.assertTrue(vendor_path.is_file())
        self.assertTrue(notice_path.is_file())
        self.assertTrue(license_path.is_file())
        self.assertGreater(vendor_path.stat().st_size, 30_000)
        header = vendor_path.read_text(encoding="utf-8")[:300]
        self.assertIn("TradingView Lightweight Charts", header)
        self.assertIn("v5.2.0", header)
        self.assertIn("Apache License 2.0", header)
        self.assertIn(
            '/static/vendor/lightweight-charts.standalone.production.js?v=5.2.0',
            self.html,
        )
        self.assertNotIn("unpkg.com", self.html)
        self.assertLess(
            self.html.index("lightweight-charts.standalone.production.js"),
            self.html.index("/static/app.js"),
        )

    def test_detail_layout_has_mobile_adaptation(self):
        self.assertIn(".stock-detail-view", self.styles)
        self.assertIn(".detail-metrics", self.styles)
        self.assertIn("@media (max-width: 640px)", self.styles)


if __name__ == "__main__":
    unittest.main()
