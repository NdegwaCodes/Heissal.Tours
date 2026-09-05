"""Crewing a trip, end to end (§8.1).

The rules are in ``test_roster.py``. This is the wiring, and four things about
it are worth more than the rest:

* a vehicle cannot be on two trips at once — the gap §2.5 left, where a
  vehicle existed only as a costing input and nothing said it was busy;
* a clash can be **overridden**, and the override leaves a name and a reason;
* a **cancelled** booking releases what it held, so the calendar stays true;
* and the departure board says what is missing, without deciding a trip is
  ready.
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
from app.modules.bookings.models import Booking
from app.modules.operations.models import CrewMember, TripAssignment
from app.modules.operations.roster import (
    LICENCE_EXPIRING,
    NO_DRIVER,
    NO_VEHICLE,
    NOT_ENOUGH_SEATS,
    TIGHT_TURNAROUND,
)
from app.modules.quotes.models import Quote, QuoteOption
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal
TODAY = date.today()
ARRIVAL = TODAY + timedelta(days=60)
DEPARTURE = ARRIVAL + timedelta(days=3)


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest_asyncio.fixture(loop_scope="session")
async def swept():
    """Remove the quotes, bookings, assignments and crew this module created."""
    quotes: list[str] = []
    crew: list[str] = []
    yield quotes, crew
    async with AsyncSessionLocal() as db:
        for quote_id in quotes:
            bookings = (
                (
                    await db.execute(
                        select(Booking).where(Booking.quote_id == uuid.UUID(quote_id))
                    )
                )
                .scalars()
                .all()
            )
            for booking in bookings:
                for row in (
                    (
                        await db.execute(
                            select(TripAssignment).where(
                                TripAssignment.booking_id == booking.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                ):
                    await db.delete(row)
                await db.flush()
                await db.delete(booking)
            await db.flush()
            row = await db.get(Quote, uuid.UUID(quote_id))
            if row is not None:
                await db.delete(row)
        await db.flush()
        for crew_id in crew:
            member = await db.get(CrewMember, uuid.UUID(crew_id))
            if member is not None:
                await db.delete(member)
        await db.commit()


async def _booking(client, h, ids, swept, *, pax=2, arrival=None, departure=None):
    """A quote, issued, accepted and booked — the state §8.1 starts from."""
    quotes, _crew = swept
    arrival = arrival or ARRIVAL
    departure = departure or DEPARTURE
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Ops Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("ops"),
            "residence_category_id": ids["residence_citizen"],
        },
    )
    assert record.status_code == 201, record.text
    created = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record.json()["id"],
            "presentation_currency": "KES",
            "residence_category_id": ids["residence_citizen"],
            "arrival_date": arrival.isoformat(),
            "departure_date": departure.isoformat(),
            "pax_count": pax,
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
    quote = created.json()
    quotes.append(quote["id"])
    assert (
        await client.post(f"{API}/quotes/{quote['id']}/options/price", headers=h)
    ).status_code == 200
    assert (
        await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    ).status_code == 200
    async with AsyncSessionLocal() as db:
        option_id = (
            (
                await db.execute(
                    select(QuoteOption.id).where(
                        QuoteOption.quote_id == uuid.UUID(quote["id"])
                    )
                )
            )
            .scalars()
            .first()
        )
    assert (
        await client.post(
            f"{API}/quotes/{quote['id']}/accept",
            headers=h,
            json={"option_id": str(option_id)},
        )
    ).status_code == 200
    booked = await client.post(
        f"{API}/quotes/{quote['id']}/booking", headers=h, json={}
    )
    assert booked.status_code == 201, booked.text
    return booked.json()


async def _crew_member(client, h, swept, **over):
    _quotes, crew = swept
    body = {
        "name": f"Joseph {uuid.uuid4().hex[:5]}",
        "roles": ["driver_guide"],
        "phone": "+254700000000",
        "licence_number": "DL-8842",
        "licence_expires_on": (TODAY + timedelta(days=400)).isoformat(),
        "languages": ["English", "Kiswahili"],
    }
    body.update(over)
    made = await client.post(f"{API}/crew", headers=h, json=body)
    assert made.status_code == 201, made.text
    crew.append(made.json()["id"])
    return made.json()


async def _vehicle(client, h, **over):
    body = {
        "name": f"Land Cruiser {uuid.uuid4().hex[:5]}",
        "vehicle_type": "4x4",
        "passenger_capacity": 6,
        "fuel_type": "diesel",
        "fuel_consumption_kmpl": "8.5",
        "daily_operating_cost": "4500",
        "driver_cost_per_day": "3000",
        "currency": "KES",
    }
    body.update(over)
    made = await client.post(f"{API}/vehicles", headers=h, json=body)
    assert made.status_code == 201, made.text
    return made.json()


async def _assign(client, h, booking_id, **body):
    return await client.post(
        f"{API}/bookings/{booking_id}/assignments", headers=h, json=body
    )


# --------------------------------------------------------------------------- #
# The register
# --------------------------------------------------------------------------- #


async def test_a_driver_guide_is_one_person_and_one_record(
    client, admin_tokens, swept
):
    """Two rows would mean assigning the same human twice.

    They would double-book against themselves and be counted twice on a cost
    sheet, which is why ``roles`` is a list rather than a table per job.
    """
    h = _h(admin_tokens)
    member = await _crew_member(client, h, swept)
    assert member["roles"] == ["driver_guide"]

    # And they turn up when either job is being staffed.
    drivers = await client.get(f"{API}/crew?role=driver", headers=h)
    guides = await client.get(f"{API}/crew?role=guide", headers=h)
    assert member["id"] in {one["id"] for one in drivers.json()}
    assert member["id"] in {one["id"] for one in guides.json()}


async def test_somebody_with_no_role_cannot_be_recorded(client, admin_tokens):
    h = _h(admin_tokens)
    refused = await client.post(
        f"{API}/crew", headers=h, json={"name": "Nobody", "roles": []}
    )
    assert refused.status_code == 422, refused.text


async def test_a_typo_in_a_role_is_refused_rather_than_dropped(
    client, admin_tokens
):
    """A person saved with a bad role would be silently unassignable.

    And the failure would surface three weeks later as "why can I not put
    Joseph on this trip".
    """
    h = _h(admin_tokens)
    refused = await client.post(
        f"{API}/crew", headers=h, json={"name": "Joseph", "roles": ["driverr"]}
    )
    assert refused.status_code == 400, refused.text
    assert "driver_guide" in refused.text


# --------------------------------------------------------------------------- #
# Putting things on a trip
# --------------------------------------------------------------------------- #


async def test_a_booking_gets_a_vehicle_and_a_driver(
    client, admin_tokens, sample_catalogue, swept
):
    """The thing that used to lead nowhere.

    A confirmed booking had no crew and no vehicle: operations read the
    bookings screen and kept a separate spreadsheet of who was driving.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h)
    member = await _crew_member(client, h, swept)

    put = await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])
    assert put.status_code == 201, put.text
    assert put.json()["assignment"]["role"] == "vehicle"
    # Defaulted to the booking's own dates.
    assert put.json()["assignment"]["starts_on"] == ARRIVAL.isoformat()
    assert put.json()["assignment"]["ends_on"] == DEPARTURE.isoformat()
    assert put.json()["advisories"] == []

    crewed = await _assign(
        client, h, booking["id"], crew_id=member["id"], role="driver"
    )
    assert crewed.status_code == 201, crewed.text

    listed = await client.get(
        f"{API}/bookings/{booking['id']}/assignments", headers=h
    )
    assert len(listed.json()) == 2

    ready = await client.get(f"{API}/bookings/{booking['id']}/readiness", headers=h)
    assert ready.json() == []


