from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from typing import Any


SUPPORTED_EXCHANGES = {"SSE", "SZSE"}
SUPPORTED_TS_CODE_SUFFIXES = (".SH", ".SZ")


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        converted = Decimal(str(value))
        return converted if converted.is_finite() else None
    except Exception:
        return None


def _to_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    text = str(value)
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value}")


class TushareClient:
    """Tushare Pro adapter optimized for full-market requests by trade date."""

    def __init__(self, token: str):
        if not token:
            raise ValueError("TUSHARE_TOKEN is empty. Add your Tushare Pro token to .env first.")
        import tushare as ts  # type: ignore

        self.pro = ts.pro_api(token)

    def query_all(
        self,
        api_name: str,
        fields: str,
        page_size: int = 6000,
        max_pages: int = 20,
        **params: Any,
    ):
        """Read every page instead of silently accepting an API row cap."""
        import pandas as pd

        pages = []
        offset = 0
        for _ in range(max_pages):
            page = self.pro.query(
                api_name,
                fields=fields,
                limit=page_size,
                offset=offset,
                **params,
            )
            if page is None or page.empty:
                break
            pages.append(page)
            if len(page) < page_size:
                break
            offset += len(page)
        else:
            raise RuntimeError(f"{api_name} exceeded pagination safety limit ({max_pages} pages)")
        return pd.concat(pages, ignore_index=True) if pages else pd.DataFrame()

    def stock_list_records(self) -> list[dict[str, Any]]:
        fields = "ts_code,symbol,name,industry,list_date,delist_date,exchange,list_status"
        status_map = {"L": "active", "D": "delisted", "P": "paused", "G": "pending"}
        records_by_code: dict[str, dict[str, Any]] = {}
        for list_status in ("L", "P", "D", "G"):
            frame = self.query_all("stock_basic", fields, exchange="", list_status=list_status)
            if frame is None or frame.empty:
                continue
            for _, row in frame.iterrows():
                code = normalize_ts_code(row.get("ts_code") or row.get("symbol"))
                if not code:
                    continue
                exchange = normalize_tushare_exchange(row.get("exchange"), row.get("ts_code"))
                if exchange not in SUPPORTED_EXCHANGES:
                    continue
                list_date = row.get("list_date")
                records_by_code[code] = {
                    "code": code,
                    "name": str(row.get("name") or code).strip(),
                    "exchange": exchange,
                    "industry": _optional_text(row.get("industry")),
                    "list_date": _to_date(list_date) if _optional_text(list_date) else None,
                    "status": status_map.get(str(row.get("list_status") or list_status), "unknown"),
                }
        return list(records_by_code.values())

    def trade_calendar_records(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        frame = self.query_all(
            "trade_cal",
            "exchange,cal_date,is_open",
            exchange="SSE",
            start_date=start_date,
            end_date=end_date,
        )
        return [
            {
                "trade_date": _to_date(row.get("cal_date")),
                "exchange": "SSE",
                "is_open": int(row.get("is_open") or 0),
            }
            for _, row in frame.iterrows()
        ]

    def daily_frames(self, trade_date: str):
        requests = (
            ("daily", "ts_code,trade_date,open,high,low,close,pct_chg,vol,amount"),
            ("adj_factor", "ts_code,trade_date,adj_factor"),
            (
                "daily_basic",
                "ts_code,trade_date,turnover_rate,pe_ttm,pb,ps_ttm,total_mv,circ_mv,limit_status",
            ),
        )
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="tushare-daily") as executor:
            futures = [
                executor.submit(self.query_all, api_name, fields, trade_date=trade_date)
                for api_name, fields in requests
            ]
            daily, factors, basics = (future.result() for future in futures)
        return tuple(filter_supported_market(frame) for frame in (daily, factors, basics))


def filter_supported_market(frame):
    """Keep Shanghai and Shenzhen instruments; Beijing-market rows are out of scope."""
    if frame is None or frame.empty or "ts_code" not in frame.columns:
        return frame
    codes = frame["ts_code"].astype(str).str.upper()
    return frame[codes.str.endswith(SUPPORTED_TS_CODE_SUFFIXES)].copy()


