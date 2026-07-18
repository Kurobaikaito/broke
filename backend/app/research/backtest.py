from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..services.portfolio import allocate_lot_positions, validate_capital


def max_drawdown(period_returns: pd.Series) -> float:
    if period_returns.empty:
        return 0.0
    equity = (1.0 + period_returns.fillna(0.0)).cumprod()
    equity = pd.concat([pd.Series([1.0]), equity.reset_index(drop=True)], ignore_index=True)
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def evaluate_predictions(
    predictions: pd.DataFrame,
    horizon: int,
    top_n: int,
    transaction_cost_bps: float = 15.0,
    initial_capital: float | None = None,
    commission_bps: float = 2.5,
    stamp_duty_bps: float = 5.0,
    slippage_bps: float = 2.5,
) -> tuple[dict[str, float | int], pd.DataFrame]:
    """Evaluate non-overlapping Top-N portfolios using next-open execution returns."""
    if horizon <= 0 or top_n <= 0:
        raise ValueError("horizon and top_n must be positive")
    if initial_capital is not None:
        return evaluate_capital_predictions(
            predictions,
            horizon=horizon,
            top_n=top_n,
            initial_capital=initial_capital,
            commission_bps=commission_bps,
            stamp_duty_bps=stamp_duty_bps,
            slippage_bps=slippage_bps,
        )
    if predictions.empty:
        raise ValueError("No walk-forward predictions to evaluate")
    if transaction_cost_bps < 0:
        raise ValueError("transaction_cost_bps cannot be negative")
    completed = predictions.dropna(subset=["forward_return"]).copy()
    if "is_scheduled_rebalance" in completed.columns:
        # walk_forward_predict may append today's cross section so the UI always
        # has fresh recommendations.  It is not a scheduled historical holding
        # period and can overlap the preceding H-session portfolio.
        completed = completed[completed["is_scheduled_rebalance"].eq(True)].copy()  # noqa: E712
    if completed.empty:
        raise ValueError("Predictions do not yet have realized forward returns")

    rows: list[dict[str, object]] = []
    previous: set[str] = set()
    cost_rate = transaction_cost_bps / 10_000.0
    for trade_date, cross_section in completed.groupby("trade_date", sort=True):
        ranked = cross_section.sort_values("score", ascending=False)
        selected = ranked.head(min(top_n, len(ranked)))
        current = set(selected["code"].astype(str))
        if not current:
            continue
        overlap = len(previous.intersection(current))
        # Sum of absolute weight changes: initial buy is 1; full replacement is 2.
        traded_notional = 1.0 if not previous else 2.0 * (1.0 - overlap / max(len(current), 1))
        gross_return = float(selected["forward_return"].mean())
        net_return = gross_return - traded_notional * cost_rate
        benchmark_return = float(ranked["forward_return"].mean())
        valid_ic = ranked[["score", "forward_return"]].dropna()
        rank_ic = np.nan
        if (
            len(valid_ic) >= 3
            and valid_ic["score"].nunique() > 1
            and valid_ic["forward_return"].nunique() > 1
        ):
            rank_ic = float(valid_ic["score"].rank().corr(valid_ic["forward_return"].rank()))
        rows.append(
            {
                "trade_date": trade_date,
                "gross_return": gross_return,
                "net_return": net_return,
                "benchmark_return": benchmark_return,
                "turnover": traded_notional,
                "rank_ic": rank_ic,
                "holdings": ",".join(sorted(current)),
            }
        )
        previous = current

    periods = pd.DataFrame(rows)
    if periods.empty:
        raise ValueError("No completed backtest periods")
    strategy_total = float((1.0 + periods["net_return"]).prod() - 1.0)
    benchmark_total = float((1.0 + periods["benchmark_return"]).prod() - 1.0)
    std = float(periods["net_return"].std(ddof=1))
    annualization = math.sqrt(252.0 / horizon)
    sharpe = 0.0 if not np.isfinite(std) or std <= 1e-12 else float(periods["net_return"].mean() / std * annualization)
    mean_rank_ic = float(periods["rank_ic"].mean())
    if not np.isfinite(mean_rank_ic):
        mean_rank_ic = 0.0
    metrics: dict[str, float | int] = {
        "top_group_return": strategy_total,
        "benchmark_return": benchmark_total,
        "win_rate": float((periods["net_return"] > 0).mean()),
        "max_drawdown": max_drawdown(periods["net_return"]),
        "sharpe": sharpe,
        "rank_ic": mean_rank_ic,
        "turnover": float(periods["turnover"].mean()),
        "periods": len(periods),
    }
    return metrics, periods


