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

    def test_token_input_is_password_field(self):
        html = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")
        self.assertIn('id="tokenInput" type="password"', html)


if __name__ == "__main__":
    unittest.main()
