from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from backend.app.db import get_engine, get_session_factory
from backend.app.research.backtest import evaluate_predictions
from backend.app.research.factors import calculate_raw_factors, standardize_cross_section
from backend.app.research.modeling import WalkForwardConfig, build_model_panel, walk_forward_predict
from backend.app.research.storage import load_bars, save_backtest_summary, save_factors, save_predictions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate factors, train rolling models, and backtest capital-aware portfolios."
    )
    parser.add_argument("--start-date", default="20180101", help="First date loaded from MySQL (YYYYMMDD).")
    parser.add_argument("--end-date", default=None, help="Optional last date loaded from MySQL (YYYYMMDD).")
    parser.add_argument("--horizons", default="5,20,60", help="Comma-separated trading-day horizons.")
    parser.add_argument("--train-window-days", type=int, default=756)
    parser.add_argument("--min-train-days", type=int, default=252)
    parser.add_argument("--min-train-rows", type=int, default=1000)
    parser.add_argument("--top-n", type=int, default=20, help="Hard cap; small-account sizing uses at most 10.")
    parser.add_argument("--min-amount", type=float, default=20_000_000.0)
    parser.add_argument("--initial-capital", type=float, default=50_000.0)
    parser.add_argument("--commission-bps", type=float, default=2.5)
    parser.add_argument("--stamp-duty-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.5)
    parser.add_argument("--model-version", default="logistic-price-volume-v1")
    parser.add_argument("--no-save-factors", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    horizons = sorted({int(value.strip()) for value in args.horizons.split(",") if value.strip()})
    if not horizons or any(value <= 0 for value in horizons):
        raise SystemExit("--horizons must contain positive integers")

    engine = get_engine()
    bars = load_bars(engine, args.start_date, args.end_date)
    if bars.empty:
        raise SystemExit("daily_bar_adj is empty for the requested date range; pull Tushare data first")
    raw_factors = calculate_raw_factors(bars)
    factor_wide, factor_long = standardize_cross_section(raw_factors)
    session_factory = get_session_factory()
    if not args.no_save_factors:
        with session_factory() as session:
            count = save_factors(session, factor_long)
        print(f"factors_upserted={count}")

    for horizon in horizons:
        config = WalkForwardConfig(
            horizon=horizon,
            train_window_days=args.train_window_days,
            min_train_days=args.min_train_days,
            min_train_rows=args.min_train_rows,
            top_n=args.top_n,
            min_amount=args.min_amount,
        )
        panel = build_model_panel(bars, factor_wide, horizon)
        predictions = walk_forward_predict(panel, config)
        if predictions.empty:
            print(f"horizon={horizon}d skipped=no_valid_training_window")
            continue

        with session_factory() as session:
            prediction_count = save_predictions(session, predictions, horizon, args.model_version)
        try:
            metrics, periods = evaluate_predictions(
                predictions,
                horizon=horizon,
                top_n=args.top_n,
                initial_capital=args.initial_capital,
                commission_bps=args.commission_bps,
                stamp_duty_bps=args.stamp_duty_bps,
                slippage_bps=args.slippage_bps,
            )
        except ValueError as exc:
            print(f"horizon={horizon}d predictions={prediction_count} backtest_skipped={exc}")
            continue

        start_date = pd.Timestamp(periods["trade_date"].min()).date()
        end_date = pd.Timestamp(periods["trade_date"].max()).date()
        notes = {
            "method": "rolling regularized logistic regression",
            "execution": "signal at close; enter next open; exit open after H sessions",
            "purge_days": horizon + 1,
            "train_window_days": args.train_window_days,
            "initial_capital": args.initial_capital,
            "position_rule": "dynamic 3-10 positions; equal cash; 100-share lots; 3% cash buffer",
            "max_positions": args.top_n,
            "commission_bps": args.commission_bps,
            "stamp_duty_bps_on_sell": args.stamp_duty_bps,
            "slippage_bps_each_side": args.slippage_bps,
            "minimum_commission": 0,
            "ending_capital": metrics.get("ending_capital"),
            "max_drawdown_frequency": "rebalance period endpoints",
            "periods": int(metrics["periods"]),
        }
        with session_factory() as session:
            save_backtest_summary(session, horizon, args.model_version, start_date, end_date, metrics, notes)
        print(
            f"horizon={horizon}d predictions={prediction_count} "
            f"backtest={json.dumps(metrics, ensure_ascii=False, allow_nan=False)}"
        )


if __name__ == "__main__":
    main()
