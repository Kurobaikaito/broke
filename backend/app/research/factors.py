from __future__ import annotations

import numpy as np
import pandas as pd


FACTOR_COLUMNS = [
    "momentum_20d",
    "momentum_60d",
    "reversal_5d",
    "trend_20d",
    "low_volatility_20d",
    "drawdown_60d",
    "liquidity_20d",
    "turnover_20d",
]


def calculate_raw_factors(bars: pd.DataFrame) -> pd.DataFrame:
    """Calculate trailing price-volume factors using data available at each close."""
    required = {"code", "trade_date", "close", "amount", "turnover_rate"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"Missing bar columns: {sorted(missing)}")

    frame = bars.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values(["code", "trade_date"]).reset_index(drop=True)
    calendar = {value: index for index, value in enumerate(sorted(frame["trade_date"].unique()))}
    frame["calendar_index"] = frame["trade_date"].map(calendar)
    numeric = ["close", "amount", "turnover_rate"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    grouped = frame.groupby("code", sort=False, group_keys=False)

    frame["return_1d"] = grouped["close"].pct_change(fill_method=None)
    frame["momentum_20d"] = grouped["close"].pct_change(20, fill_method=None)
    frame["momentum_60d"] = grouped["close"].pct_change(60, fill_method=None)
    frame["reversal_5d"] = -grouped["close"].pct_change(5, fill_method=None)
    ma20 = grouped["close"].transform(lambda values: values.rolling(20, min_periods=20).mean())
    frame["trend_20d"] = frame["close"] / ma20 - 1.0
    volatility = grouped["return_1d"].transform(lambda values: values.rolling(20, min_periods=20).std())
    frame["low_volatility_20d"] = -volatility * np.sqrt(252.0)
    rolling_high = grouped["close"].transform(lambda values: values.rolling(60, min_periods=60).max())
    frame["drawdown_60d"] = frame["close"] / rolling_high - 1.0
    mean_amount = grouped["amount"].transform(lambda values: values.rolling(20, min_periods=20).mean())
    frame["liquidity_20d"] = np.log1p(mean_amount.clip(lower=0))
    frame["turnover_20d"] = grouped["turnover_rate"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    previous_60_index = grouped["calendar_index"].shift(60)
    continuous_history = (frame["calendar_index"] - previous_60_index).eq(60)
    frame.loc[~continuous_history, FACTOR_COLUMNS] = np.nan
    return frame[["code", "trade_date", *FACTOR_COLUMNS]].replace([np.inf, -np.inf], np.nan)


def standardize_cross_section(
    raw_factors: pd.DataFrame,
    lower_quantile: float = 0.01,
    upper_quantile: float = 0.99,
    min_observations: int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Winsorize and z-score each factor independently on each trade date."""
    if not 0 <= lower_quantile < upper_quantile <= 1:
        raise ValueError("Winsorization quantiles must satisfy 0 <= lower < upper <= 1")
    wide = raw_factors.copy()

    def normalize(values: pd.Series) -> pd.Series:
        valid = values.dropna()
        if len(valid) < min_observations:
            return pd.Series(np.nan, index=values.index, dtype=float)
        lower, upper = valid.quantile([lower_quantile, upper_quantile])
        clipped = values.clip(lower=lower, upper=upper)
        std = clipped.std(ddof=0)
        if not np.isfinite(std) or std <= 1e-12:
            return pd.Series(0.0, index=values.index).where(values.notna())
        return (clipped - clipped.mean()) / std

    for factor in FACTOR_COLUMNS:
        wide[factor] = wide.groupby("trade_date", group_keys=False)[factor].transform(normalize)

    raw_long = raw_factors.melt(
        id_vars=["code", "trade_date"], value_vars=FACTOR_COLUMNS, var_name="factor_name", value_name="factor_value"
    )
    z_long = wide.melt(
        id_vars=["code", "trade_date"], value_vars=FACTOR_COLUMNS, var_name="factor_name", value_name="factor_zscore"
    )
    long_frame = raw_long.merge(z_long, on=["code", "trade_date", "factor_name"], how="inner")
    long_frame = long_frame.dropna(subset=["factor_value"])
    return wide, long_frame
