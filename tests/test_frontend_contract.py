import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTestCase(unittest.TestCase):
    def test_javascript_element_ids_exist_in_html(self):
        html = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "backend/app/static/app.js").read_text(encoding="utf-8")
        html_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
        referenced_ids = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', javascript))
        self.assertEqual(referenced_ids.difference(html_ids), set())

    def test_token_configuration_is_not_exposed_in_frontend(self):
        html = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")
        self.assertNotIn('id="tokenInput"', html)
        self.assertNotIn('id="saveTokenBtn"', html)

    def test_data_sync_dates_are_managed_automatically(self):
        html = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "backend/app/static/app.js").read_text(encoding="utf-8")
        self.assertNotIn('id="startDateInput"', html)
        self.assertNotIn('id="endDateInput"', html)
        self.assertIn('id="syncPolicy"', html)
        self.assertIn('id="startSyncBtn" class="primary-btn" type="button"', html)
        self.assertNotIn('id="syncForm"', html)
        self.assertIn('$("startSyncBtn").addEventListener("click", startSync)', javascript)

    def test_static_assets_are_cache_busted(self):
        html = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")
        self.assertRegex(html, r'/static/styles\.css\?v=[^"?]+')
        self.assertRegex(html, r'/static/app\.js\?v=[^"?]+')

    def test_analysis_uses_capital_instead_of_a_fixed_stock_count(self):
        html = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")
        self.assertIn('id="capitalInput"', html)
        self.assertNotIn('id="limitSelect"', html)


if __name__ == "__main__":
    unittest.main()
