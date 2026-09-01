"""Import a filled-in rate intake sheet.

    python -m app.modules.rate_intake.cli <sheet>            # dry run, reports only
    python -m app.modules.rate_intake.cli <sheet> --commit   # writes

Dry run is the default deliberately: a sheet of a few thousand supplier rates
carries questions only the person who typed it can answer, and the report is how
they surface. Nothing is written without ``--commit``.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.modules.currency.models import ExchangeRate
from app.modules.rate_intake.service import (
    RateIntakeService,
    group_rejections,
    rejections_by_property,
)


async def run(path: str, *, commit: bool, limit: int) -> int:
    async with AsyncSessionLocal() as db:
        service = RateIntakeService(db)
        report = await service.import_sheet(path, dry_run=not commit)

        print(f"\n=== {path} ===")
        # Printed on every run, and deliberately: this command writes supplier
        # rates, and knowing which database is receiving them is the difference
        # between a test load and a data incident.
        target = (settings.DATABASE_URL or "?").split("@")[-1]
        print(f"database: {target}\n")
        print(report.summary())

        if report.skipped:
            print(f"\n-- skipped ({len(report.skipped)})")
            for note in report.skipped[:limit]:
                print(f"   {note}")

        grouped = group_rejections(report)
        if grouped:
            print(f"\n-- REJECTED {len(report.rejected)} rows, by cause")
            for field_name, problems in sorted(
                grouped.items(), key=lambda kv: -len(kv[1])
            ):
                print(f"\n   {field_name}: {len(problems)} rows")
                print(f"     {problems[0].message}")
                lines = ", ".join(str(p.line) for p in problems[:20])
                more = "" if len(problems) <= 20 else f" ... +{len(problems) - 20}"
                print(f"     rows: {lines}{more}")

            print("\n-- REJECTED rows by property (this is the actionable list)")
            for name, counter in sorted(
                rejections_by_property(report).items(),
                key=lambda kv: -sum(kv[1].values()),
            ):
                fields = ", ".join(f"{f} x{n}" for f, n in counter.most_common())
                print(f"   {sum(counter.values()):>4}  {name}: {fields}")

        if report.conflicts:
            print(
                f"\n-- UNRESOLVED CONFLICTS ({len(report.conflicts)}): more than "
                "one rate for the same room-night, and not a rack/NETT pair.\n"
                "   Left out rather than guessed at. Fix the sheet and re-import."
            )
            for note in report.conflicts[:limit]:
                print(f"   {note}")
            if len(report.conflicts) > limit:
                print(f"   ... +{len(report.conflicts) - limit} more")

        if report.label_variants:
            print(
                f"\n-- DAY-OF-WEEK / UNREPRESENTABLE VARIANTS "
                f"({len(report.label_variants)}): the sheet prices one "
                "room-night two ways\n   under different labels, and the schema "
                "has no column for the distinction.\n   The HIGHER figure was "
                "kept, so a weeknight over-quotes visibly rather than\n   a "
                "weekend under-quoting silently. Check these."
            )
            for note in report.label_variants[:limit]:
                print(f"   {note}")
            if len(report.label_variants) > limit:
                print(f"   ... +{len(report.label_variants) - limit} more")

        if report.derived_capacity:
            print(
                f"\n-- room capacity INFERRED for {len(report.derived_capacity)} "
                "room types (room_sleeps was blank; used the largest price_covers)"
            )
            for name, cap in list(report.derived_capacity.items())[:limit]:
                print(f"   {cap} guests  {name}")
            if len(report.derived_capacity) > limit:
                print(f"   ... +{len(report.derived_capacity) - limit} more")

        if report.near_duplicate_destinations:
            print("\n-- destinations that may be the same place (NOT merged)")
            for _first, members in report.near_duplicate_destinations.items():
                print(f"   {' | '.join(members)}")

        if report.warnings:
            print(f"\n-- warnings ({len(report.warnings)})")
            for note in report.warnings[:limit]:
                print(f"   {note}")
            if len(report.warnings) > limit:
                print(f"   ... +{len(report.warnings) - limit} more")

        pairs = {
            (row.base_currency, row.quote_currency)
            for row in (await db.execute(select(ExchangeRate))).scalars().all()
        }
        missing = service.missing_fx(report, pairs)
        if missing:
            print(
                "\n-- NO EXCHANGE RATE ON FILE for "
                + ", ".join(f"{c}->KES" for c in missing)
                + "\n   Properties quoted in these cannot be priced in KES until a "
                "rate is added."
            )

        if not commit:
            print("\nDRY RUN — nothing written. Re-run with --commit to import.")
        return 1 if report.rejected and not commit else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sheet")
    parser.add_argument(
        "--commit", action="store_true", help="write to the database"
    )
    parser.add_argument("--limit", type=int, default=15, help="lines per report block")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.sheet, commit=args.commit, limit=args.limit)))


if __name__ == "__main__":
    main()