async def test_a_vehicle_cannot_be_on_two_trips_at_once(
    client, admin_tokens, sample_catalogue, swept
):
    """The gap §2.5 left open.

    A vehicle was a fuel consumption and a daily rate; nothing anywhere said it
    was busy, so two bookings could be priced with the same Land Cruiser over
    the same week and the first anybody knew was a Tuesday morning in Diani.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    first = await _booking(client, h, ids, swept)
    second = await _booking(
        client, h, ids, swept, arrival=ARRIVAL + timedelta(days=1)
    )
    vehicle = await _vehicle(client, h)

    assert (
        await _assign(client, h, first["id"], vehicle_id=vehicle["id"])
    ).status_code == 201
    refused = await _assign(client, h, second["id"], vehicle_id=vehicle["id"])
    assert refused.status_code == 400, refused.text
    assert "already out" in refused.text
    assert first["reference"] in refused.text
    assert "say why you are overriding it" in refused.text


async def test_a_person_cannot_be_on_two_trips_at_once(
    client, admin_tokens, sample_catalogue, swept
):
    h, ids = _h(admin_tokens), sample_catalogue
    first = await _booking(client, h, ids, swept)
    second = await _booking(
        client, h, ids, swept, arrival=ARRIVAL + timedelta(days=1)
    )
    member = await _crew_member(client, h, swept)

    assert (
        await _assign(client, h, first["id"], crew_id=member["id"], role="driver")
    ).status_code == 201
    refused = await _assign(
        client, h, second["id"], crew_id=member["id"], role="driver"
    )
    assert refused.status_code == 400, refused.text
    assert "already out" in refused.text


async def test_a_same_day_handover_goes_through_with_an_advisory(
    client, admin_tokens, sample_catalogue, swept
):
    """Drop one group at the airport in the morning, collect another after lunch.

    Refusing it would make the calendar a nuisance an operator works around;
    swallowing it silently would make a tight turnaround and a comfortable one
    look identical.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    first = await _booking(client, h, ids, swept)
    second = await _booking(
        client,
        h,
        ids,
        swept,
        arrival=DEPARTURE,
        departure=DEPARTURE + timedelta(days=3),
    )
    vehicle = await _vehicle(client, h)

    assert (
        await _assign(client, h, first["id"], vehicle_id=vehicle["id"])
    ).status_code == 201
    put = await _assign(client, h, second["id"], vehicle_id=vehicle["id"])
    assert put.status_code == 201, put.text
    assert [one["code"] for one in put.json()["advisories"]] == [TIGHT_TURNAROUND]
    assert put.json()["advisories"][0]["blocking"] is False


