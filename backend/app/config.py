from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
MIN_FINITE_RAW_RETENTION_DAYS = 650


def _load_local_env() -> None:
    """Load simple KEY=VALUE entries without adding a runtime dependency."""
    if not ENV_PATH.exists():
        return
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_non_negative_int(value: str | None, default: int, name: str) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} cannot be negative")
    return parsed


def _parse_positive_int(value: str | None, default: int, name: str) -> int:
    parsed = _parse_non_negative_int(value, default, name)
    if parsed == 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def _parse_raw_retention(value: str | None) -> int:
    parsed = _parse_non_negative_int(value, 0, "DATA_RAW_RETENTION_DAYS")
    if 0 < parsed < MIN_FINITE_RAW_RETENTION_DAYS:
        raise ValueError(
            "DATA_RAW_RETENTION_DAYS must be 0 (permanent) or at least "
            f"{MIN_FINITE_RAW_RETENTION_DAYS}; shorter history cannot satisfy factor warm-up, "
            "purge, and minimum training windows"
        )
    return parsed


@dataclass(frozen=True)
class Settings:
    app_name: str
    demo_mode: bool
    auto_create_tables: bool
    database_url: str
    tushare_token: str
    static_dir: Path
    # Zero retention means permanent storage. Raw data is intentionally permanent by default.
    data_raw_retention_days: int
    data_factor_retention_days: int
    data_prediction_retention_days: int
    data_maintenance_audit_retention_days: int
    data_maintenance_batch_size: int


def get_settings() -> Settings:
    _load_local_env()
    root = Path(__file__).resolve().parent
    return Settings(
        app_name=os.getenv("APP_NAME", "A-Share Stock Selector"),
        demo_mode=_parse_bool(os.getenv("APP_DEMO_MODE"), default=True),
        auto_create_tables=_parse_bool(os.getenv("AUTO_CREATE_TABLES"), default=False),
        database_url=os.getenv(
            "DATABASE_URL",
            "mysql+pymysql://stock_user:stock_pass@localhost:3306/stock_selector?charset=utf8mb4",
        ),
        tushare_token=os.getenv("TUSHARE_TOKEN", "").strip(),
        static_dir=root / "static",
        data_raw_retention_days=_parse_raw_retention(os.getenv("DATA_RAW_RETENTION_DAYS")),
        data_factor_retention_days=_parse_non_negative_int(
            os.getenv("DATA_FACTOR_RETENTION_DAYS"), 90, "DATA_FACTOR_RETENTION_DAYS"
        ),
        data_prediction_retention_days=_parse_non_negative_int(
            os.getenv("DATA_PREDICTION_RETENTION_DAYS"),
            365,
            "DATA_PREDICTION_RETENTION_DAYS",
        ),
        data_maintenance_audit_retention_days=_parse_non_negative_int(
            os.getenv("DATA_MAINTENANCE_AUDIT_RETENTION_DAYS"),
            180,
            "DATA_MAINTENANCE_AUDIT_RETENTION_DAYS",
        ),
        data_maintenance_batch_size=_parse_positive_int(
            os.getenv("DATA_MAINTENANCE_BATCH_SIZE"),
            5000,
            "DATA_MAINTENANCE_BATCH_SIZE",
        ),
    )
