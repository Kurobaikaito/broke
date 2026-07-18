from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .diagnostics import select_stable_factors
from .factors import FACTOR_COLUMNS


SUPPORTED_MODEL_TYPES = {"logistic", "ridge"}
ModelProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class WalkForwardConfig:
    horizon: int = 20
    train_window_days: int = 756
    min_train_days: int = 252
    min_train_rows: int = 1000
    top_n: int = 20
    rebalance_step: int | None = None
    min_amount: float = 20_000_000.0
    random_state: int = 42
    model_type: str = "logistic"
    logistic_c: float = 0.1
    ridge_alpha: float = 10.0
    max_features: int | None = None
    min_ic_dates: int = 20
    date_weighting: bool = True
    half_life_days: float | None = None
    include_latest: bool = True
    prediction_scope: str = "full"
    explanation_scope: str = "all"

    def __post_init__(self) -> None:
        if self.horizon <= 0 or self.train_window_days <= 0 or self.min_train_days <= 0:
            raise ValueError("horizon and training windows must be positive")
        if self.min_train_days > self.train_window_days:
            raise ValueError("min_train_days cannot exceed train_window_days")
        if self.min_train_rows <= 0 or self.top_n <= 0:
            raise ValueError("min_train_rows and top_n must be positive")
        if self.rebalance_step is not None and self.rebalance_step <= 0:
            raise ValueError("rebalance_step must be positive")
        if self.min_amount < 0:
            raise ValueError("min_amount cannot be negative")
        if self.model_type not in SUPPORTED_MODEL_TYPES:
            raise ValueError(f"model_type must be one of {sorted(SUPPORTED_MODEL_TYPES)}")
        if self.logistic_c <= 0 or self.ridge_alpha <= 0:
            raise ValueError("model regularization parameters must be positive")
        if self.max_features is not None and not 1 <= self.max_features <= len(FACTOR_COLUMNS):
            raise ValueError(f"max_features must be between 1 and {len(FACTOR_COLUMNS)}")
        if self.min_ic_dates <= 0:
            raise ValueError("min_ic_dates must be positive")
        if self.half_life_days is not None and self.half_life_days <= 0:
            raise ValueError("half_life_days must be positive")
        if self.prediction_scope not in {"full", "latest"}:
            raise ValueError("prediction_scope must be 'full' or 'latest'")
        if self.explanation_scope not in {"all", "latest"}:
            raise ValueError("explanation_scope must be 'all' or 'latest'")


