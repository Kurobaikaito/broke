from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd


# Keep the catalogue explicit: downstream storage and model explanations use these
# names as a public contract.  Lookbacks are expressed as the calendar-index lag
# needed to prove that the full input window is continuous for a security.
FACTOR_LOOKBACKS: dict[str, int] = {
    "momentum_20d": 20,
    "momentum_60d": 60,
    "momentum_120_20d": 120,
    "reversal_5d": 5,
    "trend_20d": 19,
    "high_proximity_120d": 119,
    "low_volatility_20d": 20,
    "downside_risk_20d": 20,
    "drawdown_60d": 59,
    "market_relative_momentum_60d": 60,
    "market_beta_60d": 60,
    "liquidity_20d": 19,
    "amihud_liquidity_20d": 20,
    "turnover_20d": 19,
    "amount_stability_20d": 19,
}
FACTOR_COLUMNS = list(FACTOR_LOOKBACKS)
FactorProgressCallback = Callable[[int, int, str], None]


def _continuous_history(grouped, lag: int) -> pd.Series:
    """Return whether a row has every market session required by ``lag``."""
    previous_index = grouped["calendar_index"].shift(lag)
    return (grouped.obj["calendar_index"] - previous_index).eq(lag)


def calculate_raw_factors(
    bars: pd.DataFrame,
    progress_callback: FactorProgressCallback | None = None,
) -> pd.DataFrame:
    """Calculate point-in-time trailing factors using information known at each close.

    The implementation deliberately uses the market-wide trading calendar rather
    than silently treating a suspension as an ordinary observation.  A factor is
    null only when *its own* lookback is incomplete, so a short-horizon diagnostic
    remains available while the 120-session model history is still warming up.
    """
    required = {"code", "trade_date", "close", "amount", "turnover_rate"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"Missing bar columns: {sorted(missing)}")
    if progress_callback is not None:
        progress_callback(0, 5, "准备行情与交易日历")

    frame = bars.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    if frame["trade_date"].isna().any():
        raise ValueError("trade_date contains invalid values")
    frame = frame.sort_values(["code", "trade_date"]).reset_index(drop=True)
    if frame.duplicated(["code", "trade_date"]).any():
        raise ValueError("bars contains duplicate (code, trade_date) rows")

    calendar = {value: index for index, value in enumerate(sorted(frame["trade_date"].unique()))}
    frame["calendar_index"] = frame["trade_date"].map(calendar)
    numeric = ["close", "amount", "turnover_rate"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    frame.loc[frame["close"].le(0), "close"] = np.nan
    frame.loc[frame["amount"].lt(0), "amount"] = np.nan
    frame.loc[frame["turnover_rate"].lt(0), "turnover_rate"] = np.nan
    grouped = frame.groupby("code", sort=False, group_keys=False)

    frame["return_1d"] = grouped["close"].pct_change(fill_method=None)
    frame.loc[~_continuous_history(grouped, 1), "return_1d"] = np.nan
    grouped = frame.groupby("code", sort=False, group_keys=False)
    if progress_callback is not None:
        progress_callback(1, 5, "计算日收益序列")

    # Price trend and reversal signals.  The 120-to-20 signal skips the most
    # recent month to reduce overlap with the short-term reversal effect.
    frame["momentum_20d"] = grouped["close"].pct_change(20, fill_method=None)
    frame["momentum_60d"] = grouped["close"].pct_change(60, fill_method=None)
    frame["momentum_120_20d"] = grouped["close"].shift(20) / grouped["close"].shift(120) - 1.0
    frame["reversal_5d"] = -grouped["close"].pct_change(5, fill_method=None)
    ma20 = grouped["close"].transform(lambda values: values.rolling(20, min_periods=20).mean())
    frame["trend_20d"] = frame["close"] / ma20 - 1.0
    rolling_high_120 = grouped["close"].transform(lambda values: values.rolling(120, min_periods=120).max())
    frame["high_proximity_120d"] = frame["close"] / rolling_high_120 - 1.0
    if progress_callback is not None:
        progress_callback(2, 5, "计算动量与趋势因子")

    # Risk factors are signed so that a larger value consistently means less
    # trailing risk; beta is left unsigned for the estimator to learn.
    volatility = grouped["return_1d"].transform(lambda values: values.rolling(20, min_periods=20).std())
    frame["low_volatility_20d"] = -volatility * np.sqrt(252.0)
    negative_return = frame["return_1d"].clip(upper=0.0)
    frame["_negative_return_sq"] = negative_return.pow(2)
    grouped = frame.groupby("code", sort=False, group_keys=False)
    downside_variance = grouped["_negative_return_sq"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    frame["downside_risk_20d"] = -np.sqrt(downside_variance * 252.0)
    rolling_high_60 = grouped["close"].transform(lambda values: values.rolling(60, min_periods=60).max())
    frame["drawdown_60d"] = frame["close"] / rolling_high_60 - 1.0
    if progress_callback is not None:
        progress_callback(3, 5, "计算波动与回撤因子")

    # Equal-weight market residuals provide a market-relative trend and a
    # transparent beta estimate without requiring an external index series.
    frame["_market_return"] = frame.groupby("trade_date")["return_1d"].transform("mean")
    safe_stock_return = frame["return_1d"].where(frame["return_1d"].gt(-1.0))
    safe_market_return = frame["_market_return"].where(frame["_market_return"].gt(-1.0))
    frame["_relative_log_return"] = np.log1p(safe_stock_return) - np.log1p(safe_market_return)
    frame["_return_market_product"] = frame["return_1d"] * frame["_market_return"]
    frame["_market_return_sq"] = frame["_market_return"].pow(2)
    grouped = frame.groupby("code", sort=False, group_keys=False)
    frame["market_relative_momentum_60d"] = grouped["_relative_log_return"].transform(
        lambda values: values.rolling(60, min_periods=60).sum()
    )
    rolling_return_mean = grouped["return_1d"].transform(lambda values: values.rolling(60, min_periods=60).mean())
    rolling_market_mean = grouped["_market_return"].transform(
        lambda values: values.rolling(60, min_periods=60).mean()
    )
    rolling_cross_mean = grouped["_return_market_product"].transform(
        lambda values: values.rolling(60, min_periods=60).mean()
    )
    rolling_market_square_mean = grouped["_market_return_sq"].transform(
        lambda values: values.rolling(60, min_periods=60).mean()
    )
    market_variance = rolling_market_square_mean - rolling_market_mean.pow(2)
    covariance = rolling_cross_mean - rolling_return_mean * rolling_market_mean
    frame["market_beta_60d"] = covariance / market_variance.where(market_variance.gt(1e-12))
    if progress_callback is not None:
        progress_callback(4, 5, "计算市场相对因子")

    # Turnover and price-impact proxies use adjusted CNY amount.  Multiplying
    # Amihud's |return| / amount ratio by 1e8 only keeps raw values readable;
    # it has no effect on the later cross-sectional z-score.
    mean_amount = grouped["amount"].transform(lambda values: values.rolling(20, min_periods=20).mean())
    frame["liquidity_20d"] = np.log1p(mean_amount.clip(lower=0))
    frame["_amihud_daily"] = frame["return_1d"].abs() * 100_000_000.0 / frame["amount"].where(
        frame["amount"].gt(0)
    )
    grouped = frame.groupby("code", sort=False, group_keys=False)
    rolling_amihud = grouped["_amihud_daily"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    frame["amihud_liquidity_20d"] = -np.log1p(rolling_amihud)
    frame["turnover_20d"] = grouped["turnover_rate"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    frame["_log_amount"] = np.log1p(frame["amount"].clip(lower=0))
    grouped = frame.groupby("code", sort=False, group_keys=False)
    frame["amount_stability_20d"] = -grouped["_log_amount"].transform(
        lambda values: values.rolling(20, min_periods=20).std(ddof=0)
    )

    # A missing market session invalidates only factors whose actual window
    # crosses that gap.  This prevents stale suspended prices from masquerading
    # as smooth momentum or low volatility.
    for factor, lag in FACTOR_LOOKBACKS.items():
        continuous = _continuous_history(grouped, lag)
        frame.loc[~continuous, factor] = np.nan
    if progress_callback is not None:
        progress_callback(5, 5, "计算流动性并校验窗口")

    identifiers = ["code", "trade_date"]
    if "industry" in frame.columns:
        identifiers.append("industry")
    return frame[[*identifiers, *FACTOR_COLUMNS]].replace([np.inf, -np.inf], np.nan)


def standardize_cross_section(
    raw_factors: pd.DataFrame,
    lower_quantile: float = 0.01,
    upper_quantile: float = 0.99,
    min_observations: int = 5,
    neutralize_by: str | None = None,
    min_group_observations: int = 3,
    materialize_long: bool = True,
    progress_callback: FactorProgressCallback | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Winsorize and z-score each factor independently on each trade date.

    ``neutralize_by`` can be set to a point-in-time classification such as
    ``industry``.  Groups that are too small fall back to the date-wide mean,
    avoiding unstable one-security residuals.  All fitted statistics are from
    the same date as the signal, so no future cross section is consulted.
    """
    if not 0 <= lower_quantile < upper_quantile <= 1:
        raise ValueError("Winsorization quantiles must satisfy 0 <= lower < upper <= 1")
    if min_observations < 2 or min_group_observations < 1:
        raise ValueError("minimum observation counts must be positive")
    required = {"code", "trade_date", *FACTOR_COLUMNS}
    missing = required.difference(raw_factors.columns)
    if missing:
        raise ValueError(f"Missing factor columns: {sorted(missing)}")
    if neutralize_by is not None and neutralize_by not in raw_factors.columns:
        raise ValueError(f"Neutralization column is missing: {neutralize_by}")

    trade_dates = pd.to_datetime(raw_factors["trade_date"], errors="coerce")
    if trade_dates.isna().any():
        raise ValueError("trade_date contains invalid values")
    # The production research path consumes only the wide representation.  In
    # that path keep a single copy instead of retaining two full eight-million
    # row frames merely to support the optional legacy long representation.
    source = raw_factors.copy() if materialize_long else None
    if source is not None:
        source["trade_date"] = trade_dates
        wide = source.copy()
    else:
        wide = raw_factors.copy()
        wide["trade_date"] = trade_dates
    if wide.duplicated(["code", "trade_date"]).any():
        raise ValueError("raw_factors contains duplicate (code, trade_date) rows")

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

    if progress_callback is not None:
        progress_callback(0, len(FACTOR_COLUMNS), "准备截面标准化")
    for factor_index, factor in enumerate(FACTOR_COLUMNS, start=1):
        values = pd.to_numeric(wide[factor], errors="coerce")
        if neutralize_by is not None:
            group_keys = [wide["trade_date"], wide[neutralize_by].fillna("__MISSING__")]
            group_mean = values.groupby(group_keys, dropna=False).transform("mean")
            group_count = values.groupby(group_keys, dropna=False).transform("count")
            date_mean = values.groupby(wide["trade_date"]).transform("mean")
            values = values - group_mean.where(group_count.ge(min_group_observations), date_mean)
        wide[factor] = values.groupby(wide["trade_date"], group_keys=False).transform(normalize)
        if progress_callback is not None:
            progress_callback(factor_index, len(FACTOR_COLUMNS), factor)

    long_frame = (
        factor_frames_to_long(source, wide)
        if materialize_long
        else pd.DataFrame(
            columns=["code", "trade_date", "factor_name", "factor_value", "factor_zscore"]
        )
    )
    return wide, long_frame


def factor_frames_to_long(
    raw_factors: pd.DataFrame,
    standardized_factors: pd.DataFrame,
) -> pd.DataFrame:
    """Materialize a selected factor slice in storage-friendly long form.

    The research runner deliberately calls this only for the latest date.  The
    wide frames remain the efficient representation for model training; turning
    eight million rows into a 15-times-larger long frame is neither required by
    the estimator nor by the serving APIs.
    """
    required = {"code", "trade_date", *FACTOR_COLUMNS}
    raw_missing = required.difference(raw_factors.columns)
    standardized_missing = required.difference(standardized_factors.columns)
    if raw_missing or standardized_missing:
        missing = sorted(raw_missing | standardized_missing)
        raise ValueError(f"Missing factor columns: {missing}")

    raw = raw_factors[["code", "trade_date", *FACTOR_COLUMNS]].reset_index(drop=True)
    standardized = standardized_factors[["code", "trade_date", *FACTOR_COLUMNS]].reset_index(drop=True)
    if len(raw) != len(standardized) or not raw[["code", "trade_date"]].equals(
        standardized[["code", "trade_date"]]
    ):
        raise ValueError("raw and standardized factor rows must align")

    raw_long = raw.melt(
        id_vars=["code", "trade_date"],
        value_vars=FACTOR_COLUMNS,
        var_name="factor_name",
        value_name="factor_value",
    )
    standardized_long = standardized.melt(
        id_vars=["code", "trade_date"],
        value_vars=FACTOR_COLUMNS,
        var_name="factor_name",
        value_name="factor_zscore",
    )
    if not raw_long[["code", "trade_date", "factor_name"]].equals(
        standardized_long[["code", "trade_date", "factor_name"]]
    ):
        raise ValueError("factor melt order is inconsistent")
    raw_long["factor_zscore"] = standardized_long["factor_zscore"].to_numpy()
    return raw_long.dropna(subset=["factor_value"]).reset_index(drop=True)