async def test_a_clash_can_be_overridden_and_the_reason_stays_on_the_row(
    client, admin_tokens, sample_catalogue, swept
):
    """The point is not to make it impossible; it is to make it attributable.

    An operator who knows the first booking is about to be cancelled needs a
    way through, and the way through leaves their name and their reason.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    first = await _booking(client, h, ids, swept)
    second = await _booking(
        client, h, ids, swept, arrival=ARRIVAL + timedelta(days=1)
    )
    vehicle = await _vehicle(client, h)
    await _assign(client, h, first["id"], vehicle_id=vehicle["id"])

    forced = await _assign(
        client,
        h,
        second["id"],
        vehicle_id=vehicle["id"],
        override_reason="First group is cancelling on Friday.",
    )
    assert forced.status_code == 201, forced.text
    row = forced.json()["assignment"]
    assert row["override_reason"] == "First group is cancelling on Friday."
    assert row["assigned_by"] is not None


async def test_a_cancelled_booking_releases_what_it_held(
    client, admin_tokens, sample_catalogue, swept
):
    """Otherwise the fleet calendar fills with vehicles nobody is using.

    And an operator who has been told twice that a free vehicle is busy stops
    believing the calendar at all.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    first = await _booking(client, h, ids, swept)
    second = await _booking(
        client, h, ids, swept, arrival=ARRIVAL + timedelta(days=1)
    )
    vehicle = await _vehicle(client, h)
    await _assign(client, h, first["id"], vehicle_id=vehicle["id"])

    cancelled = await client.post(
        f"{API}/bookings/{first['id']}/cancel",
        headers=h,
        json={"reason": "Client's visa was refused."},
    )
    assert cancelled.status_code == 200, cancelled.text

    freed = await _assign(client, h, second["id"], vehicle_id=vehicle["id"])
    assert freed.status_code == 201, freed.text


async def test_a_cancelled_booking_cannot_be_crewed(
    client, admin_tokens, sample_catalogue, swept
):
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h)
    await client.post(
        f"{API}/bookings/{booking['id']}/cancel",
        headers=h,
        json={"reason": "Client's visa was refused."},
    )
    refused = await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])
    assert refused.status_code == 400, refused.text
    assert "no trip to crew" in refused.text


