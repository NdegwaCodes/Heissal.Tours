"""Importing a filled-in intake sheet into the catalogue.

Two passes, always. The first resolves and validates every row and reports what
it found; the second writes. ``dry_run=True`` stops after the first, and it is
the intended way to use this: a sheet of three thousand supplier rates has
questions in it that only the person who typed it can answer, and finding them
after the write is worse than finding them before.

**What this service refuses to invent.** A row missing its validity window, its
occupancy, its room type or its meal plan is *rejected and reported*, never
defaulted. A rate with a guessed season window is a price the supplier never
quoted, and it would price real quotes. The one thing defaulted is the season
*name*, because "Standard" is a label rather than a figure.

**What it normalises without asking.** Spelling. ``B&B`` and ``BB`` are the same
plan, ``STO`` and ``sto`` the same rate kind, ``Non-Resident`` and
``non_resident`` the same category. None of those change a number.

Provenance: every rate created carries the sheet it came from, so a quoted price
can be traced back to the row an agent typed.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.vat import DEFAULT_VAT_PCT, to_vat_inclusive
from app.modules.accommodations.models import (
    Accommodation,
    AccommodationRate,
    AccommodationSupplement,
    MealPlan,
    RoomType,
)
from app.modules.destinations.models import Destination
from app.modules.rate_intake import normalise as N
from app.modules.rate_intake.reader import Row, read_sheet
from app.modules.residence.models import ResidenceCategory


@dataclass
class Problem:
    line: int
    field: str
    message: str
    # Which property the row belongs to. A row number alone is not actionable —
    # nobody fixes "row 1039", they fix a rate sheet.
    property_name: str = ""

    def __str__(self) -> str:
        where = f" ({self.property_name})" if self.property_name else ""
        return f"row {self.line} [{self.field}]{where} {self.message}"


@dataclass
class IntakeReport:
    """Everything the import found, whether or not it wrote anything."""

    total_rows: int = 0
    skipped: list[str] = field(default_factory=list)
    rejected: list[Problem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    date_order: str = "unknown"

    destinations_created: list[str] = field(default_factory=list)
    properties_created: list[str] = field(default_factory=list)
    room_types_created: int = 0
    rates_created: int = 0
    rates_updated: int = 0
    supplements_created: int = 0
    supplements_duplicate: int = 0
    rack_net_merged: int = 0
    conflicts: list[str] = field(default_factory=list)
    label_variants: list[str] = field(default_factory=list)
    vat_unstated: Counter[str] = field(default_factory=Counter)

    derived_capacity: dict[str, int] = field(default_factory=dict)
    currencies: Counter[str] = field(default_factory=Counter)
    near_duplicate_destinations: dict[str, list[str]] = field(default_factory=dict)
    committed: bool = False

    @property
    def rejected_rows(self) -> int:
        """Distinct rows rejected. ``rejected`` is a list of *problems*, and one
        row commonly has several — a missing window fails valid_from and
        valid_to — so counting problems overstated the damage by a third."""
        return len({problem.line for problem in self.rejected})

    @property
    def accepted(self) -> int:
        return self.total_rows - self.rejected_rows

    def summary(self) -> str:
        lines = [
            f"rows read              {self.total_rows}",
            f"  accepted             {self.accepted}",
            f"  rejected             {self.rejected_rows} "
            f"({len(self.rejected)} problems)",
            f"date order             {self.date_order}",
            f"destinations created   {len(self.destinations_created)}",
            f"properties created     {len(self.properties_created)}",
            f"room types created     {self.room_types_created}",
            f"rates created          {self.rates_created}",
            f"rates updated          {self.rates_updated}",
            f"supplements created    {self.supplements_created}",
            f"  duplicate, skipped   {self.supplements_duplicate}",
            f"rack+NETT pairs merged {self.rack_net_merged}",
            f"unresolved conflicts   {len(self.conflicts)}",
            f"day-of-week variants   {len(self.label_variants)} (kept the higher)",
            f"VAT unstated           {sum(self.vat_unstated.values())} rows "
            "(read as inclusive)",
            f"currencies             {dict(self.currencies)}",
            f"committed              {self.committed}",
        ]
        return "\n".join(lines)


class RateIntakeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def import_sheet(
        self, path: str | Path, *, dry_run: bool = True
    ) -> IntakeReport:
        rows, skipped = read_sheet(path)
        report = IntakeReport(total_rows=len(rows), skipped=skipped)
        source = Path(path).name

        # Date order is a property of the file, decided from the file. Doing this
        # once over every date beats guessing per row: a single unambiguous date
        # anywhere settles the whole sheet.
        report.date_order = N.date_order(
            [N.clean(r["valid_from"]) for r in rows]
            + [N.clean(r["valid_to"]) for r in rows]
        )
        if report.date_order == "conflicting":
            report.warnings.append(
                "This sheet contains BOTH day-first and month-first dates. Every "
                "date is therefore suspect and nothing was imported."
            )
            return report
        if report.date_order == "ambiguous":
            report.warnings.append(
                "No date in this sheet has a component above 12, so day-first "
                "versus month-first cannot be told from the data. Read as "
                "day-first, per the template; check a few before relying on it."
            )

        meal_plans = {
            row.code: row
            for row in (await self.db.execute(select(MealPlan))).scalars().all()
        }
        residences = {
            row.key: row
            for row in (await self.db.execute(select(ResidenceCategory))).scalars().all()
        }

        report.near_duplicate_destinations = N.near_duplicates(
            [N.clean(r["destination"]) for r in rows]
        )

        capacity = self._capacities(rows, report)
        resolved = self._resolve(rows, report, meal_plans, residences, capacity)
        resolved = self._collapse_rack_net_pairs(resolved, report)

        if dry_run:
            return report

        await self._write(resolved, report, source, meal_plans, residences, capacity)
        await self.db.commit()
        report.committed = True
        return report

    # -- pass one: resolve and validate ------------------------------------- #

    def _capacities(self, rows: list[Row], report: IntakeReport) -> dict[tuple[str, str], int]:
        """Sleeping capacity per (property, room type).

        Only 416 of 3,016 rate rows in the client's workbook state
        ``room_sleeps``, but rooming arithmetic needs it: rooms are
        ``ceil(guests / capacity)``, so defaulting a six-sleeper suite to two
        would book three of them. Where it is stated it is used; otherwise the
        largest ``price_covers`` on that room type is the best available floor,
        and every derivation is reported because it is an inference about someone
        else's inventory.

        **Both columns are read as lower bounds, and the largest wins.** The
        obvious rule — trust ``room_sleeps`` where stated — is wrong against real
        data: in the client's workbook that column mirrors ``price_covers`` row
        by row (a single row says 1, the double row of the same room says 2)
        rather than stating the room's capacity. Treating either as authoritative
        produced two dozen false conflicts on one property. A room priced for
        two guests sleeps at least two; that is all either column proves, so the
        maximum across both is the honest floor.

        Erring low is the safe direction: too small a capacity books *more*
        rooms than needed, which over-quotes visibly, where too large books
        fewer and under-quotes silently.
        """
        capacity: dict[tuple[str, str], int] = {}
        from_sleeps: set[tuple[str, str]] = set()
        for row in rows:
            if N.row_kind(row) != "RATE":
                continue
            room = N.clean(row["room_type"])
            if not room:
                continue
            pair = (N.clean(row["property_name"]), room)
            for column in ("room_sleeps", "price_covers"):
                value = N.parse_int(row[column])
                if value:
                    capacity[pair] = max(capacity.get(pair, 0), value)
                    if column == "room_sleeps":
                        from_sleeps.add(pair)

        for pair, value in capacity.items():
            if pair not in from_sleeps:
                report.derived_capacity[f"{pair[0]} / {pair[1]}"] = value
        return capacity

    def _collapse_rack_net_pairs(
        self, resolved: list[dict[str, Any]], report: IntakeReport
    ) -> list[dict[str, Any]]:
        """Fold a rack row and its NETT twin into one rate carrying the discount.

        Rate sheets publish both figures, and agents transcribe both, so a room
        night arrives as two rows: "450, rack" and "360, sto — Published Agent
        NETT = rack less 20%". They are not duplicates and neither one alone is
        the right thing to store.

        §3.5 already models this as **one** row: the rack figure plus the stated
        percentage, from which the engine derives both what Heissal pays (the
        whole discount) and what the client is costed (half of it). So the pair
        is collapsed into exactly that, with the percentage derived from the two
        figures — `1 - nett/rack` — since the sheets state it in prose the
        `discount_percent` column never received.

        Keeping either row alone loses real money in opposite directions. The
        rack row alone quotes the client 450 and believes we pay 450, discarding
        the whole concession from the margin. The NETT row alone costs the client
        360 and hands them all of it. On this corpus that is 649 room-nights.

        Anything that is not a clean rack/NETT pair — three rates for one
        room-night, two rack rows — is a genuine conflict this importer must not
        resolve by picking one. Those are reported and left out.
        """
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        out: list[dict[str, Any]] = []
        for record in resolved:
            if record["kind"] != "RATE":
                out.append(record)
                continue
            groups[
                (
                    record["property_name"],
                    record["room_type"].casefold(),
                    record["meal_plan"],
                    record["residence"],
                    record["occupancy"],
                    record["starts"],
                    record["currency"],
                )
            ].append(record)

        for key, members in groups.items():
            if len(members) == 1:
                out.append(members[0])
                continue

            by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for member in members:
                by_kind[member["rate_kind"]].append(member)

            is_pair = (
                len(members) == 2
                and set(by_kind) == {"rack", "sto"}
                and len(by_kind["rack"]) == 1
                and len(by_kind["sto"]) == 1
            )
            if is_pair:
                rack, net = by_kind["rack"][0], by_kind["sto"][0]
                if rack["amount"] > 0 and 0 < net["amount"] <= rack["amount"]:
                    pct = (
                        (Decimal(1) - net["amount"] / rack["amount"]) * 100
                    ).quantize(Decimal("0.001"))
                    merged = dict(rack)
                    merged["discount_pct"] = pct
                    # The column holds three decimal places, so check the figure
                    # round-trips to the NETT the sheet published rather than
                    # assuming it does.
                    paid = (rack["amount"] * (Decimal(1) - pct / 100)).quantize(
                        Decimal("0.01")
                    )
                    if abs(paid - net["amount"]) > Decimal("0.01"):
                        report.warnings.append(
                            f"{key[0]} / {key[1]}: {pct}% off {rack['amount']} gives "
                            f"{paid}, but the sheet's NETT is {net['amount']} "
                            "(rounding, so what we pay is out by pennies)"
                        )
                    out.append(merged)
                    report.rack_net_merged += 1
                    continue

            # A distinction the sheet draws in prose that the schema has no
            # column for — a weeknight rate beside a weekend one, priced the
            # same way, on the same room-night. One Stop Nanyuki charges 10,000
            # Sunday to Thursday and 13,500 on Friday and Saturday.
            #
            # Dropping these makes the property unquotable. Keeping the first
            # depends on spreadsheet row order, and at One Stop that is the
            # cheaper figure, so every weekend stay would under-charge by 35%
            # with nothing to show it happened. Keeping the *highest* over-quotes
            # a weeknight visibly, where the agent can see the figure and correct
            # it against the sheet — the same reasoning as capacity inference.
            labels = {m["label"] for m in members}
            if len(by_kind) == 1 and len(labels) == len(members):
                kept = max(members, key=lambda m: m["amount"])
                spread = ", ".join(
                    f"{m['amount']} ({m['label']})"
                    for m in sorted(members, key=lambda m: m["amount"])
                )
                report.label_variants.append(
                    f"{key[0]} / {key[1]} / {key[2]} / occ{key[4]} / from {key[5]}: "
                    f"{spread} — kept {kept['amount']} {kept['currency']}"
                )
                out.append(kept)
                continue

            amounts = ", ".join(
                f"{m['amount']} {m['currency']} ({m['rate_kind']}, row {m['line']})"
                for m in sorted(members, key=lambda m: m["line"])
            )
            report.conflicts.append(
                f"{key[0]} / {key[1]} / {key[2]} / {key[3]} / occ{key[4]} / "
                f"from {key[5]}: {len(members)} rates — {amounts}"
            )
        return out

    def _resolve(
        self,
        rows: list[Row],
        report: IntakeReport,
        meal_plans: dict[str, MealPlan],
        residences: dict[str, ResidenceCategory],
        capacity: dict[tuple[str, str], int],
    ) -> list[dict[str, Any]]:
        order = "month_first" if report.date_order == "month_first" else "day_first"
        resolved: list[dict[str, Any]] = []

        for row in rows:
            line = row.line
            problems: list[Problem] = []

            # Bound as defaults rather than closed over: the loop rebinds both
            # each iteration, and a closure over them is a bug waiting for
            # someone to defer the call.
            def fail(
                field_name: str,
                message: str,
                _into: list[Problem] = problems,
                _line: int = line,
                _row: Row = row,
            ) -> None:
                _into.append(
                    Problem(_line, field_name, message, N.clean(_row["property_name"]))
                )

            kind = N.row_kind(row)
            if kind not in N.ROW_TYPES:
                fail("row_type", f"{row['row_type']!r} is not RATE, SUPPLEMENT or EXTRA")

            property_name = N.clean(row["property_name"])
            if not property_name:
                fail("property_name", "required")
            destination = N.clean(row["destination"])
            if not destination:
                fail("destination", "required")

            starts = N.parse_date(row["valid_from"], order=order)
            ends = N.parse_date(row["valid_to"], order=order)
            if starts is None:
                fail("valid_from", "missing or unreadable — a price needs a window")
            if ends is None:
                fail("valid_to", "missing or unreadable — a price needs a window")
            if starts and ends and ends < starts:
                fail("valid_to", f"{ends} is before {starts}")

            currency = N.clean(row["currency"]).upper()
            if len(currency) != 3:
                fail("currency", f"{row['currency']!r} is not a 3-letter code")
            amount = N.parse_amount(row["amount"])
            if amount is None:
                fail("amount", f"{row['amount']!r} is not a usable figure")

            charged_per = N.clean(row["charged_per"]).lower()
            vat_inclusive = N.key(row["vat"]) != "EXCLUSIVE"
            vat_pct = DEFAULT_VAT_PCT
            if N.key(row["vat"]) not in {"INCLUSIVE", "EXCLUSIVE"}:
                # Read as inclusive, per the client's decision of 2026-09-02, but
                # counted. 45% of the corpus states no VAT position at all, and a
                # 16% assumption on 1,335 rows should not be invisible just
                # because it is the agreed one.
                report.vat_unstated[property_name] += 1

            record: dict[str, Any] = {
                "line": line,
                "kind": kind,
                "property_name": property_name,
                "destination": destination,
                "label": N.clean(row["label"]) or "Standard",
                "starts": starts,
                "ends": ends,
                "currency": currency,
                "amount": amount,
                "vat_inclusive": vat_inclusive,
                "vat_pct": vat_pct,
                "notes": N.clean(row["notes"]),
            }

            if kind == "RATE":
                room_type = N.clean(row["room_type"])
                if not room_type:
                    fail("room_type", "required on a RATE row")
                plan = N.meal_plan_code(row["meal_plan"])
                if plan is None:
                    fail("meal_plan", f"{row['meal_plan']!r} is not a known meal plan")
                elif plan not in meal_plans:
                    fail("meal_plan", f"{plan} is not seeded in this database")
                residence = N.residence_key(row["guest_residence"])
                if residence is None:
                    fail(
                        "guest_residence",
                        f"{row['guest_residence']!r} is not a known residence category",
                    )
                elif residence not in residences:
                    fail("guest_residence", f"{residence} is not seeded in this database")
                occupancy = N.parse_int(row["price_covers"])
                if not occupancy:
                    fail("price_covers", "required — a rate is meaningless without it")
                basis = N.RATE_BASES.get(charged_per)
                if basis is None:
                    fail(
                        "charged_per",
                        f"{charged_per!r} cannot be a nightly rate; use "
                        "room_per_night or person_per_night",
                    )
                record.update(
                    room_type=room_type,
                    meal_plan=plan,
                    residence=residence,
                    occupancy=occupancy,
                    basis=basis,
                    rate_kind=(N.clean(row["rack_or_sto"]).lower() or "rack"),
                    discount_pct=N.parse_percent(row["discount_percent"]),
                    child_amount=N.parse_amount(row["child_amount"]),
                    child_ages=N.parse_child_ages(row["child_ages"]),
                    min_nights=N.parse_int(row["min_nights"]),
                    capacity=capacity.get((property_name, room_type)),
                )
                if record["rate_kind"] not in {"rack", "sto"}:
                    fail("rack_or_sto", f"{row['rack_or_sto']!r} is not rack or sto")
                if N.clean(row["child_ages"]) and record["child_ages"] is None:
                    report.warnings.append(
                        f"row {line}: child_ages {row['child_ages']!r} not understood; "
                        "the child rate on this row was dropped"
                    )
                    record["child_amount"] = None
            else:
                # The label is what a client reads — "Christmas Eve supplement".
                # Defaulting it the way a rate's season name is defaulted would
                # print the word "Standard" on a proposal, and it also collapsed
                # distinct extras onto one natural key, dropping 43 of them
                # without a word. An unnamed extra is not offerable.
                if not N.clean(row["label"]):
                    fail("label", f"required on a {kind} row - it is the name shown")
                basis = N.SUPPLEMENT_BASES.get(charged_per)
                if basis is None:
                    fail("charged_per", f"{charged_per!r} is not a supplement basis")
                record.update(
                    basis=basis,
                    is_mandatory=(kind == "SUPPLEMENT"),
                    room_type=N.clean(row["room_type"]) or None,
                    meal_plan=N.meal_plan_code(row["meal_plan"]),
                    residence=N.residence_key(row["guest_residence"]),
                )

            if problems:
                report.rejected.extend(problems)
                continue
            report.currencies[currency] += 1
            resolved.append(record)

        return resolved

    # -- pass two: write ---------------------------------------------------- #

    async def _write(
        self,
        resolved: list[dict[str, Any]],
        report: IntakeReport,
        source: str,
        meal_plans: dict[str, MealPlan],
        residences: dict[str, ResidenceCategory],
        capacity: dict[tuple[str, str], int],
    ) -> None:
        destinations = await self._destinations(resolved, report)
        properties = await self._properties(resolved, destinations, report)
        rooms = await self._room_types(resolved, properties, capacity, report)

        # Existing rates for the properties being touched, keyed on the natural
        # key, so a re-import corrects rather than duplicating or colliding with
        # the uniqueness constraint.
        existing = await self._existing_rates(set(properties.values()))
        seen: set[tuple[Any, ...]] = set()

        for record in resolved:
            if record["kind"] != "RATE":
                continue
            accommodation_id = properties[record["property_name"]]
            room_id = rooms[(record["property_name"], record["room_type"])]
            plan_id = meal_plans[record["meal_plan"]].id
            residence_id = residences[record["residence"]].id

            amount = record["amount"]
            if record["basis"] == "per_person":
                # The catalogue stores rates per room; a per-person-sharing sheet
                # is converted here, once, where the occupancy it was quoted at
                # is known. Doing it in the spreadsheet is where it gets done
                # twice or not at all.
                amount = amount * record["occupancy"]
            amount = to_vat_inclusive(
                amount,
                vat_inclusive=record["vat_inclusive"],
                vat_pct=record["vat_pct"],
            )
            child = record["child_amount"]
            if child is not None:
                if record["basis"] == "per_person":
                    child = child * record["occupancy"]
                child = to_vat_inclusive(
                    child,
                    vat_inclusive=record["vat_inclusive"],
                    vat_pct=record["vat_pct"],
                )

            natural = (
                room_id,
                plan_id,
                residence_id,
                record["occupancy"],
                record["starts"],
                record["currency"],
            )
            if natural in seen:
                report.warnings.append(
                    f"row {record['line']}: still collides on room, plan, residence, "
                    "occupancy, start date and currency after pair-merging; "
                    "kept the first"
                )
                continue
            seen.add(natural)

            ages = record["child_ages"] or (None, None)
            values = {
                "season_name": record["label"],
                "effective_to": record["ends"],
                "currency": record["currency"],
                "rate_per_night": amount,
                "child_rate": child,
                "child_min_age": ages[0],
                "child_max_age": ages[1],
                "min_nights": record["min_nights"],
                "rate_kind": record["rate_kind"],
                "supplier_discount_pct": record["discount_pct"],
                "vat_inclusive": True,
                "vat_pct": record["vat_pct"],
            }
            row = existing.get(natural)
            if row is not None:
                changed = any(getattr(row, k) != v for k, v in values.items())
                if changed:
                    for k, v in values.items():
                        setattr(row, k, v)
                    report.rates_updated += 1
                continue

            self.db.add(
                AccommodationRate(
                    accommodation_id=accommodation_id,
                    room_type_id=room_id,
                    meal_plan_id=plan_id,
                    residence_category_id=residence_id,
                    occupancy=record["occupancy"],
                    effective_from=record["starts"],
                    **values,
                )
            )
            report.rates_created += 1

        await self._write_supplements(
            resolved, properties, rooms, meal_plans, residences, report, source
        )

    async def _destinations(
        self, resolved: list[dict[str, Any]], report: IntakeReport
    ) -> dict[str, uuid.UUID]:
        wanted = {r["destination"] for r in resolved}
        rows = (await self.db.execute(select(Destination))).scalars().all()
        by_slug = {row.slug: row for row in rows}
        out: dict[str, uuid.UUID] = {}
        for name in sorted(wanted):
            slug = N.slugify(name)
            row = by_slug.get(slug)
            if row is None:
                row = Destination(
                    name=name,
                    slug=slug,
                    type="other",
                    country=N.destination_country(name),
                )
                self.db.add(row)
                await self.db.flush()
                by_slug[slug] = row
                report.destinations_created.append(name)
            out[name] = row.id
        return out

    async def _properties(
        self,
        resolved: list[dict[str, Any]],
        destinations: dict[str, uuid.UUID],
        report: IntakeReport,
    ) -> dict[str, uuid.UUID]:
        pairs = {(r["property_name"], r["destination"]) for r in resolved}
        rows = (await self.db.execute(select(Accommodation))).scalars().all()
        by_slug = {row.slug: row for row in rows}
        out: dict[str, uuid.UUID] = {}
        for name, destination in sorted(pairs):
            slug = N.slugify(name)
            row = by_slug.get(slug)
            if row is None:
                row = Accommodation(
                    name=name,
                    slug=slug,
                    destination_id=destinations[destination],
                    category="hotel",
                )
                self.db.add(row)
                await self.db.flush()
                by_slug[slug] = row
                report.properties_created.append(name)
            out[name] = row.id
        return out

    async def _room_types(
        self,
        resolved: list[dict[str, Any]],
        properties: dict[str, uuid.UUID],
        capacity: dict[tuple[str, str], int],
        report: IntakeReport,
    ) -> dict[tuple[str, str], uuid.UUID]:
        pairs = {
            (r["property_name"], r["room_type"])
            for r in resolved
            if r["kind"] == "RATE"
        }
        rows = (await self.db.execute(select(RoomType))).scalars().all()
        by_key = {(row.accommodation_id, row.name.casefold()): row for row in rows}
        out: dict[tuple[str, str], uuid.UUID] = {}
        for property_name, room_name in sorted(pairs):
            accommodation_id = properties[property_name]
            row = by_key.get((accommodation_id, room_name.casefold()))
            if row is None:
                row = RoomType(
                    accommodation_id=accommodation_id,
                    name=room_name,
                    max_occupancy=capacity.get((property_name, room_name)) or 2,
                )
                self.db.add(row)
                await self.db.flush()
                by_key[(accommodation_id, room_name.casefold())] = row
                report.room_types_created += 1
            out[(property_name, room_name)] = row.id
        return out

    async def _existing_rates(
        self, accommodation_ids: set[uuid.UUID]
    ) -> dict[tuple[Any, ...], AccommodationRate]:
        if not accommodation_ids:
            return {}
        rows = (
            (
                await self.db.execute(
                    select(AccommodationRate).where(
                        AccommodationRate.accommodation_id.in_(accommodation_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        return {
            (
                row.room_type_id,
                row.meal_plan_id,
                row.residence_category_id,
                row.occupancy,
                row.effective_from,
                row.currency,
            ): row
            for row in rows
        }

    async def _write_supplements(
        self,
        resolved: list[dict[str, Any]],
        properties: dict[str, uuid.UUID],
        rooms: dict[tuple[str, str], uuid.UUID],
        meal_plans: dict[str, MealPlan],
        residences: dict[str, ResidenceCategory],
        report: IntakeReport,
        source: str,
    ) -> None:
        existing = {
            (row.accommodation_id, row.label, row.effective_from)
            for row in (
                await self.db.execute(select(AccommodationSupplement))
            ).scalars().all()
        }
        for record in resolved:
            if record["kind"] == "RATE":
                continue
            accommodation_id = properties[record["property_name"]]
            natural = (accommodation_id, record["label"], record["starts"])
            if natural in existing:
                report.supplements_duplicate += 1
                report.warnings.append(
                    f"row {record['line']}: a charge called {record['label']!r} "
                    f"already starts {record['starts']} at {record['property_name']}; "
                    "this row was not added"
                )
                continue
            existing.add(natural)
            room_key = (record["property_name"], record["room_type"] or "")
            self.db.add(
                AccommodationSupplement(
                    accommodation_id=accommodation_id,
                    room_type_id=rooms.get(room_key),
                    meal_plan_id=(
                        meal_plans[record["meal_plan"]].id
                        if record["meal_plan"] in meal_plans
                        else None
                    ),
                    residence_category_id=(
                        residences[record["residence"]].id
                        if record["residence"] in residences
                        else None
                    ),
                    label=record["label"],
                    kind="festive" if record["is_mandatory"] else "extra",
                    basis=record["basis"],
                    amount=to_vat_inclusive(
                        record["amount"],
                        vat_inclusive=record["vat_inclusive"],
                        vat_pct=record["vat_pct"],
                    ),
                    currency=record["currency"],
                    vat_inclusive=True,
                    vat_pct=record["vat_pct"],
                    effective_from=record["starts"],
                    effective_to=record["ends"],
                    is_mandatory=record["is_mandatory"],
                )
            )
            report.supplements_created += 1

    # -- reporting ---------------------------------------------------------- #

    @staticmethod
    def missing_fx(report: IntakeReport, known: set[tuple[str, str]]) -> list[str]:
        """Currencies in the sheet with no conversion into KES on file.

        A property quoted in a currency the engine cannot convert raises at
        pricing time, not at import time, so it is worth saying now.
        """
        out = []
        for currency in report.currencies:
            if currency != "KES" and (currency, "KES") not in known:
                out.append(currency)
        return sorted(out)


def group_rejections(report: IntakeReport) -> dict[str, list[Problem]]:
    """Rejections by field, since three thousand rows fail in a handful of ways."""
    grouped: dict[str, list[Problem]] = defaultdict(list)
    for problem in report.rejected:
        grouped[problem.field].append(problem)
    return dict(grouped)


def rejections_by_property(report: IntakeReport) -> dict[str, Counter[str]]:
    """Which properties need work, and on which columns.

    This is the view an agent can act on: "Swahili Beach is missing dates on 96
    rows" sends somebody back to one rate sheet, where a list of row numbers
    spanning nine properties sends them nowhere.
    """
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for problem in report.rejected:
        out[problem.property_name or "(no property named)"][problem.field] += 1
    return dict(out)


def fx_pairs_on_file(rows: list[tuple[str, str]]) -> set[tuple[str, str]]:
    return set(rows)


__all__ = [
    "IntakeReport",
    "Problem",
    "RateIntakeService",
    "date",
    "Decimal",
    "fx_pairs_on_file",
    "group_rejections",
]
