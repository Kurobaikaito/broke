from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from backend.app.db import get_engine, get_session_factory
from backend.app.research.backtest import evaluate_predictions
from backend.app.research.factors import (
    FACTOR_COLUMNS,
    FACTOR_LOOKBACKS,
    calculate_raw_factors,
    factor_frames_to_long,
    standardize_cross_section,
)
from backend.app.research.modeling import (
    WalkForwardConfig,
    build_model_panel,
    prediction_window_count,
    walk_forward_predict,
)
from backend.app.research.storage import (
    ensure_research_model_run_table,
    get_serving_model_run,
    load_bars,
    publish_model_run,
    resolve_recent_start_date,
    save_backtest_summary,
    save_factors,
    save_predictions,
)


FACTOR_SET_VERSION = "technical-point-in-time-v2"
REQUIRED_BACKTEST_METRICS = {
    "top_group_return",
    "benchmark_return",
    "win_rate",
    "max_drawdown",
    "sharpe",
    "rank_ic",
    "turnover",
    "periods",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate factors, train rolling models, and backtest capital-aware portfolios."
    )
    parser.add_argument("--start-date", default="20180101", help="First full-backtest date (YYYYMMDD).")
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
    parser.add_argument(
        "--model-type",
        choices=("logistic", "ridge"),
        default="logistic",
        help="Logistic positive-return classifier or Ridge forward-return challenger.",
    )
    parser.add_argument(
        "--max-features",
        type=int,
        default=None,
        help="Optional training-only Rank-IC feature-selection cap.",
    )
    parser.add_argument(
        "--half-life-days",
        type=float,
        default=None,
        help="Optional exponential recency half-life in market sessions.",
    )
    parser.add_argument(
        "--industry-neutral",
        action="store_true",
        help="Neutralize factors by the available industry classification before z-scoring.",
    )
    parser.add_argument(
        "--model-version",
        default=None,
        help="Human-readable strategy version; each publication receives a separate immutable id.",
    )
    parser.add_argument(
        "--run-mode",
        "--mode",
        dest="run_mode",
        choices=("auto", "full", "latest"),
        default="auto",
        help="Auto reuses a compatible full backtest and otherwise performs one.",
    )
    parser.add_argument(
        "--factor-storage",
        choices=("latest", "none"),
        default="latest",
        help="Persist only the latest rebuildable cross section, or persist no factor rows.",
    )
    parser.add_argument(
        "--no-save-factors",
        action="store_true",
        help="Deprecated alias for --factor-storage none.",
    )
    return parser.parse_args()


def resolve_model_version(args: argparse.Namespace) -> str:
    """Return the readable strategy version, not the immutable publication id."""
    if args.model_version:
        return args.model_version
    factor_tag = f"pv{len(FACTOR_COLUMNS)}"
    if args.model_type == "ridge":
        version = f"ridge-return-{factor_tag}-v1"
    else:
        version = f"logistic-{factor_tag}-dateweighted-v2"
    if args.max_features is not None:
        version += f"-icfs{args.max_features}"
    if args.half_life_days is not None:
        compact_half_life = f"{args.half_life_days:g}".replace(".", "p")
        version += f"-hl{compact_half_life}"
    if args.industry_neutral:
        version += "-industry"
    if len(version) > 64:
        raise SystemExit("generated model version exceeds the 64-character database limit")
    return version


def research_parameters(
    args: argparse.Namespace,
    horizon: int,
    strategy_version: str,
) -> dict[str, Any]:
    """Canonical parameters whose metrics and latest fit may be safely reused."""
    return {
        "strategy_version": strategy_version,
        "factor_set_version": FACTOR_SET_VERSION,
        "factor_names": FACTOR_COLUMNS,
        "industry_neutral": bool(args.industry_neutral),
        "market": ["SSE", "SZSE"],
        "model_type": args.model_type,
        "logistic_c": WalkForwardConfig.logistic_c,
        "ridge_alpha": WalkForwardConfig.ridge_alpha,
        "max_features": args.max_features,
        "min_ic_dates": WalkForwardConfig.min_ic_dates,
        "date_weighting": True,
        "half_life_days": args.half_life_days,
        "random_state": WalkForwardConfig.random_state,
        "horizon": horizon,
        "rebalance_step": horizon,
        "train_window_days": args.train_window_days,
        "min_train_days": args.min_train_days,
        "min_train_rows": args.min_train_rows,
        "min_amount": args.min_amount,
        "top_n": args.top_n,
        "initial_capital": args.initial_capital,
        "commission_bps": args.commission_bps,
        "stamp_duty_bps": args.stamp_duty_bps,
        "slippage_bps": args.slippage_bps,
        "requested_start_date": args.start_date,
        "requested_end_date": args.end_date,
        "label_contract": "signal_close-entry_next_open-exit_open_after_h_sessions",
        "purge_days": horizon + 1,
    }


