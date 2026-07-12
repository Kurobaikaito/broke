from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from backend.app.config import get_settings
from backend.app.db import Base, get_engine
from backend.app import models  # noqa: F401 - registers ORM tables


def main() -> None:
    url = make_url(get_settings().database_url)
    database = url.database
    if not database or not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise SystemExit("DATABASE_URL must contain a safe database name")
    connection = pymysql.connect(
        host=url.host or "localhost",
        port=url.port or 3306,
        user=url.username or "root",
        password=url.password or "",
        charset=url.query.get("charset", "utf8mb4"),
        connect_timeout=5,
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci"
            )
    finally:
        connection.close()
    Base.metadata.create_all(bind=get_engine())
    inspector = inspect(get_engine())
    daily_basic_columns = {column["name"] for column in inspector.get_columns("daily_basic")}
    if "limit_status" not in daily_basic_columns:
        with get_engine().begin() as sql_connection:
            sql_connection.execute(text("ALTER TABLE daily_basic ADD COLUMN limit_status TINYINT NULL"))
    print(f"mysql_initialized={url.host}:{url.port or 3306}/{database}")


if __name__ == "__main__":
    main()
