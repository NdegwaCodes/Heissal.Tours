"""The day-by-day, end to end (Stage 4.1).

The pure rules are covered in ``test_itinerary.py``. What this file checks is
the wiring, which is where the value actually is:

* the programme is **frozen** into the version, so a leg re-dated after the
  quote went out cannot change the page the client is holding;
* it reaches the rendered document, with the movements and the excursions on
  the days they happen;
* and a day that is not a day of this trip is **refused at creation**, where an
  agent can still fix it, rather than priced off the wrong fare table.
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
from app.modules.quotes.models import (
    Quote,
    QuoteOption,
    QuoteOptionLeg,
    QuoteVersion,
)
from app.modules.residence.models import ResidenceCategory
from app.modules.transport.models import TransferRate
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal

# Four days, three nights: Nairobi for one, then the coast for two.
ARRIVAL, DEPARTURE = date(2026, 7, 1), date(2026, 7, 4)
MIDDLE = date(2026, 7, 2)
SEASON_FROM, SEASON_TO = date(2026, 1, 1), date(2026, 12, 31)

TWIN = D("18000")
CRUISE = "Sunset dhow cruise"
CRUISE_FARE = D("4000")
TRANSFER = D("3500")


@pytest_asyncio.fixture(loop_scope="session")
async def two_stop_trip():
    """Two destinations, two properties, and an excursion at the second."""
    tag = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        fb = (
            await db.execute(select(MealPlan).where(MealPlan.code == "FB"))
        ).scalar_one()
        # Half board on the city leg, not bed and breakfast: a self-catering
        # plan would need a chef fee and a food cost before the quote could be
        # issued (§3.4), which is a different rule being tested elsewhere.
        hb = (
            await db.execute(select(MealPlan).where(MealPlan.code == "HB"))
        ).scalar_one()
        citizen = (
            await db.execute(
                select(ResidenceCategory).where(ResidenceCategory.key == "citizen")
            )
        ).scalar_one()

        made: dict[str, str] = {}
        for key, label, plan in (
            ("city", "Programme City", hb),
            ("coast", "Programme Coast", fb),
        ):
            where = Destination(
                name=f"{label} {tag}",
                slug=f"{key}-programme-{tag}",
                type="city" if key == "city" else "beach",
            )
            db.add(where)
            await db.flush()
            hotel = Accommodation(
                name=f"{label} Hotel {tag}",
                slug=f"{key}-hotel-{tag}",
                destination_id=where.id,
                category="hotel",
            )
            db.add(hotel)
            await db.flush()
            room = RoomType(
                accommodation_id=hotel.id,
                name="Twin",
                code=f"{key[:3].upper()}TW",
                max_occupancy=2,
            )
            db.add(room)
            await db.flush()
            for occupancy, amount in ((2, TWIN), (1, TWIN * D("0.75"))):
                db.add(
                    AccommodationRate(
                        accommodation_id=hotel.id,
                        room_type_id=room.id,
                        meal_plan_id=plan.id,
                        residence_category_id=citizen.id,
                        season_name="standard",
                        occupancy=occupancy,
                        effective_from=SEASON_FROM,
                        effective_to=SEASON_TO,
                        currency="KES",
                        rate_per_night=amount,
                        rate_kind="sto",
                    )
                )
            # A road transfer tariff for this destination, or the journey is
            # unpriced and the quote cannot be issued at all (§3.10) — which is
            # a different rule, correctly blocking, and not this file's subject.
            db.add(
                TransferRate(
                    destination_id=where.id,
                    vehicle_type="saloon",
                    passenger_capacity=3,
                    route_label="Airport transfer",
                    price_per_leg=TRANSFER,
                    currency="KES",
                    effective_from=SEASON_FROM,
                    effective_to=SEASON_TO,
                )
            )
            made[f"{key}_destination"] = str(where.id)
            made[f"{key}_hotel"] = str(hotel.id)

        cruise = Activity(
            name=CRUISE,
            slug=f"programme-cruise-{tag}",
            destination_id=uuid.UUID(made["coast_destination"]),
            is_optional=False,
            is_mandatory=True,
        )
        db.add(cruise)
        await db.flush()
        db.add(
            ActivityRate(
                activity_id=cruise.id,
                residence_category_id=citizen.id,
                currency="KES",
                adult_price=CRUISE_FARE,
                child_price=CRUISE_FARE / 2,
                effective_from=SEASON_FROM,
                effective_to=SEASON_TO,
            )
        )
        await db.commit()
        ids = {
            **made,
            "cruise_id": str(cruise.id),
            "citizen": str(citizen.id),
            "meal_plan_fb": str(fb.id),
            "meal_plan_hb": str(hb.id),
        }

    yield ids

    async with AsyncSessionLocal() as db:
        mine = [uuid.UUID(ids["city_hotel"]), uuid.UUID(ids["coast_hotel"])]
        via_option = (
            (
                await db.execute(
                    select(QuoteOption.quote_id).where(
                        QuoteOption.accommodation_id.in_(mine)
                    )
                )
            )
            .scalars()
            .all()
        )
        via_leg = (
            (
                await db.execute(
                    select(QuoteOption.quote_id)
                    .join(
                        QuoteOptionLeg,
                        QuoteOptionLeg.quote_option_id == QuoteOption.id,
                    )
                    .where(QuoteOptionLeg.accommodation_id.in_(mine))
                )
            )
            .scalars()
            .all()
        )
        for quote_id in set(via_option) | set(via_leg):
            row = await db.get(Quote, quote_id)
            if row is not None:
                await db.delete(row)
        await db.flush()
        activity = await db.get(Activity, uuid.UUID(ids["cruise_id"]))
        if activity is not None:
            await db.delete(activity)
        for key in ("city_hotel", "coast_hotel"):
            row = await db.get(Accommodation, uuid.UUID(ids[key]))
            if row is not None:
                await db.delete(row)
        await db.flush()
        for key in ("city_destination", "coast_destination"):
            row = await db.get(Destination, uuid.UUID(ids[key]))
            if row is not None:
                await db.delete(row)
        await db.commit()


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _package(ids):
    return [
        {
            "sequence": 1,
            "destination_id": ids["city_destination"],
            "accommodation_id": ids["city_hotel"],
            "check_in": ARRIVAL.isoformat(),
            "check_out": MIDDLE.isoformat(),
            "requested_meal_plan_id": ids["meal_plan_hb"],
        },
        {
            "sequence": 2,
            "destination_id": ids["coast_destination"],
            "accommodation_id": ids["coast_hotel"],
            "check_in": MIDDLE.isoformat(),
            "check_out": DEPARTURE.isoformat(),
            "requested_meal_plan_id": ids["meal_plan_fb"],
        },
    ]


async def _create(
    client,
    h,
    ids,
    *,
    cruise_day=3,
    segments=None,
    departure=DEPARTURE,
    expect=201,
):
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Programme Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("programme"),
            "residence_category_id": ids["citizen"],
        },
    )
    assert record.status_code == 201, record.text
    body = {
        "client_id": record.json()["id"],
        "presentation_currency": "KES",
        "residence_category_id": ids["citizen"],
        "arrival_date": ARRIVAL.isoformat(),
        "departure_date": departure.isoformat(),
        "pax_count": 2,
        "requested_meal_plan_id": ids["meal_plan_fb"],
        "legs": [
            {
                "destination_id": ids["coast_destination"],
                "nights": 2,
                "activities": [{"activity_id": ids["cruise_id"], "day": cruise_day}],
            }
        ],
        "transport_segments": segments or [],
        "options": [
            {
                "accommodation_id": ids["city_hotel"],
                "is_recommended": True,
                "legs": _package(ids),
            }
        ],
    }
    created = await client.post(f"{API}/quotes", headers=h, json=body)
    assert created.status_code == expect, created.text
    return created


async def _issued(client, h, ids, **kwargs):
    quote = (await _create(client, h, ids, **kwargs)).json()
    priced = await client.post(
        f"{API}/quotes/{quote['id']}/options/price", headers=h
    )
    assert priced.status_code == 200, priced.text
    issued = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert issued.status_code == 200, issued.text
    return quote, await _days(issued.json()["id"])


async def _days(version_id: str) -> list[dict]:
    """The frozen programme, read from the row rather than over the API.

    The snapshot is deliberately not on any response model: it holds cost and
    margin, and a schema with no field for them cannot leak them however the
    renderer is called (§2). So a test that wants to prove what was frozen
    reads the column.
    """
    async with AsyncSessionLocal() as db:
        version = await db.get(QuoteVersion, uuid.UUID(version_id))
        assert version is not None
        options = (version.snapshot or {}).get("options") or []
        assert options, version.snapshot
        return options[0]["days"]


async def test_the_programme_is_frozen_into_the_version(
    client, admin_tokens, two_stop_trip
):
    """Four days for three nights, each under the property that holds its night.

    Frozen rather than derived at render time, for the reason the money is: a
    leg re-dated next week must not change what this version says the client
    was offered.
    """
    h = _h(admin_tokens)
    _, days = await _issued(client, h, two_stop_trip)
    assert [day["number"] for day in days] == [1, 2, 3, 4]
    assert [day["date"] for day in days] == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
    ]
    assert [day["has_night"] for day in days] == [True, True, True, False]
    assert days[0]["destination"].startswith("Programme City")
    assert days[1]["destination"].startswith("Programme Coast")
    assert days[0]["board"] == "HB"
    assert days[1]["board"] == "FB"
    assert days[-1]["board"] == ""
    assert days[-1]["is_departure"] is True


async def test_the_excursion_lands_on_the_day_it_was_scheduled_for(
    client, admin_tokens, two_stop_trip
):
    """Day three, and nowhere else — the same day its fare was picked for."""
    h = _h(admin_tokens)
    _, days = await _issued(client, h, two_stop_trip, cruise_day=3)
    assert days[2]["excursions"] == [CRUISE]
    assert all(not day["excursions"] for day in days if day["number"] != 3)


async def test_a_dated_transfer_lands_on_its_own_day(
    client, admin_tokens, two_stop_trip
):
    """Arrival transfer on day one, departure transfer on day four."""
    h = _h(admin_tokens)
    _, days = await _issued(
        client,
        h,
        two_stop_trip,
        segments=[
            {
                "sequence": 1,
                "kind": "transfer",
                "mode": "road",
                "vehicle_type": "saloon",
                "destination_id": two_stop_trip["city_destination"],
                "description": "Airport to hotel",
                "travel_date": ARRIVAL.isoformat(),
            },
            {
                "sequence": 2,
                "kind": "transfer",
                "mode": "road",
                "vehicle_type": "saloon",
                "destination_id": two_stop_trip["coast_destination"],
                "description": "Hotel to airport",
                "travel_date": DEPARTURE.isoformat(),
            },
        ],
    )
    # Label and duration since §4.2: the day-by-day says how long a drive takes
    # where the route table knows, and these transfers have no route row.
    assert days[0]["movements"] == [
        {"label": "Airport to hotel", "minutes": None}
    ]
    assert days[1]["movements"] == []
    assert days[-1]["movements"] == [
        {"label": "Hotel to airport", "minutes": None}
    ]


async def test_the_day_they_change_hotels_says_so(
    client, admin_tokens, two_stop_trip
):
    """Day two is the move: out of the city hotel, into the coast one.

    It belongs to the coast leg because that is where the night is spent, so
    the property alone would read as though they had woken up there.
    """
    h = _h(admin_tokens)
    quote, days = await _issued(client, h, two_stop_trip)
    assert days[0]["moves_from"] == ""
    assert days[1]["moves_from"].startswith("Programme City Hotel")
    assert days[1]["property_name"].startswith("Programme Coast Hotel")

    page = await client.get(f"{API}/quotes/{quote['id']}/document.html", headers=h)
    assert page.status_code == 200, page.text
    assert "Programme City Hotel" in page.text
    # The arrow between them, which is how the page says "you move today".
    assert "&#8594;" in page.text or "→" in page.text


async def test_the_rendered_document_prints_the_programme(
    client, admin_tokens, two_stop_trip
):
    """The page a client reads before they look at the price.

    One page for the recommended option rather than one per option: five
    options would be five near-identical pages of the same journey.
    """
    h = _h(admin_tokens)
    quote, _ = await _issued(client, h, two_stop_trip)
    page = await client.get(f"{API}/quotes/{quote['id']}/document.html", headers=h)
    assert page.status_code == 200, page.text
    html = page.text

    assert "Your Itinerary" in html
    assert "Day 01" in html and "Day 04" in html
    # The weekday matters on a programme: a client checks it against a diary.
    assert "Wed 01 Jul" in html
    assert "Sat 04 Jul" in html
    assert CRUISE in html
    # The board basis in the words a client reads, not the two-letter code —
    # and per leg, which is what a package's plan is (§3.9).
    assert "Half-board meals" in html
    assert "Full-board meals" in html
    # The departure day promises no meals it did not sell.
    assert "Checkout" in html


async def test_a_beach_stay_with_nothing_happening_gets_no_programme_page(
    client, admin_tokens, sample_catalogue
):
    """A page reading "Diani, full board" four times over is padding.

    The document's whole argument is that it does not pad, so the programme
    appears where the trip has a shape: a journey, an excursion, or more than
    one property.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Quiet Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("quiet"),
            "residence_category_id": ids["residence_citizen"],
        },
    )
    quote = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record.json()["id"],
            "presentation_currency": "KES",
            "residence_category_id": ids["residence_citizen"],
            "arrival_date": "2026-07-01",
            "departure_date": "2026-07-04",
            "pax_count": 2,
            "requested_meal_plan_id": ids["meal_plan_fb"],
            "options": [
                {
                    "accommodation_id": ids["acc_sto_full_board"],
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
    page = await client.get(f"{API}/quotes/{quote_id}/document.html", headers=h)
    assert page.status_code == 200, page.text
    assert "Your Itinerary" not in page.text
    # The days are still frozen on the version — the page is a presentation
    # decision, and a later itinerary view can render them all.
    assert len(await _days(issued.json()["id"])) == 4


async def test_an_excursion_off_the_trip_is_refused_at_creation(
    client, admin_tokens, two_stop_trip
):
    """Day nine of a four-day trip: the fare comes from the wrong date.

    Refused where the agent can still fix it. The alternative is a quote that
    prices cleanly off a tariff window the group is not here for, which nothing
    on the finished document would show.
    """
    h = _h(admin_tokens)
    refused = await _create(client, h, two_stop_trip, cruise_day=9, expect=400)
    body = refused.json()
    assert "itinerary_activity_off_trip" in body["error"]["message"]
    assert "day 9" in body["error"]["message"]


async def test_a_transfer_dated_outside_the_trip_is_refused_at_creation(
    client, admin_tokens, two_stop_trip
):
    """It would price off a tariff window that is not the one it is charged at."""
    h = _h(admin_tokens)
    refused = await _create(
        client,
        h,
        two_stop_trip,
        segments=[
            {
                "sequence": 1,
                "kind": "transfer",
                "mode": "road",
                "vehicle_type": "saloon",
                "destination_id": two_stop_trip["city_destination"],
                "description": "Airport to hotel",
                "travel_date": (DEPARTURE + timedelta(days=30)).isoformat(),
            }
        ],
        expect=400,
    )
    assert "itinerary_movement_off_trip" in refused.json()["error"]["message"]


async def test_an_undated_transfer_is_an_advisory_at_readiness(
    client, admin_tokens, two_stop_trip
):
    """Priced at the arrival tariff, and absent from the day-by-day.

    Not blocking: the price is right and the programme is incomplete, which is
    a document to finish rather than a figure to fix.
    """
    h = _h(admin_tokens)
    quote = (
        await _create(
            client,
            h,
            two_stop_trip,
            segments=[
                {
                    "sequence": 1,
                    "kind": "transfer",
                    "mode": "road",
                    "vehicle_type": "saloon",
                    "destination_id": two_stop_trip["city_destination"],
                    "description": "Airport to hotel",
                }
            ],
        )
    ).json()
    readiness = await client.get(
        f"{API}/quotes/{quote['id']}/readiness", headers=h
    )
    assert readiness.status_code == 200, readiness.text
    body = readiness.json()
    undated = [
        p for p in body["problems"] if p["code"] == "itinerary_movement_undated"
    ]
    assert undated, body["problems"]
    assert undated[0]["severity"] == "advisory"
    assert body["is_ready"] is True
