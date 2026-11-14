"""Check stored image URLs over plain HTTP (free) and choose one primary per model.

python -m tools.validate_images              # rows still 'unvalidated'
python -m tools.validate_images --all        # re-check every row
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select

from autohub.core.logging import configure_logging
from autohub.db.models import VehicleImage
from autohub.db.session import get_session_factory
from autohub.images.service import validate_pending
from autohub.images.validator import ImageValidator


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--all", action="store_true", help="re-validate every image, not only unvalidated ones"
    )
    args = parser.parse_args(argv)
    configure_logging()
    statuses = ("unvalidated", "valid", "broken", "rejected") if args.all else ("unvalidated",)
    db = get_session_factory()()
    try:
        stats = validate_pending(db, ImageValidator(), statuses=statuses)
        counts: dict[str, int] = {
            status: int(n)
            for status, n in db.execute(
                select(VehicleImage.validation_status, func.count()).group_by(VehicleImage.validation_status)
            )
        }
        primaries = db.execute(select(func.count()).where(VehicleImage.is_primary.is_(True))).scalar_one()
    finally:
        db.close()
    print(f"checked: valid={stats.valid} broken={stats.broken} rejected={stats.rejected}")
    print(f"all rows by status: {counts}")
    print(f"models with a primary image: {primaries}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
