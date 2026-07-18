from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from .factors import FACTOR_COLUMNS


def daily_rank_ic(
    panel: pd.DataFrame,
    factor_columns: Sequence[str] = FACTOR_COLUMNS,
    target_column: str = "forward_return",
    min_observations: int = 5,
) -> pd.DataFrame:
    """Calculate per-date Spearman information coefficients in tidy form.

    This is intentionally a pure diagnostic: callers decide which historical
    slice is legal.  ``walk_forward_predict`` passes only its already-purged
    training frame when it performs optional feature selection.
    """
    if min_observations < 3:
        raise ValueError("min_observations must be at least 3")
    required = {"trade_date", target_column, *factor_columns}
    missing = required.difference(panel.columns)
    if missing:
        raise ValueError(f"Missing diagnostic columns: {sorted(missing)}")

    rows: list[dict[str, object]] = []
    ordered = panel.sort_values("trade_date")
    for trade_date, cross_section in ordered.groupby("trade_date", sort=True):
        numeric = cross_section[[target_column, *factor_columns]].apply(pd.to_numeric, errors="coerce")
        numeric = numeric.replace([np.inf, -np.inf], np.nan)
        ranked = numeric.rank(method="average")
        target = numeric[target_column]
        ranked_target = ranked[target_column]
        correlations = ranked[list(factor_columns)].corrwith(ranked_target)
        observations = numeric[list(factor_columns)].notna().mul(target.notna(), axis=0).sum()
        target_unique = target.nunique(dropna=True)
        for factor in factor_columns:
            factor_observations = int(observations[factor])
            rank_ic = correlations[factor]
            if (
                factor_observations < min_observations
                or target_unique < 2
                or numeric[factor].nunique(dropna=True) < 2
            ):
                rank_ic = np.nan
            rows.append(
                {
                    "trade_date": trade_date,
                    "factor_name": factor,
                    "rank_ic": float(rank_ic) if np.isfinite(rank_ic) else np.nan,
                    "observations": factor_observations,
                }
            )
    return pd.DataFrame(rows, columns=["trade_date", "factor_name", "rank_ic", "observations"])


def summarize_factor_performance(
    panel: pd.DataFrame,
    factor_columns: Sequence[str] = FACTOR_COLUMNS,
    target_column: str = "forward_return",
    min_observations: int = 5,
) -> pd.DataFrame:
    """Summarize Rank IC, IC stability and directional hit rate by factor."""
    daily = daily_rank_ic(panel, factor_columns, target_column, min_observations)
    rows: list[dict[str, object]] = []
    for factor in factor_columns:
        values = daily.loc[daily["factor_name"].eq(factor), "rank_ic"].dropna()
        mean_ic = float(values.mean()) if not values.empty else np.nan
        std_ic = float(values.std(ddof=1)) if len(values) > 1 else np.nan
        ic_ir = mean_ic / std_ic if np.isfinite(std_ic) and std_ic > 1e-12 else np.nan
        rows.append(
            {
                "factor_name": factor,
                "mean_rank_ic": mean_ic,
                "rank_ic_std": std_ic,
                "rank_ic_ir": ic_ir,
                "positive_rate": float(values.gt(0).mean()) if not values.empty else np.nan,
                "dates": int(len(values)),
            }
        )
    return pd.DataFrame(rows)


def select_stable_factors(
    training_panel: pd.DataFrame,
    max_features: int,
    factor_columns: Sequence[str] = FACTOR_COLUMNS,
    target_column: str = "forward_return",
    min_observations: int = 5,
    min_dates: int = 20,
) -> list[str]:
    """Select factors by training-only Rank-IC stability.

    Absolute direction is used because a linear estimator can learn either sign.
    The score discounts one-off IC estimates by their time-series variability and
    never looks at the prediction date or later labels.
    """
    if max_features <= 0:
        raise ValueError("max_features must be positive")
    candidates = list(dict.fromkeys(factor_columns))
    if max_features >= len(candidates):
        return candidates
    summary = summarize_factor_performance(
        training_panel,
        candidates,
        target_column=target_column,
        min_observations=min_observations,
    )
    valid = summary[summary["dates"].ge(min_dates) & summary["mean_rank_ic"].notna()].copy()
    if valid.empty:
        return candidates[:max_features]
    dispersion = valid["rank_ic_std"].clip(lower=1e-6).fillna(1.0)
    valid["selection_score"] = valid["mean_rank_ic"].abs() * np.sqrt(valid["dates"]) / dispersion
    valid = valid.sort_values(["selection_score", "factor_name"], ascending=[False, True])
    selected = valid["factor_name"].head(max_features).tolist()
    if len(selected) < max_features:
        selected.extend(name for name in candidates if name not in selected)
    return selected[:max_features]