async def test_an_assignment_is_one_vehicle_or_one_person(
    client, admin_tokens, sample_catalogue, swept
):
    """A row with neither commits nothing; a row with both needs two checks."""
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h)
    member = await _crew_member(client, h, swept)

    assert (await _assign(client, h, booking["id"])).status_code == 400
    both = await _assign(
        client,
        h,
        booking["id"],
        vehicle_id=vehicle["id"],
        crew_id=member["id"],
        role="driver",
    )
    assert both.status_code == 400, both.text
    assert "one vehicle or one person" in both.text


async def test_a_driver_guide_must_be_told_which_job_they_are_doing(
    client, admin_tokens, sample_catalogue, swept
):
    """A trip sheet has to name one.

    Guessing would put somebody down as a driver on a trip they were sent on to
    guide, and the readiness check would then report a trip with nobody at the
    wheel as ready.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    member = await _crew_member(client, h, swept, roles=["driver", "guide"])

    refused = await _assign(client, h, booking["id"], crew_id=member["id"])
    assert refused.status_code == 400, refused.text
    assert "Say which of those" in refused.text


async def test_somebody_with_one_job_does_not_have_to_be_told(
    client, admin_tokens, sample_catalogue, swept
):
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    member = await _crew_member(client, h, swept, roles=["driver"])
    put = await _assign(client, h, booking["id"], crew_id=member["id"])
    assert put.status_code == 201, put.text
    assert put.json()["assignment"]["role"] == "driver"


async def test_a_licence_expiring_during_the_trip_is_refused(
    client, admin_tokens, sample_catalogue, swept
):
    """It passes every check made today, and the group is in Tsavo on the day."""
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    member = await _crew_member(
        client,
        h,
        swept,
        licence_expires_on=(ARRIVAL + timedelta(days=1)).isoformat(),
    )
    refused = await _assign(
        client, h, booking["id"], crew_id=member["id"], role="driver"
    )
    assert refused.status_code == 400, refused.text
    assert "in the middle of this trip" in refused.text


async def test_a_guide_who_does_not_drive_cannot_be_sent_with_the_vehicle(
    client, admin_tokens, sample_catalogue, swept
):
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    member = await _crew_member(client, h, swept, roles=["guide"])
    refused = await _assign(
        client, h, booking["id"], crew_id=member["id"], role="driver"
    )
    assert refused.status_code == 400, refused.text
    assert "not down as a driver" in refused.text


async def test_a_vehicle_can_be_taken_off_a_trip_and_frees_up(
    client, admin_tokens, sample_catalogue, swept
):
    h, ids = _h(admin_tokens), sample_catalogue
    first = await _booking(client, h, ids, swept)
    second = await _booking(
        client, h, ids, swept, arrival=ARRIVAL + timedelta(days=1)
    )
    vehicle = await _vehicle(client, h)
    put = await _assign(client, h, first["id"], vehicle_id=vehicle["id"])

    dropped = await client.delete(
        f"{API}/assignments/{put.json()['assignment']['id']}", headers=h
    )
    assert dropped.status_code == 204, dropped.text
    assert (
        await _assign(client, h, second["id"], vehicle_id=vehicle["id"])
    ).status_code == 201


# --------------------------------------------------------------------------- #
# The two lists an operator opens
# --------------------------------------------------------------------------- #


async def test_the_departure_board_says_what_is_missing(
    client, admin_tokens, sample_catalogue, swept
):
    """The operational half of §5.2's morning list."""
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)

    board = await client.get(f"{API}/operations/departures?days=90", headers=h)
    assert board.status_code == 200, board.text
    mine = next(
        one for one in board.json() if one["reference"] == booking["reference"]
    )
    codes = {gap["code"] for gap in mine["gaps"]}
    assert NO_VEHICLE in codes
    assert NO_DRIVER in codes
    assert mine["roster"]["vehicles"] == []
    assert mine["roster"]["seats"] == 0


async def test_a_group_too_big_for_its_vehicles_is_on_the_board(
    client, admin_tokens, sample_catalogue, swept
):
    """Counted across every vehicle, because twelve in two Land Cruisers is normal."""
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept, pax=9)
    vehicle = await _vehicle(client, h, passenger_capacity=6)
    member = await _crew_member(client, h, swept)
    await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])
    await _assign(client, h, booking["id"], crew_id=member["id"], role="driver")

    gaps = await client.get(f"{API}/bookings/{booking['id']}/readiness", headers=h)
    short = next(one for one in gaps.json() if one["code"] == NOT_ENOUGH_SEATS)
    assert "3 short" in short["message"]

    second = await _vehicle(client, h, passenger_capacity=6)
    await _assign(client, h, booking["id"], vehicle_id=second["id"])
    assert (
        await client.get(f"{API}/bookings/{booking['id']}/readiness", headers=h)
    ).json() == []


