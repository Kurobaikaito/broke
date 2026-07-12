from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .factors import FACTOR_COLUMNS


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

    def __post_init__(self) -> None:
        if self.horizon <= 0 or self.train_window_days <= 0 or self.min_train_days <= 0:
            raise ValueError("horizon and training windows must be positive")
        if self.top_n <= 0:
            raise ValueError("top_n must be positive")


def build_model_panel(bars: pd.DataFrame, factors: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Build features and next-open-to-future-open labels without using future features."""
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    needed = ["code", "trade_date", "open", "close", "amount"]
    missing = set(needed).difference(bars.columns)
    if missing:
        raise ValueError(f"Missing bar columns: {sorted(missing)}")
    prices = bars[needed].copy()
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices[["open", "close", "amount"]] = prices[["open", "close", "amount"]].apply(pd.to_numeric, errors="coerce")
    prices = prices.sort_values(["code", "trade_date"]).reset_index(drop=True)
    calendar = {value: index for index, value in enumerate(sorted(prices["trade_date"].unique()))}
    prices["calendar_index"] = prices["trade_date"].map(calendar)
    opens = prices[["code", "calendar_index", "open"]]
    entry = opens.rename(columns={"open": "entry_open"}).copy()
    entry["calendar_index"] -= 1
    exit_prices = opens.rename(columns={"open": "exit_open"}).copy()
    exit_prices["calendar_index"] -= horizon + 1
    prices = prices.merge(entry, on=["code", "calendar_index"], how="left", validate="one_to_one")
    prices = prices.merge(exit_prices, on=["code", "calendar_index"], how="left", validate="one_to_one")
    prices["forward_return"] = prices["exit_open"] / prices["entry_open"] - 1.0
    prices["label"] = np.where(prices["forward_return"].notna(), (prices["forward_return"] > 0).astype(float), np.nan)
    factor_frame = factors.copy()
    factor_frame["trade_date"] = pd.to_datetime(factor_frame["trade_date"])
    panel = prices.merge(factor_frame, on=["code", "trade_date"], how="inner", validate="one_to_one")
    panel = panel.replace([np.inf, -np.inf], np.nan)
    panel["eligible"] = panel[FACTOR_COLUMNS].notna().all(axis=1) & panel["close"].gt(0) & panel["amount"].ge(0)
    return panel.sort_values(["trade_date", "code"]).reset_index(drop=True)


def _make_estimator(random_state: int):
    try:
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise RuntimeError("scikit-learn is required; run: pip install -r requirements.txt") from exc
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(C=0.1, max_iter=1000, random_state=random_state, solver="lbfgs")),
        ]
    )


def walk_forward_predict(panel: pd.DataFrame, config: WalkForwardConfig) -> pd.DataFrame:
    """Refit on each rebalance date with an H-day purge gap and predict out of sample."""
    dates = pd.Index(sorted(panel["trade_date"].dropna().unique()))
    step = config.rebalance_step or config.horizon
    first_index = config.min_train_days + config.horizon + 1
    if len(dates) <= first_index:
        return pd.DataFrame()
    prediction_indices = list(range(first_index, len(dates), step))
    if prediction_indices[-1] != len(dates) - 1:
        prediction_indices.append(len(dates) - 1)

    outputs: list[pd.DataFrame] = []
    for date_index in prediction_indices:
        prediction_date = dates[date_index]
        # A label dated t exits at t + horizon + 1 because entry is next open.
        purge_index = date_index - config.horizon - 1
        train_start_index = max(0, purge_index - config.train_window_days + 1)
        train_start = dates[train_start_index]
        train_end = dates[purge_index]
        train = panel[
            panel["trade_date"].between(train_start, train_end)
            & panel["eligible"]
            & panel["label"].notna()
            & panel["amount"].ge(config.min_amount)
        ]
        test = panel[
            (panel["trade_date"] == prediction_date)
            & panel["eligible"]
            & panel["amount"].ge(config.min_amount)
        ].copy()
        if len(train) < config.min_train_rows or test.empty or train["label"].nunique() < 2:
            continue

        estimator = _make_estimator(config.random_state)
        estimator.fit(train[FACTOR_COLUMNS], train["label"].astype(int))
        test["probability"] = estimator.predict_proba(test[FACTOR_COLUMNS])[:, 1]
        test["score"] = estimator.decision_function(test[FACTOR_COLUMNS])
        test["rank_no"] = test["score"].rank(method="first", ascending=False).astype(int)
        coefficients = estimator.named_steps["model"].coef_[0]
        transformed = estimator[:-1].transform(test[FACTOR_COLUMNS])
        ordered = sorted(zip(FACTOR_COLUMNS, coefficients), key=lambda item: abs(item[1]), reverse=True)
        transformed_by_name = {name: transformed[:, index] for index, name in enumerate(FACTOR_COLUMNS)}
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
        test["train_start"] = train_start
        test["train_end"] = train_end
        outputs.append(test)
    return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()
