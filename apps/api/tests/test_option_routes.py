"""The drive on our own vehicle, priced (§4.2).

Stage 3.10 put transport in the price and left one hole in it. A hired transfer
is priced from a tariff; a movement on **our own** vehicle was skipped, on the
reasoning that the Stage 2 fleet model would cost it — and the Stage 2 model is
not in the Stage 3 build-up. So an option whose group is driven to the Mara in
the company Land Cruiser carried the beds, the park fees, and **nothing at all**
for the eight-hour drive.

What was missing to close it was the distance, which the catalogue cannot give:
coordinates answer a straight line, and no projection knows that the last
stretch wants a 4×4 after rain. So the route table is hand-entered, and this
file is about what follows from it — the fuel and the crew in the price, and a
vehicle the road does not take refusing to be issued.

Every figure invented, and worked by hand in the test that asserts it.
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
from app.modules.quotes.models import Quote, QuoteOption, QuoteVersion
from app.modules.residence.models import ResidenceCategory
from app.modules.transport.models import Route
from app.modules.vehicles.models import FuelPrice, Vehicle
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal

ARRIVAL, DEPARTURE = "2026-07-01", "2026-07-04"
DRIVE_DAY = date(2026, 7, 1)
SEASON_FROM, SEASON_TO = date(2026, 1, 1), date(2026, 12, 31)

TWIN = D("20000")
# The road, as an operations team would state it.
DISTANCE_KM, DRIVE_MINUTES = D("270"), 330
CRUISER = "safari_land_cruiser"
# The vehicle: 8 km to the litre, a driver at 3,000 a day, 2,000 of running.
KMPL, DRIVER, RUNNING = D("8"), D("3000"), D("2000")
FUEL_PER_LITRE = D("180")
#   270 / 8            = 33.75 litres
#   33.75 x 180        = 6,075 of fuel
#   3,000 + 2,000      = 5,000 of crew and running for the day
FUEL_COST, CREW_COST = D("6075.00"), D("5000")
DRIVE_TOTAL = FUEL_COST + CREW_COST


@pytest_asyncio.fixture(loop_scope="session")
async def own_vehicle_trip():
    """Two places, a road between them, a Land Cruiser and a pump price."""
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

        made: dict[str, str] = {}
        for key, label in (
            ("city", "Route City"),
            ("park", "Route Park"),
            # A third place with no road to anywhere, so "no route on file" can
            # be tested without dating a movement outside the trip — which is
            # its own blocking fault (§4.1).
            ("far", "Route Faraway"),
        ):
            where = Destination(
                name=f"{label} {tag}",
                slug=f"{key}-route-{tag}",
                type="city" if key == "city" else "park",
            )
            db.add(where)
            await db.flush()
            made[f"{key}_destination"] = str(where.id)
        hotel = Accommodation(
            name=f"Route Lodge {tag}",
            slug=f"route-lodge-{tag}",
            destination_id=uuid.UUID(made["park_destination"]),
            category="lodge",
        )
        db.add(hotel)
        await db.flush()
        room = RoomType(
            accommodation_id=hotel.id, name="Twin", code="RTW", max_occupancy=2
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

        # The road: stated once, in one direction, and it needs a Land Cruiser.
        db.add(
            Route(
                origin_id=uuid.UUID(made["city_destination"]),
                destination_id=uuid.UUID(made["park_destination"]),
                label=f"Route City to Route Park {tag}",
                distance_km=DISTANCE_KM,
                drive_time_minutes=DRIVE_MINUTES,
                required_vehicle_types=[CRUISER],
                notes="Last 40 km is murram; impassable after heavy rain.",
                effective_from=SEASON_FROM,
                effective_to=SEASON_TO,
            )
        )
        cruiser = Vehicle(
            name=f"Land Cruiser {tag}",
            slug=f"land-cruiser-{tag}",
            vehicle_type=CRUISER,
            passenger_capacity=6,
            fuel_type=f"diesel-{tag}",
            fuel_consumption_kmpl=KMPL,
            daily_operating_cost=RUNNING,
            driver_cost_per_day=DRIVER,
            currency="KES",
        )
        saloon = Vehicle(
            name=f"Saloon {tag}",
            slug=f"saloon-{tag}",
            vehicle_type="saloon",
            passenger_capacity=3,
            fuel_type=f"diesel-{tag}",
            fuel_consumption_kmpl=D("14"),
            daily_operating_cost=D("800"),
            driver_cost_per_day=D("2000"),
            currency="KES",
        )
        db.add_all([cruiser, saloon])
        await db.flush()
        # A fuel type of our own, so the demo catalogue's pump price cannot
        # quietly stand in for this one.
        db.add(
            FuelPrice(
                fuel_type=f"diesel-{tag}",
                price_per_litre=FUEL_PER_LITRE,
                currency="KES",
                effective_from=SEASON_FROM,
            )
        )
        await db.commit()
        ids = {
            **made,
            "accommodation_id": str(hotel.id),
            "cruiser_id": str(cruiser.id),
            "saloon_id": str(saloon.id),
            "citizen": str(citizen.id),
            "meal_plan_fb": str(fb.id),
            "tag": tag,
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
            row = await db.get(Quote, quote_id)
            if row is not None:
                await db.delete(row)
        await db.flush()
        for key in ("cruiser_id", "saloon_id"):
            row = await db.get(Vehicle, uuid.UUID(ids[key]))
            if row is not None:
                await db.delete(row)
        for price in (
            (
                await db.execute(
                    select(FuelPrice).where(
                        FuelPrice.fuel_type == f"diesel-{ids['tag']}"
                    )
                )
            )
            .scalars()
            .all()
        ):
            await db.delete(price)
        hotel_row = await db.get(Accommodation, uuid.UUID(ids["accommodation_id"]))
        if hotel_row is not None:
            await db.delete(hotel_row)
        await db.flush()
        for key in ("city_destination", "park_destination", "far_destination"):
            row = await db.get(Destination, uuid.UUID(ids[key]))
            if row is not None:
                await db.delete(row)  # cascades to the route
        await db.commit()


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _drive(ids, *, vehicle="cruiser_id", origin=True, destination=True, units=1):
    segment = {
        "sequence": 1,
        "kind": "line_haul",
        "mode": "road",
        "vehicle_id": ids[vehicle],
        "units": units,
        "description": "City to the park",
        "travel_date": DRIVE_DAY.isoformat(),
    }
    if origin:
        segment["origin_id"] = ids["city_destination"]
    if destination:
        segment["destination_id"] = ids["park_destination"]
    return [segment]


async def _quote(client, h, ids, *, segments, pax=4):
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Route Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("route"),
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
            "arrival_date": ARRIVAL,
            "departure_date": DEPARTURE,
            "pax_count": pax,
            "requested_meal_plan_id": ids["meal_plan_fb"],
            "transport_segments": segments,
            "options": [
                {
                    "accommodation_id": ids["accommodation_id"],
                    "is_recommended": True,
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


async def _priced(client, h, ids, **kwargs):
    quote = await _quote(client, h, ids, **kwargs)
    priced = await client.post(
        f"{API}/quotes/{quote['id']}/options/price", headers=h
    )
    assert priced.status_code == 200, priced.text
    return quote, priced.json()


async def test_the_drive_reaches_the_price(client, admin_tokens, own_vehicle_trip):
    """270 km at 8 km/L and 180 a litre, plus a day of driver and running.

    6,075 of fuel and 5,000 of crew: 11,075 that used to be charged at zero,
    on a quote whose beds are 60,000. This is the hole §3.10 left and §4.2
    closes.
    """
    h = _h(admin_tokens)
    _, body = await _priced(
        client, h, own_vehicle_trip, segments=_drive(own_vehicle_trip)
    )
    components = body["options"][0]["build_up"]["components"]
    assert "transport" in components, components
    assert D(components["transport"]) == DRIVE_TOTAL
    assert D(components["transport"]) == D("11075.00")


async def test_the_fuel_and_the_crew_are_separate_lines(
    client, admin_tokens, own_vehicle_trip
):
    """Two costs in two currencies, even when they happen to be one.

    Fuel is bought in the currency the pump price is recorded in and the crew
    is paid in the vehicle's own. Keeping them apart means no exchange rate is
    applied to a figure that never needed one — and an operator reconciling a
    fuel invoice can find the litres.
    """
    h = _h(admin_tokens)
    _, body = await _priced(
        client, h, own_vehicle_trip, segments=_drive(own_vehicle_trip)
    )
    charges = body["transport"]
    assert len(charges) == 2, charges
    fuel = next(one for one in charges if "fuel" in one["label"])
    crew = next(one for one in charges if "driver" in one["label"])

    assert D(fuel["cost"]) == FUEL_COST
    assert D(fuel["unit_amount"]) == FUEL_PER_LITRE
    # The label carries the two figures an operator checks the litres against.
    assert "270 km" in fuel["label"] and "8 km/L" in fuel["label"]
    assert D(crew["cost"]) == CREW_COST
    # And the drive time, because that is what a day of crew is bought for.
    assert "5.5 h" in crew["label"]
    # Both traceable to the route row and the vehicle.
    assert all("routes " in one["source"] for one in charges)
    assert all("vehicles " in one["source"] for one in charges)


async def test_three_vehicles_cost_three_drives(
    client, admin_tokens, own_vehicle_trip
):
    """``units`` is how many vehicles the movement takes.

    A group of twenty needs three Land Cruisers, and three Land Cruisers burn
    three lots of fuel and pay three drivers.
    """
    h = _h(admin_tokens)
    _, body = await _priced(
        client,
        h,
        own_vehicle_trip,
        segments=_drive(own_vehicle_trip, units=3),
        pax=18,
    )
    assert D(body["options"][0]["build_up"]["components"]["transport"]) == (
        DRIVE_TOTAL * 3
    )


async def test_a_vehicle_the_road_does_not_take_blocks_the_issue(
    client, admin_tokens, own_vehicle_trip
):
    """The column the client asked for, doing its job.

    A saloon up a road stated to need a Land Cruiser is under-priced *and* a
    drive that does not happen. Both are invisible on a proposal, so it blocks
    rather than warns — and the route's own note travels with the message,
    because "impassable after heavy rain" is why the requirement exists.
    """
    h = _h(admin_tokens)
    quote, _ = await _priced(
        client,
        h,
        own_vehicle_trip,
        segments=_drive(own_vehicle_trip, vehicle="saloon_id"),
        pax=3,
    )
    readiness = await client.get(
        f"{API}/quotes/{quote['id']}/readiness", headers=h
    )
    assert readiness.status_code == 200, readiness.text
    body = readiness.json()
    fault = next(
        p for p in body["problems"] if p["code"] == "route_vehicle_not_suitable"
    )
    assert fault["severity"] == "blocking"
    assert "safari_land_cruiser" in fault["message"]
    assert "murram" in fault["message"]
    assert body["is_ready"] is False

    refused = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert refused.status_code == 400, refused.text


async def test_a_road_with_no_route_on_file_is_unpriced_rather_than_free(
    client, admin_tokens, own_vehicle_trip
):
    """Blocking, for the §3.10 reason: a zero reads as a leg not charged for."""
    h = _h(admin_tokens)
    quote, body = await _priced(
        client,
        h,
        own_vehicle_trip,
        # A place with no road on file. The park-to-city direction would price
        # happily off the row read backwards, which is the next test.
        segments=[
            {
                "sequence": 1,
                "kind": "line_haul",
                "mode": "road",
                "vehicle_id": own_vehicle_trip["cruiser_id"],
                "origin_id": own_vehicle_trip["city_destination"],
                "destination_id": own_vehicle_trip["far_destination"],
                "description": "City to faraway",
                "travel_date": DRIVE_DAY.isoformat(),
            }
        ],
    )
    assert "transport" not in body["options"][0]["build_up"]["components"]
    readiness = await client.get(
        f"{API}/quotes/{quote['id']}/readiness", headers=h
    )
    problems = readiness.json()["problems"]
    fault = next(p for p in problems if p["code"] == "route_not_on_file")
    assert fault["severity"] == "blocking"
    assert "driven kilometres" in fault["message"]


async def test_the_road_is_read_in_either_direction(
    client, admin_tokens, own_vehicle_trip
):
    """The return leg is not typed twice.

    Distance is symmetric and time roughly is, so a route entered one way
    prices the other — and the worksheet says the row was read in reverse,
    because an operator checking a fuel figure has to be able to find it.
    """
    h = _h(admin_tokens)
    _, body = await _priced(
        client,
        h,
        own_vehicle_trip,
        segments=[
            {
                "sequence": 1,
                "kind": "line_haul",
                "mode": "road",
                "vehicle_id": own_vehicle_trip["cruiser_id"],
                "origin_id": own_vehicle_trip["park_destination"],
                "destination_id": own_vehicle_trip["city_destination"],
                "description": "The park back to the city",
                "travel_date": DRIVE_DAY.isoformat(),
            }
        ],
    )
    assert D(body["options"][0]["build_up"]["components"]["transport"]) == DRIVE_TOTAL
    assert all(
        "read in reverse" in one["source"] for one in body["transport"]
    ), body["transport"]


async def test_a_drive_with_no_origin_says_which_end_is_missing(
    client, admin_tokens, own_vehicle_trip
):
    """Asked for, not guessed: transport is priced once for the whole quote."""
    h = _h(admin_tokens)
    quote, body = await _priced(
        client,
        h,
        own_vehicle_trip,
        segments=_drive(own_vehicle_trip, origin=False),
    )
    assert "transport" not in body["options"][0]["build_up"]["components"]
    readiness = await client.get(
        f"{API}/quotes/{quote['id']}/readiness", headers=h
    )
    fault = next(
        p
        for p in readiness.json()["problems"]
        if p["code"] == "route_endpoints_missing"
    )
    assert fault["severity"] == "blocking"
    assert "no start" in fault["message"]


async def test_the_route_is_reachable_over_the_api(
    client, admin_tokens, own_vehicle_trip
):
    """Hand-entered means an endpoint, not a seed script.

    The client's operations team is the source of this table — they told us
    they will state which roads need a 4×4 — so entering one has to be
    something they can do.
    """
    h = _h(admin_tokens)
    created = await client.post(
        f"{API}/routes",
        headers=h,
        json={
            "origin_id": own_vehicle_trip["park_destination"],
            "destination_id": own_vehicle_trip["city_destination"],
            "label": "Park to city, the long way",
            "distance_km": "310.5",
            "drive_time_minutes": 400,
            # Entered with the spacing and casing a person actually types.
            "required_vehicle_types": [" Safari_Land_Cruiser ", "minibus_4x4"],
            "notes": "Avoids the escarpment.",
            "effective_from": SEASON_FROM.isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["required_vehicle_types"] == ["safari_land_cruiser", "minibus_4x4"]
    assert D(body["distance_km"]) == D("310.5")

    listed = await client.get(
        f"{API}/routes",
        headers=h,
        params={"destination_id": own_vehicle_trip["city_destination"]},
    )
    assert listed.status_code == 200, listed.text
    # Either end: an operator looking up a place means every road to and from it.
    assert body["id"] in [row["id"] for row in listed.json()]

    gone = await client.delete(f"{API}/routes/{body['id']}", headers=h)
    assert gone.status_code == 204, gone.text


async def test_a_route_needs_two_different_places(client, admin_tokens, own_vehicle_trip):
    h = _h(admin_tokens)
    refused = await client.post(
        f"{API}/routes",
        headers=h,
        json={
            "origin_id": own_vehicle_trip["city_destination"],
            "destination_id": own_vehicle_trip["city_destination"],
            "distance_km": "10",
            "drive_time_minutes": 20,
            "effective_from": SEASON_FROM.isoformat(),
        },
    )
    assert refused.status_code == 422, refused.text


async def test_a_transfer_tariff_can_be_loaded_over_the_api(
    client, admin_tokens, own_vehicle_trip
):
    """Readiness has said "load the fare before issuing" since §3.10.

    Until now the only way to load one was to edit a seed script — a blocking
    message whose fix needed a developer.
    """
    h = _h(admin_tokens)
    created = await client.post(
        f"{API}/destinations/{own_vehicle_trip['park_destination']}/transfer-rates",
        headers=h,
        json={
            "vehicle_type": "saloon",
            "passenger_capacity": 3,
            "route_label": "Airstrip to lodge",
            "price_per_leg": "4500",
            "currency": "kes",
            "effective_from": SEASON_FROM.isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["currency"] == "KES"

    listed = await client.get(
        f"{API}/destinations/{own_vehicle_trip['park_destination']}/transfer-rates",
        headers=h,
    )
    assert created.json()["id"] in [row["id"] for row in listed.json()]
    await client.delete(f"{API}/transfer-rates/{created.json()['id']}", headers=h)


async def test_a_flight_fare_cannot_be_loaded_and_the_message_says_why(
    client, admin_tokens, own_vehicle_trip
):
    """Air is unpriceable, not merely unpriced (§3.10).

    The refusal explains the licence rather than listing permitted values,
    because an operator who reads "must be one of road, rail" will assume air
    is coming.
    """
    h = _h(admin_tokens)
    refused = await client.post(
        f"{API}/destinations/{own_vehicle_trip['park_destination']}/transport-modes",
        headers=h,
        json={
            "mode": "air",
            "price": "12000",
            "currency": "KES",
            "effective_from": SEASON_FROM.isoformat(),
        },
    )
    assert refused.status_code == 400, refused.text
    assert "client's to buy" in refused.json()["error"]["message"]


async def test_the_drive_time_reaches_the_day_by_day(
    client, admin_tokens, own_vehicle_trip
):
    """"about 5 h 30" on the day the group drives (§4.1 + §4.2).

    The route table is where the time comes from, and the day-by-day is where a
    client plans around it. Hedged with "about" deliberately: the figure is an
    operator's timing of a Kenyan road, and printing it flat invites a
    proposal to be held to a five-thirty arrival on a route where a lorry on
    the escarpment costs an hour.
    """
    h = _h(admin_tokens)
    quote, _ = await _priced(
        client, h, own_vehicle_trip, segments=_drive(own_vehicle_trip)
    )
    issued = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert issued.status_code == 200, issued.text

    async with AsyncSessionLocal() as db:
        version = await db.get(QuoteVersion, uuid.UUID(issued.json()["id"]))
        assert version is not None
        days = version.snapshot["options"][0]["days"]
    # Frozen with the label AND the minutes, so the page can phrase it and a
    # later itinerary view can use the figure.
    assert days[0]["movements"] == [
        {"label": "City to the park", "minutes": DRIVE_MINUTES}
    ]

    page = await client.get(f"{API}/quotes/{quote['id']}/document.html", headers=h)
    assert page.status_code == 200, page.text
    assert "about 5 h 30" in page.text
    # And never as a flat promise.
    assert "5 h 30 exactly" not in page.text


async def test_a_movement_with_no_route_is_named_without_a_time(
    client, admin_tokens, sample_catalogue
):
    """A hired transfer has a tariff and no route row, so it gets no time.

    Estimating one from the tariff would be inventing a drive time, and "about
    two hours" is the kind of invention a client plans a flight around.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"No Route Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("noroute"),
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
            "arrival_date": ARRIVAL,
            "departure_date": DEPARTURE,
            "pax_count": 2,
            "requested_meal_plan_id": ids["meal_plan_fb"],
            "transport_segments": [
                {
                    "sequence": 1,
                    "kind": "transfer",
                    "mode": "road",
                    "vehicle_type": "saloon",
                    "destination_id": ids["destination_diani"],
                    "description": "Airport to hotel",
                    "travel_date": ARRIVAL,
                }
            ],
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
    assert "Airport to hotel" in page.text
    assert "about" not in page.text.split("Day by day")[-1].split("</section>")[0]
