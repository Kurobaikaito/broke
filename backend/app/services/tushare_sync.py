from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from threading import Event
from typing import Any, Callable

from ..db import get_session_factory
from ..repositories import MysqlRepository
from .data_sources import (
    TushareClient,
    normalize_ts_code,
    tushare_daily_bundle_records,
    validate_tushare_frames,
)


ProgressCallback = Callable[[dict[str, Any]], None]
FULL_HISTORY_START = "20180101"
FULL_SYNC_DATASET = "a_share_daily_full_v2"


class SyncCancelled(Exception):
    pass


@dataclass(frozen=True)
class SyncOptions:
    start_date: str | None = None
    end_date: str = date.today().strftime("%Y%m%d")
    codes: str | None = None
    sleep_seconds: float = 0.0
    retry: int = 3
    max_dates: int | None = None
    continue_on_error: bool = False
    use_checkpoint: bool = True

    def validate(self) -> None:
        if self.start_date:
            datetime.strptime(self.start_date, "%Y%m%d")
        datetime.strptime(self.end_date, "%Y%m%d")
        if self.start_date and self.start_date > self.end_date:
            raise ValueError("start_date cannot be after end_date")
        if self.retry <= 0 or self.sleep_seconds < 0:
            raise ValueError("retry must be positive and sleep_seconds cannot be negative")
        if self.max_dates is not None and self.max_dates <= 0:
            raise ValueError("max_dates must be positive")
        if self.codes and not self.start_date:
            raise ValueError("codes requires an explicit start_date")


def yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")


def resolve_start_date(explicit: str | None, latest: date | None) -> str:
    if explicit:
        return explicit
    if latest:
        return yyyymmdd(latest + timedelta(days=1))
    return FULL_HISTORY_START


def sync_scope(codes: set[str] | None) -> str:
    if not codes:
        return "full_market"
    digest = hashlib.sha256(",".join(sorted(codes)).encode("ascii")).hexdigest()[:16]
    return f"codes_{digest}"


def filter_frame(frame, codes: set[str] | None):
    if frame is None or frame.empty or not codes:
        return frame
    normalized = frame["ts_code"].map(normalize_ts_code)
    return frame[normalized.isin(codes)].copy()


def _wait(seconds: float, stop_event: Event | None) -> None:
    if seconds <= 0:
        return
    if stop_event:
        if stop_event.wait(seconds):
            raise SyncCancelled()
    else:
        time.sleep(seconds)


def fetch_with_retry(client: TushareClient, trade_date: str, retries: int, stop_event: Event | None):
    for attempt in range(1, retries + 1):
        try:
            return client.daily_frames(trade_date)
        except Exception:
            if attempt == retries:
                raise
            _wait(2**attempt, stop_event)
    raise RuntimeError("unreachable")


