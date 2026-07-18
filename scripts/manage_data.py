from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import get_settings
from backend.app.db import get_engine
from backend.app.services.data_governance import DataGovernanceService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit and maintain stock-selector data. The default is a read-only preview; "
            "pass --apply to delete rows."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Apply retention, de-duplication and optional orphan cleanup.",
    )
    mode.add_argument(
        "--inventory-only",
        action="store_true",
        help="Only print inventory and quality findings; do not plan maintenance.",
    )
    parser.add_argument(
        "--skip-deduplicate",
        action="store_true",
        help="Skip business-key duplicate detection/removal.",
    )
    parser.add_argument(
        "--purge-orphans",
        action="store_true",
        help="Remove rows whose stock code is absent from dim_stock (otherwise audit only).",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="After applied deletions, reclaim space with SQLite VACUUM or MySQL OPTIMIZE TABLE.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Rows per delete transaction (default: DATA_MAINTENANCE_BATCH_SIZE).",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="Evaluate retention relative to this date (default: today).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.batch_size is not None and args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    if args.compact and not args.apply:
        raise SystemExit("--compact requires --apply")
    if args.purge_orphans and not args.apply:
        raise SystemExit("--purge-orphans requires --apply")

    settings = get_settings()
    service = DataGovernanceService(
        get_engine(),
        batch_size=args.batch_size or settings.data_maintenance_batch_size,
    )
    if args.inventory_only:
        result = service.inventory(include_quality=True)
    else:
        result = service.maintain(
            dry_run=not args.apply,
            deduplicate=not args.skip_deduplicate,
            purge_orphans=args.purge_orphans,
            compact=args.compact,
            as_of=args.as_of,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
