from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import get_settings
from backend.app.services.tushare_sync import SyncOptions, run_tushare_sync


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incrementally sync full-market A-share data from Tushare Pro.")
    parser.add_argument("--start-date", default=None, help="YYYYMMDD; defaults to 20260101 or the saved checkpoint")
    parser.add_argument("--end-date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--codes", default=None, help="Optional comma-separated six-digit codes")
    parser.add_argument("--refresh-days", type=int, default=7)
    parser.add_argument("--sleep", type=float, default=0.8)
    parser.add_argument("--retry", type=int, default=3)
    parser.add_argument("--max-dates", type=int, default=None)
    parser.add_argument("--continue-on-error", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    if settings.demo_mode:
        raise SystemExit("APP_DEMO_MODE=true. Set APP_DEMO_MODE=false before syncing MySQL.")
    options = SyncOptions(
        start_date=args.start_date,
        end_date=args.end_date,
        codes=args.codes,
        refresh_days=args.refresh_days,
        sleep_seconds=args.sleep,
        retry=args.retry,
        max_dates=args.max_dates,
        continue_on_error=args.continue_on_error,
        use_checkpoint=args.start_date is None,
    )

    def print_progress(payload: dict) -> None:
        message = payload.get("message")
        if message:
            print(message)

    try:
        result = run_tushare_sync(options, settings.tushare_token, on_progress=print_progress)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"status={result['status']}")
    print(f"totals={result['totals']}")
    print(f"failed_dates={','.join(result['failures'])}")
    print(f"checkpoint={result.get('checkpoint') or ''}")


if __name__ == "__main__":
    main()
