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
    daily_basic_column_map = {
        column["name"]: column for column in inspect(get_engine()).get_columns("daily_basic")
    }
    status_columns = ("is_st", "is_suspended")
    if any(not daily_basic_column_map[name]["nullable"] for name in status_columns):
        with get_engine().begin() as sql_connection:
            sql_connection.execute(text("ALTER TABLE daily_basic MODIFY is_st TINYINT NULL"))
            sql_connection.execute(text("ALTER TABLE daily_basic MODIFY is_suspended TINYINT NULL"))
            # Earlier versions fabricated 0 for both fields, so existing values cannot be trusted.
            sql_connection.execute(text("UPDATE daily_basic SET is_st = NULL, is_suspended = NULL"))
    with get_engine().begin() as sql_connection:
        bse_stock_count = int(
            sql_connection.execute(
                text("SELECT COUNT(*) FROM dim_stock WHERE exchange = 'BSE'")
            ).scalar_one()
        )
        if bse_stock_count:
            for table_name in (
                "daily_bar",
                "daily_bar_adj",
                "adj_factor",
                "daily_basic",
                "factor_daily",
                "model_prediction",
            ):
                sql_connection.execute(
                    text(
                        f"DELETE target FROM {table_name} AS target "
                        "INNER JOIN dim_stock AS stock ON stock.code = target.code "
                        "WHERE stock.exchange = 'BSE'"
                    )
                )
            # Aggregate backtests may have included the removed market and must be rebuilt.
            sql_connection.execute(text("DELETE FROM backtest_summary"))
            sql_connection.execute(text("DELETE FROM dim_stock WHERE exchange = 'BSE'"))
            print(f"bse_stocks_removed={bse_stock_count}")
    redundant_indexes = {
        "daily_bar": ("ix_daily_bar_code", "ix_daily_bar_trade_date_code"),
        "daily_bar_adj": ("ix_daily_bar_adj_code", "ix_daily_bar_adj_trade_date_code"),
        "adj_factor": ("ix_adj_factor_code", "ix_adj_factor_trade_date_code"),
        "daily_basic": ("ix_daily_basic_code", "ix_daily_basic_trade_date_code"),
    }
    for table_name, index_names in redundant_indexes.items():
        existing_indexes = {
            index["name"] for index in inspect(get_engine()).get_indexes(table_name)
        }
        removable = [name for name in index_names if name in existing_indexes]
        if not removable:
            continue
        drop_sql = ", ".join(f"DROP INDEX `{name}`" for name in removable)
        with get_engine().begin() as sql_connection:
            sql_connection.execute(text(f"ALTER TABLE `{table_name}` {drop_sql}"))
        print(f"redundant_indexes_removed={table_name}:{','.join(removable)}")
    print(f"mysql_initialized={url.host}:{url.port or 3306}/{database}")


if __name__ == "__main__":
    main()
