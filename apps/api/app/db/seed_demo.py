"""Demo / test catalogue data (Stage 3.1).

Why this exists
---------------
Tests were building an entire scenario per test through the HTTP API — roughly a
dozen round trips each, which against a hosted database costs ~100 seconds per
test. Seeding one realistic dataset once and asserting against it makes the suite
both faster and better: the fixture covers cases nobody would hand-build every
time (an STO rate beside a discounted rack rate, a property with no full-board
rate, a 4-guest villa, resident *and* non-resident pricing, activity price
tiers, rail fares and per-vehicle transfer prices).

**The property names here are invented.** They are shaped like the real reference
quotation but the rates are synthetic, so this data can never be mistaken for a
supplier's actual pricing. Real rates arrive through the Stage 3.2 ingestion
pipeline — extracted from hotel documents and confirmed by a person — never from
a seed script.

Idempotent: re-running matches on slug / natural key and skips what exists, so it
is safe to call before every test session.

Run it directly with::

    python -m app.db.seed_demo --yes

It prints the target database first and refuses without ``--yes``, because
loading invented hotel rates into the live catalogue is exactly the accident that
would put a fabricated price in front of a client.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.modules.accommodations.models import (
    Accommodation,
    AccommodationRate,
    MealPlan,
    RoomType,
)
from app.modules.activities.models import Activity, ActivityPriceTier, ActivityRate
from app.modules.destinations.models import Destination
from app.modules.park_fees.models import ParkFee
from app.modules.residence.models import ResidenceCategory
from app.modules.transport.models import DestinationTransportMode, TransferRate
from app.modules.vehicles.models import FuelPrice, Vehicle

# A wide season so any test date inside 2026 resolves.
SEASON_FROM = date(2026, 1, 1)
SEASON_TO = date(2026, 12, 31)

DEMO_MARKER = "demo-sample"


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-").replace("&", "and").replace("--", "-")


async def _get_or_create(
    db: AsyncSession, model: Any, where: Any, **defaults: Any
) -> Any:
    """Fetch a row by a natural key, creating it from ``defaults`` if absent."""
    existing = (await db.execute(select(model).where(where))).scalar_one_or_none()
    if existing is not None:
        return existing
    row = model(**defaults)
    db.add(row)
    await db.flush()
    return row


async def seed_demo(db: AsyncSession) -> dict[str, Any]:
    """Load the demo catalogue and return the ids tests need."""
    residence = {
        r.key: r for r in (await db.execute(select(ResidenceCategory))).scalars().all()
    }
    meal_plans = {m.code: m for m in (await db.execute(select(MealPlan))).scalars().all()}
    if not residence or not meal_plans:
        raise RuntimeError("Run `python -m app.db.seed` first — base reference data missing.")

    citizen = residence["citizen"]
    non_resident = residence["non_resident"]
    bb, hb, fb = meal_plans["BB"], meal_plans["HB"], meal_plans["FB"]

    # --- Destinations: a coastal area and a park, the two shapes in use ------ #
    diani = await _get_or_create(
        db,
        Destination,
        Destination.slug == "demo-diani",
        name="Diani (demo)",
        slug="demo-diani",
        type="beach",
        country="Kenya",
        region="Coast",
    )
    mara = await _get_or_create(
        db,
        Destination,
        Destination.slug == "demo-mara-reserve",
        name="Mara Reserve (demo)",
        slug="demo-mara-reserve",
        type="reserve",
        country="Kenya",
        region="Rift Valley",
    )

    # --- Properties, each chosen to exercise one rule ------------------------ #
    # 1. Full board on an STO rate — the preferred rate kind, no discount maths.
    # 2. Rack rate carrying a stated 15% discount — half of it reaches the client.
    # 3. Bed & breakfast only — forces the FB -> HB -> BB fallback chain.
    # 4. A 4-guest villa — rooming by capacity rather than twin-sharing.
    properties: dict[str, Accommodation] = {}
    for name, category in (
        ("Coral Sands Resort", "resort"),
        ("Baobab Beach Lodge", "lodge"),
        ("Kaskazi Guest House", "guesthouse"),
        ("Pendo Demo Villas", "villa"),
    ):
        properties[name] = await _get_or_create(
            db,
            Accommodation,
            Accommodation.slug == _slug(f"demo {name}"),
            name=f"{name} (demo)",
            slug=_slug(f"demo {name}"),
            destination_id=diani.id,
            category=category,
            blurb=f"{name} is invented demo data used by the test suite.",
        )

    def _room(prop: Accommodation, label: str, occupancy: int) -> dict[str, Any]:
        return {
            "accommodation_id": prop.id,
            "name": label,
            "code": label[:3].upper(),
            "max_occupancy": occupancy,
        }

    rooms: dict[str, RoomType] = {}
    for key, prop, label, occupancy in (
        ("coral_twin", properties["Coral Sands Resort"], "Twin", 2),
        ("coral_superior", properties["Coral Sands Resort"], "Superior Twin", 2),
        ("baobab_twin", properties["Baobab Beach Lodge"], "Twin", 2),
        ("kaskazi_twin", properties["Kaskazi Guest House"], "Twin", 2),
        ("villa_two_bed", properties["Pendo Demo Villas"], "Two Bedroom", 4),
    ):
        rooms[key] = await _get_or_create(
            db,
            RoomType,
            (RoomType.accommodation_id == prop.id) & (RoomType.name == label),
            **_room(prop, label, occupancy),
        )

    async def _rate(
        room: RoomType,
        plan: MealPlan,
        rc: ResidenceCategory,
        amount: str,
        *,
        kind: str = "rack",
        discount: str | None = None,
        currency: str = "KES",
        child: str | None = None,
        child_bounds: tuple[int, int] | None = None,
    ) -> AccommodationRate:
        return await _get_or_create(
            db,
            AccommodationRate,
            (AccommodationRate.room_type_id == room.id)
            & (AccommodationRate.meal_plan_id == plan.id)
            & (AccommodationRate.residence_category_id == rc.id)
            & (AccommodationRate.effective_from == SEASON_FROM),
            accommodation_id=room.accommodation_id,
            room_type_id=room.id,
            meal_plan_id=plan.id,
            residence_category_id=rc.id,
            season_name=f"{DEMO_MARKER} standard",
            effective_from=SEASON_FROM,
            effective_to=SEASON_TO,
            currency=currency,
            rate_per_night=Decimal(amount),
            child_rate=Decimal(child) if child else None,
            # Stage 3 provenance: VAT-inclusive by default, rate kind, and the
            # supplier discount left un-applied so pricing halves it later.
            vat_inclusive=True,
            vat_pct=Decimal("16"),
            rate_kind=kind,
            supplier_discount_pct=Decimal(discount) if discount else None,
            child_min_age=child_bounds[0] if child_bounds else None,
            child_max_age=child_bounds[1] if child_bounds else None,
        )

    # Coral Sands: STO full board (preferred), plus a cheaper twin so
    # "cheapest within the hotel" has something to choose between.
    await _rate(rooms["coral_twin"], fb, citizen, "9000", kind="sto")
    await _rate(rooms["coral_superior"], fb, citizen, "12500", kind="sto")
    # Non-resident pricing is materially higher — the gap tests §3.6a.
    await _rate(rooms["coral_twin"], fb, non_resident, "180", kind="sto", currency="USD")

    # Baobab: rack rate with a stated 15% discount, and a child policy where a
    # child is 3–11 (over 11 pays adult).
    await _rate(
        rooms["baobab_twin"], fb, citizen, "24000",
        kind="rack", discount="15", child="12000", child_bounds=(3, 11),
    )

    # Kaskazi: bed & breakfast ONLY — no FB or HB row exists, so a full-board
    # request must fall through the chain and land on BB + chef cost.
    await _rate(rooms["kaskazi_twin"], bb, citizen, "6000")

    # Pendo villas: half board on a 4-guest unit.
    await _rate(rooms["villa_two_bed"], hb, citizen, "16000")

    # --- Park fees: per day of stay, resident and non-resident -------------- #
    for rc, amount, currency in ((citizen, "1200", "KES"), (non_resident, "70", "USD")):
        await _get_or_create(
            db,
            ParkFee,
            (ParkFee.destination_id == mara.id)
            & (ParkFee.residence_category_id == rc.id)
            & (ParkFee.effective_from == SEASON_FROM),
            destination_id=mara.id,
            fee_type="park_entry",
            residence_category_id=rc.id,
            currency=currency,
            adult=Decimal(amount),
            child=Decimal(amount) / 2,
            infant=Decimal("0"),
            child_min_age=3,
            child_max_age=11,
            effective_from=SEASON_FROM,
            effective_to=SEASON_TO,
        )

    # --- Activities: one mandatory, one optional with a duration ladder ----- #
    boat = await _get_or_create(
        db,
        Activity,
        Activity.slug == "demo-dhow-cruise",
        name="Dhow sunset cruise (demo)",
        slug="demo-dhow-cruise",
        destination_id=diani.id,
        duration_minutes=120,
        is_optional=False,
        is_mandatory=True,
    )
    quad = await _get_or_create(
        db,
        Activity,
        Activity.slug == "demo-quad-biking",
        name="Quad biking (demo)",
        slug="demo-quad-biking",
        destination_id=diani.id,
        is_optional=True,
        has_own_section=True,
    )
    for activity, adult, child in ((boat, "3500", "1800"), (quad, "2500", "2500")):
        await _get_or_create(
            db,
            ActivityRate,
            (ActivityRate.activity_id == activity.id)
            & (ActivityRate.residence_category_id == citizen.id)
            & (ActivityRate.effective_from == SEASON_FROM),
            activity_id=activity.id,
            residence_category_id=citizen.id,
            currency="KES",
            adult_price=Decimal(adult),
            child_price=Decimal(child),
            effective_from=SEASON_FROM,
            effective_to=SEASON_TO,
        )
    # The timed ladder the quotation renders as a small table.
    for label, minutes, price, order in (
        ("10 minutes", 10, "1500", 1),
        ("15 minutes", 15, "2000", 2),
        ("30 minutes", 30, "3500", 3),
    ):
        await _get_or_create(
            db,
            ActivityPriceTier,
            (ActivityPriceTier.activity_id == quad.id)
            & (ActivityPriceTier.residence_category_id == citizen.id)
            & (ActivityPriceTier.label == label)
            & (ActivityPriceTier.effective_from == SEASON_FROM),
            activity_id=quad.id,
            residence_category_id=citizen.id,
            label=label,
            duration_minutes=minutes,
            price=Decimal(price),
            currency="KES",
            effective_from=SEASON_FROM,
            effective_to=SEASON_TO,
            sort_order=order,
        )

    # --- Fleet + fuel (Stage 2 costing, still used for game drives) --------- #
    fuel_type = "demo-diesel"
    coaster = await _get_or_create(
        db,
        Vehicle,
        Vehicle.slug == "demo-coaster",
        name="Coaster 25-seater (demo)",
        slug="demo-coaster",
        vehicle_type="minibus",
        passenger_capacity=25,
        fuel_type=fuel_type,
        fuel_consumption_kmpl=Decimal("6"),
        daily_operating_cost=Decimal("4000"),
        driver_cost_per_day=Decimal("3000"),
        currency="KES",
    )
    await _get_or_create(
        db,
        FuelPrice,
        (FuelPrice.fuel_type == fuel_type) & (FuelPrice.effective_from == SEASON_FROM),
        fuel_type=fuel_type,
        price_per_litre=Decimal("185"),
        currency="KES",
        effective_from=SEASON_FROM,
    )

    # --- Rail fares and transfer prices (Stage 3.8) ------------------------- #
    # Seeded at the stated current fares; rows, not constants, because fares move.
    for travel_class, fare in (("economy", "1500"), ("business", "12000")):
        await _get_or_create(
            db,
            DestinationTransportMode,
            (DestinationTransportMode.destination_id == diani.id)
            & (DestinationTransportMode.mode == "rail")
            & (DestinationTransportMode.travel_class == travel_class)
            & (DestinationTransportMode.effective_from == SEASON_FROM),
            destination_id=diani.id,
            mode="rail",
            travel_class=travel_class,
            label=f"SGR {travel_class} (demo)",
            cost_basis="per_person",
            price=Decimal(fare),
            currency="KES",
            effective_from=SEASON_FROM,
        )
    # Same leg, two vehicle types, two prices — the §3.8 rule in data form.
    for vehicle_type, price in (("minibus", "12000"), ("saloon", "4500")):
        await _get_or_create(
            db,
            TransferRate,
            (TransferRate.destination_id == diani.id)
            & (TransferRate.vehicle_type == vehicle_type)
            & (TransferRate.route_label == "Terminus to hotel")
            & (TransferRate.effective_from == SEASON_FROM),
            destination_id=diani.id,
            vehicle_type=vehicle_type,
            route_label="Terminus to hotel",
            price_per_leg=Decimal(price),
            currency="KES",
            effective_from=SEASON_FROM,
        )

    await db.commit()

    return {
        "destination_diani": str(diani.id),
        "destination_mara": str(mara.id),
        "residence_citizen": str(citizen.id),
        "residence_non_resident": str(non_resident.id),
        "meal_plan_bb": str(bb.id),
        "meal_plan_hb": str(hb.id),
        "meal_plan_fb": str(fb.id),
        "acc_sto_full_board": str(properties["Coral Sands Resort"].id),
        "acc_rack_discounted": str(properties["Baobab Beach Lodge"].id),
        "acc_bb_only": str(properties["Kaskazi Guest House"].id),
        "acc_villa": str(properties["Pendo Demo Villas"].id),
        "room_coral_twin": str(rooms["coral_twin"].id),
        "room_coral_superior": str(rooms["coral_superior"].id),
        "room_baobab_twin": str(rooms["baobab_twin"].id),
        "room_kaskazi_twin": str(rooms["kaskazi_twin"].id),
        "room_villa": str(rooms["villa_two_bed"].id),
        "activity_mandatory": str(boat.id),
        "activity_optional": str(quad.id),
        "vehicle_coaster": str(coaster.id),
        "fuel_type": fuel_type,
        "season_from": SEASON_FROM.isoformat(),
        "season_to": SEASON_TO.isoformat(),
    }


async def _main() -> None:
    uri = settings.sqlalchemy_sync_uri
    target = uri.split("@")[-1].split("?")[0]
    print(f"[seed_demo] target database: {target}")
    if "--yes" not in sys.argv:
        print(
            "[seed_demo] refusing to run without --yes.\n"
            "            This loads INVENTED hotel rates. Never load it into the\n"
            "            live catalogue: a fabricated rate could reach a client."
        )
        raise SystemExit(1)
    async with AsyncSessionLocal() as db:
        ids = await seed_demo(db)
    print(f"[seed_demo] done — {len(ids)} reference ids available")


if __name__ == "__main__":
    asyncio.run(_main())
