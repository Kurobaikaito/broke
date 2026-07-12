import unittest

from backend.app.services.scoring import build_prediction, probability_from_score, weighted_score


class ScoringTestCase(unittest.TestCase):
    def test_probability_is_bounded(self):
        self.assertGreater(probability_from_score(-100, "20d"), 0)
        self.assertLess(probability_from_score(100, "20d"), 1)

    def test_weighted_score_uses_available_factors(self):
        score = weighted_score({"momentum_20d": 1.0, "volatility_20d": 1.0})
        self.assertGreaterEqual(score, -1)
        self.assertLessEqual(score, 1)

    def test_build_prediction_shape(self):
        prediction = build_prediction(
            {"code": "600519", "name": "贵州茅台", "industry": "食品饮料"},
            {"momentum_20d": 0.5, "quality": 0.8},
            "20d",
            rank_no=1,
        )
        self.assertEqual(prediction["code"], "600519")
        self.assertEqual(prediction["rank"], 1)
        self.assertTrue(prediction["factor_highlights"])


if __name__ == "__main__":
    unittest.main()