def build_model_panel(bars: pd.DataFrame, factors: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Build point-in-time features and next-open-to-future-open labels.

    ``entry_date`` and ``label_available_date`` make the timing contract
    inspectable.  A per-security missing market session is never skipped: if the
    exact next-session or exit-session open is absent, that label stays null.
    """
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    needed = ["code", "trade_date", "open", "close", "amount"]
    missing = set(needed).difference(bars.columns)
    if missing:
        raise ValueError(f"Missing bar columns: {sorted(missing)}")
    missing_factors = set(FACTOR_COLUMNS).difference(factors.columns)
    if missing_factors:
        raise ValueError(f"Missing factor columns: {sorted(missing_factors)}")

    prices = bars[needed].copy()
    prices["trade_date"] = pd.to_datetime(prices["trade_date"], errors="coerce")
    if prices["trade_date"].isna().any():
        raise ValueError("bars.trade_date contains invalid values")
    prices[["open", "close", "amount"]] = prices[["open", "close", "amount"]].apply(
        pd.to_numeric, errors="coerce"
    )
    prices = prices.sort_values(["code", "trade_date"]).reset_index(drop=True)
    if prices.duplicated(["code", "trade_date"]).any():
        raise ValueError("bars contains duplicate (code, trade_date) rows")
    calendar = {value: index for index, value in enumerate(sorted(prices["trade_date"].unique()))}
    prices["calendar_index"] = prices["trade_date"].map(calendar)

    opens = prices[["code", "calendar_index", "trade_date", "open"]]
    entry = opens.rename(columns={"open": "entry_open", "trade_date": "entry_date"}).copy()
    entry["calendar_index"] -= 1
    exit_prices = opens.rename(
        columns={"open": "exit_open", "trade_date": "label_available_date"}
    ).copy()
    exit_prices["calendar_index"] -= horizon + 1
    prices = prices.merge(entry, on=["code", "calendar_index"], how="left", validate="one_to_one")
    prices = prices.merge(exit_prices, on=["code", "calendar_index"], how="left", validate="one_to_one")
    executable = prices["entry_open"].gt(0) & prices["exit_open"].gt(0)
    prices["forward_return"] = (prices["exit_open"] / prices["entry_open"] - 1.0).where(executable)
    prices["label"] = np.where(
        prices["forward_return"].notna(),
        prices["forward_return"].gt(0).astype(float),
        np.nan,
    )
    # This is a label-side field only.  It is useful for a return-ranking model
    # and diagnostics but is never part of FACTOR_COLUMNS.
    cross_section_median = prices.groupby("trade_date")["forward_return"].transform("median")
    prices["excess_forward_return"] = prices["forward_return"] - cross_section_median

    factor_frame = factors.copy()
    factor_frame["trade_date"] = pd.to_datetime(factor_frame["trade_date"], errors="coerce")
    if factor_frame["trade_date"].isna().any():
        raise ValueError("factors.trade_date contains invalid values")
    if factor_frame.duplicated(["code", "trade_date"]).any():
        raise ValueError("factors contains duplicate (code, trade_date) rows")
    panel = prices.merge(factor_frame, on=["code", "trade_date"], how="inner", validate="one_to_one")
    panel = panel.replace([np.inf, -np.inf], np.nan)
    panel["feature_coverage"] = panel[FACTOR_COLUMNS].notna().mean(axis=1)
    panel["eligible"] = (
        panel[FACTOR_COLUMNS].notna().all(axis=1)
        & panel["close"].gt(0)
        & panel["amount"].ge(0)
    )
    return panel.sort_values(["trade_date", "code"]).reset_index(drop=True)


def _make_estimator(
    random_state: int,
    model_type: str = "logistic",
    logistic_c: float = 0.1,
    ridge_alpha: float = 10.0,
):
    try:
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression, Ridge
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise RuntimeError("scikit-learn is required; run: pip install -r requirements.txt") from exc

    if model_type == "logistic":
        model = LogisticRegression(
            C=logistic_c,
            max_iter=1000,
            random_state=random_state,
            solver="lbfgs",
        )
    elif model_type == "ridge":
        model = Ridge(alpha=ridge_alpha)
    else:
        raise ValueError(f"Unsupported model_type: {model_type}")
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", model),
        ]
    )


def _sample_weights(train: pd.DataFrame, config: WalkForwardConfig) -> np.ndarray:
    weights = pd.Series(1.0, index=train.index, dtype=float)
    if config.date_weighting:
        date_counts = train.groupby("trade_date")["code"].transform("size").astype(float)
        weights = weights / date_counts
    if config.half_life_days is not None:
        ordered_dates = pd.Index(sorted(train["trade_date"].unique()))
        date_index = {value: index for index, value in enumerate(ordered_dates)}
        ages = (len(ordered_dates) - 1 - train["trade_date"].map(date_index)).astype(float)
        weights = weights * np.power(0.5, ages / config.half_life_days)
    mean_weight = float(weights.mean())
    if not np.isfinite(mean_weight) or mean_weight <= 0:
        return np.ones(len(train), dtype=float)
    return (weights / mean_weight).to_numpy(dtype=float)


def _ridge_probability(scores: np.ndarray, residual_scale: float) -> np.ndarray:
    """Map predicted returns to a bounded, explicitly approximate probability."""
    scale = max(float(residual_scale), 1e-8)
    logits = np.clip(np.asarray(scores, dtype=float) / scale, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-logits))


def _cross_sectional_risk_flags(row: pd.Series, threshold: float = 1.5) -> list[str]:
    """Create explainable relative-risk flags from standardized factor values."""
    flags: list[str] = []
    if row.get("low_volatility_20d", 0.0) < -threshold or row.get("downside_risk_20d", 0.0) < -threshold:
        flags.append("高波动")
    if row.get("liquidity_20d", 0.0) < -threshold or row.get("amihud_liquidity_20d", 0.0) < -threshold:
        flags.append("流动性偏低")
    if row.get("drawdown_60d", 0.0) < -threshold:
        flags.append("回撤较深")
    if row.get("market_beta_60d", 0.0) > threshold:
        flags.append("市场敏感度偏高")
    if row.get("amount_stability_20d", 0.0) < -threshold:
        flags.append("成交额不稳定")
    return flags


def _prediction_schedule(total_dates: int, config: WalkForwardConfig) -> tuple[list[int], set[int]]:
    if total_dates < 0:
        raise ValueError("total_dates cannot be negative")
    first_index = config.min_train_days + config.horizon + 1
    if total_dates <= first_index:
        return [], set()
    step = config.rebalance_step or config.horizon
    scheduled_indices = list(range(first_index, total_dates, step))
    latest_index = total_dates - 1
    if config.prediction_scope == "latest":
        prediction_indices = [latest_index]
    else:
        prediction_indices = list(scheduled_indices)
        if config.include_latest and latest_index not in prediction_indices:
            prediction_indices.append(latest_index)
    return sorted(set(prediction_indices)), set(scheduled_indices)


def prediction_window_count(total_dates: int, config: WalkForwardConfig) -> int:
    """Return the exact number of rolling windows the predictor will attempt."""
    prediction_indices, _ = _prediction_schedule(total_dates, config)
    return len(prediction_indices)


def walk_forward_predict(
    panel: pd.DataFrame,
    config: WalkForwardConfig,
    *,
    progress_callback: ModelProgressCallback | None = None,
) -> pd.DataFrame:
    """Refit on each rebalance date with an H+1-session purge and predict OOS.

    Training rows are date-weighted by default so a date with unusually many
    securities does not dominate the estimator.  Optional feature selection is
    based only on Rank IC inside the already-purged training slice.  The most
    recent date can be appended for live recommendations; it is marked as an
    unscheduled snapshot so the backtest can exclude a potentially overlapping
    final holding period.  ``prediction_scope='latest'`` reuses exactly the same
    purge, training, feature-selection, and fitting logic but evaluates only the
    most recent cross section for inexpensive daily publication.
    """
    required = {"code", "trade_date", "amount", "eligible", "label", "forward_return", *FACTOR_COLUMNS}
    missing = required.difference(panel.columns)
    if missing:
        raise ValueError(f"Missing panel columns: {sorted(missing)}")
    working = panel.copy()
    working["trade_date"] = pd.to_datetime(working["trade_date"], errors="coerce")
    if working["trade_date"].isna().any():
        raise ValueError("panel.trade_date contains invalid values")
    if "label_available_date" in working.columns:
        working["label_available_date"] = pd.to_datetime(working["label_available_date"], errors="coerce")
    working["amount"] = pd.to_numeric(working["amount"], errors="coerce")
    working["label"] = pd.to_numeric(working["label"], errors="coerce")
    working["forward_return"] = pd.to_numeric(working["forward_return"], errors="coerce")
    working[FACTOR_COLUMNS] = working[FACTOR_COLUMNS].apply(pd.to_numeric, errors="coerce")
    working = working.sort_values(["trade_date", "code"]).reset_index(drop=True)
    eligible = working["eligible"].eq(True)  # noqa: E712

    dates = pd.Index(sorted(working["trade_date"].dropna().unique()))
    prediction_indices, scheduled_set = _prediction_schedule(len(dates), config)
    if not prediction_indices:
        return pd.DataFrame()
    latest_index = len(dates) - 1

    outputs: list[pd.DataFrame] = []
    fitted_windows = 0
    total_windows = len(prediction_indices)
    for window_index, date_index in enumerate(prediction_indices, start=1):
        prediction_date = dates[date_index]

        def report_window(status: str) -> None:
            if progress_callback is not None:
                progress_callback(
                    {
                        "completed_windows": window_index,
                        "total_windows": total_windows,
                        "fitted_windows": fitted_windows,
                        "prediction_date": pd.Timestamp(prediction_date).date().isoformat(),
                        "status": status,
                    }
                )

        # A label dated t exits at t + horizon + 1 because entry is next open.
        purge_index = date_index - config.horizon - 1
        train_start_index = max(0, purge_index - config.train_window_days + 1)
        train_start = dates[train_start_index]
        train_end = dates[purge_index]
        target_column = "label" if config.model_type == "logistic" else "forward_return"
        train_mask = (
            working["trade_date"].between(train_start, train_end)
            & eligible
            & working[target_column].notna()
            & working["forward_return"].notna()
            & working["amount"].ge(config.min_amount)
        )
        if "label_available_date" in working.columns:
            train_mask &= working["label_available_date"].le(prediction_date)
        train = working.loc[train_mask].copy()
        test = working[
            working["trade_date"].eq(prediction_date)
            & eligible
            & working["amount"].ge(config.min_amount)
        ].copy()
        if (
            len(train) < config.min_train_rows
            or train["trade_date"].nunique() < config.min_train_days
            or test.empty
        ):
            report_window("skipped")
            continue
        target = pd.to_numeric(train[target_column], errors="coerce")
        if target.isna().any() or target.nunique() < 2:
            report_window("skipped")
            continue
        if config.model_type == "logistic" and not set(target.unique()).issubset({0.0, 1.0}):
            raise ValueError("logistic labels must contain only 0 and 1")

        feature_columns = list(FACTOR_COLUMNS)
        if config.max_features is not None:
            feature_columns = select_stable_factors(
                train,
                max_features=config.max_features,
                factor_columns=feature_columns,
                target_column="forward_return",
                min_dates=config.min_ic_dates,
            )

        estimator = _make_estimator(
            config.random_state,
            model_type=config.model_type,
            logistic_c=config.logistic_c,
            ridge_alpha=config.ridge_alpha,
        )
        weights = _sample_weights(train, config)
        estimator.fit(train[feature_columns], target, model__sample_weight=weights)
        fitted_windows += 1
        if config.model_type == "logistic":
            test["probability"] = estimator.predict_proba(test[feature_columns])[:, 1]
            test["score"] = estimator.decision_function(test[feature_columns])
            probability_kind = "positive_return_logistic"
        else:
            test["score"] = estimator.predict(test[feature_columns])
            train_residual = target.to_numpy(dtype=float) - estimator.predict(train[feature_columns])
            residual_scale = float(np.nanstd(train_residual, ddof=1))
            if not np.isfinite(residual_scale) or residual_scale <= 1e-8:
                residual_scale = float(np.nanstd(target.to_numpy(dtype=float), ddof=1))
            test["probability"] = _ridge_probability(test["score"].to_numpy(), residual_scale)
            probability_kind = "positive_return_residual_approximation"

        test = test.sort_values(["score", "code"], ascending=[False, True]).reset_index(drop=True)
        test["rank_no"] = np.arange(1, len(test) + 1, dtype=int)
        test["rank_percentile"] = 1.0 - (test["rank_no"] - 1) / max(len(test), 1)
        test["selected_top_n"] = test["rank_no"].le(config.top_n)
        coefficients = np.asarray(estimator.named_steps["model"].coef_).reshape(-1)
        materialize_explanations = config.explanation_scope == "all" or date_index == latest_index
        if materialize_explanations:
            test["risk_flags"] = test.apply(_cross_sectional_risk_flags, axis=1)
            transformed = estimator[:-1].transform(test[feature_columns])
            ordered = sorted(zip(feature_columns, coefficients), key=lambda item: abs(item[1]), reverse=True)
            transformed_by_name = {name: transformed[:, index] for index, name in enumerate(feature_columns)}
            snapshots = []
            for row_index in range(len(test)):
                snapshots.append(
                    [
                        {
                            "name": name,
                            "value": round(float(transformed_by_name[name][row_index]), 6),
                            "weight": round(float(weight), 6),
                            "contribution": round(float(transformed_by_name[name][row_index] * weight), 6),
                        }
                        for name, weight in ordered
                    ]
                )
            test["factor_snapshot"] = snapshots
        else:
            test["risk_flags"] = None
            test["factor_snapshot"] = None
        test["selected_factors"] = [feature_columns] * len(test)
        test["train_start"] = train_start
        test["train_end"] = train_end
        test["train_rows"] = len(train)
        test["train_dates"] = train["trade_date"].nunique()
        test["train_positive_rate"] = float(train["label"].mean())
        test["model_type"] = config.model_type
        test["probability_kind"] = probability_kind
        test["is_scheduled_rebalance"] = date_index in scheduled_set
        test["is_latest_snapshot"] = date_index == latest_index
        test["prediction_calendar_index"] = date_index
        # Keep the accumulated full-backtest result compact.  The model panel
        # and factor matrix remain available to this iteration, while consumers
        # of predictions need only execution prices, outcomes, ranks and audit
        # metadata.
        output_columns = [
            "code",
            "trade_date",
            "amount",
            "entry_open",
            "exit_open",
            "forward_return",
            "score",
            "probability",
            "rank_no",
            "rank_percentile",
            "selected_top_n",
            "risk_flags",
            "factor_snapshot",
            "selected_factors",
            "train_start",
            "train_end",
            "train_rows",
            "train_dates",
            "train_positive_rate",
            "model_type",
            "probability_kind",
            "is_scheduled_rebalance",
            "is_latest_snapshot",
            "prediction_calendar_index",
        ]
        for optional_column in ("entry_open", "exit_open"):
            if optional_column not in test.columns:
                test[optional_column] = np.nan
        outputs.append(test[output_columns].copy())
        report_window("fitted")
    return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()
