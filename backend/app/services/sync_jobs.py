from __future__ import annotations

import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

from .tushare_sync import SyncOptions, run_tushare_sync


class SyncJobManager:
    def __init__(self, runner: Callable[..., dict[str, Any]] = run_tushare_sync):
        self._runner = runner
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._logs: deque[dict[str, str]] = deque(maxlen=200)
        self._state: dict[str, Any] = self._idle_state()

    @staticmethod
    def _idle_state() -> dict[str, Any]:
        return {
            "job_id": None,
            "status": "idle",
            "stage": "idle",
            "message": "暂无运行中的任务",
            "progress_pct": 0.0,
            "completed_dates": 0,
            "total_dates": 0,
            "current_date": None,
            "totals": {"daily": 0, "adjusted": 0, "factors": 0, "basics": 0},
            "failures": [],
            "started_at": None,
            "finished_at": None,
        }

    def start(self, options: SyncOptions, token: str) -> dict[str, Any]:
        options.validate()
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A sync job is already running")
            self._stop_event = threading.Event()
            self._logs.clear()
            self._state = self._idle_state()
            self._state.update(
                {
                    "job_id": uuid.uuid4().hex,
                    "status": "running",
                    "stage": "queued",
                    "message": "任务已创建",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._append_log("任务已创建")
            self._thread = threading.Thread(
                target=self._run,
                args=(options, token),
                name="tushare-sync",
                daemon=True,
            )
            self._thread.start()
            return self.snapshot()

    def _run(self, options: SyncOptions, token: str) -> None:
        try:
            result = self._runner(
                options=options,
                token=token,
                on_progress=self._handle_progress,
                stop_event=self._stop_event,
            )
            with self._lock:
                status = result.get("status", "completed")
                self._state["status"] = status
                self._state["stage"] = status
                self._state["totals"] = result.get("totals", self._state["totals"])
                self._state["failures"] = result.get("failures", self._state["failures"])
                self._state["finished_at"] = datetime.now(timezone.utc).isoformat()
                if status == "completed":
                    self._state["progress_pct"] = 100.0
                    self._state["message"] = "数据同步完成"
                elif status == "stopped":
                    self._state["message"] = "任务已停止"
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
                self._append_log(f"任务失败：{exc}", level="error")

    def _handle_progress(self, payload: dict[str, Any]) -> None:
        with self._lock:
            message = payload.get("message")
            if message:
                self._append_log(str(message), "error" if payload.get("stage") == "error" else "info")
            self._state.update({key: value for key, value in payload.items() if key != "message"})
            if message:
                self._state["message"] = message
            total = int(self._state.get("total_dates") or 0)
            completed = int(self._state.get("completed_dates") or 0)
            self._state["progress_pct"] = round(completed / total * 100, 2) if total else 0.0

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
                raise RuntimeError("No sync job is running")
            self._stop_event.set()
            self._state["status"] = "stopping"
            self._state["message"] = "正在停止任务"
            self._append_log("收到停止请求")
            return self.snapshot()

    def shutdown(self, timeout: float = 10.0) -> None:
        """Cooperatively stop an in-process sync thread during app shutdown."""
        with self._lock:
            thread = self._thread
            if not thread or not thread.is_alive():
                return
            self._stop_event.set()
            self._state["status"] = "stopping"
            self._state["message"] = "服务退出，正在停止同步任务"
        if thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            payload = dict(self._state)
            payload["totals"] = dict(self._state.get("totals") or {})
            payload["failures"] = list(self._state.get("failures") or [])
            payload["logs"] = list(self._logs)
            return payload