async def test_a_licence_expiring_after_a_trip_is_a_warning_not_a_refusal(
    client, admin_tokens, sample_catalogue, swept
):
    """It will not stop this trip; it will stop the next one."""
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h)
    member = await _crew_member(
        client,
        h,
        swept,
        licence_expires_on=(DEPARTURE + timedelta(days=5)).isoformat(),
    )
    await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])
    put = await _assign(
        client, h, booking["id"], crew_id=member["id"], role="driver"
    )
    assert put.status_code == 201, put.text

    gaps = await client.get(f"{API}/bookings/{booking['id']}/readiness", headers=h)
    warning = next(one for one in gaps.json() if one["code"] == LICENCE_EXPIRING)
    assert "it will stop the next one" in warning["message"]


async def test_a_driver_guide_sent_out_to_guide_is_not_a_driver_on_that_trip(
    client, admin_tokens, sample_catalogue, swept
):
    """By the role on the assignment, not by what the person is capable of.

    Otherwise a trip with a guide and nobody at the wheel reports as ready.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h)
    member = await _crew_member(client, h, swept)
    await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])
    await _assign(client, h, booking["id"], crew_id=member["id"], role="guide")

    gaps = await client.get(f"{API}/bookings/{booking['id']}/readiness", headers=h)
    assert NO_DRIVER in {one["code"] for one in gaps.json()}


async def test_the_diary_says_what_is_already_out(
    client, admin_tokens, sample_catalogue, swept
):
    """"What have I got free that week" is really "what is already out"."""
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h)
    await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])

    busy = await client.get(
        f"{API}/operations/diary"
        f"?starts_on={ARRIVAL.isoformat()}&ends_on={DEPARTURE.isoformat()}"
        f"&vehicle_id={vehicle['id']}",
        headers=h,
    )
    assert busy.status_code == 200, busy.text
    assert len(busy.json()) == 1

    quiet = await client.get(
        f"{API}/operations/diary"
        f"?starts_on={(DEPARTURE + timedelta(days=10)).isoformat()}"
        f"&ends_on={(DEPARTURE + timedelta(days=20)).isoformat()}"
        f"&vehicle_id={vehicle['id']}",
        headers=h,
    )
    assert quiet.json() == []


async def test_committing_a_vehicle_is_a_different_permission_from_reading(
    client, admin_tokens, sample_catalogue, swept
):
    """An operator overriding a clash makes a decision somebody is held to."""
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h)

    email = unique_email("viewer")
    made = await client.post(
        f"{API}/users",
        headers=h,
        json={"email": email, "password": "ViewerPass123", "role_keys": ["viewer"]},
    )
    assert made.status_code in (200, 201), made.text
    logged_in = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "ViewerPass123"}
    )
    viewer_h = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}

    refused = await _assign_as(client, viewer_h, booking["id"], vehicle["id"])
    assert refused.status_code == 403, refused.text


async def test_operations_can_crew_a_trip_and_cannot_take_money(
    client, admin_tokens, sample_catalogue, swept
):
    """The role that has been "extended in Stage 8" since Stage 1.

    Operations runs the trips: it reads a booking and crews it, and it does not
    sell one or record a payment against one.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h)

    email = unique_email("ops")
    made = await client.post(
        f"{API}/users",
        headers=h,
        json={"email": email, "password": "OpsPass12345", "role_keys": ["operations"]},
    )
    assert made.status_code in (200, 201), made.text
    logged_in = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "OpsPass12345"}
    )
    ops_h = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}

    assert (
        await _assign_as(client, ops_h, booking["id"], vehicle["id"])
    ).status_code == 201
    assert (
        await client.get(f"{API}/operations/departures", headers=ops_h)
    ).status_code == 200
    # But not the till.
    refused = await client.post(
        f"{API}/bookings/{booking['id']}/payments",
        headers=ops_h,
        json={
            "amount": "1000",
            "currency": "KES",
            "paid_on": TODAY.isoformat(),
            "method": "cash",
        },
    )
    assert refused.status_code == 403, refused.text


