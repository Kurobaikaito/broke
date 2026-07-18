from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, select

from ..models import DailyBar, DailyBasic, DimStock
from ..repositories import DemoRepository, MysqlRepository
from .data_sources import normalize_ts_code
from .demo_data import DEMO_STOCKS, DEMO_TRADE_DATE


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _percent_to_ratio(value: Any) -> float | None:
    converted = _as_float(value)
    return converted / 100.0 if converted is not None else None


def _normalise_code(code: str) -> str:
    return normalize_ts_code(code.strip())


def get_stock_detail(
    repository: DemoRepository | MysqlRepository,
    code: str,
    limit: int,
) -> dict[str, Any] | None:
    """Return a stock snapshot and chronological OHLCV bars for the detail view."""
    normalised = _normalise_code(code)
    if not normalised:
        return None
    if isinstance(repository, DemoRepository):
        return _demo_stock_detail(normalised, limit)
    return _mysql_stock_detail(repository, normalised, limit)


def _mysql_stock_detail(
    repository: MysqlRepository,
    code: str,
    limit: int,
) -> dict[str, Any] | None:
    session = repository.session
    stock = session.execute(
        select(DimStock).where(
            DimStock.code == code,
            DimStock.exchange.in_(("SSE", "SZSE")),
        )
    ).scalar_one_or_none()
    newest_first = list(
        session.execute(
            select(DailyBar)
            .where(DailyBar.code == code)
            .order_by(desc(DailyBar.trade_date))
            .limit(limit)
        ).scalars()
    )
    if stock is None and not newest_first:
        return None

    bars = list(reversed(newest_first))
    latest = newest_first[0] if newest_first else None
    previous = newest_first[1] if len(newest_first) > 1 else None
    basic = session.execute(
        select(DailyBasic)
        .where(DailyBasic.code == code)
        .order_by(desc(DailyBasic.trade_date))
        .limit(1)
    ).scalar_one_or_none()

    last_close = _as_float(latest.close) if latest else None
    previous_close = _as_float(previous.close) if previous else None
    change = None
    change_pct = _percent_to_ratio(latest.pct_chg) if latest else None
    # Tushare pct_chg uses the adjusted previous close, so it remains correct
    # on ex-right/ex-dividend dates where adjacent raw closes are misleading.
    if last_close is not None and change_pct is not None and change_pct > -1.0:
        adjusted_previous_close = last_close / (1.0 + change_pct)
        change = last_close - adjusted_previous_close
    elif last_close is not None and previous_close not in (None, 0.0):
        change = last_close - previous_close
        change_pct = change / previous_close

    return {
        "mode": "mysql",
        "stock": {
            "code": code,
            "name": stock.name if stock else code,
            "industry": stock.industry if stock else None,
            "trade_date": _iso(latest.trade_date) if latest else None,
            "last_close": last_close,
            "change": change,
            "change_pct": change_pct,
            "open": _as_float(latest.open) if latest else None,
            "high": _as_float(latest.high) if latest else None,
            "low": _as_float(latest.low) if latest else None,
            "volume": _as_float(latest.volume) if latest else None,
            "amount": _as_float(latest.amount) if latest else None,
            "turnover_rate": _percent_to_ratio(basic.turnover_rate)
            if basic
            else _percent_to_ratio(latest.turnover_rate)
            if latest
            else None,
            "pe_ttm": _as_float(basic.pe_ttm) if basic else None,
            "pb": _as_float(basic.pb) if basic else None,
            # Ingestion already converts Tushare's 10k-CNY units to CNY.
            "total_market_value": _as_float(basic.total_mv) if basic else None,
            "circ_market_value": _as_float(basic.float_mv) if basic else None,
        },
        "bars": [
            {
                "trade_date": _iso(bar.trade_date),
                "open": _as_float(bar.open),
                "high": _as_float(bar.high),
                "low": _as_float(bar.low),
                "close": _as_float(bar.close),
                "volume": _as_float(bar.volume),
                "amount": _as_float(bar.amount),
            }
            for bar in bars
        ],
    }


def _demo_stock_detail(code: str, limit: int) -> dict[str, Any] | None:
    item = next((stock for stock in DEMO_STOCKS if stock["code"] == code), None)
    if item is None:
        return None

    end = date.fromisoformat(DEMO_TRADE_DATE)
    dates: list[date] = []
    cursor = end
    while len(dates) < limit:
        if cursor.weekday() < 5:
            dates.append(cursor)
        cursor -= timedelta(days=1)
    dates.reverse()

    seed = sum((index + 1) * int(digit) for index, digit in enumerate(code) if digit.isdigit())
    target_close = float(item["last_close"])
    relative_values = [
        1.0 + index * 0.00065 + 0.035 * math.sin((index + seed) / 9.0) + 0.016 * math.cos((index + seed) / 21.0)
        for index in range(limit)
    ]
    scale = target_close / relative_values[-1]
    closes = [max(0.01, value * scale) for value in relative_values]
    bars: list[dict[str, Any]] = []
    for index, (trade_date, close) in enumerate(zip(dates, closes)):
        previous_close = closes[index - 1] if index else close * 0.997
        open_price = previous_close * (1.0 + 0.004 * math.sin((index + seed) / 3.0))
        amplitude = 0.009 + 0.005 * abs(math.cos((index + seed) / 5.0))
        high = max(open_price, close) * (1.0 + amplitude)
        low = min(open_price, close) * (1.0 - amplitude * 0.85)
        volume = float(1_800_000 + ((index * 79_123 + seed * 31_337) % 6_200_000))
        bars.append(
            {
                "trade_date": trade_date.isoformat(),
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": volume,
                "amount": round(volume * close, 2),
            }
        )

    latest = bars[-1]
    previous_close = float(bars[-2]["close"]) if len(bars) > 1 else None
    change = float(latest["close"]) - previous_close if previous_close else None
    change_pct = change / previous_close if change is not None and previous_close else None
    factor_seed = abs(float(item["factors"].get("turnover_20d", item["factors"].get("turnover_rate", 0.2))))
    return {
        "mode": "demo",
        "stock": {
            "code": code,
            "name": item["name"],
            "industry": item["industry"],
            "trade_date": latest["trade_date"],
            "last_close": latest["close"],
            "change": change,
            "change_pct": change_pct,
            "open": latest["open"],
            "high": latest["high"],
            "low": latest["low"],
            "volume": latest["volume"],
            "amount": latest["amount"],
            "turnover_rate": round((1.2 + factor_seed * 2.8) / 100.0, 6),
            "pe_ttm": round(12.0 + seed % 28 + factor_seed, 2),
            "pb": round(1.2 + (seed % 35) / 10.0, 2),
            "total_market_value": round(target_close * (2_000_000_000 + seed * 10_000_000), 2),
            "circ_market_value": round(target_close * (1_600_000_000 + seed * 8_000_000), 2),
        },
        "bars": bars,
    }
