"""Route sequencing at readiness (§4.3), end to end.

The rules are covered in ``test_sequencing.py``. What this file checks is that
they reach the agent — with real route rows, real package legs, and the
threshold read from the pricing config rather than a literal.

The one thing worth stating about the wiring: the shorter-order search asks
about roads the itinerary does **not** use, which is the point of it. The first
version cached only the drives already sequenced, so every alternative ordering
looked like a road with no row on file and nothing was ever suggested. The test
below is the one that would have caught it.
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
from app.modules.destinations.models import Destination
from app.modules.quotes.models import (
    Quote,
    QuoteOption,
    QuoteOptionLeg,
    QuoteVersion,
)
from app.modules.residence.models import ResidenceCategory
from app.modules.transport.models import Route
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal

ARRIVAL = date(2026, 7, 1)
SEASON_FROM, SEASON_TO = date(2026, 1, 1), date(2026, 12, 31)
TWIN = D("18000")

# Four destinations, and the reason for four: with the ends fixed, a trip of
# four legs has one alternative ordering and it is the reverse of the given
# one — the same distance, every time. A genuine saving needs three legs in the
# middle, which is a five-leg itinerary. That is also the real shape of the
# mistake this catches: nobody mis-orders a two-stop trip.
#
# Invented, shaped like the real network: the hub is close to everything, the
# two parks are far from each other, and the coast is next to the southern park
# and a long way from the northern one.
#   hub   -> north   240 km,  5 h
#   hub   -> south   270 km,  5.5 h
#   hub   -> coast   490 km, 10 h
#   north -> south   700 km, 12 h
#   north -> coast   800 km, 15 h
#   south -> coast   200 km,  4 h
ROADS = {
    ("hub", "north"): (D("240"), 300),
    ("hub", "south"): (D("270"), 330),
    ("hub", "coast"): (D("490"), 600),
    ("north", "south"): (D("700"), 720),
    ("north", "coast"): (D("800"), 900),
    ("south", "coast"): (D("200"), 240),
}
PLACES = (
    ("hub", "Sequence Hub", "city"),
    ("north", "Sequence North Park", "park"),
    ("south", "Sequence South Park", "park"),
    ("coast", "Sequence Coast", "beach"),
)


@pytest_asyncio.fixture(loop_scope="session")
async def three_stop_network():
    """Three destinations, a property in each, and the roads between them."""
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

        ids: dict[str, str] = {"tag": tag}
        places: dict[str, uuid.UUID] = {}
        for key, label, kind in PLACES:
            where = Destination(
                name=f"{label} {tag}",
                slug=f"{key}-sequence-{tag}",
                type=kind,
            )
            db.add(where)
            await db.flush()
            places[key] = where.id
            ids[f"{key}_destination"] = str(where.id)

            hotel = Accommodation(
                name=f"{label} Lodge {tag}",
                slug=f"{key}-sequence-lodge-{tag}",
                destination_id=where.id,
                category="lodge",
            )
            db.add(hotel)
            await db.flush()
            room = RoomType(
                accommodation_id=hotel.id,
                name="Twin",
                code=f"{key[:2].upper()}T",
                max_occupancy=2,
            )
            db.add(room)
            await db.flush()
            for occupancy, amount in ((2, TWIN), (1, TWIN * D("0.75"))):
                db.add(
                    AccommodationRate(
                        accommodation_id=hotel.id,
                        room_type_id=room.id,
                        meal_plan_id=fb.id,
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
            ids[f"{key}_hotel"] = str(hotel.id)

        for (start, end), (km, minutes) in ROADS.items():
            db.add(
                Route(
                    origin_id=places[start],
                    destination_id=places[end],
                    label=f"{start} to {end} {tag}",
                    distance_km=km,
                    drive_time_minutes=minutes,
                    effective_from=SEASON_FROM,
                    effective_to=SEASON_TO,
                )
            )
        await db.commit()
        ids["citizen"] = str(citizen.id)
        ids["meal_plan_fb"] = str(fb.id)

    yield ids

    async with AsyncSessionLocal() as db:
        mine = [uuid.UUID(ids[f"{key}_hotel"]) for key, _, _ in PLACES]
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
        for key, _, _ in PLACES:
            row = await db.get(Accommodation, uuid.UUID(ids[f"{key}_hotel"]))
            if row is not None:
                await db.delete(row)
        await db.flush()
        for key, _, _ in PLACES:
            row = await db.get(Destination, uuid.UUID(ids[f"{key}_destination"]))
            if row is not None:
                await db.delete(row)  # cascades to the routes
        await db.commit()


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _legs(ids, order, nights):
    """Package legs for ``order``, each holding its own count of nights."""
    out = []
    day = ARRIVAL
    for index, (key, count) in enumerate(zip(order, nights, strict=True), start=1):
        out.append(
            {
                "sequence": index,
                "destination_id": ids[f"{key}_destination"],
                "accommodation_id": ids[f"{key}_hotel"],
                "check_in": day.isoformat(),
                "check_out": (day + timedelta(days=count)).isoformat(),
            }
        )
        day += timedelta(days=count)
    return out, day


async def _readiness(client, h, ids, *, order, nights):
    legs, departure = _legs(ids, order, nights)
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Sequence Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("sequence"),
            "residence_category_id": ids["citizen"],
        },
    )
    assert record.status_code == 201, record.text
    created = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record.json()["id"],
            "presentation_currency": "KES",
            "residence_category_id": ids["citizen"],
            "arrival_date": ARRIVAL.isoformat(),
            "departure_date": departure.isoformat(),
            "pax_count": 2,
            "requested_meal_plan_id": ids["meal_plan_fb"],
            "options": [
                {
                    "accommodation_id": ids[f"{order[0]}_hotel"],
                    "is_recommended": True,
                    "legs": legs,
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    quote_id = created.json()["id"]
    priced = await client.post(f"{API}/quotes/{quote_id}/options/price", headers=h)
    assert priced.status_code == 200, priced.text
    readiness = await client.get(f"{API}/quotes/{quote_id}/readiness", headers=h)
    assert readiness.status_code == 200, readiness.text
    return quote_id, readiness.json()


def _codes(body, code):
    return [p for p in body["problems"] if p["code"] == code]


async def test_a_drive_past_the_working_day_is_reported(
    client, admin_tokens, three_stop_network
):
    """North park to south park is twelve hours, and park gates close.

    Contiguity says every night has a bed (§3.9). It says nothing about the
    road between the beds, and this is the first thing in the system that
    reads the map.
    """
    _, body = await _readiness(
        client,
        _h(admin_tokens),
        three_stop_network,
        order=["hub", "north", "south"],
        nights=[1, 3, 3],
    )
    fault = _codes(body, "sequence_drive_too_long")
    assert fault, body["problems"]
    assert fault[0]["severity"] == "advisory"
    assert "12.0 hours" in fault[0]["message"]
    assert "Option 1" in fault[0]["message"]
    # Advisory: a long day is sellable, and the agent may know the client asked
    # for it. Nothing here stops the quote going out.
    assert body["is_ready"] is True


async def test_a_shorter_order_is_suggested_with_its_saving(
    client, admin_tokens, three_stop_network
):
    """The test that would have caught the cache bug.

    As sequenced — hub, coast, north, south, hub — the trip drives
    490 + 800 + 700 + 270 = **2,260 km**, which is the itinerary an agent
    builds by working down a list of places rather than looking at a map.
    Hub, north, coast, south, hub drives 240 + 800 + 200 + 270 = **1,510**.
    750 km saved: the best part of two days nobody spends in a vehicle.

    The suggestion needs the north-to-coast and south-to-coast roads in
    positions the given itinerary never uses. The first version of this looked
    up only the drives already sequenced, so every alternative ordering read as
    a road with no row on file and nothing was ever suggested.
    """
    _, body = await _readiness(
        client,
        _h(admin_tokens),
        three_stop_network,
        order=["hub", "coast", "north", "south", "hub"],
        nights=[1, 3, 2, 2, 1],
    )
    fault = _codes(body, "sequence_shorter_order_exists")
    assert fault, body["problems"]
    assert fault[0]["severity"] == "advisory"
    assert "750 km less" in fault[0]["message"]
    assert "Sequence North Park" in fault[0]["message"]
    # A note, not a correction: packages are curated, and the agent may have
    # sequenced it for a flight time.
    assert "may be deliberate" in fault[0]["message"]
    # And it is advice — a long, badly ordered trip still goes out if the agent
    # says so.
    assert body["is_ready"] is True


async def test_an_order_that_is_already_shortest_is_left_alone(
    client, admin_tokens, three_stop_network
):
    """The same four places, ordered off a map, and it gets no note."""
    _, body = await _readiness(
        client,
        _h(admin_tokens),
        three_stop_network,
        order=["hub", "north", "coast", "south", "hub"],
        nights=[1, 3, 2, 2, 1],
    )
    assert not _codes(body, "sequence_shorter_order_exists")


async def test_a_two_stop_trip_gets_no_reorder_note(
    client, admin_tokens, three_stop_network
):
    """With the ends fixed there is nothing to permute, and no false advice.

    Hub to the north park and back has one ordering. So does any four-leg
    trip: its only alternative is the reverse, which is the same distance.
    """
    _, body = await _readiness(
        client,
        _h(admin_tokens),
        three_stop_network,
        order=["hub", "north", "hub"],
        nights=[1, 3, 1],
    )
    assert not _codes(body, "sequence_shorter_order_exists")


async def test_one_night_after_a_long_drive_is_flagged(
    client, admin_tokens, three_stop_network
):
    """Most of two days on the road for a single evening there."""
    _, body = await _readiness(
        client,
        _h(admin_tokens),
        three_stop_network,
        order=["hub", "north", "south"],
        nights=[1, 3, 1],
    )
    fault = _codes(body, "sequence_stay_shorter_than_drive")
    assert fault, body["problems"]
    assert "one night after about 12.0 hours" in fault[0]["message"]


async def test_a_missing_road_is_reported_against_the_package(
    client, admin_tokens, three_stop_network, sample_catalogue
):
    """A leg to a place with no road on file from the last one.

    Advisory here: the blocking version lives where the money is — a movement
    on our own vehicle with no route refuses to be issued (§4.2). This is the
    note that the itinerary itself has a gap in it.
    """
    h = _h(admin_tokens)
    ids = dict(three_stop_network)
    # Diani from the demo catalogue: no route to any of our three places.
    ids["far_destination"] = sample_catalogue["destination_diani"]
    ids["far_hotel"] = sample_catalogue["acc_sto_full_board"]
    _, body = await _readiness(
        client, h, ids, order=["hub", "far"], nights=[1, 3]
    )
    fault = _codes(body, "sequence_road_not_on_file")
    assert fault, body["problems"]
    assert fault[0]["severity"] == "advisory"
    assert "Enter the route" in fault[0]["message"]


async def test_a_single_property_option_has_no_order_to_grade(
    client, admin_tokens, sample_catalogue
):
    """Most quotes are one hotel, and they must not collect notes about roads."""
    h, ids = _h(admin_tokens), sample_catalogue
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"One Hotel Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("onehotel"),
            "residence_category_id": ids["residence_citizen"],
        },
    )
    created = await client.post(
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
    assert created.status_code == 201, created.text
    quote_id = created.json()["id"]
    await client.post(f"{API}/quotes/{quote_id}/options/price", headers=h)
    body = (
        await client.get(f"{API}/quotes/{quote_id}/readiness", headers=h)
    ).json()
    assert not [
        p for p in body["problems"] if p["code"].startswith("sequence_")
    ], body["problems"]


async def test_the_working_day_is_configuration(
    client, admin_tokens, three_stop_network, restore_pricing_config
):
    """Raise what the operation will sell and the note goes away.

    "Too long to drive in a day" is a commercial judgement about what Heissal
    puts in front of a client, not a fact about roads — so it is a config row,
    and this is the test that proves the config is what is doing the work.
    """
    h = _h(admin_tokens)
    config = (await client.get(f"{API}/pricing-config", headers=h)).json()
    assert config["max_drive_minutes_per_day"] == 600

    raised = await client.patch(
        f"{API}/pricing-config",
        headers=h,
        json={"max_drive_minutes_per_day": 780},
    )
    assert raised.status_code == 200, raised.text
    _, body = await _readiness(
        client,
        h,
        three_stop_network,
        order=["hub", "north", "south"],
        nights=[1, 3, 3],
    )
    assert not _codes(body, "sequence_drive_too_long")


async def test_the_driving_is_frozen_on_the_version_and_reaches_the_worksheet(
    client, admin_tokens, three_stop_network
):
    """Hub → north → coast → south → hub: 1,510 km and 26 hours of it.

    240 + 800 + 200 + 270 kilometres; 300 + 900 + 240 + 330 minutes. The
    longest single drive is named beside the total because that is the figure
    an operator argues with a client about — two thousand-kilometre trips are
    different trips if one is a single fifteen-hour push.

    Frozen with the money for the same reason: a route re-measured next month
    must not change what this version says the trip was.
    """
    h = _h(admin_tokens)
    legs, departure = _legs(
        three_stop_network,
        ["hub", "north", "coast", "south", "hub"],
        [1, 3, 2, 2, 1],
    )
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Driving Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("driving"),
            "residence_category_id": three_stop_network["citizen"],
        },
    )
    created = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record.json()["id"],
            "presentation_currency": "KES",
            "residence_category_id": three_stop_network["citizen"],
            "arrival_date": ARRIVAL.isoformat(),
            "departure_date": departure.isoformat(),
            "pax_count": 2,
            "requested_meal_plan_id": three_stop_network["meal_plan_fb"],
            "options": [
                {
                    "accommodation_id": three_stop_network["hub_hotel"],
                    "is_recommended": True,
                    "legs": legs,
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    quote_id = created.json()["id"]
    priced = await client.post(f"{API}/quotes/{quote_id}/options/price", headers=h)
    assert priced.status_code == 200, priced.text
    issued = await client.post(f"{API}/quotes/{quote_id}/issue", headers=h)
    assert issued.status_code == 200, issued.text

    async with AsyncSessionLocal() as db:
        version = await db.get(QuoteVersion, uuid.UUID(issued.json()["id"]))
        assert version is not None
        driving = version.snapshot["options"][0]["driving"]
    assert D(driving["total_km"]) == D("1510")
    assert driving["total_minutes"] == 1770
    assert driving["longest_minutes"] == 900
    assert driving["unknown_hops"] == 0
    assert driving["hops"] == 4
    # Already the shortest ordering of these four places, so nothing to suggest.
    assert driving["better_order"] == []

    sheet = await client.get(f"{API}/quotes/{quote_id}/worksheet.html", headers=h)
    assert sheet.status_code == 200, sheet.text
    assert "1,510 km" in sheet.text
    assert "30 h driving" in sheet.text
    assert "longest 15.0 h" in sheet.text


async def test_a_single_property_option_has_no_driving_block(
    client, admin_tokens, sample_catalogue
):
    """Nothing to grade, so nothing frozen — and no 0 km on the worksheet.

    A zero would read as a trip with no driving in it, which is a claim about
    the itinerary rather than the absence of one.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"No Driving Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("nodriving"),
            "residence_category_id": ids["residence_citizen"],
        },
    )
    created = await client.post(
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
    quote_id = created.json()["id"]
    await client.post(f"{API}/quotes/{quote_id}/options/price", headers=h)
    issued = await client.post(f"{API}/quotes/{quote_id}/issue", headers=h)
    assert issued.status_code == 200, issued.text
    async with AsyncSessionLocal() as db:
        version = await db.get(QuoteVersion, uuid.UUID(issued.json()["id"]))
        assert version.snapshot["options"][0]["driving"] is None
    sheet = await client.get(f"{API}/quotes/{quote_id}/worksheet.html", headers=h)
    assert "km driving" not in sheet.text
    assert "0 km" not in sheet.text
