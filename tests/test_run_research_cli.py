import argparse
import unittest

from scripts.run_research import resolve_model_version


def args(**overrides):
    values = {
        "model_version": None,
        "model_type": "logistic",
        "max_features": None,
        "half_life_days": None,
        "industry_neutral": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ResearchCliVersionTestCase(unittest.TestCase):
    def test_default_version_distinguishes_new_factor_and_weighting_contract(self):
        self.assertEqual(resolve_model_version(args()), "logistic-pv15-dateweighted-v2")

    def test_challenger_configuration_gets_a_distinct_version(self):
        version = resolve_model_version(
            args(
                model_type="ridge",
                max_features=8,
                half_life_days=126.0,
                industry_neutral=True,
            )
        )
        self.assertEqual(version, "ridge-return-pv15-v1-icfs8-hl126-industry")

    def test_explicit_version_wins(self):
        self.assertEqual(resolve_model_version(args(model_version="manual-v9")), "manual-v9")


if __name__ == "__main__":
    unittest.main()