# --------------------------------------------------------------------------- #
# What the vehicle actually did (§8.2)
# --------------------------------------------------------------------------- #


async def _out(client, h, assignment_id, **over):
    body = {"odometer_out_km": "84300"}
    body.update(over)
    opened = await client.post(
        f"{API}/assignments/{assignment_id}/log", headers=h, json=body
    )
    assert opened.status_code == 201, opened.text
    return opened.json()


async def test_a_trip_records_what_the_vehicle_actually_did(
    client, admin_tokens, sample_catalogue, swept
):
    """The first time a transport figure has been checkable against a receipt.

    §2.5 put ``fuel_consumption_kmpl`` on a vehicle and every quote since has
    been priced from it. Nothing could ever disprove it.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h, fuel_consumption_kmpl="8.5")
    put = await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])
    assignment_id = put.json()["assignment"]["id"]

    opened = await _out(client, h, assignment_id)
    log_id = opened["log"]["id"]
    assert opened["log"]["is_open"] is True
    assert opened["log"]["distance_km"] is None

    fuelled = await client.post(
        f"{API}/trip-logs/{log_id}/fuel",
        headers=h,
        json={
            "litres": "130",
            "amount": "23400",
            "currency": "KES",
            "bought_on": TODAY.isoformat(),
            "station": "Total Ukunda",
            "receipt_ref": "R-99213",
        },
    )
    assert fuelled.status_code == 201, fuelled.text

    closed = await client.post(
        f"{API}/trip-logs/{log_id}/close",
        headers=h,
        json={"odometer_in_km": "85210"},
    )
    assert closed.status_code == 200, closed.text
    assert D(closed.json()["distance_km"]) == D("910")
    assert closed.json()["is_open"] is False

    actual = await client.get(f"{API}/trip-logs/{log_id}/actual", headers=h)
    body = actual.json()
    assert D(body["distance_km"]) == D("910")
    assert D(body["litres"]) == D("130")
    assert D(body["fuel_cost"]) == D("23400")
    # Priced at 8.5, managed 7.00 — the gap this stage exists to make visible.
    assert D(body["model_kmpl"]) == D("8.5")
    assert D(body["actual_kmpl"]) == D("7.00")
    assert D(body["variance_pct"]) < 0


async def test_an_odometer_reading_that_runs_backwards_is_refused(
    client, admin_tokens, sample_catalogue, swept
):
    """A negative distance in a fleet average poisons every figure from it."""
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h)
    put = await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])
    opened = await _out(client, h, put.json()["assignment"]["id"])

    refused = await client.post(
        f"{API}/trip-logs/{opened['log']['id']}/close",
        headers=h,
        json={"odometer_in_km": "83210"},
    )
    assert refused.status_code == 400, refused.text
    assert "does not run backwards" in refused.text


async def test_kilometres_between_trips_come_back_as_an_observation(
    client, admin_tokens, sample_catalogue, swept
):
    """The one thing an odometer is uniquely good at seeing.

    Repositioning, a service run, or somebody's weekend — reported without
    deciding which, and never as an error.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    first = await _booking(client, h, ids, swept)
    second = await _booking(
        client,
        h,
        ids,
        swept,
        arrival=DEPARTURE + timedelta(days=5),
        departure=DEPARTURE + timedelta(days=8),
    )
    vehicle = await _vehicle(client, h)

    one = await _assign(client, h, first["id"], vehicle_id=vehicle["id"])
    opened = await _out(client, h, one.json()["assignment"]["id"])
    await client.post(
        f"{API}/trip-logs/{opened['log']['id']}/close",
        headers=h,
        json={"odometer_in_km": "84300"},
    )

    two = await _assign(client, h, second["id"], vehicle_id=vehicle["id"])
    again = await _out(
        client, h, two.json()["assignment"]["id"], odometer_out_km="84910"
    )
    assert len(again["observations"]) == 1
    assert "610 km since the vehicle last came back" in again["observations"][0]


