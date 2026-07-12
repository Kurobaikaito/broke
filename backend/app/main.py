from __future__ import annotations

from collections.abc import Generator
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError
from pydantic import BaseModel, Field

from .config import get_settings, save_tushare_token
from .db import Base, get_engine, get_session_factory
from .repositories import DemoRepository, MysqlRepository
from .services.sync_jobs import SyncJobManager
from .services.tushare_sync import SyncOptions

settings = get_settings()
sync_manager = SyncJobManager()


class TokenUpdate(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class DataSyncRequest(BaseModel):
    start_date: date = date(2026, 1, 1)
    end_date: date | None = None
    sleep_seconds: float = Field(default=0.8, ge=0, le=60)
    retry: int = Field(default=3, ge=1, le=10)
    continue_on_error: bool = False
    use_checkpoint: bool = True

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
    return FileResponse(settings.static_dir / "index.html")


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
    limit: int = Query(20, ge=1, le=100),
    min_score: float | None = Query(None),
):
    rows = repository.recommendations(horizon=horizon, limit=limit, min_score=min_score)
    if not rows and not settings.demo_mode:
        rows = DemoRepository().recommendations(horizon=horizon, limit=limit, min_score=min_score)
        return {"mode": "mysql-empty-demo-fallback", "items": rows}
    return {"mode": "demo" if settings.demo_mode else "mysql", "items": rows}


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
    current = get_settings()
    token = current.tushare_token
    return {
        "provider": "tushare",
        "token_configured": bool(token),
        "token_suffix": token[-4:] if token else None,
        "defaults": {
            "start_date": "2026-01-01",
            "end_date": date.today().isoformat(),
            "sleep_seconds": 0.8,
            "retry": 3,
            "continue_on_error": False,
            "use_checkpoint": True,
        },
    }


@app.put("/api/data/token")
def update_data_token(payload: TokenUpdate):
    try:
        save_tushare_token(payload.token)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "saved", "token_configured": True, "token_suffix": payload.token[-4:]}


@app.get("/api/data/inventory")
def data_inventory(repository: RepositoryDep):
    if not isinstance(repository, MysqlRepository):
        return {"tables": [], "states": []}
    return repository.data_inventory()


@app.get("/api/data/sync/status")
def data_sync_status():
    return sync_manager.snapshot()


@app.post("/api/data/sync", status_code=202)
def start_data_sync(payload: DataSyncRequest):
    if settings.demo_mode:
        raise HTTPException(status_code=409, detail="MySQL mode is required")
    end_date = payload.end_date or date.today()
    if payload.start_date > end_date:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
    current = get_settings()
    if not current.tushare_token:
        raise HTTPException(status_code=422, detail="请先保存 Tushare Token")
    options = SyncOptions(
        start_date=payload.start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
        sleep_seconds=payload.sleep_seconds,
        retry=payload.retry,
        continue_on_error=payload.continue_on_error,
        use_checkpoint=payload.use_checkpoint,
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
