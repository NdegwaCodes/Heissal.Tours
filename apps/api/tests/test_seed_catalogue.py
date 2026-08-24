"""Stage 3.1 — the seeded catalogue is what the tests think it is.

These assert the *shape* of the seeded data, so a later change to `seed_demo`
that quietly drops a case (the bed-and-breakfast-only property, say, or the
non-resident rate) fails here rather than silently weakening every test that
depends on it.

They also exercise the new Stage 3 columns and tables end to end through the
ORM: rate provenance, child policy, activity price tiers, rail fares and
per-vehicle transfer prices.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.modules.accommodations.models import AccommodationRate, RoomType
from app.modules.activities.models import ActivityPriceTier
from app.modules.transport.models import DestinationTransportMode, TransferRate

pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal


async def test_seed_is_idempotent(sample_catalogue):
    """Re-seeding must reuse the same rows, not duplicate the catalogue."""
    from app.db.seed_demo import seed_demo

    async with AsyncSessionLocal() as db:
        second = await seed_demo(db)
    assert second == sample_catalogue


async def test_sto_and_discounted_rack_rates_coexist(sample_catalogue):
    """The two rate kinds price differently, so both must be present."""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(AccommodationRate).where(
                    AccommodationRate.room_type_id.in_(
                        [
                            sample_catalogue["room_coral_twin"],
                            sample_catalogue["room_baobab_twin"],
                        ]
                    )
                )
            )
        ).scalars().all()

    by_kind = {r.rate_kind for r in rows}
    assert by_kind == {"sto", "rack"}

    sto = next(r for r in rows if r.rate_kind == "sto")
    rack = next(r for r in rows if r.rate_kind == "rack")

    # Every seeded rate is VAT-inclusive at 16%, so nothing gets taxed twice.
    assert all(r.vat_inclusive for r in rows)
    assert all(r.vat_pct == D("16") for r in rows)

    # An STO rate carries no discount to halve; the rack rate carries 15% as
    # stated by the supplier, stored un-applied.
    assert sto.supplier_discount_pct is None
    assert rack.supplier_discount_pct == D("15.000")
    # The halved figure is derived, never stored: 24 000 -> 22 200 at pricing.
    assert rack.rate_per_night == D("24000.0000")


async def test_child_policy_is_per_property(sample_catalogue):
    """Bounds live on the rate; absence means charge as an adult."""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(AccommodationRate).where(
                    AccommodationRate.room_type_id.in_(
                        [
                            sample_catalogue["room_baobab_twin"],
                            sample_catalogue["room_coral_twin"],
                        ]
                    )
                )
            )
        ).scalars().all()

    with_policy = [r for r in rows if r.child_min_age is not None]
    without_policy = [r for r in rows if r.child_min_age is None]
    assert with_policy and without_policy, "need both cases to test the default"

    policy = with_policy[0]
    assert (policy.child_min_age, policy.child_max_age) == (3, 11)
    assert policy.child_rate == D("12000.0000")
    # Silence on children is the common case and must stay expressible.
    assert without_policy[0].child_rate is None


async def test_resident_and_non_resident_rates_both_exist(sample_catalogue):
    """The gap is large, so a missing category must not fall back silently."""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(AccommodationRate).where(
                    AccommodationRate.room_type_id == sample_catalogue["room_coral_twin"]
                )
            )
        ).scalars().all()

    by_rc = {str(r.residence_category_id): r for r in rows}
    assert sample_catalogue["residence_citizen"] in by_rc
    assert sample_catalogue["residence_non_resident"] in by_rc
    # Different categories, different currencies — the reason FX exists.
    assert by_rc[sample_catalogue["residence_citizen"]].currency == "KES"
    assert by_rc[sample_catalogue["residence_non_resident"]].currency == "USD"


async def test_bed_and_breakfast_only_property_has_no_board_rates(sample_catalogue):
    """This property is what forces the FB -> HB -> BB fallback chain."""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(AccommodationRate).where(
                    AccommodationRate.room_type_id == sample_catalogue["room_kaskazi_twin"]
                )
            )
        ).scalars().all()

    assert len(rows) == 1
    assert str(rows[0].meal_plan_id) == sample_catalogue["meal_plan_bb"]


async def test_villa_capacity_drives_rooming(sample_catalogue):
    """A 4-guest villa needs 7 units for 25 pax; a twin needs 13."""
    async with AsyncSessionLocal() as db:
        villa = (
            await db.execute(
                select(RoomType).where(RoomType.id == sample_catalogue["room_villa"])
            )
        ).scalar_one()
        twin = (
            await db.execute(
                select(RoomType).where(RoomType.id == sample_catalogue["room_coral_twin"])
            )
        ).scalar_one()

    assert villa.max_occupancy == 4
    assert twin.max_occupancy == 2
    # ceil(pax / capacity) — the rule from design §3.3, checked against the
    # numbers in the reference quotation.
    assert -(-25 // villa.max_occupancy) == 7
    assert -(-25 // twin.max_occupancy) == 13


async def test_activity_price_tiers_form_an_ascending_ladder(sample_catalogue):
    """Timed activities are sold as a ladder; order and prices must both hold."""
    async with AsyncSessionLocal() as db:
        tiers = (
            await db.execute(
                select(ActivityPriceTier)
                .where(ActivityPriceTier.activity_id == sample_catalogue["activity_optional"])
                .order_by(ActivityPriceTier.sort_order)
            )
        ).scalars().all()

    assert [t.label for t in tiers] == ["10 minutes", "15 minutes", "30 minutes"]
    assert [t.duration_minutes for t in tiers] == [10, 15, 30]
    prices = [t.price for t in tiers]
    assert prices == sorted(prices), "a longer ride must not cost less"
    assert all(t.vat_inclusive for t in tiers)


async def test_rail_fares_are_rows_not_constants(sample_catalogue):
    """SGR economy 1 500 / business 12 000 per person, one way, effective-dated."""
    async with AsyncSessionLocal() as db:
        modes = (
            await db.execute(
                select(DestinationTransportMode).where(
                    DestinationTransportMode.destination_id
                    == sample_catalogue["destination_diani"]
                )
            )
        ).scalars().all()

    fares = {m.travel_class: m for m in modes}
    assert fares["economy"].price == D("1500.0000")
    assert fares["business"].price == D("12000.0000")
    for m in modes:
        assert m.mode == "rail"
        assert m.cost_basis == "per_person"
        assert m.effective_from == date(2026, 1, 1)
    # A return journey is two of these segments.
    assert fares["economy"].price * 25 * 2 == D("75000")


async def test_air_is_never_an_offerable_mode(sample_catalogue):
    """Heissal cannot ticket flights, so no seeded mode may be air."""
    async with AsyncSessionLocal() as db:
        modes = (await db.execute(select(DestinationTransportMode))).scalars().all()
    assert modes, "expected seeded transport modes"
    assert all(m.mode != "air" for m in modes)


async def test_transfer_price_depends_on_vehicle_type(sample_catalogue):
    """Same leg, different vehicle, different price — the §3.8 rule."""
    async with AsyncSessionLocal() as db:
        rates = (
            await db.execute(
                select(TransferRate).where(
                    TransferRate.destination_id == sample_catalogue["destination_diani"],
                    TransferRate.route_label == "Terminus to hotel",
                )
            )
        ).scalars().all()

    by_vehicle = {r.vehicle_type: r.price_per_leg for r in rates}
    assert by_vehicle["minibus"] == D("12000.0000")
    assert by_vehicle["saloon"] == D("4500.0000")
    assert by_vehicle["minibus"] != by_vehicle["saloon"]
    # route_label defaults to '' rather than NULL so the uniqueness constraint
    # over (destination, vehicle_type, route_label, effective_from) actually bites.
    assert all(r.route_label for r in rates)