async def test_leaving_on_less_than_the_last_return_is_refused(
    client, admin_tokens, sample_catalogue, swept
):
    h, ids = _h(admin_tokens), sample_catalogue
    first = await _booking(client, h, ids, swept)
    second = await _booking(
        client,
        h,
        ids,
        swept,
        arrival=DEPARTURE + timedelta(days=5),
        departure=DEPARTURE + timedelta(days=8),
    )
    vehicle = await _vehicle(client, h)
    one = await _assign(client, h, first["id"], vehicle_id=vehicle["id"])
    opened = await _out(client, h, one.json()["assignment"]["id"])
    await client.post(
        f"{API}/trip-logs/{opened['log']['id']}/close",
        headers=h,
        json={"odometer_in_km": "84300"},
    )

    two = await _assign(client, h, second["id"], vehicle_id=vehicle["id"])
    refused = await client.post(
        f"{API}/assignments/{two.json()['assignment']['id']}/log",
        headers=h,
        json={"odometer_out_km": "84000"},
    )
    assert refused.status_code == 400, refused.text
    assert "One of the two readings is wrong" in refused.text


async def test_a_person_does_not_have_an_odometer(
    client, admin_tokens, sample_catalogue, swept
):
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    member = await _crew_member(client, h, swept)
    put = await _assign(
        client, h, booking["id"], crew_id=member["id"], role="driver"
    )
    refused = await client.post(
        f"{API}/assignments/{put.json()['assignment']['id']}/log",
        headers=h,
        json={"odometer_out_km": "84300"},
    )
    assert refused.status_code == 400, refused.text
    assert "people do not have odometers" in refused.text


async def test_one_log_per_vehicle_per_trip(
    client, admin_tokens, sample_catalogue, swept
):
    """A second would double the distance in every fleet average.

    Which is the kind of error that looks like a fact.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h)
    put = await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])
    assignment_id = put.json()["assignment"]["id"]
    await _out(client, h, assignment_id)

    refused = await client.post(
        f"{API}/assignments/{assignment_id}/log",
        headers=h,
        json={"odometer_out_km": "84300"},
    )
    assert refused.status_code == 400, refused.text
    assert "already has a log" in refused.text


async def test_a_group_in_two_vehicles_keeps_two_logs(
    client, admin_tokens, sample_catalogue, swept
):
    """Pooling them would lose exactly the comparison worth making."""
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept, pax=9)
    first = await _vehicle(client, h)
    second = await _vehicle(client, h)
    one = await _assign(client, h, booking["id"], vehicle_id=first["id"])
    two = await _assign(client, h, booking["id"], vehicle_id=second["id"])
    await _out(client, h, one.json()["assignment"]["id"])
    await _out(client, h, two.json()["assignment"]["id"], odometer_out_km="51200")

    logs = await client.get(f"{API}/bookings/{booking['id']}/trip-logs", headers=h)
    assert logs.status_code == 200, logs.text
    assert len(logs.json()) == 2
    assert {one["vehicle_id"] for one in logs.json()} == {first["id"], second["id"]}


async def test_a_receipt_needs_litres_as_well_as_money(
    client, admin_tokens, sample_catalogue, swept
):
    """A shilling figure alone says nothing about consumption."""
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h)
    put = await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])
    opened = await _out(client, h, put.json()["assignment"]["id"])

    refused = await client.post(
        f"{API}/trip-logs/{opened['log']['id']}/fuel",
        headers=h,
        json={"litres": "0", "amount": "23400", "currency": "KES"},
    )
    assert refused.status_code == 422, refused.text


async def test_fuel_bought_in_two_currencies_is_not_totalled(
    client, admin_tokens, sample_catalogue, swept
):
    """What the pump charged is a fact; the exchange rate is a decision."""
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h)
    put = await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])
    opened = await _out(client, h, put.json()["assignment"]["id"])
    log_id = opened["log"]["id"]

    for currency, amount in (("KES", "23400"), ("TZS", "180000")):
        made = await client.post(
            f"{API}/trip-logs/{log_id}/fuel",
            headers=h,
            json={"litres": "60", "amount": amount, "currency": currency},
        )
        assert made.status_code == 201, made.text

    await client.post(
        f"{API}/trip-logs/{log_id}/close",
        headers=h,
        json={"odometer_in_km": "85210"},
    )
    refused = await client.get(f"{API}/trip-logs/{log_id}/actual", headers=h)
    assert refused.status_code == 400, refused.text
    assert "a decision and not arithmetic" in refused.text


async def test_the_fuel_audit_says_the_model_is_out_and_changes_nothing(
    client, admin_tokens, sample_catalogue, swept
):
    """The finding that pays for this stage.

    A vehicle priced at 8.5 km/L that manages about 7 under-costs every safari
    it goes on — quietly, for as long as nobody measures. The audit says so and
    leaves ``fuel_consumption_kmpl`` exactly where it was, because moving a live
    pricing input re-prices work in flight.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    vehicle = await _vehicle(client, h, fuel_consumption_kmpl="8.5")

    odometer = 100000
    for index in range(3):
        booking = await _booking(
            client,
            h,
            ids,
            swept,
            arrival=ARRIVAL + timedelta(days=index * 10),
            departure=ARRIVAL + timedelta(days=index * 10 + 3),
        )
        put = await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])
        opened = await _out(
            client,
            h,
            put.json()["assignment"]["id"],
            odometer_out_km=str(odometer),
        )
        await client.post(
            f"{API}/trip-logs/{opened['log']['id']}/fuel",
            headers=h,
            json={"litres": "130", "amount": "23400", "currency": "KES"},
        )
        odometer += 910
        await client.post(
            f"{API}/trip-logs/{opened['log']['id']}/close",
            headers=h,
            json={"odometer_in_km": str(odometer)},
        )

    report = await client.get(
        f"{API}/operations/fuel-audit?vehicle_id={vehicle['id']}", headers=h
    )
    assert report.status_code == 200, report.text
    truth = report.json()[0]
    assert truth["trips"] == 3
    assert D(truth["actual_kmpl"]) == D("7.00")
    assert D(truth["model_kmpl"]) == D("8.5")
    finding = truth["findings"][0]
    assert finding["code"] == "vehicle_consumption_optimistic"
    assert "under-costing fuel by about" in finding["message"]
    assert "nothing here changes it" in finding["message"]

    # And the vehicle is untouched: every quote still prices on 8.5.
    unchanged = await client.get(f"{API}/vehicles/{vehicle['id']}", headers=h)
    assert D(unchanged.json()["fuel_consumption_kmpl"]) == D("8.5")