def run_tushare_sync(
    options: SyncOptions,
    token: str,
    on_progress: ProgressCallback | None = None,
    stop_event: Event | None = None,
) -> dict[str, Any]:
    options.validate()
    if not token:
        raise ValueError("TUSHARE_TOKEN is empty")

    def emit(**payload: Any) -> None:
        if on_progress:
            on_progress(payload)

    selected_codes = None
    if options.codes:
        selected_codes = {
            normalize_ts_code(code)
            for code in options.codes.split(",")
            if normalize_ts_code(code)
        }
    scope = sync_scope(selected_codes)
    totals = {"daily": 0, "adjusted": 0, "factors": 0, "basics": 0}
    failures: list[str] = []
    client = TushareClient(token)
    session_factory = get_session_factory()
    emit(stage="preparing", message="正在读取股票列表和交易日历")

    with session_factory() as session:
        repository = MysqlRepository(session)
        state = repository.get_sync_state("tushare", FULL_SYNC_DATASET, scope)
        checkpoint = state.last_trade_date if state else None
        start_date = resolve_start_date(
            options.start_date,
            checkpoint if options.use_checkpoint else None,
        )
        if start_date > options.end_date:
            if checkpoint:
                repository.set_sync_state(
                    "tushare",
                    FULL_SYNC_DATASET,
                    scope,
                    checkpoint,
                    state.last_row_count if state else None,
                    "ready",
                )
            emit(
                stage="completed",
                message="数据已是最新，无需继续拉取",
                completed_dates=0,
                total_dates=0,
                checkpoint=checkpoint.isoformat() if checkpoint else None,
                totals=dict(totals),
            )
            return {
                "status": "completed",
                "totals": totals,
                "failures": failures,
                "checkpoint": checkpoint.isoformat() if checkpoint else None,
            }

        stocks = client.stock_list_records()
        stock_count = repository.upsert_stocks(stocks)
        calendar = client.trade_calendar_records(start_date, options.end_date)
        repository.upsert_trade_calendar(calendar)
        open_dates = sorted(yyyymmdd(row["trade_date"]) for row in calendar if row["is_open"] == 1)
        if options.max_dates:
            open_dates = open_dates[: options.max_dates]
        emit(
            stage="syncing",
            message=f"交易日历就绪，共 {len(open_dates)} 个交易日",
            total_dates=len(open_dates),
            completed_dates=0,
            stock_count=stock_count,
            start_date=start_date,
            end_date=options.end_date,
            totals=dict(totals),
        )

        checkpoint_is_contiguous = True
        # Never move a durable checkpoint backwards when an explicit CLI backfill overlaps old data.
        last_contiguous_date = checkpoint
        for index, trade_date in enumerate(open_dates, start=1):
            if stop_event and stop_event.is_set():
                repository.set_sync_state(
                    "tushare", FULL_SYNC_DATASET, scope, last_contiguous_date, None, "stopped"
                )
                emit(stage="stopped", message="任务已停止", current_date=trade_date)
                return {"status": "stopped", "totals": totals, "failures": failures}
            emit(stage="syncing", current_date=trade_date, message=f"正在拉取 {trade_date}")
            try:
                fetch_started = time.perf_counter()
                daily, factors, basics = fetch_with_retry(client, trade_date, options.retry, stop_event)
                fetch_seconds = time.perf_counter() - fetch_started
                prepare_started = time.perf_counter()
                quality = validate_tushare_frames(daily, factors, basics, trade_date)
                daily = filter_frame(daily, selected_codes)
                factors = filter_frame(factors, selected_codes)
                basics = filter_frame(basics, selected_codes)
                bundle = tushare_daily_bundle_records(daily, factors, basics)
                prepare_seconds = time.perf_counter() - prepare_started
                database_started = time.perf_counter()
                committed_counts: dict[str, int] = {}
                write_seconds: dict[str, float] = {}
                writers = (
                    ("daily", repository.upsert_daily_bars, bundle["daily"]),
                    ("factors", repository.upsert_adj_factors, bundle["factors"]),
                    ("basics", repository.upsert_daily_basics, bundle["basics"]),
                    ("adjusted", repository.upsert_adjusted_bars, bundle["adjusted"]),
                )
                for name, writer, records in writers:
                    write_started = time.perf_counter()
                    committed_counts[name] = writer(records, commit=False)
                    write_seconds[name] = time.perf_counter() - write_started
                completed_date = datetime.strptime(trade_date, "%Y%m%d").date()
                next_checkpoint = None
                if checkpoint_is_contiguous and (
                    last_contiguous_date is None or completed_date > last_contiguous_date
                ):
                    next_checkpoint = completed_date
                    repository.set_sync_state(
                        "tushare",
                        FULL_SYNC_DATASET,
                        scope,
                        next_checkpoint,
                        len(bundle["daily"]),
                        "ready",
                        commit=False,
                    )
                commit_started = time.perf_counter()
                session.commit()
                commit_seconds = time.perf_counter() - commit_started
                database_seconds = time.perf_counter() - database_started
                if next_checkpoint is not None:
                    last_contiguous_date = next_checkpoint
                for name, count in committed_counts.items():
                    totals[name] += count
                emit(
                    stage="syncing",
                    message=(
                        f"{trade_date} 完成，{len(bundle['daily'])} 行 · "
                        f"拉取 {fetch_seconds:.1f}s / 处理 {prepare_seconds:.1f}s / "
                        f"入库 {database_seconds:.1f}s"
                        f"（日线 {write_seconds['daily']:.1f} / 因子 {write_seconds['factors']:.1f} / "
                        f"指标 {write_seconds['basics']:.1f} / 复权 {write_seconds['adjusted']:.1f} / "
                        f"提交 {commit_seconds:.1f}）"
                    ),
                    current_date=trade_date,
                    completed_dates=index,
                    total_dates=len(open_dates),
                    totals=dict(totals),
                    basic_coverage=quality["basic_coverage"],
                    timings={
                        "fetch_seconds": round(fetch_seconds, 2),
                        "prepare_seconds": round(prepare_seconds, 2),
                        "database_seconds": round(database_seconds, 2),
                        "write_seconds": {
                            name: round(seconds, 2) for name, seconds in write_seconds.items()
                        },
                        "commit_seconds": round(commit_seconds, 2),
                    },
                    checkpoint=last_contiguous_date.isoformat() if last_contiguous_date else None,
                )
            except SyncCancelled:
                repository.set_sync_state(
                    "tushare", FULL_SYNC_DATASET, scope, last_contiguous_date, None, "stopped"
                )
                emit(stage="stopped", message="任务已停止", current_date=trade_date)
                return {"status": "stopped", "totals": totals, "failures": failures}
            except Exception as exc:
                session.rollback()
                failures.append(trade_date)
                checkpoint_is_contiguous = False
                message = f"{type(exc).__name__}: {exc}"[:1000]
                repository.set_sync_state(
                    "tushare",
                    FULL_SYNC_DATASET,
                    scope,
                    last_contiguous_date,
                    None,
                    "failed",
                    message,
                )
                emit(
                    stage="error",
                    message=f"{trade_date} 失败：{message}",
                    current_date=trade_date,
                    failures=list(failures),
                    totals=dict(totals),
                )
                if not options.continue_on_error:
                    raise RuntimeError(f"sync stopped at {trade_date}: {message}") from exc
            try:
                _wait(options.sleep_seconds, stop_event)
            except SyncCancelled:
                repository.set_sync_state(
                    "tushare", FULL_SYNC_DATASET, scope, last_contiguous_date, None, "stopped"
                )
                emit(stage="stopped", message="任务已停止", current_date=trade_date)
                return {"status": "stopped", "totals": totals, "failures": failures}

        if not open_dates and checkpoint:
            repository.set_sync_state(
                "tushare",
                FULL_SYNC_DATASET,
                scope,
                checkpoint,
                state.last_row_count if state else None,
                "ready",
            )

    emit(stage="completed", message="数据同步完成", completed_dates=len(open_dates), total_dates=len(open_dates))
    return {
        "status": "completed",
        "totals": totals,
        "failures": failures,
        "checkpoint": last_contiguous_date.isoformat() if last_contiguous_date else None,
    }
