from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..config import PROJECT_ROOT


@dataclass(frozen=True)
class ResearchRunOptions:
    start_date: str = "20180101"
    horizons: tuple[int, ...] = (5, 20, 60)
    initial_capital: float = 50_000.0
    run_mode: str = "auto"

    def validate(self) -> None:
        if len(self.start_date) != 8 or not self.start_date.isdigit():
            raise ValueError("start_date must use YYYYMMDD")
        if not self.horizons or any(value <= 0 for value in self.horizons):
            raise ValueError("horizons must contain positive integers")
        if not 10_000 <= self.initial_capital <= 100_000:
            raise ValueError("initial_capital must be between 10000 and 100000")
        if self.run_mode not in {"auto", "full", "latest"}:
            raise ValueError("run_mode must be auto, full, or latest")


def run_research_subprocess(
    options: ResearchRunOptions,
    on_progress: Callable[[dict[str, Any]], None],
    stop_event: threading.Event,
    on_process: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> dict[str, Any]:
    """Run the shared research CLI while streaming bounded progress to the web job."""
    command = [
        sys.executable,
        "-u",
        str(Path(PROJECT_ROOT) / "scripts" / "run_research.py"),
        "--start-date",
        options.start_date,
        "--horizons",
        ",".join(str(value) for value in options.horizons),
        "--initial-capital",
        str(options.initial_capital),
        "--run-mode",
        options.run_mode,
        "--factor-storage",
        "latest",
    ]
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYTHONUNBUFFERED", "1")
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creation_flags,
    )
    if on_process is not None:
        on_process(process)
    output_queue: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output_queue.put(line.rstrip())
        output_queue.put(None)

    threading.Thread(target=read_output, name="research-output", daemon=True).start()
    completed: set[int] = set()
    skipped: set[int] = set()
    actual_run_mode: str | None = None
    model_totals: dict[int, int] = {}
    model_completed: dict[int, int] = {}
    model_fitted: dict[int, int] = {}
    last_progress_pct = 0.0

    def model_progress_pct() -> float:
        total_windows = sum(model_totals.values())
        completed_windows = sum(
            min(model_completed.get(horizon, 0), total)
            for horizon, total in model_totals.items()
        )
        window_share = completed_windows / total_windows if total_windows else 0.0
        horizon_share = len(completed | skipped) / len(options.horizons)
        # Keep one final percentage point for successful process completion.
        return min(99.0, 25.0 + 65.0 * window_share + 10.0 * horizon_share)

    output_finished = False
    try:
        while process.poll() is None or not output_finished:
            if stop_event.is_set() and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                on_progress({"stage": "stopped", "message": "研究计算已停止"})
                return {"status": "stopped", "completed_horizons": len(completed)}
            try:
                line = output_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                output_finished = True
                continue
            if not line:
                continue
            payload: dict[str, Any] = {"message": line, "stage": "running"}
            if line.startswith("run_mode="):
                candidate = line.split(maxsplit=1)[0].removeprefix("run_mode=")
                if candidate in {"full", "latest"}:
                    actual_run_mode = candidate
                    payload.update(
                        {
                            "stage": "preparing",
                            "progress_pct": 2.0,
                            "actual_run_mode": candidate,
                            "current_step": "确定完整或增量运行方式",
                        }
                    )
            elif line.startswith("bars_loaded="):
                payload.update(
                    {
                        "stage": "loading_bars",
                        "progress_pct": 8.0,
                        "current_step": "读取并校验行情数据",
                    }
                )
            elif line.startswith("factor_progress="):
                try:
                    event = json.loads(line.removeprefix("factor_progress="))
                    step_completed = max(0, int(event["completed"]))
                    step_total = max(1, int(event["total"]))
                    step_completed = min(step_completed, step_total)
                    current_step = str(event.get("current_step") or "计算研究因子")
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    pass
                else:
                    payload.update(
                        {
                            "stage": "calculating_factors",
                            "progress_pct": 8.0 + 14.0 * step_completed / step_total,
                            "current_step": current_step,
                            "step_completed": step_completed,
                            "step_total": step_total,
                            "message": f"{current_step}（{step_completed} / {step_total}）",
                            "log": False,
                        }
                    )
            elif line.startswith("factors_calculated="):
                payload.update(
                    {
                        "stage": "storing_factors",
                        "progress_pct": 22.0,
                        "current_step": "保存最新因子截面",
                        "step_completed": 0,
                        "step_total": 1,
                    }
                )
            elif line.startswith(("factors_completed=", "factors_upserted=")):
                payload.update(
                    {
                        "stage": "factors_completed",
                        "progress_pct": 25.0,
                        "current_step": "因子计算与存储完成",
                        "step_completed": 1,
                        "step_total": 1,
                    }
                )
            elif line.startswith("model_plan="):
                try:
                    event = json.loads(line.removeprefix("model_plan="))
                    if "horizon" in event and "total_windows" in event:
                        parsed_totals = {
                            int(event["horizon"]): max(0, int(event["total_windows"]))
                        }
                    else:
                        parsed_totals = {
                            int(horizon): max(0, int(total))
                            for horizon, total in event.items()
                        }
                except (json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError):
                    pass
                else:
                    model_totals.update(parsed_totals)
                    for horizon in parsed_totals:
                        model_completed.setdefault(horizon, 0)
                        model_fitted.setdefault(horizon, 0)
                    total_windows = sum(model_totals.values())
                    payload.update(
                        {
                            "stage": "modeling",
                            "progress_pct": 25.0,
                            "current_step": "准备滚动训练计划",
                            "step_completed": 0,
                            "step_total": 0,
                            "completed_windows": sum(model_completed.values()),
                            "fitted_windows": sum(model_fitted.values()),
                            "total_windows": total_windows,
                            "message": f"滚动训练计划：共 {total_windows} 个窗口",
                        }
                    )
            elif line.startswith("model_stage="):
                try:
                    event = json.loads(line.removeprefix("model_stage="))
                    horizon = int(event["horizon"])
                    current_step = str(event["current_step"])
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    pass
                else:
                    payload.update(
                        {
                            "stage": "modeling",
                            "progress_pct": model_progress_pct(),
                            "current_horizon": horizon,
                            "current_step": current_step,
                            "step_completed": model_completed.get(horizon, 0),
                            "step_total": model_totals.get(horizon, 0),
                            "completed_windows": sum(model_completed.values()),
                            "fitted_windows": sum(model_fitted.values()),
                            "total_windows": sum(model_totals.values()),
                            "message": current_step,
                            "log": False,
                        }
                    )
            elif line.startswith("model_progress="):
                try:
                    event = json.loads(line.removeprefix("model_progress="))
                    horizon = int(event["horizon"])
                    total = max(0, int(event["total_windows"]))
                    window_completed = max(0, int(event["completed_windows"]))
                    fitted = max(0, int(event["fitted_windows"]))
                    prediction_date = str(event.get("prediction_date") or "")
                    window_status = str(event.get("status") or "")
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    pass
                else:
                    if horizon not in model_totals:
                        model_totals[horizon] = total
                    else:
                        model_totals[horizon] = max(model_totals[horizon], total)
                    total = model_totals[horizon]
                    model_completed[horizon] = max(
                        model_completed.get(horizon, 0), min(window_completed, total)
                    )
                    model_fitted[horizon] = max(
                        model_fitted.get(horizon, 0), min(fitted, model_completed[horizon])
                    )
                    payload.update(
                        {
                            "stage": "modeling",
                            "progress_pct": model_progress_pct(),
                            "current_horizon": horizon,
                            "current_step": f"训练 {horizon} 日滚动窗口",
                            "step_completed": model_completed[horizon],
                            "step_total": total,
                            "completed_windows": sum(model_completed.values()),
                            "fitted_windows": sum(model_fitted.values()),
                            "total_windows": sum(model_totals.values()),
                            "prediction_date": prediction_date or None,
                            "window_status": window_status or None,
                            "message": (
                                f"{horizon} 日滚动训练："
                                f"{model_completed[horizon]} / {total} 个窗口"
                            ),
                            "log": False,
                        }
                    )
            if line.startswith("horizon="):
                prefix = line.split("d", 1)[0].removeprefix("horizon=")
                if prefix.isdigit():
                    horizon = int(prefix)
                    if "backtest_skipped=" in line or ("skipped=" in line and "predictions=" not in line):
                        skipped.add(horizon)
                        completed.discard(horizon)
                    elif "predictions=" in line or "status=up_to_date" in line:
                        completed.add(horizon)
                        skipped.discard(horizon)
                    if horizon in model_totals:
                        model_completed[horizon] = model_totals[horizon]
                    processed = len(completed | skipped)
                    if model_totals:
                        progress_pct = model_progress_pct()
                    else:
                        progress_pct = min(
                            99.0,
                            25.0 + 75.0 * processed / len(options.horizons),
                        )
                    payload.update(
                        {
                            "stage": "modeling",
                            "current_horizon": horizon,
                            "completed_horizons": len(completed),
                            "skipped_horizons": sorted(skipped),
                            "progress_pct": progress_pct,
                            "current_step": (
                                f"{horizon} 日结果已发布"
                                if horizon in completed
                                else f"{horizon} 日结果已跳过"
                            ),
                            "step_completed": model_completed.get(horizon, 0),
                            "step_total": model_totals.get(horizon, 0),
                            "completed_windows": sum(model_completed.values()),
                            "fitted_windows": sum(model_fitted.values()),
                            "total_windows": sum(model_totals.values()),
                        }
                    )
            if "progress_pct" in payload:
                try:
                    candidate_progress = min(99.0, max(0.0, float(payload["progress_pct"])))
                except (TypeError, ValueError):
                    payload.pop("progress_pct", None)
                else:
                    last_progress_pct = max(last_progress_pct, candidate_progress)
                    payload["progress_pct"] = last_progress_pct
            on_progress(payload)
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if on_process is not None:
            on_process(None)

    return_code = process.wait()
    if stop_event.is_set():
        on_progress({"stage": "stopped", "message": "研究计算已停止"})
        return {
            "status": "stopped",
            "completed_horizons": len(completed),
            "skipped_horizons": sorted(skipped),
        }
    if return_code != 0:
        raise RuntimeError(f"research process exited with code {return_code}")
    if not completed:
        horizons = ",".join(f"{value}d" for value in sorted(skipped)) or "全部"
        raise RuntimeError(f"没有生成有效预测；跳过周期：{horizons}。请检查历史长度、流动性门槛和数据完整性")
    return {
        "status": "completed",
        "completed_horizons": len(completed),
        "skipped_horizons": sorted(skipped),
        "actual_run_mode": actual_run_mode,
    }


