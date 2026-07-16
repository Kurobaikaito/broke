import unittest

import pandas as pd

from backend.app.research.backtest import evaluate_predictions
from backend.app.services.portfolio import allocate_lot_positions, target_position_count


class SmallCapitalPortfolioTestCase(unittest.TestCase):
    def test_position_count_scales_between_three_and_ten(self):
        self.assertEqual(target_position_count(10_000), 3)
        self.assertEqual(target_position_count(50_000), 7)
        self.assertEqual(target_position_count(100_000), 10)

    def test_allocation_skips_stocks_that_cannot_fit_one_lot(self):
        candidates = [
            {"code": "EXPENSIVE", "rank": 1, "last_close": 40.0},
            {"code": "A", "rank": 2, "last_close": 10.0},
            {"code": "B", "rank": 3, "last_close": 8.0},
            {"code": "C", "rank": 4, "last_close": 5.0},
        ]
        positions = allocate_lot_positions(candidates, capital=10_000)
        self.assertEqual([item["code"] for item in positions], ["A", "B", "C"])
        self.assertTrue(all(item["target_shares"] % 100 == 0 for item in positions))
        self.assertTrue(all(item["target_amount"] <= 10_000 * 0.97 / 3 for item in positions))

    def test_backtest_uses_actual_cash_and_whole_lots(self):
        rows = [
            {
                "trade_date": pd.Timestamp("2024-01-02"),
                "code": code,
                "score": score,
                "entry_open": 10.0,
                "exit_open": 11.0,
                "forward_return": 0.10,
            }
            for code, score in (("A", 3.0), ("B", 2.0), ("C", 1.0))
        ]
        metrics, periods = evaluate_predictions(
            pd.DataFrame(rows),
            horizon=5,
            top_n=3,
            initial_capital=10_000,
            commission_bps=0,
            stamp_duty_bps=0,
            slippage_bps=0,
        )
        self.assertEqual(periods.iloc[0]["position_count"], 3)
        self.assertAlmostEqual(metrics["ending_capital"], 10_900.0)
        self.assertAlmostEqual(metrics["top_group_return"], 0.09)


if __name__ == "__main__":
    unittest.main()