def validate_tushare_frames(daily, factors, basics, expected_date: str) -> dict[str, Any]:
    """Reject truncated, duplicated, cross-date, or internally inconsistent daily bundles."""
    import pandas as pd

    required = {
        "daily": (daily, {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"}),
        "adj_factor": (factors, {"ts_code", "trade_date", "adj_factor"}),
        "daily_basic": (basics, {"ts_code", "trade_date", "turnover_rate"}),
    }
    for name, (frame, columns) in required.items():
        if frame is None or frame.empty:
            raise ValueError(f"{name} returned no rows for open date {expected_date}")
        missing = columns.difference(frame.columns)
        if missing:
            raise ValueError(f"{name} missing columns: {sorted(missing)}")
        if frame.duplicated(["ts_code", "trade_date"]).any():
            raise ValueError(f"{name} contains duplicate code/date rows")
        dates = set(frame["trade_date"].astype(str))
        if dates != {expected_date}:
            raise ValueError(f"{name} returned unexpected dates: {sorted(dates)}")

    numeric = daily[["open", "high", "low", "close", "vol", "amount"]].apply(pd.to_numeric, errors="coerce")
    invalid_ohlc = (
        numeric[["open", "high", "low", "close"]].le(0).any(axis=1)
        | numeric["high"].lt(numeric[["open", "close", "low"]].max(axis=1))
        | numeric["low"].gt(numeric[["open", "close", "high"]].min(axis=1))
        | numeric[["vol", "amount"]].lt(0).any(axis=1)
    )
    if invalid_ohlc.any():
        raise ValueError(f"daily contains {int(invalid_ohlc.sum())} invalid OHLCV rows")

    daily_codes = set(daily["ts_code"].astype(str))
    factor_codes = set(factors["ts_code"].astype(str))
    basic_codes = set(basics["ts_code"].astype(str))
    missing_factors = daily_codes.difference(factor_codes)
    if missing_factors:
        raise ValueError(f"adj_factor missing {len(missing_factors)} traded stocks")
    basic_coverage = len(daily_codes.intersection(basic_codes)) / max(len(daily_codes), 1)
    if basic_coverage < 0.98:
        raise ValueError(f"daily_basic coverage too low: {basic_coverage:.2%}")
    return {
        "daily_rows": len(daily),
        "factor_rows": len(factors),
        "basic_rows": len(basics),
        "basic_coverage": basic_coverage,
    }


def tushare_daily_bundle_records(daily, factors, basics) -> dict[str, list[dict[str, Any]]]:
    """Convert one market date and build stable total-return adjusted OHLC values."""
    import pandas as pd

    if daily is None or daily.empty:
        return {"daily": [], "adjusted": [], "factors": [], "basics": []}
    frame = daily.copy()
    factor_frame = factors.copy() if factors is not None else pd.DataFrame()
    basic_frame = basics.copy() if basics is not None else pd.DataFrame()
    if factor_frame.empty:
        raise ValueError("Tushare adj_factor returned no rows for a non-empty daily response")
    frame = frame.merge(factor_frame[["ts_code", "trade_date", "adj_factor"]], on=["ts_code", "trade_date"], how="left", validate="one_to_one")
    if not basic_frame.empty:
        frame = frame.merge(
            basic_frame[["ts_code", "trade_date", "turnover_rate"]],
            on=["ts_code", "trade_date"],
            how="left",
            validate="one_to_one",
        )
    else:
        frame["turnover_rate"] = None

    daily_records: list[dict[str, Any]] = []
    adjusted_records: list[dict[str, Any]] = []
    factor_records: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        code = normalize_ts_code(row.get("ts_code"))
        trade_date = _to_date(row.get("trade_date"))
        factor = _to_decimal(row.get("adj_factor"))
        if not code or factor is None or factor <= 0:
            continue
        base = {
            "code": code,
            "trade_date": trade_date,
            "open": _to_decimal(row.get("open")),
            "high": _to_decimal(row.get("high")),
            "low": _to_decimal(row.get("low")),
            "close": _to_decimal(row.get("close")),
            "volume": _scale_decimal(row.get("vol"), Decimal("100")),
            "amount": _scale_decimal(row.get("amount"), Decimal("1000")),
            "pct_chg": _to_decimal(row.get("pct_chg")),
            "turnover_rate": _to_decimal(row.get("turnover_rate")),
        }
        daily_records.append(base)
        adjusted = dict(base)
        for field in ("open", "high", "low", "close"):
            adjusted[field] = base[field] * factor if base[field] is not None else None
        adjusted_records.append(adjusted)
        factor_records.append({"code": code, "trade_date": trade_date, "adj_factor": factor})

    basic_records: list[dict[str, Any]] = []
    if not basic_frame.empty:
        for _, row in basic_frame.iterrows():
            code = normalize_ts_code(row.get("ts_code"))
            if not code:
                continue
            basic_records.append(
                {
                    "code": code,
                    "trade_date": _to_date(row.get("trade_date")),
                    "pe_ttm": _to_decimal(row.get("pe_ttm")),
                    "pb": _to_decimal(row.get("pb")),
                    "ps_ttm": _to_decimal(row.get("ps_ttm")),
                    "total_mv": _scale_decimal(row.get("total_mv"), Decimal("10000")),
                    "float_mv": _scale_decimal(row.get("circ_mv"), Decimal("10000")),
                    "turnover_rate": _to_decimal(row.get("turnover_rate")),
                    # ST and suspension history come from separate endpoints; do not invent false values.
                    "is_st": None,
                    "is_suspended": None,
                    "limit_status": _to_int(row.get("limit_status")),
                }
            )
    return {"daily": daily_records, "adjusted": adjusted_records, "factors": factor_records, "basics": basic_records}


def normalize_ts_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.split(".", 1)[0] if text else ""


def normalize_tushare_exchange(exchange: Any, ts_code: Any) -> str:
    value = str(exchange or "").strip().upper()
    if value in {"SSE", "SZSE", "BSE"}:
        return value
    suffix = str(ts_code or "").upper().rsplit(".", 1)[-1]
    return {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(suffix, "UNKNOWN")


def _scale_decimal(value: Any, multiplier: Decimal) -> Decimal | None:
    converted = _to_decimal(value)
    return converted * multiplier if converted is not None else None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return None if not text or text.lower() == "nan" else text


def _to_int(value: Any) -> int | None:
    converted = _to_decimal(value)
    return int(converted) if converted is not None else None