def evaluate_capital_predictions(
    predictions: pd.DataFrame,
    horizon: int,
    top_n: int,
    initial_capital: float,
    commission_bps: float = 2.5,
    stamp_duty_bps: float = 5.0,
    slippage_bps: float = 2.5,
) -> tuple[dict[str, float | int], pd.DataFrame]:
    """Backtest affordable whole-lot positions without a minimum commission."""
    capital = validate_capital(initial_capital)
    if predictions.empty:
        raise ValueError("No walk-forward predictions to evaluate")
    if top_n <= 0 or min(commission_bps, stamp_duty_bps, slippage_bps) < 0:
        raise ValueError("position count and cost rates must be non-negative")
    completed = predictions.dropna(subset=["forward_return", "entry_open", "exit_open"]).copy()
    if "is_scheduled_rebalance" in completed.columns:
        completed = completed[completed["is_scheduled_rebalance"].eq(True)].copy()  # noqa: E712
    if completed.empty:
        raise ValueError("Predictions do not yet have realized executable returns")

    commission_rate = commission_bps / 10_000.0
    stamp_rate = stamp_duty_bps / 10_000.0
    slippage_rate = slippage_bps / 10_000.0
    rows: list[dict[str, object]] = []
    current_capital = capital

    for trade_date, cross_section in completed.groupby("trade_date", sort=True):
        ranked = cross_section.sort_values("score", ascending=False).copy()
        ranked["execution_entry"] = pd.to_numeric(ranked["entry_open"], errors="coerce") * (1 + slippage_rate)
        ranked["execution_exit"] = pd.to_numeric(ranked["exit_open"], errors="coerce") * (1 - slippage_rate)
        candidates = ranked.replace([np.inf, -np.inf], np.nan).dropna(
            subset=["execution_entry", "execution_exit"]
        )
        positions = allocate_lot_positions(
            candidates.to_dict("records"),
            capital=current_capital,
            price_key="execution_entry",
            max_positions=top_n,
            enforce_input_range=False,
        )
        if not positions:
            continue

        raw_buy = sum(float(item["entry_open"]) * int(item["target_shares"]) for item in positions)
        raw_sell = sum(float(item["exit_open"]) * int(item["target_shares"]) for item in positions)
        buy_notional = sum(float(item["execution_entry"]) * int(item["target_shares"]) for item in positions)
        sell_notional = sum(float(item["execution_exit"]) * int(item["target_shares"]) for item in positions)
        buy_cost = buy_notional * commission_rate
        sell_cost = sell_notional * (commission_rate + stamp_rate)
        ending_capital = current_capital - buy_notional - buy_cost + sell_notional - sell_cost
        gross_return = (current_capital - raw_buy + raw_sell) / current_capital - 1.0
        net_return = ending_capital / current_capital - 1.0
        benchmark_return = float(ranked["forward_return"].mean())
        valid_ic = ranked[["score", "forward_return"]].dropna()
        rank_ic = np.nan
        if (
            len(valid_ic) >= 3
            and valid_ic["score"].nunique() > 1
            and valid_ic["forward_return"].nunique() > 1
        ):
            rank_ic = float(valid_ic["score"].rank().corr(valid_ic["forward_return"].rank()))
        rows.append(
            {
                "trade_date": trade_date,
                "starting_capital": current_capital,
                "ending_capital": ending_capital,
                "gross_return": gross_return,
                "net_return": net_return,
                "benchmark_return": benchmark_return,
                "turnover": (buy_notional + sell_notional) / current_capital,
                "rank_ic": rank_ic,
                "position_count": len(positions),
                "holdings": ",".join(str(item["code"]) for item in positions),
            }
        )
        current_capital = ending_capital

    periods = pd.DataFrame(rows)
    if periods.empty:
        raise ValueError("No affordable completed backtest periods")
    std = float(periods["net_return"].std(ddof=1))
    annualization = math.sqrt(252.0 / horizon)
    sharpe = 0.0 if not np.isfinite(std) or std <= 1e-12 else float(periods["net_return"].mean() / std * annualization)
    mean_rank_ic = float(periods["rank_ic"].mean())
    if not np.isfinite(mean_rank_ic):
        mean_rank_ic = 0.0
    metrics: dict[str, float | int] = {
        "top_group_return": float(current_capital / capital - 1.0),
        "benchmark_return": float((1.0 + periods["benchmark_return"]).prod() - 1.0),
        "win_rate": float((periods["net_return"] > 0).mean()),
        "max_drawdown": max_drawdown(periods["net_return"]),
        "sharpe": sharpe,
        "rank_ic": mean_rank_ic,
        "turnover": float(periods["turnover"].mean()),
        "periods": len(periods),
        "ending_capital": float(current_capital),
    }
    return metrics, periods
