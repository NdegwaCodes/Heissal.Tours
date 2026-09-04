"""Mandatory activities in an option's price — the other half of §3.8's last item.

``Activity.is_mandatory`` and ``activity_rates`` have both been in the schema
since Stage 3.1, and the Stage 3 build-up — the one the client's document
renders — charged neither. An included excursion was therefore named on the
proposal and paid for by nobody: the same shape of hole transport had before
§3.10 and park entry before §3.8, and on the reference proposal's Wasini Island
day it is the second largest line after the beds.

Two decisions this file pins down:

* **``is_mandatory`` decides the treatment, not the scope.** An activity is on
  the quote because an agent put it there; the flag then says it is costed into
  the package and listed under Included rather than offered beside it as a
  priced extra. The first version of this charged every mandatory activity at
  the destination instead, which reads well until a beach quote silently buys
  twenty five people a dhow cruise nobody asked for — twenty six existing tests
  said so, in unison. A charge that really does apply to every visitor to a
  place is a park or conservancy fee, and those have their own module.
* **The fare is per cohort.** A resident child pays the resident child fare in
  the currency their family is billed in, exactly as with park entry. That is
  what makes this the last cost on the vector: it was the one still charged at
  a single rate to everybody.

The excursion is also charged **once for the quote and into every option**, the
same as the journey (§3.10): a dhow cruise does not change with the hotel, so
holding it per option would only let two options disagree about what the client
is getting.

Fares invented; the shape (residence-tiered, adult and child, effective-dated)
is the schema's own.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
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
from app.modules.activities.models import Activity, ActivityRate
from app.modules.destinations.models import Destination
from app.modules.quotes.models import Quote, QuoteOption
from app.modules.residence.models import ResidenceCategory
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal

ARRIVAL, DEPARTURE = "2026-07-01", "2026-07-04"
SEASON_FROM, SEASON_TO = date(2026, 1, 1), date(2026, 12, 31)
NIGHTS = 3

KES_TWIN, USD_TWIN = D("20000"), D("300")
# The included excursion: charged once per person, not per night.
DHOW_ADULT_KES, DHOW_CHILD_KES = D("4000"), D("2000")
DHOW_ADULT_USD, DHOW_CHILD_USD = D("60"), D("30")
DHOW = "Sunset dhow cruise"
# Priced for residents only, so a missing schedule can be seen for what it is.
REEF = "Reef snorkelling"
REEF_ADULT_KES = D("3000")
# The same cruise after a fare revision on the third day of the stay.
DHOW_ADULT_KES_LATER = D("5000")
REVISED_FROM = date(2026, 7, 3)

CONTINGENCY, PROFIT = D("1.05"), D("1.24")


@pytest_asyncio.fixture(loop_scope="session")
async def excursion_coast():
    """A destination with two included excursions and a hotel to stay in."""
    tag = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        fb = (
            await db.execute(select(MealPlan).where(MealPlan.code == "FB"))
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

        coast = Destination(
            name=f"Excursion Coast {tag}", slug=f"excursion-coast-{tag}", type="beach"
        )
        db.add(coast)
        await db.flush()
        hotel = Accommodation(
            name=f"Dhow Hotel {tag}",
            slug=f"dhow-hotel-{tag}",
            destination_id=coast.id,
            category="hotel",
        )
        db.add(hotel)
        await db.flush()
        twin = RoomType(
            accommodation_id=hotel.id, name="Sea Twin", code="STW", max_occupancy=2
        )
        db.add(twin)
        await db.flush()
        for rc, currency, double in (
            (citizen, "KES", KES_TWIN),
            (visitor, "USD", USD_TWIN),
        ):
            for occupancy, amount in ((2, double), (1, double * D("0.75"))):
                db.add(
                    AccommodationRate(
                        accommodation_id=hotel.id,
                        room_type_id=twin.id,
                        meal_plan_id=fb.id,
                        residence_category_id=rc.id,
                        season_name="standard",
                        occupancy=occupancy,
                        effective_from=SEASON_FROM,
                        effective_to=SEASON_TO,
                        currency=currency,
                        rate_per_night=amount,
                        rate_kind="sto",
                    )
                )

        dhow = Activity(
            name=DHOW,
            slug=f"sunset-dhow-{tag}",
            destination_id=coast.id,
            is_optional=False,
            is_mandatory=True,
        )
        # Included, but priced for residents only: the gap has to be reported
        # rather than quietly quoting the visitors without it.
        reef = Activity(
            name=REEF,
            slug=f"reef-snorkelling-{tag}",
            destination_id=coast.id,
            is_optional=False,
            is_mandatory=True,
        )
        # And one genuinely optional activity at the same destination, which
        # must NOT enter the price — otherwise "mandatory" means nothing.
        kite = Activity(
            name=f"Kitesurfing lesson {tag}",
            slug=f"kitesurfing-{tag}",
            destination_id=coast.id,
            is_optional=True,
            is_mandatory=False,
        )
        db.add_all([dhow, reef, kite])
        await db.flush()
        # A fare revision two days into the stay, so "which day does it fall
        # on" is a question with two different answers.
        db.add(
            ActivityRate(
                activity_id=dhow.id,
                residence_category_id=citizen.id,
                currency="KES",
                adult_price=DHOW_ADULT_KES_LATER,
                child_price=DHOW_CHILD_KES,
                effective_from=REVISED_FROM,
                effective_to=SEASON_TO,
            )
        )
        for activity, rows in (
            (
                dhow,
                (
                    (citizen, "KES", DHOW_ADULT_KES, DHOW_CHILD_KES),
                    (visitor, "USD", DHOW_ADULT_USD, DHOW_CHILD_USD),
                ),
            ),
            (reef, ((citizen, "KES", REEF_ADULT_KES, REEF_ADULT_KES / 2),)),
            (
                kite,
                (
                    (citizen, "KES", D("9000"), D("9000")),
                    (visitor, "USD", D("120"), D("120")),
                ),
            ),
        ):
            for rc, currency, adult, child in rows:
                db.add(
                    ActivityRate(
                        activity_id=activity.id,
                        residence_category_id=rc.id,
                        currency=currency,
                        adult_price=adult,
                        child_price=child,
                        effective_from=SEASON_FROM,
                        effective_to=(
                            REVISED_FROM - timedelta(days=1)
                            if activity is dhow
                            else SEASON_TO
                        ),
                    )
                )
        await db.commit()
        ids = {
            "destination_id": str(coast.id),
            "accommodation_id": str(hotel.id),
            "dhow_id": str(dhow.id),
            "reef_id": str(reef.id),
            "kite_id": str(kite.id),
            "citizen": str(citizen.id),
            "non_resident": str(visitor.id),
            "meal_plan_fb": str(fb.id),
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
        hotel_row = await db.get(Accommodation, uuid.UUID(ids["accommodation_id"]))
        if hotel_row is not None:
            await db.delete(hotel_row)
            await db.flush()
        for activity_id in (
            await db.execute(
                select(Activity.id).where(
                    Activity.destination_id == uuid.UUID(ids["destination_id"])
                )
            )
        ).scalars().all():
            row = await db.get(Activity, activity_id)
            if row is not None:
                await db.delete(row)  # cascades to its rates
        await db.flush()
        coast_row = await db.get(Destination, uuid.UUID(ids["destination_id"]))
        if coast_row is not None:
            await db.delete(coast_row)
        await db.commit()


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _quote(
    client,
    h,
    coast,
    *,
    cohorts,
    currency="KES",
    recommend=False,
    select=("dhow_id", "reef_id"),
    day=None,
):
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Excursion Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("activity"),
            "residence_category_id": coast["citizen"],
        },
    )
    assert record.status_code == 201, record.text
    quote = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record.json()["id"],
            "presentation_currency": currency,
            "residence_category_id": coast["citizen"],
            "arrival_date": ARRIVAL,
            "departure_date": DEPARTURE,
            "requested_meal_plan_id": coast["meal_plan_fb"],
            "cohorts": [
                {
                    "residence_category_id": coast[residence],
                    "traveller_type": kind,
                    "headcount": n,
                }
                for residence, kind, n in cohorts
            ],
            # What the agent put on the quote. The counts on a selection row
            # are the Stage 2 shape and are deliberately ignored: the group
            # vector is the one answer to who is travelling (§3.8), and a
            # second headcount here could only disagree with it.
            "legs": [
                {
                    "destination_id": coast["destination_id"],
                    "nights": NIGHTS,
                    "activities": [
                        {"activity_id": coast[key], "day": day}
                        for key in select
                    ],
                }
            ],
            "options": [
                {
                    "accommodation_id": coast["accommodation_id"],
                    "is_recommended": recommend,
                }
            ],
        },
    )
    assert quote.status_code == 201, quote.text
    quote_id = quote.json()["id"]
    priced = await client.post(f"{API}/quotes/{quote_id}/options/price", headers=h)
    assert priced.status_code == 200, priced.text
    body = priced.json()
    assert len(body["options"]) == 1, body
    return quote_id, body, body["options"][0]


def _by_cohort(option):
    return {
        f"{row['residence']}:{row['traveller_type']}": row
        for row in option["cohorts"]
    }


async def test_a_mandatory_activity_reaches_the_build_up(
    client, admin_tokens, excursion_coast
):
    """Two resident adults: the dhow at 4,000 each and the reef at 3,000 each.

    14,000 of excursions on a 120,000 quote. Before this it was zero, and the
    document still promised both of them.
    """
    _, _, option = await _quote(
        client, _h(admin_tokens), excursion_coast, cohorts=[("citizen", "adult", 2)]
    )
    components = option["build_up"]["components"]
    assert "activities" in components, components
    assert D(components["activities"]) == (DHOW_ADULT_KES + REEF_ADULT_KES) * 2
    assert D(components["activities"]) == D("14000")


async def test_it_is_charged_once_per_person_not_once_per_night(
    client, admin_tokens, excursion_coast
):
    """A three-night stay does not buy three dhow cruises.

    The basis is the point: nights are what a bed is charged on and an
    excursion is not a bed. Multiplying by the stay would treble the largest
    non-accommodation line on a coastal quote.
    """
    _, _, option = await _quote(
        client, _h(admin_tokens), excursion_coast, cohorts=[("citizen", "adult", 2)]
    )
    charged = D(option["build_up"]["components"]["activities"])
    assert charged == (DHOW_ADULT_KES + REEF_ADULT_KES) * 2
    assert charged != (DHOW_ADULT_KES + REEF_ADULT_KES) * 2 * NIGHTS


async def test_an_optional_activity_on_the_quote_is_not_in_the_package_price(
    client, admin_tokens, excursion_coast
):
    """The kitesurfing lesson is on the quote at 9,000 a head and stays outside.

    This is the flag doing the work: three activities selected, two of them
    included in the price and named under Included, and the third an extra the
    client can decline. Without it "mandatory" would mean nothing at all.
    """
    _, _, option = await _quote(
        client,
        _h(admin_tokens),
        excursion_coast,
        cohorts=[("citizen", "adult", 2)],
        select=("dhow_id", "reef_id", "kite_id"),
    )
    assert D(option["build_up"]["components"]["activities"]) == D("14000")
    assert all("Kitesurfing" not in name for name in option["activities"])


async def test_nothing_is_charged_for_an_excursion_nobody_selected(
    client, admin_tokens, excursion_coast
):
    """The destination's excursions are not levies.

    An agent quoting three nights on this coast and nothing else gets the beds
    and the beds only. The opposite rule — every mandatory activity at the
    destination, on every quote to it — would put 14,000 a couple onto a quote
    nobody asked for it on, and it is the reason this scope is the selection.
    """
    _, _, option = await _quote(
        client,
        _h(admin_tokens),
        excursion_coast,
        cohorts=[("citizen", "adult", 2)],
        select=(),
    )
    assert "activities" not in option["build_up"]["components"]
    assert option["activities"] == []


async def test_a_child_pays_the_child_fare(client, admin_tokens, excursion_coast):
    """Two adults and two children on the dhow: 2 x 4,000 + 2 x 2,000.

    Plus the reef at 3,000 and 1,500. The cohort vector carries the traveller
    type, so no age is inferred to know who qualifies — the same reason park
    entry can be charged per cohort.
    """
    _, _, option = await _quote(
        client,
        _h(admin_tokens),
        excursion_coast,
        cohorts=[("citizen", "adult", 2), ("citizen", "child", 2)],
    )
    expected = (DHOW_ADULT_KES + REEF_ADULT_KES) * 2 + (
        DHOW_CHILD_KES + REEF_ADULT_KES / 2
    ) * 2
    assert D(option["build_up"]["components"]["activities"]) == expected
    rows = _by_cohort(option)
    assert D(rows["citizen:child"]["per_person"]) < D(
        rows["citizen:adult"]["per_person"]
    )


async def test_each_cohort_is_charged_the_fare_for_its_own_residency(
    client, admin_tokens, excursion_coast
):
    """A resident and a visitor on the same dhow pay two different fares.

    KES 4,000 against USD 60 — and the visitor's lands in dollars, because that
    is the currency their whole quote is billed in (§3.8). Converting a KES fare
    onto a USD invoice would put an exchange rate inside a figure the supplier
    quoted directly.
    """
    _, _, option = await _quote(
        client,
        _h(admin_tokens),
        excursion_coast,
        cohorts=[("citizen", "adult", 2), ("non_resident", "adult", 2)],
    )
    rows = _by_cohort(option)
    # Visitors: one twin at 300 for three nights, plus the dhow at 60 each. The
    # reef has no non-resident fare, which the warning below covers.
    #   900 + 120 = 1,020 x 1.05 x 1.24 = 1,328.04 -> 664.02 each -> USD 665
    assert rows["non_resident:adult"]["currency"] == "USD"
    assert D(rows["non_resident:adult"]["per_person"]) == D("665")
    # Residents: 60,000 + 14,000 = 74,000 x 1.05 x 1.24 = 96,348 -> 48,174 each
    assert D(rows["citizen:adult"]["per_person"]) == D("48200")


async def test_a_missing_fare_is_reported_rather_than_quoted_at_zero(
    client, admin_tokens, excursion_coast
):
    """The reef trip has no non-resident fare on file.

    Included in the trip, so the visitors are going; unpriced, so they are not
    being charged for it. That is a hole in our data and it is said out loud in
    the internal warnings — the client document never carries it, because it is
    a statement about our sheets and not about the excursion (§3.3a).
    """
    _, body, _ = await _quote(
        client,
        _h(admin_tokens),
        excursion_coast,
        cohorts=[("citizen", "adult", 2), ("non_resident", "adult", 2)],
    )
    warnings = " ".join(body["options"][0]["warnings"])
    assert REEF in warnings, warnings
    assert "non_resident" in warnings
    assert "Load the rate" in warnings


async def test_an_infant_is_charged_the_adult_fare_and_it_is_said_so(
    client, admin_tokens, excursion_coast
):
    """No sheet in the corpus prices an infant, so nothing is invented.

    The adult fare stands and the assumption is surfaced. It errs toward
    over-charging, which is the visible direction, and a boat that carries a lap
    infant free is a discount to ask the supplier for rather than to assume.
    """
    _, _, option = await _quote(
        client,
        _h(admin_tokens),
        excursion_coast,
        cohorts=[("citizen", "adult", 2), ("citizen", "infant", 1)],
    )
    expected = (DHOW_ADULT_KES + REEF_ADULT_KES) * 3
    assert D(option["build_up"]["components"]["activities"]) == expected
    assert any("adult fare" in note for note in option["warnings"])


async def test_the_client_document_lists_what_it_charged_for(
    client, admin_tokens, excursion_coast
):
    """Charged into the price and named under Included — one fact, said twice.

    A client who is paying for the dhow has to be able to read that they are
    getting it, and an operator has to be able to see that what the document
    promises is what the price covers.
    """
    h = _h(admin_tokens)
    quote_id, _, option = await _quote(
        client,
        h,
        excursion_coast,
        cohorts=[("citizen", "adult", 2)],
        recommend=True,
    )
    assert DHOW in option["activities"]
    issued = await client.post(f"{API}/quotes/{quote_id}/issue", headers=h)
    assert issued.status_code == 200, issued.text

    page = await client.get(f"{API}/quotes/{quote_id}/document.html", headers=h)
    assert page.status_code == 200, page.text
    assert DHOW in page.text
    assert REEF in page.text


async def test_the_worksheet_traces_each_fare_to_its_row(
    client, admin_tokens, excursion_coast
):
    """Its own group in the ledger, per cohort, with the rate row behind it."""
    h = _h(admin_tokens)
    quote_id, _, _ = await _quote(
        client,
        h,
        excursion_coast,
        cohorts=[("citizen", "adult", 2), ("citizen", "child", 2)],
        recommend=True,
    )
    issued = await client.post(f"{API}/quotes/{quote_id}/issue", headers=h)
    assert issued.status_code == 200, issued.text
    sheet = await client.get(f"{API}/quotes/{quote_id}/worksheet.html", headers=h)
    assert sheet.status_code == 200, sheet.text
    html = sheet.text
    assert "Included activities" in html
    assert f"{DHOW} — included (citizen adult)" in html
    assert f"{DHOW} — included (citizen child)" in html
    assert "activity_rates" in html
    assert "per_person" in html


async def test_the_flag_is_settable_through_the_api(
    client, admin_tokens, excursion_coast
):
    """``is_mandatory`` was on the model since §3.1 with no way to set it.

    Which made every activity optional in practice — the same gap the transport
    segments had before §3.10, and worth a test rather than a comment.
    """
    h = _h(admin_tokens)
    created = await client.post(
        f"{API}/activities",
        headers=h,
        json={
            "name": f"Village walk {uuid.uuid4().hex[:6]}",
            "destination_id": excursion_coast["destination_id"],
            "is_optional": False,
            "is_mandatory": True,
            "has_own_section": True,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["is_mandatory"] is True
    assert body["has_own_section"] is True

    flipped = await client.patch(
        f"{API}/activities/{body['id']}",
        headers=h,
        json={"is_mandatory": False},
    )
    assert flipped.status_code == 200, flipped.text
    assert flipped.json()["is_mandatory"] is False


async def test_the_fare_is_the_one_in_force_on_the_day_it_falls(
    client, admin_tokens, excursion_coast
):
    """The cruise costs 4,000 on day one and 5,000 from the third day.

    Rates are effective-dated for a reason, and an excursion happens on a day
    rather than across the stay — so the day the agent scheduled it on is the
    day it prices at. Pricing every activity at the arrival date would quote a
    revised fare at the old figure for the whole of a long trip.
    """
    h = _h(admin_tokens)
    _, _, first = await _quote(
        client, h, excursion_coast,
        cohorts=[("citizen", "adult", 2)], select=("dhow_id",), day=1,
    )
    _, _, third = await _quote(
        client, h, excursion_coast,
        cohorts=[("citizen", "adult", 2)], select=("dhow_id",), day=3,
    )
    assert D(first["build_up"]["components"]["activities"]) == DHOW_ADULT_KES * 2
    assert D(third["build_up"]["components"]["activities"]) == (
        DHOW_ADULT_KES_LATER * 2
    )
