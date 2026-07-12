from __future__ import annotations

import math

import numpy as np
import pandas as pd


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
) -> tuple[dict[str, float | int], pd.DataFrame]:
    """Evaluate non-overlapping Top-N portfolios using next-open execution returns."""
    if predictions.empty:
        raise ValueError("No walk-forward predictions to evaluate")
    if transaction_cost_bps < 0:
        raise ValueError("transaction_cost_bps cannot be negative")
    completed = predictions.dropna(subset=["forward_return"]).copy()
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
        rank_ic = (
            float(valid_ic["score"].rank().corr(valid_ic["forward_return"].rank()))
            if len(valid_ic) >= 3
            else np.nan
        )
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
