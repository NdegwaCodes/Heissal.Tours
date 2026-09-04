"""Child accommodation rates on the cohort vector — the last of §3.8.

``accommodation_rates.child_rate`` has existed since Stage 3.1 and nothing read
it. A child was therefore an ordinary occupant: counted into the rooming, given
a share of a room, and charged what an adult is charged. Two things were wrong
with that at once, and they pull in opposite directions —

* the group was quoted more rooms than it needs (two adults and two children in
  twins came out as two rooms, when the sheet is selling one room and two extra
  beds), and
* the children were charged an adult's share of a room instead of the rate the
  supplier actually publishes for them.

So a family quote was both over-priced and unreconcilable against the sheet it
came from. This file builds a resort of its own — no park fees, no included
activities, nothing else in the build-up — so every figure below is the
accommodation and the arithmetic on top of it, and nothing else.

Every rate here is invented. The shape is the client's corpus: a per-room rate
per occupancy, with a separate per-child-per-night figure beside it.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.modules.accommodations.models import (
    Accommodation,
    AccommodationRate,
    MealPlan,
    RoomType,
)
from app.modules.destinations.models import Destination
from app.modules.quotes.models import Quote, QuoteOption
from app.modules.residence.models import ResidenceCategory
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal

# Three nights, wholly inside the season that states a child rate.
ARRIVAL, DEPARTURE = "2026-07-01", "2026-07-04"
# And three nights crossing into the one that does not: 30 and 31 July are in
# the first window, 1 August is in the second.
CROSSING_ARRIVAL, CROSSING_DEPARTURE = "2026-07-30", "2026-08-02"

STATED_FROM, STATED_TO = date(2026, 1, 1), date(2026, 7, 31)
SILENT_FROM, SILENT_TO = date(2026, 8, 1), date(2026, 12, 31)

NIGHTS = 3
# Per room per night, and per child per night beside it.
KES_TWIN, KES_SINGLE, KES_CHILD = D("20000"), D("15000"), D("5000")
USD_TWIN, USD_SINGLE, USD_CHILD = D("300"), D("225"), D("50")

# The pricing config's defaults, restated because the figures below are worked
# by hand from them.
CONTINGENCY, PROFIT = D("1.05"), D("1.24")


def _up(amount: Decimal, step: Decimal) -> Decimal:
    """The build-up's per-person rounding: up to the next whole step (§3.6)."""
    return (amount / step).to_integral_value(rounding="ROUND_CEILING") * step


