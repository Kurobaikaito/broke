import os
import unittest
from unittest.mock import patch

from backend.app.config import MIN_FINITE_RAW_RETENTION_DAYS, get_settings


class RetentionConfigTestCase(unittest.TestCase):
    def test_short_finite_raw_retention_is_rejected(self):
        with patch.dict(os.environ, {"DATA_RAW_RETENTION_DAYS": "365"}):
            with self.assertRaisesRegex(ValueError, "minimum training windows"):
                get_settings()

    def test_permanent_and_safe_finite_raw_retention_are_allowed(self):
        with patch.dict(os.environ, {"DATA_RAW_RETENTION_DAYS": "0"}):
            self.assertEqual(get_settings().data_raw_retention_days, 0)
        with patch.dict(
            os.environ,
            {"DATA_RAW_RETENTION_DAYS": str(MIN_FINITE_RAW_RETENTION_DAYS)},
        ):
            self.assertEqual(
                get_settings().data_raw_retention_days,
                MIN_FINITE_RAW_RETENTION_DAYS,
            )


if __name__ == "__main__":
    unittest.main()