async def test_two_trips_are_not_enough_to_conclude_from(
    client, admin_tokens, sample_catalogue, swept
):
    h, ids = _h(admin_tokens), sample_catalogue
    vehicle = await _vehicle(client, h, fuel_consumption_kmpl="8.5")
    booking = await _booking(client, h, ids, swept)
    put = await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])
    opened = await _out(client, h, put.json()["assignment"]["id"])
    await client.post(
        f"{API}/trip-logs/{opened['log']['id']}/fuel",
        headers=h,
        json={"litres": "130", "amount": "23400", "currency": "KES"},
    )
    await client.post(
        f"{API}/trip-logs/{opened['log']['id']}/close",
        headers=h,
        json={"odometer_in_km": "85210"},
    )

    report = await client.get(
        f"{API}/operations/fuel-audit?vehicle_id={vehicle['id']}", headers=h
    )
    finding = report.json()[0]["findings"][0]
    assert finding["code"] == "vehicle_not_enough_data"
    assert "is not a pattern" in finding["message"]


async def test_recording_a_receipt_is_a_different_permission_from_reading_one(
    client, admin_tokens, sample_catalogue, swept
):
    """A fuel receipt is money leaving the business.

    And what a vehicle actually burned is the evidence every future transport
    price rests on — a wrong figure here mis-prices every safari quietly.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    booking = await _booking(client, h, ids, swept)
    vehicle = await _vehicle(client, h)
    put = await _assign(client, h, booking["id"], vehicle_id=vehicle["id"])

    email = unique_email("viewer")
    made = await client.post(
        f"{API}/users",
        headers=h,
        json={"email": email, "password": "ViewerPass123", "role_keys": ["viewer"]},
    )
    assert made.status_code in (200, 201), made.text
    logged_in = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "ViewerPass123"}
    )
    viewer_h = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}

    refused = await client.post(
        f"{API}/assignments/{put.json()['assignment']['id']}/log",
        headers=viewer_h,
        json={"odometer_out_km": "84300"},
    )
    assert refused.status_code == 403, refused.text


async def _assign_as(client, headers, booking_id, vehicle_id):
    return await client.post(
        f"{API}/bookings/{booking_id}/assignments",
        headers=headers,
        json={"vehicle_id": vehicle_id},
    )
