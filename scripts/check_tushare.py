from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import get_settings
from backend.app.services.data_sources import TushareClient, validate_tushare_frames


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Tushare token and dataset access check.")
    parser.add_argument("--trade-date", default="20240102", help="Known open date in YYYYMMDD format")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    datetime.strptime(args.trade_date, "%Y%m%d")
    settings = get_settings()
    if not settings.tushare_token:
        raise SystemExit("TUSHARE_TOKEN is empty")

    client = TushareClient(settings.tushare_token)
    stocks = client.stock_list_records()
    calendar = client.trade_calendar_records(args.trade_date, args.trade_date)
    if not calendar or calendar[0]["is_open"] != 1:
        raise SystemExit(f"{args.trade_date} is not an open SSE trading date")
    daily, factors, basics = client.daily_frames(args.trade_date)
    try:
        quality = validate_tushare_frames(daily, factors, basics, args.trade_date)
    except ValueError:
        daily_codes = set(daily["ts_code"].astype(str))
        basic_codes = set(basics["ts_code"].astype(str))
        missing_basics = sorted(daily_codes.difference(basic_codes))
        print(f"raw_daily_rows={len(daily)}")
        print(f"raw_adj_factor_rows={len(factors)}")
        print(f"raw_daily_basic_rows={len(basics)}")
        print(f"missing_daily_basic_rows={len(missing_basics)}")
        print(f"missing_daily_basic_sample={','.join(missing_basics[:20])}")
        raise
    print("token_valid=true")
    print(f"stock_records={len(stocks)}")
    print(f"trade_date={args.trade_date}")
    print(f"daily_rows={quality['daily_rows']}")
    print(f"adj_factor_rows={quality['factor_rows']}")
    print(f"daily_basic_rows={quality['basic_rows']}")
    print(f"daily_basic_coverage={quality['basic_coverage']:.2%}")


if __name__ == "__main__":
    main()