class ResearchJobManager:
    def __init__(self, runner: Callable[..., dict[str, Any]] | None = None):
        self._runner = runner or self._run_subprocess
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._stop_event = threading.Event()
        self._logs: deque[dict[str, str]] = deque(maxlen=200)
        self._state: dict[str, Any] = self._idle_state()

    @staticmethod
    def _idle_state() -> dict[str, Any]:
        return {
            "job_id": None,
            "status": "idle",
            "stage": "idle",
            "message": "暂无研究计算任务",
            "progress_pct": 0.0,
            "completed_horizons": 0,
            "skipped_horizons": [],
            "total_horizons": 0,
            "current_horizon": None,
            "current_step": "--",
            "step_completed": 0,
            "step_total": 0,
            "completed_windows": 0,
            "fitted_windows": 0,
            "total_windows": 0,
            "run_mode": "auto",
            "actual_run_mode": None,
            "started_at": None,
            "finished_at": None,
        }

    def start(self, options: ResearchRunOptions) -> dict[str, Any]:
        options.validate()
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A research job is already running")
            self._stop_event = threading.Event()
            self._logs.clear()
            self._state = self._idle_state()
            self._state.update(
                {
                    "job_id": uuid.uuid4().hex,
                    "status": "running",
                    "stage": "queued",
                    "message": "研究任务已创建",
                    "total_horizons": len(options.horizons),
                    "run_mode": options.run_mode,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._append_log("研究任务已创建")
            self._thread = threading.Thread(
                target=self._run,
                args=(options,),
                name="stock-research",
                daemon=True,
            )
            self._thread.start()
            return self.snapshot()

    def _run(self, options: ResearchRunOptions) -> None:
        try:
            result = self._runner(
                options=options,
                on_progress=self._handle_progress,
                stop_event=self._stop_event,
            )
            with self._lock:
                status = result.get("status", "completed")
                self._state.update(
                    {
                        "status": status,
                        "stage": status,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "completed_horizons": result.get(
                            "completed_horizons", self._state["completed_horizons"]
                        ),
                        "skipped_horizons": result.get(
                            "skipped_horizons", self._state["skipped_horizons"]
                        ),
                        "actual_run_mode": result.get(
                            "actual_run_mode", self._state["actual_run_mode"]
                        ),
                    }
                )
                if status == "completed":
                    self._state["progress_pct"] = 100.0
                    self._state["current_step"] = "研究计算与发布完成"
                    skipped_count = len(self._state["skipped_horizons"])
                    self._state["message"] = (
                        f"研究计算完成：{self._state['completed_horizons']} 个周期成功，"
                        f"{skipped_count} 个跳过"
                    )
                    self._append_log(self._state["message"])
                elif status == "stopped":
                    self._state["message"] = "研究任务已停止"
        except Exception as exc:
            with self._lock:
                self._state.update(
                    {
                        "status": "failed",
                        "stage": "failed",
                        "message": str(exc),
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                self._append_log(f"研究任务失败：{exc}", level="error")

    def _run_subprocess(
        self,
        options: ResearchRunOptions,
        on_progress: Callable[[dict[str, Any]], None],
        stop_event: threading.Event,
    ) -> dict[str, Any]:
        return run_research_subprocess(
            options,
            on_progress,
            stop_event,
            on_process=self._set_process,
        )

    def _set_process(self, process: subprocess.Popen[str] | None) -> None:
        with self._lock:
            self._process = process

    def _handle_progress(self, payload: dict[str, Any]) -> None:
        with self._lock:
            should_log = payload.get("log", True) is not False
            message = payload.get("message")
            if message and should_log:
                self._append_log(str(message))
            state_update = {
                key: value for key, value in payload.items() if key not in {"message", "log"}
            }
            if "progress_pct" in state_update:
                try:
                    candidate = float(state_update["progress_pct"])
                except (TypeError, ValueError):
                    state_update.pop("progress_pct")
                else:
                    if self._state.get("status") == "running":
                        candidate = min(candidate, 99.0)
                    state_update["progress_pct"] = max(
                        float(self._state.get("progress_pct") or 0.0), candidate
                    )
            self._state.update(state_update)
            if message:
                self._state["message"] = message

    def _append_log(self, message: str, level: str = "info") -> None:
        self._logs.append(
            {
                "time": datetime.now().astimezone().strftime("%H:%M:%S"),
                "level": level,
                "message": message,
            }
        )

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                raise RuntimeError("No research job is running")
            self._stop_event.set()
            self._state["status"] = "stopping"
            self._state["message"] = "正在停止研究任务"
            self._append_log("收到研究任务停止请求")
            return self.snapshot()

    def shutdown(self, timeout: float = 10.0) -> None:
        """Stop a child process on application reload/exit and wait for its thread."""
        with self._lock:
            thread = self._thread
            process = self._process
            if not thread or not thread.is_alive():
                return
            self._stop_event.set()
            self._state["status"] = "stopping"
            self._state["message"] = "服务退出，正在停止研究任务"
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=min(timeout, 5.0))
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    process.kill()
        if thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            payload = dict(self._state)
            payload["skipped_horizons"] = list(self._state.get("skipped_horizons") or [])
            payload["logs"] = list(self._logs)
            return payload