def research_fingerprint(
    args: argparse.Namespace,
    horizon: int,
    strategy_version: str,
) -> str:
    canonical = json.dumps(
        research_parameters(args, horizon, strategy_version),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_publication_version(strategy_version: str) -> str:
    suffix = f"-p{datetime.now(timezone.utc):%y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
    return f"{strategy_version[: 64 - len(suffix)]}{suffix}"


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _load_serving_baselines(session_factory, horizons: list[int]) -> dict[int, dict[str, Any]]:
    baselines: dict[int, dict[str, Any]] = {}
    with session_factory() as session:
        ensure_research_model_run_table(session)
        for horizon in horizons:
            run = get_serving_model_run(session, horizon)
            if run is None:
                continue
            baselines[horizon] = {
                "run_id": run.run_id,
                "model_version": run.model_version,
                "prediction_date": run.prediction_date,
                "start_date": run.start_date,
                "end_date": run.end_date,
                "config": _json_object(run.config_json),
                "metrics": _json_object(run.metrics_json),
            }
    return baselines


def baseline_is_compatible(baseline: dict[str, Any] | None, fingerprint: str) -> bool:
    if not baseline or baseline.get("config", {}).get("config_fingerprint") != fingerprint:
        return False
    metrics = baseline.get("metrics")
    if not isinstance(metrics, dict) or not REQUIRED_BACKTEST_METRICS.issubset(metrics):
        return False
    try:
        return all(math.isfinite(float(metrics[name])) for name in REQUIRED_BACKTEST_METRICS)
    except (TypeError, ValueError):
        return False


def resolve_run_mode(
    requested_mode: str,
    horizons: list[int],
    baselines: dict[int, dict[str, Any]],
    fingerprints: dict[int, str],
) -> str:
    if requested_mode == "full":
        return "full"
    compatible = all(
        baseline_is_compatible(baselines.get(horizon), fingerprints[horizon])
        for horizon in horizons
    )
    if requested_mode == "latest":
        if not compatible:
            raise ValueError("latest mode requires a compatible completed full-backtest baseline")
        return "latest"
    return "latest" if compatible else "full"


def _run_notes(
    args: argparse.Namespace,
    horizon: int,
    strategy_version: str,
    publication_version: str,
    fingerprint: str,
    run_mode: str,
) -> dict[str, Any]:
    return {
        "method": (
            "rolling regularized logistic classification"
            if args.model_type == "logistic"
            else "rolling ridge forward-return regression"
        ),
        "model_type": args.model_type,
        "model_version": strategy_version,
        "strategy_version": strategy_version,
        "publication_version": publication_version,
        "config_fingerprint": fingerprint,
        "research_parameters": research_parameters(args, horizon, strategy_version),
        "run_mode": run_mode,
        "factor_count": len(FACTOR_COLUMNS),
        "factor_names": FACTOR_COLUMNS,
        "factor_set_version": FACTOR_SET_VERSION,
        "max_features": args.max_features,
        "feature_selection": (
            "purged-training-only Rank IC stability" if args.max_features is not None else "disabled"
        ),
        "date_weighting": "equal total weight per market date",
        "half_life_days": args.half_life_days,
        "industry_neutral": args.industry_neutral,
        "probability_kind": (
            "positive-return logistic probability"
            if args.model_type == "logistic"
            else "residual-scale approximation; use score for ranking"
        ),
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
        "max_drawdown_frequency": "rebalance period endpoints",
        "latest_snapshot_excluded_from_overlapping_backtest": True,
        "stored_prediction_scope": "latest cross section only",
        "factor_storage_scope": "latest cross section only" if args.factor_storage == "latest" else "none",
    }


def main() -> None:
    args = parse_args()
    if args.no_save_factors:
        args.factor_storage = "none"
    for option_name in ("start_date", "end_date"):
        value = getattr(args, option_name)
        if value is None:
            continue
        parsed = pd.to_datetime(value, format="%Y%m%d", errors="coerce")
        if len(value) != 8 or not value.isdigit() or pd.isna(parsed):
            raise SystemExit(f"--{option_name.replace('_', '-')} must use YYYYMMDD")
    if args.max_features is not None and not 1 <= args.max_features <= len(FACTOR_COLUMNS):
        raise SystemExit(f"--max-features must be between 1 and {len(FACTOR_COLUMNS)}")
    if args.half_life_days is not None and args.half_life_days <= 0:
        raise SystemExit("--half-life-days must be positive")
    strategy_version = resolve_model_version(args)
    horizons = sorted({int(value.strip()) for value in args.horizons.split(",") if value.strip()})
    if not horizons or any(value <= 0 for value in horizons):
        raise SystemExit("--horizons must contain positive integers")

    engine = get_engine()
    session_factory = get_session_factory()
    fingerprints = {
        horizon: research_fingerprint(args, horizon, strategy_version) for horizon in horizons
    }
    baselines = _load_serving_baselines(session_factory, horizons)
    try:
        actual_run_mode = resolve_run_mode(args.run_mode, horizons, baselines, fingerprints)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"run_mode={actual_run_mode} requested={args.run_mode}", flush=True)

    effective_start_date = args.start_date
    if actual_run_mode == "latest":
        required_sessions = (
            args.train_window_days + max(FACTOR_LOOKBACKS.values()) + max(horizons) + 2
        )
        recent_start = resolve_recent_start_date(engine, required_sessions, args.end_date)
        if recent_start is None:
            raise SystemExit("daily_bar_adj has no supported-market dates; pull Tushare data first")
        effective_start_date = max(args.start_date, recent_start)

    bars = load_bars(engine, effective_start_date, args.end_date)
    if bars.empty:
        raise SystemExit("daily_bar_adj is empty for the requested date range; pull Tushare data first")
    current_prediction_date = pd.Timestamp(bars["trade_date"].max()).date()
    print(
        f"bars_loaded={len(bars)} start={pd.Timestamp(bars['trade_date'].min()).date()} "
        f"end={current_prediction_date}",
        flush=True,
    )

    up_to_date: set[int] = set()
    if actual_run_mode == "latest":
        for horizon in horizons:
            baseline_date = baselines[horizon]["prediction_date"]
            if current_prediction_date < baseline_date:
                raise SystemExit(
                    f"latest data date {current_prediction_date} precedes serving {horizon}d date {baseline_date}"
                )
            if current_prediction_date == baseline_date:
                up_to_date.add(horizon)
        if len(up_to_date) == len(horizons):
            for horizon in horizons:
                print(
                    f"horizon={horizon}d status=up_to_date run_id={baselines[horizon]['run_id']}",
                    flush=True,
                )
            return

    raw_factor_steps = 5
    standardization_steps = len(FACTOR_COLUMNS)
    total_factor_steps = raw_factor_steps + standardization_steps

    def emit_factor_progress(completed: int, current_step: str) -> None:
        print(
            "factor_progress="
            + json.dumps(
                {
                    "completed": completed,
                    "total": total_factor_steps,
                    "current_step": current_step,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            flush=True,
        )

    raw_factors = calculate_raw_factors(
        bars,
        progress_callback=lambda completed, _total, label: emit_factor_progress(
            completed, label
        ),
    )
    neutralize_by = "industry" if args.industry_neutral else None
    factor_wide, _ = standardize_cross_section(
        raw_factors,
        neutralize_by=neutralize_by,
        materialize_long=False,
        progress_callback=lambda completed, _total, label: emit_factor_progress(
            raw_factor_steps + completed,
            "准备截面标准化" if completed == 0 else f"标准化因子：{label}",
        ),
    )
    print(f"factors_calculated={len(factor_wide)}", flush=True)

    if args.factor_storage == "latest":
        latest_mask = pd.to_datetime(raw_factors["trade_date"]).dt.date.eq(current_prediction_date)
        raw_latest = raw_factors.loc[latest_mask]
        standardized_latest = factor_wide.loc[latest_mask]
        factor_long = factor_frames_to_long(raw_latest, standardized_latest)
        with session_factory() as session:
            factor_count = save_factors(session, factor_long)
        del raw_latest, standardized_latest, factor_long
    else:
        factor_count = 0
    print(
        f"factors_completed={factor_count} storage={args.factor_storage}",
        flush=True,
    )
    del raw_factors
    gc.collect()

    publication_version = create_publication_version(strategy_version)
    configs = {
        horizon: WalkForwardConfig(
            horizon=horizon,
            train_window_days=args.train_window_days,
            min_train_days=args.min_train_days,
            min_train_rows=args.min_train_rows,
            top_n=args.top_n,
            min_amount=args.min_amount,
            model_type=args.model_type,
            max_features=args.max_features,
            half_life_days=args.half_life_days,
            prediction_scope=actual_run_mode,
            explanation_scope="latest",
        )
        for horizon in horizons
    }
    total_trade_dates = int(pd.to_datetime(bars["trade_date"]).nunique())
    model_plan = {
        str(horizon): (
            0
            if horizon in up_to_date
            else prediction_window_count(total_trade_dates, configs[horizon])
        )
        for horizon in horizons
    }
    print(
        "model_plan="
        + json.dumps(model_plan, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )

    def emit_model_stage(horizon: int, current_step: str) -> None:
        print(
            "model_stage="
            + json.dumps(
                {"horizon": horizon, "current_step": current_step},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            flush=True,
        )

    for horizon in horizons:
        if horizon in up_to_date:
            print(
                f"horizon={horizon}d status=up_to_date run_id={baselines[horizon]['run_id']}",
                flush=True,
            )
            continue
        config = configs[horizon]
        emit_model_stage(horizon, f"构建 {horizon} 日训练面板")
        panel = build_model_panel(bars, factor_wide, horizon)
        emit_model_stage(horizon, f"训练 {horizon} 日滚动窗口")

        def emit_model_progress(payload: dict[str, Any]) -> None:
            print(
                "model_progress="
                + json.dumps(
                    {"horizon": horizon, **payload},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                flush=True,
            )

        predictions = walk_forward_predict(
            panel,
            config,
            progress_callback=emit_model_progress,
        )
        del panel
        gc.collect()
        if predictions.empty:
            print(f"horizon={horizon}d skipped=no_valid_training_window", flush=True)
            continue

        latest_predictions = predictions[
            pd.to_datetime(predictions["trade_date"]).dt.date.eq(current_prediction_date)
        ].copy()
        if latest_predictions.empty:
            print(f"horizon={horizon}d skipped=no_latest_prediction_snapshot", flush=True)
            del predictions
            continue

        notes = _run_notes(
            args,
            horizon,
            strategy_version,
            publication_version,
            fingerprints[horizon],
            actual_run_mode,
        )
        if actual_run_mode == "full":
            emit_model_stage(horizon, f"回测并发布 {horizon} 日结果")
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
                print(f"horizon={horizon}d backtest_skipped={exc}", flush=True)
                del predictions, latest_predictions
                gc.collect()
                continue
            start_date = pd.Timestamp(periods["trade_date"].min()).date()
            end_date = pd.Timestamp(periods["trade_date"].max()).date()
            notes["ending_capital"] = metrics.get("ending_capital")
            notes["periods"] = int(metrics["periods"])
            notes["backtest_reused"] = False
        else:
            emit_model_stage(horizon, f"复用回测并发布 {horizon} 日结果")
            baseline = baselines[horizon]
            metrics = dict(baseline["metrics"])
            start_date = baseline["start_date"]
            end_date = baseline["end_date"]
            source_run_id = baseline["config"].get("backtest_source_run_id") or baseline["run_id"]
            notes.update(
                {
                    "ending_capital": metrics.get("ending_capital"),
                    "periods": int(metrics["periods"]),
                    "backtest_reused": True,
                    "backtest_reused_from_run_id": baseline["run_id"],
                    "backtest_source_run_id": source_run_id,
                    "backtest_as_of": end_date.isoformat(),
                }
            )

        with session_factory() as session:
            prediction_count = save_predictions(
                session,
                latest_predictions,
                horizon,
                publication_version,
            )
        with session_factory() as session:
            ensure_research_model_run_table(session)
            if actual_run_mode == "full":
                save_backtest_summary(
                    session,
                    horizon,
                    publication_version,
                    start_date,
                    end_date,
                    metrics,
                    notes,
                    commit=False,
                )
            run_id = publish_model_run(
                session,
                horizon=horizon,
                model_version=publication_version,
                predictions=latest_predictions,
                start_date=start_date,
                end_date=end_date,
                metrics=metrics,
                config=notes,
            )
        print(
            f"horizon={horizon}d mode={actual_run_mode} model={strategy_version} "
            f"publication={publication_version} run_id={run_id} predictions={prediction_count} "
            f"backtest_reused={str(actual_run_mode == 'latest').lower()} "
            f"backtest={json.dumps(metrics, ensure_ascii=False, allow_nan=False)}",
            flush=True,
        )
        del predictions, latest_predictions
        gc.collect()


if __name__ == "__main__":
    main()
