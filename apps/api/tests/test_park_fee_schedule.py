"""The real KWS and Maasai Mara entry fees, asserted against the published figures.

Same pattern as ``test_rate_sheet_shapes.py``: where a number comes from a
document the client supplied, the test quotes that number rather than a
convenient one, so a transcription error shows up here and not in a quotation.

Source: ``KWS-Conservation-Fee-October-2025.pdf`` (authoritative) and
bestkenya.ke for the non-KWS reserves (indicative — see the seeder's docstring).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.seed_park_fees import (
    KWS_CATEGORIES,
    KWS_CHILD_MAX,
    KWS_CHILD_MIN,
    KWS_PARKS,
    MICE_SOURCE_NOTE,
    VEHICLE_DAY_CHARGES_KES,
    seed_park_fees,
)
from app.db.session import AsyncSessionLocal
from app.modules.destinations.models import Destination
from app.modules.park_fees.models import ParkFee
from app.modules.park_fees.service import classify_age, compute_park_fee
from app.modules.residence.models import ResidenceCategory

pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal


async def _fee(db, park_slug: str, residence_key: str, on: date) -> ParkFee:
    destination = (
        await db.execute(select(Destination).where(Destination.slug == park_slug))
    ).scalar_one()
    residence = (
        await db.execute(
            select(ResidenceCategory).where(ResidenceCategory.key == residence_key)
        )
    ).scalar_one()
    return (
        await db.execute(
            select(ParkFee).where(
                ParkFee.destination_id == destination.id,
                ParkFee.residence_category_id == residence.id,
                ParkFee.effective_from <= on,
                ParkFee.effective_to >= on,
            )
        )
    ).scalar_one()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def park_fees():
    async with AsyncSessionLocal() as db:
        return await seed_park_fees(db)


# --------------------------------------------------------------------------- #
# The schedule, as published
# --------------------------------------------------------------------------- #


async def test_the_schedule_seeds_every_park_and_is_idempotent(park_fees):
    """A re-run creates nothing and corrects nothing.

    Both halves matter. Nothing created is ordinary idempotence; nothing
    *corrected* is the stronger claim that the rows on file already match the
    schedule, which is what makes a non-zero corrected count on a real run a
    signal worth reading.
    """
    assert park_fees["destinations"] == len(KWS_PARKS) + 1  # + the Mara
    async with AsyncSessionLocal() as db:
        again = await seed_park_fees(db)
    assert again["fees_created"] == 0
    assert again["fees_corrected"] == 0
    assert again["fees_unchanged"] > 0


async def test_a_wrong_figure_on_file_is_corrected_not_skipped(park_fees):
    """An insert-only seeder makes a transcription error permanent.

    Superseding a schedule and fixing a typo are different operations: a new
    schedule adds rows at a later effective_from and never rewrites history,
    while a wrong reading of *this* schedule has to be repairable.
    """
    async with AsyncSessionLocal() as db:
        fee = await _fee(db, "amboseli-national-park", "citizen", date(2026, 7, 1))
        fee.adult = D("999999")
        fee.currency = "USD"
        await db.commit()

        result = await seed_park_fees(db)
        assert result["fees_corrected"] == 1

        fixed = await _fee(db, "amboseli-national-park", "citizen", date(2026, 7, 1))
        assert fixed.adult == D("1500")
        assert fixed.currency == "KES"


async def test_amboseli_premium_park_fees(park_fees):
    """The Premium Parks line, all four columns.

    East African Citizen 1,500/750 | Kenya Resident 2,025/1,050 |
    Non-Resident USD 90/45 | African Citizen USD 50/25
    """
    on = date(2026, 7, 1)
    async with AsyncSessionLocal() as db:
        citizen = await _fee(db, "amboseli-national-park", "citizen", on)
        resident = await _fee(db, "amboseli-national-park", "resident", on)
        non_resident = await _fee(db, "amboseli-national-park", "non_resident", on)
        african = await _fee(db, "amboseli-national-park", "african_citizen", on)

    assert (citizen.adult, citizen.child, citizen.currency) == (D("1500"), D("750"), "KES")
    assert (resident.adult, resident.child, resident.currency) == (
        D("2025"), D("1050"), "KES",
    )
    assert (non_resident.adult, non_resident.child, non_resident.currency) == (
        D("90"), D("45"), "USD",
    )
    assert (african.adult, african.child, african.currency) == (D("50"), D("25"), "USD")


async def test_an_african_citizen_is_charged_far_less_than_a_non_resident(park_fees):
    """The category that did not exist until the schedule was read, so every such
    traveller was being quoted as a full non-resident — USD 90 against 50."""
    on = date(2026, 7, 1)
    async with AsyncSessionLocal() as db:
        african = await _fee(db, "amboseli-national-park", "african_citizen", on)
        non_resident = await _fee(db, "amboseli-national-park", "non_resident", on)
    assert african.adult < non_resident.adult
    assert african.adult == D("50")


async def test_a_kenya_resident_is_charged_in_shillings_not_dollars(park_fees):
    """KWS bills a Kenya Resident 2,025 KES.

    The bug this caught: the fee currency was being taken from the residence
    category's billing default, which was seeded as USD — so the row said 2,025
    *dollars*. A fee's currency belongs to the schedule column it was read from,
    which is a different fact from what we choose to quote a traveller in, and it
    is also what makes a stored fee reconcilable against the source PDF.
    """
    async with AsyncSessionLocal() as db:
        resident = await _fee(db, "amboseli-national-park", "resident", date(2026, 7, 1))
    assert resident.currency == "KES"
    assert resident.adult == D("2025")


async def test_the_marine_parks_the_reference_proposal_needs(park_fees):
    """A Diani itinerary running a Kisite-Mpunguti excursion owes marine fees, so
    a coastal quote with no safari in it is still under-priced without them."""
    async with AsyncSessionLocal() as db:
        citizen = await _fee(
            db, "kisite-mpunguti-marine-national-park", "citizen", date(2026, 7, 1)
        )
        non_resident = await _fee(
            db, "kisite-mpunguti-marine-national-park", "non_resident", date(2026, 7, 1)
        )
    assert citizen.adult == D("500")
    assert non_resident.adult == D("25")


async def test_child_bounds_encode_the_exemption(park_fees):
    """KWS: a child is 5-to-under-18, but a child of five and under is exempt. The
    exemption wins at the boundary, so the fee-bearing band is 6-17."""
    assert (KWS_CHILD_MIN, KWS_CHILD_MAX) == (6, 17)
    async with AsyncSessionLocal() as db:
        fee = await _fee(db, "amboseli-national-park", "citizen", date(2026, 7, 1))
    assert (fee.child_min_age, fee.child_max_age) == (6, 17)
    assert fee.infant == 0
    # A five-year-old is exempt, a six-year-old is a child, an eighteen-year-old
    # is an adult.
    assert classify_age(5, fee.child_min_age, fee.child_max_age) == "infant"
    assert classify_age(6, fee.child_min_age, fee.child_max_age) == "child"
    assert classify_age(17, fee.child_min_age, fee.child_max_age) == "child"
    assert classify_age(18, fee.child_min_age, fee.child_max_age) == "adult"


# --------------------------------------------------------------------------- #
# The Mara: the one seasonal entry fee
# --------------------------------------------------------------------------- #


async def test_the_mara_doubles_between_its_seasons(park_fees):
    """Non-resident USD 100 green against USD 200 peak — the largest single swing
    in Kenyan safari pricing, and the reason per-night rate selection matters."""
    async with AsyncSessionLocal() as db:
        green = await _fee(
            db, "maasai-mara-national-reserve", "non_resident", date(2026, 3, 15)
        )
        peak = await _fee(
            db, "maasai-mara-national-reserve", "non_resident", date(2026, 9, 15)
        )
    assert green.adult == D("100")
    assert peak.adult == D("200")
    assert peak.adult == green.adult * 2


async def test_the_mara_child_band_differs_by_residence(park_fees):
    """A citizen child is charged from 3, a non-resident child only from 9 — which
    is why the bounds live on the fee row rather than on the park."""
    async with AsyncSessionLocal() as db:
        citizen = await _fee(
            db, "maasai-mara-national-reserve", "citizen", date(2026, 9, 15)
        )
        non_resident = await _fee(
            db, "maasai-mara-national-reserve", "non_resident", date(2026, 9, 15)
        )
    assert citizen.child_min_age == 3
    assert non_resident.child_min_age == 9
    # An eight-year-old non-resident is exempt where a citizen of the same age
    # is charged.
    assert classify_age(8, non_resident.child_min_age, non_resident.child_max_age) == "infant"
    assert classify_age(8, citizen.child_min_age, citizen.child_max_age) == "child"


async def test_a_kws_park_is_not_seasonal(park_fees):
    """One open-ended row, so a stay crossing a month boundary cannot price two
    ways."""
    async with AsyncSessionLocal() as db:
        january = await _fee(db, "tsavo-east-national-park", "citizen", date(2026, 1, 5))
        august = await _fee(db, "tsavo-east-national-park", "citizen", date(2026, 8, 5))
    assert january.id == august.id
    assert january.adult == D("1000")


# --------------------------------------------------------------------------- #
# What the fees cost a real group
# --------------------------------------------------------------------------- #


async def test_a_mixed_family_in_the_mara_in_peak_season(park_fees):
    """Two non-resident adults and two children, 8 and 12, three days.

    The eight-year-old is under the non-resident child band and pays nothing;
    the twelve-year-old pays USD 50.
        adults    2 x 200 x 3 = 1,200
        child     1 x  50 x 3 =   150
        exempt    1 x   0 x 3 =     0
    """
    async with AsyncSessionLocal() as db:
        fee = await _fee(
            db, "maasai-mara-national-reserve", "non_resident", date(2026, 9, 15)
        )
    result = compute_park_fee(
        adult_fee=fee.adult,
        child_fee=fee.child,
        infant_fee=fee.infant,
        adults=2,
        ages=[8, 12],
        days=3,
        child_min_age=fee.child_min_age,
        child_max_age=fee.child_max_age,
    )
    assert result["counts"] == {"adult": 2, "child": 1, "infant": 1}
    assert result["adult_total"] == D("1200")
    assert result["child_total"] == D("150")
    assert result["total"] == D("1350")


async def test_a_twenty_five_pax_group_owes_real_money_in_park_fees(park_fees):
    """The gap this closes, in figures. 25 resident adults, Amboseli, two days:
    25 x 1,500 x 2 = 75,000 that no quotation has ever included."""
    async with AsyncSessionLocal() as db:
        fee = await _fee(db, "amboseli-national-park", "citizen", date(2026, 7, 1))
    result = compute_park_fee(
        adult_fee=fee.adult,
        child_fee=fee.child,
        infant_fee=fee.infant,
        adults=25,
        ages=[],
        days=2,
        child_min_age=fee.child_min_age,
        child_max_age=fee.child_max_age,
    )
    assert result["total"] == D("75000")


# --------------------------------------------------------------------------- #
# Recorded, not yet charged
# --------------------------------------------------------------------------- #


def test_the_vehicle_seat_bands_are_recorded_for_transport_costing():
    """A 25-pax Coaster into a park is 4,500 a day on top of every entry fee.

    Charged per vehicle rather than per person, so it belongs to transport
    costing (3.10) — but it is transcribed now, because it is exactly the line
    that gets forgotten.
    """
    bands = {(low, high): amount for low, high, amount in VEHICLE_DAY_CHARGES_KES}
    assert bands[(25, 44)] == "4500"
    assert bands[(6, 12)] == "1500"
    # The bands must tile without a gap, or a 5-seater or a 44-seater falls
    # through and is charged nothing.
    ordered = sorted(VEHICLE_DAY_CHARGES_KES)
    for (_, high, _), (low, _, _) in zip(ordered, ordered[1:], strict=False):
        assert low == high + 1


def test_the_group_discount_reading_is_written_down_not_assumed():
    """The KWS MICE wording is ambiguous and the two readings differ by an order
    of magnitude, so the reading in use is recorded rather than inferred."""
    assert "ambiguous" in MICE_SOURCE_NOTE.lower()


def test_every_kws_category_prices_every_residence():
    """A category missing a column would quote that residence nothing at all."""
    expected = {"citizen", "ea_resident", "resident", "non_resident", "african_citizen"}
    for name, table in KWS_CATEGORIES.items():
        assert set(table) == expected, name
        for residence, (adult, child, currency) in table.items():
            assert Decimal(adult) > 0, (name, residence)
            assert Decimal(child) > 0, (name, residence)
            # Every schedule column charges a child less than an adult.
            assert Decimal(child) < Decimal(adult), (name, residence)
            # Shilling columns for East Africans and residents, dollars for the
            # rest — read off the schedule, not inferred from the category.
            expected_ccy = (
                "KES" if residence in {"citizen", "ea_resident", "resident"} else "USD"
            )
            assert currency == expected_ccy, (name, residence)
