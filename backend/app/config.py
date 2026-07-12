from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


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


def save_tushare_token(token: str) -> None:
    value = token.strip()
    if len(value) < 20 or any(character.isspace() for character in value):
        raise ValueError("Tushare token format is invalid")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith("TUSHARE_TOKEN="):
            updated.append(f"TUSHARE_TOKEN={value}")
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"TUSHARE_TOKEN={value}")
    temporary = ENV_PATH.with_name(".env.tmp")
    temporary.write_text("\n".join(updated) + "\n", encoding="utf-8")
    temporary.replace(ENV_PATH)
    os.environ["TUSHARE_TOKEN"] = value


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str
    demo_mode: bool
    auto_create_tables: bool
    database_url: str
    tushare_token: str
    static_dir: Path


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
    )
