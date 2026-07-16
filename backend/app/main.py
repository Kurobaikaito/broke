from __future__ import annotations

from collections.abc import Generator
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from .config import get_settings
from .db import Base, get_engine, get_session_factory
from .repositories import DemoRepository, MysqlRepository
from .services.portfolio import DEFAULT_CAPITAL, allocate_lot_positions, target_position_count
from .services.sync_jobs import SyncJobManager
from .services.tushare_sync import FULL_HISTORY_START, FULL_SYNC_DATASET, SyncOptions

settings = get_settings()
sync_manager = SyncJobManager()


app = FastAPI(title=settings.app_name, version="0.1.0")
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


@app.on_event("startup")
def startup() -> None:
    if not settings.demo_mode and settings.auto_create_tables:
        from . import models  # noqa: F401

        Base.metadata.create_all(bind=get_engine())


def get_repository() -> Generator[DemoRepository | MysqlRepository, None, None]:
    if settings.demo_mode:
        yield DemoRepository()
        return

    session_factory = get_session_factory()
    session = session_factory()
    try:
        yield MysqlRepository(session)
    finally:
        session.close()


RepositoryDep = Annotated[DemoRepository | MysqlRepository, Depends(get_repository)]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        settings.static_dir / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/health")
def health(repository: RepositoryDep):
    payload = {
        "app": settings.app_name,
        "demo_mode": settings.demo_mode,
    }
    try:
        payload.update(repository.health())
    except SQLAlchemyError as exc:
        payload.update({"database": "mysql", "ok": False, "error": str(exc)})
    return payload


@app.get("/api/recommendations")
def recommendations(
    repository: RepositoryDep,
    horizon: str = Query("20d", pattern="^(5d|20d|60d)$"),
    limit: int = Query(100, ge=1, le=100),
    min_score: float | None = Query(None),
    capital: float = Query(DEFAULT_CAPITAL, ge=10_000, le=100_000),
):
    rows = repository.recommendations(horizon=horizon, limit=limit, min_score=min_score)
    mode = "demo" if settings.demo_mode else "mysql"
    if not rows and not settings.demo_mode:
        rows = DemoRepository().recommendations(horizon=horizon, limit=limit, min_score=min_score)
        mode = "mysql-empty-demo-fallback"
    items = allocate_lot_positions(rows, capital=capital)
    allocated = sum(float(item["target_amount"]) for item in items)
    return {
        "mode": mode,
        "capital": capital,
        "target_position_count": target_position_count(capital),
        "allocated_amount": round(allocated, 2),
        "cash_remaining": round(capital - allocated, 2),
        "items": items,
    }


@app.get("/api/stocks/{code}/explain")
def stock_explanation(
    code: str,
    repository: RepositoryDep,
    horizon: str = Query("20d", pattern="^(5d|20d|60d)$"),
):
    explanation = repository.stock_explanation(code=code, horizon=horizon)
    if explanation is None and not settings.demo_mode:
        explanation = DemoRepository().stock_explanation(code=code, horizon=horizon)
    if explanation is None:
        raise HTTPException(status_code=404, detail=f"Stock {code} not found")
    return explanation


@app.get("/api/backtest/summary")
def backtest_summary(
    repository: RepositoryDep,
    horizon: str = Query("20d", pattern="^(5d|20d|60d)$"),
):
    summary = repository.backtest_summary(horizon=horizon)
    if summary is None and not settings.demo_mode:
        summary = DemoRepository().backtest_summary(horizon=horizon)
        summary["notes"] = "MySQL 中暂无回测结果，当前返回 demo fallback。"
    return summary


@app.get("/api/data/config")
def data_config():
    return {
        "provider": "tushare",
        "markets": ["SSE", "SZSE"],
        "history_start": f"{FULL_HISTORY_START[:4]}-{FULL_HISTORY_START[4:6]}-{FULL_HISTORY_START[6:]}",
        "sync_dataset": FULL_SYNC_DATASET,
        "resume_automatically": True,
    }


@app.get("/api/data/inventory")
def data_inventory(repository: RepositoryDep):
    if not isinstance(repository, MysqlRepository):
        return {"tables": [], "states": []}
    return repository.data_inventory()


@app.get("/api/data/sync/status")
def data_sync_status():
    return sync_manager.snapshot()


@app.post("/api/data/sync", status_code=202)
def start_data_sync():
    if settings.demo_mode:
        raise HTTPException(status_code=409, detail="MySQL mode is required")
    current = get_settings()
    if not current.tushare_token:
        raise HTTPException(status_code=422, detail="项目 .env 未配置 TUSHARE_TOKEN")
    options = SyncOptions(
        end_date=date.today().strftime("%Y%m%d"),
        sleep_seconds=0.0,
        retry=3,
        continue_on_error=False,
        use_checkpoint=True,
    )
    try:
        return sync_manager.start(options, current.tushare_token)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/data/sync/stop", status_code=202)
def stop_data_sync():
    try:
        return sync_manager.stop()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