@pytest_asyncio.fixture(loop_scope="session")
async def family_resort():
    """A resort whose sheet prices children, and one plan where it does not.

    Full board carries a child rate; bed and breakfast deliberately does not, so
    a sheet's silence can be tested against its own property rather than
    against a second one that might differ in some other way.
    """
    tag = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        fb = (
            await db.execute(select(MealPlan).where(MealPlan.code == "FB"))
        ).scalar_one()
        bb = (
            await db.execute(select(MealPlan).where(MealPlan.code == "BB"))
        ).scalar_one()
        citizen = (
            await db.execute(
                select(ResidenceCategory).where(ResidenceCategory.key == "citizen")
            )
        ).scalar_one()
        visitor = (
            await db.execute(
                select(ResidenceCategory).where(
                    ResidenceCategory.key == "non_resident"
                )
            )
        ).scalar_one()

        beach = Destination(
            name=f"Child Rate Bay {tag}", slug=f"child-rate-bay-{tag}", type="beach"
        )
        db.add(beach)
        await db.flush()
        resort = Accommodation(
            name=f"Family Resort {tag}",
            slug=f"family-resort-{tag}",
            destination_id=beach.id,
            category="resort",
        )
        db.add(resort)
        await db.flush()
        twin = RoomType(
            accommodation_id=resort.id, name="Family Twin", code="FTW", max_occupancy=2
        )
        db.add(twin)
        await db.flush()

        for rc, currency, double, single, child in (
            (citizen, "KES", KES_TWIN, KES_SINGLE, KES_CHILD),
            (visitor, "USD", USD_TWIN, USD_SINGLE, USD_CHILD),
        ):
            for occupancy, amount in ((2, double), (1, single)):
                # Full board, first season: the child rate is stated.
                db.add(
                    AccommodationRate(
                        accommodation_id=resort.id,
                        room_type_id=twin.id,
                        meal_plan_id=fb.id,
                        residence_category_id=rc.id,
                        season_name="stated",
                        occupancy=occupancy,
                        effective_from=STATED_FROM,
                        effective_to=STATED_TO,
                        currency=currency,
                        rate_per_night=amount,
                        child_rate=child,
                        rate_kind="sto",
                    )
                )
                # Full board, second season: the same room, no child rate. This
                # is what a real sheet looks like when one season's table has
                # the column and the next one does not.
                db.add(
                    AccommodationRate(
                        accommodation_id=resort.id,
                        room_type_id=twin.id,
                        meal_plan_id=fb.id,
                        residence_category_id=rc.id,
                        season_name="silent",
                        occupancy=occupancy,
                        effective_from=SILENT_FROM,
                        effective_to=SILENT_TO,
                        currency=currency,
                        rate_per_night=amount,
                        rate_kind="sto",
                    )
                )
                # Bed and breakfast, all year, no child rate at all.
                db.add(
                    AccommodationRate(
                        accommodation_id=resort.id,
                        room_type_id=twin.id,
                        meal_plan_id=bb.id,
                        residence_category_id=rc.id,
                        season_name="stated",
                        occupancy=occupancy,
                        effective_from=STATED_FROM,
                        effective_to=SILENT_TO,
                        currency=currency,
                        rate_per_night=amount - (amount / 4),
                        rate_kind="sto",
                    )
                )
        await db.commit()
        ids = {
            "destination_id": str(beach.id),
            "accommodation_id": str(resort.id),
            "citizen": str(citizen.id),
            "non_resident": str(visitor.id),
            "meal_plan_fb": str(fb.id),
            "meal_plan_bb": str(bb.id),
        }

    yield ids

    async with AsyncSessionLocal() as db:
        quote_ids = (
            (
                await db.execute(
                    select(QuoteOption.quote_id).where(
                        QuoteOption.accommodation_id
                        == uuid.UUID(ids["accommodation_id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        for quote_id in set(quote_ids):
            quote = await db.get(Quote, quote_id)
            if quote is not None:
                await db.delete(quote)
        await db.flush()
        resort_row = await db.get(Accommodation, uuid.UUID(ids["accommodation_id"]))
        if resort_row is not None:
            await db.delete(resort_row)
            await db.flush()
        beach_row = await db.get(Destination, uuid.UUID(ids["destination_id"]))
        if beach_row is not None:
            await db.delete(beach_row)
        await db.commit()


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _priced(
    client,
    h,
    resort,
    *,
    cohorts,
    plan="meal_plan_fb",
    arrival=ARRIVAL,
    departure=DEPARTURE,
    currency="KES",
):
    """Create a quote on the resort and return its one priced option."""
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Family Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("childrate"),
            "residence_category_id": resort["citizen"],
        },
    )
    assert record.status_code == 201, record.text
    quote = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record.json()["id"],
            "presentation_currency": currency,
            "residence_category_id": resort["citizen"],
            "arrival_date": arrival,
            "departure_date": departure,
            "requested_meal_plan_id": resort[plan],
            "cohorts": [
                {
                    "residence_category_id": resort[residence],
                    "traveller_type": kind,
                    "headcount": n,
                }
                for residence, kind, n in cohorts
            ],
            "options": [{"accommodation_id": resort["accommodation_id"]}],
        },
    )
    assert quote.status_code == 201, quote.text
    priced = await client.post(
        f"{API}/quotes/{quote.json()['id']}/options/price", headers=h
    )
    assert priced.status_code == 200, priced.text
    body = priced.json()
    assert len(body["options"]) == 1, body
    return body["options"][0]


def _by_cohort(option):
    return {
        f"{row['residence']}:{row['traveller_type']}": row
        for row in option["cohorts"]
    }


async def test_a_child_sharing_is_charged_the_sheets_child_rate(
    client, admin_tokens, family_resort
):
    """Two adults, two children, three nights.

    One twin at 20,000 = 60,000, plus two children at 5,000 a night = 30,000.
    Accommodation 90,000.

    What it used to be: four occupants, two twins, 120,000 — and no line
    anywhere corresponding to anything the supplier's sheet says.
    """
    option = await _priced(
        client,
        _h(admin_tokens),
        family_resort,
        cohorts=[("citizen", "adult", 2), ("citizen", "child", 2)],
    )
    components = option["build_up"]["components"]
    assert D(components["accommodation"]) == KES_TWIN * NIGHTS + KES_CHILD * 2 * NIGHTS
    assert D(components["accommodation"]) == D("90000")


async def test_the_children_do_not_add_a_room(client, admin_tokens, family_resort):
    """The rooming is what the sheet is selling: one room, two extra beds.

    Quoting the second room is not a conservative error. It is a room the hotel
    will not hold and the client will not use, on a document they compare
    against a competitor's.
    """
    option = await _priced(
        client,
        _h(admin_tokens),
        family_resort,
        cohorts=[("citizen", "adult", 2), ("citizen", "child", 2)],
    )
    assert option["rooms_required"] == 1


async def test_the_child_bears_its_own_bed_and_not_a_share_of_the_room(
    client, admin_tokens, family_resort
):
    """The figures each cohort is billed, worked by hand.

        adults:   60,000 x 1.05 x 1.24 = 78,120 -> 39,060 each -> 39,100
        children: 30,000 x 1.05 x 1.24 = 39,060 -> 19,530 each -> 19,600

    This is the attribution the whole vector exists for: the room reaches the
    travellers it was priced for, the extra beds reach the children, and neither
    pays any part of the other.
    """
    option = await _priced(
        client,
        _h(admin_tokens),
        family_resort,
        cohorts=[("citizen", "adult", 2), ("citizen", "child", 2)],
    )
    rows = _by_cohort(option)
    adult, child = rows["citizen:adult"], rows["citizen:child"]

    assert D(adult["per_person"]) == _up(
        KES_TWIN * NIGHTS * CONTINGENCY * PROFIT / 2, D("100")
    )
    assert D(adult["per_person"]) == D("39100")
    assert D(child["per_person"]) == _up(
        KES_CHILD * 2 * NIGHTS * CONTINGENCY * PROFIT / 2, D("100")
    )
    assert D(child["per_person"]) == D("19600")
    # And the rows still add up to the figure printed beside them (§3.6).
    assert D(option["group_total"]) == D(adult["total"]) + D(child["total"])


async def test_a_sheet_that_states_no_child_rate_charges_a_child_as_an_adult(
    client, admin_tokens, family_resort
):
    """Bed and breakfast has no child rate, so nothing changes for it.

    Four occupants, two twins at 15,000 = 90,000, and every traveller pays the
    same. A property that publishes no child rate is not offering a child
    discount, and inventing one would quote a price nobody has to honour.
    """
    option = await _priced(
        client,
        _h(admin_tokens),
        family_resort,
        cohorts=[("citizen", "adult", 2), ("citizen", "child", 2)],
        plan="meal_plan_bb",
    )
    assert option["rooms_required"] == 2
    assert D(option["build_up"]["components"]["accommodation"]) == D("90000")
    rows = _by_cohort(option)
    assert (
        D(rows["citizen:adult"]["per_person"])
        == D(rows["citizen:child"]["per_person"])
    )


async def test_a_child_rate_missing_for_part_of_the_stay_is_not_used_at_all(
    client, admin_tokens, family_resort
):
    """30 July to 2 August: two nights with a child rate and one without.

    All or nothing per residency, deliberately. Pricing the children into the
    room for one night and beside it for the others is neither of the two things
    the sheet could mean, and reconciles with nothing.
    """
    option = await _priced(
        client,
        _h(admin_tokens),
        family_resort,
        cohorts=[("citizen", "adult", 2), ("citizen", "child", 2)],
        arrival=CROSSING_ARRIVAL,
        departure=CROSSING_DEPARTURE,
    )
    assert option["rooms_required"] == 2
    assert D(option["build_up"]["components"]["accommodation"]) == KES_TWIN * 2 * NIGHTS
    rows = _by_cohort(option)
    assert (
        D(rows["citizen:adult"]["per_person"])
        == D(rows["citizen:child"]["per_person"])
    )


async def test_each_residency_charges_its_children_off_its_own_sheet(
    client, admin_tokens, family_resort
):
    """Two families, one resident and one visiting, on one quote.

        resident adults:  20,000 x 3 x 1.05 x 1.24 = 78,120 -> KES 39,100 each
        resident child:    5,000 x 3 x 1.05 x 1.24 = 19,530 -> KES 19,600
        visiting adults:     300 x 3 x 1.05 x 1.24 =  1,171.80 -> USD 586 each
        visiting child:       50 x 3 x 1.05 x 1.24 =    195.30 -> USD 196

    Four cohorts, two currencies, two child rates, two roomings — and each
    child charged in the currency its own family is billed in (§3.8). The USD
    figures also depend on the per-currency rounding step: at the old global
    step of 100 the visiting child would be quoted 200 for a 195.30 cost.
    """
    option = await _priced(
        client,
        _h(admin_tokens),
        family_resort,
        cohorts=[
            ("citizen", "adult", 2),
            ("citizen", "child", 1),
            ("non_resident", "adult", 2),
            ("non_resident", "child", 1),
        ],
    )
    rows = _by_cohort(option)
    assert set(rows) == {
        "citizen:adult",
        "citizen:child",
        "non_resident:adult",
        "non_resident:child",
    }

    assert rows["citizen:adult"]["currency"] == "KES"
    assert D(rows["citizen:adult"]["per_person"]) == D("39100")
    assert D(rows["citizen:child"]["per_person"]) == D("19600")

    assert rows["non_resident:adult"]["currency"] == "USD"
    assert D(rows["non_resident:adult"]["per_person"]) == D("586")
    assert D(rows["non_resident:child"]["per_person"]) == D("196")

    # Two rooms in total — one per family — not the four an occupancy count
    # would have produced.
    assert option["rooms_required"] == 2


async def test_children_only_are_room_occupants(
    client, admin_tokens, family_resort
):
    """A cohort of children with no adults is priced into the rooms.

    An extra bed needs a room to be extra to. Charging two unaccompanied
    children a child rate each and no room at all would quote a holiday with
    nowhere to sleep, so the plain rule stands: they are occupants.
    """
    option = await _priced(
        client,
        _h(admin_tokens),
        family_resort,
        cohorts=[("citizen", "child", 2)],
    )
    assert option["rooms_required"] == 1
    assert D(option["build_up"]["components"]["accommodation"]) == KES_TWIN * NIGHTS


async def test_the_worksheet_states_both_bases(
    client, admin_tokens, family_resort
):
    """The room and the child bed are charged on different multipliers.

    Per room per night against per person per night. An operator reconciling
    90,000 against a sheet needs both stated, because 60,000 and 30,000 cannot
    be checked against the same column.
    """
    h = _h(admin_tokens)
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Worksheet Family {uuid.uuid4().hex[:8]}",
            "email": unique_email("childsheet"),
            "residence_category_id": family_resort["citizen"],
        },
    )
    quote = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record.json()["id"],
            "presentation_currency": "KES",
            "residence_category_id": family_resort["citizen"],
            "arrival_date": ARRIVAL,
            "departure_date": DEPARTURE,
            "requested_meal_plan_id": family_resort["meal_plan_fb"],
            "cohorts": [
                {
                    "residence_category_id": family_resort["citizen"],
                    "traveller_type": kind,
                    "headcount": n,
                }
                for kind, n in (("adult", 2), ("child", 2))
            ],
            "options": [
                {
                    "accommodation_id": family_resort["accommodation_id"],
                    "is_recommended": True,
                }
            ],
        },
    )
    assert quote.status_code == 201, quote.text
    quote_id = quote.json()["id"]
    priced = await client.post(f"{API}/quotes/{quote_id}/options/price", headers=h)
    assert priced.status_code == 200, priced.text
    issued = await client.post(f"{API}/quotes/{quote_id}/issue", headers=h)
    assert issued.status_code == 200, issued.text

    sheet = await client.get(f"{API}/quotes/{quote_id}/worksheet.html", headers=h)
    assert sheet.status_code == 200, sheet.text
    html = sheet.text
    assert "child sharing (citizen)" in html
    assert "per_room_per_night" in html
    assert "per_person_per_night" in html
    # Six child-nights at 5,000, and three room-nights at 20,000.
    assert "KES 30,000" in html
    assert "KES 60,000" in html
