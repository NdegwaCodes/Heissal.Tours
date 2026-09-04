"""Road routes: the rules (§4.2). No database.

The table exists because the catalogue cannot answer the question. Latitude and
longitude are stored and useless for it — Nairobi to the Mara is about 225 km
straight and about 270 km driven, and the time depends on the surface far more
than on either figure. So the distance, the drive time and the vehicle types a
road takes are hand-entered by the people who drive them.

What is defended here is the judgement that follows: **a vehicle the road does
not take is blocking**. It is two failures at once — the quote is under-priced,
because the vehicle the trip needs costs more than the one it was costed on,
and the drive cannot be run as sold. A saloon and a Land Cruiser look identical
on a proposal, and the second failure is discovered on the road.
"""

from __future__ import annotations

from decimal import Decimal

from app.modules.quotes.routing import (
    DAYS_PER_MOVEMENT,
    NO_ENDPOINTS,
    NO_FUEL_PRICE,
    NO_ROUTE,
    WRONG_VEHICLE,
    Road,
    check_endpoints,
    check_vehicle,
    missing_fuel_price,
    missing_route,
    normalise_types,
    vehicle_is_suitable,
)
from app.modules.vehicles.service import compute_transport_cost

D = Decimal


def _road(**over) -> Road:
    fields = {
        "label": "Nairobi to the Maasai Mara",
        "distance_km": D("270"),
        "drive_time_minutes": 330,
        "required_vehicle_types": ("safari_land_cruiser",),
    }
    fields.update(over)
    return Road(**fields)


# --------------------------------------------------------------------------- #
# The vehicle a road takes
# --------------------------------------------------------------------------- #


def test_a_road_with_no_stated_requirement_takes_anything():
    """Most tarmac does not care, and an empty list has to mean that.

    Requiring every route to name its vehicles would make the column a chore
    rather than a fact, and a chore gets filled in with whatever passes.
    """
    road = _road(required_vehicle_types=())
    assert vehicle_is_suitable(road.required_vehicle_types, "saloon")
    assert check_vehicle(label="Transfer", road=road, offered="saloon") is None


def test_a_vehicle_the_road_does_not_take_is_blocking():
    road = _road()
    fault = check_vehicle(label="Nairobi to the Mara", road=road, offered="saloon")
    assert fault is not None
    assert fault.code == WRONG_VEHICLE
    assert fault.blocking
    # The message names both halves of the failure, because an agent reading it
    # needs to know it is not only a presentation problem.
    assert "under-priced" in fault.message
    assert "cannot be run as sold" in fault.message
    assert "safari_land_cruiser" in fault.message


def test_the_vehicle_the_road_does_take_passes():
    road = _road()
    assert (
        check_vehicle(
            label="Nairobi to the Mara", road=road, offered="safari_land_cruiser"
        )
        is None
    )


def test_a_road_may_take_several_types():
    """The client states the vehicles per route, so it is a list, not a flag.

    "Needs a 4×4" is a capability nobody records; "takes a Land Cruiser or a
    4×4 minibus" is what an operations team actually says.
    """
    road = _road(required_vehicle_types=("safari_land_cruiser", "minibus_4x4"))
    assert vehicle_is_suitable(road.required_vehicle_types, "minibus_4x4")
    assert not vehicle_is_suitable(road.required_vehicle_types, "minibus")


def test_no_vehicle_at_all_is_not_suitable_for_a_road_that_asks():
    """Silence is not compliance.

    The alternative sends a saloon — or nothing at all — up a road that needs a
    Land Cruiser, on the grounds that no vehicle was named to object to.
    """
    road = _road()
    assert not vehicle_is_suitable(road.required_vehicle_types, None)
    assert not vehicle_is_suitable(road.required_vehicle_types, "")
    fault = check_vehicle(label="Drive", road=road, offered=None)
    assert fault is not None and "no vehicle" in fault.message


def test_hand_entered_types_are_trimmed_and_case_folded():
    """A quote refused over a trailing space is a rule nobody can satisfy."""
    assert normalise_types([" 4X4 ", "4x4", "", None, "Saloon"]) == ("4x4", "saloon")
    assert vehicle_is_suitable(("4X4",), " 4x4 ")


def test_a_route_note_travels_with_the_fault():
    """"Impassable after heavy rain" is why the requirement exists.

    An agent seeing only "takes a Land Cruiser" reads a preference. Seeing the
    note reads a road.
    """
    road = _road(notes="Last 40 km is murram; impassable after heavy rain.")
    fault = check_vehicle(label="Drive", road=road, offered="saloon")
    assert fault is not None
    assert "impassable after heavy rain" in fault.message.lower()


# --------------------------------------------------------------------------- #
# What is missing, and how loudly
# --------------------------------------------------------------------------- #


def test_a_drive_with_one_end_missing_says_which_end():
    """A route is a pair, and the far end is asked for rather than guessed.

    Transport is priced once for the quote (§3.10) while each option has its
    own legs, so "the previous destination" is a different place depending on
    which option is being priced. Guessing it produces a fuel figure for a
    drive nobody is making.
    """
    missing_start = check_endpoints(
        label="Drive", has_origin=False, has_destination=True, sequence=2
    )
    assert missing_start is not None
    assert missing_start.code == NO_ENDPOINTS
    assert missing_start.blocking
    assert "no start" in missing_start.message
    assert missing_start.sequence == 2

    missing_end = check_endpoints(
        label="Drive", has_origin=True, has_destination=False
    )
    assert missing_end is not None and "no destination" in missing_end.message

    assert (
        check_endpoints(label="Drive", has_origin=True, has_destination=True) is None
    )


def test_a_road_with_no_row_is_blocking_and_says_what_to_enter():
    fault = missing_route(label="Nairobi to Amboseli", on="2026-07-04")
    assert fault.code == NO_ROUTE
    assert fault.blocking
    assert "no route on file" in fault.message
    assert "2026-07-04" in fault.message
    # The fix, in the words of the table: kilometres, time, vehicles.
    assert "driven kilometres" in fault.message


def test_a_missing_pump_price_is_its_own_fault():
    """Not folded into "unpriced": the route is right and a monthly row is not.

    Two different people fix these two things, and a message that cannot tell
    them apart sends both to the wrong one.
    """
    fault = missing_fuel_price(label="Drive", fuel_type="diesel")
    assert fault.code == NO_FUEL_PRICE
    assert fault.blocking
    assert "diesel" in fault.message
    assert "pump price" in fault.message


# --------------------------------------------------------------------------- #
# The drive itself
# --------------------------------------------------------------------------- #


def test_drive_time_reads_in_hours_for_a_rate():
    assert _road(drive_time_minutes=330).hours == D("5.50")
    assert _road(drive_time_minutes=45).hours == D("0.75")


def test_a_movement_costs_one_day_of_its_crew():
    """A ten-hour drive and a two-hour transfer both cost a day.

    Neither leaves the day free for another job, and deriving a fraction from
    the drive time would bill half a driver. A multi-day drive is entered as
    the movements it actually is, which is also how the itinerary reads it.
    """
    assert DAYS_PER_MOVEMENT == 1


def test_the_fuel_arithmetic_is_the_one_from_stage_2():
    """Not reimplemented here, and this is the test that says so.

    270 km at 8 km/L is 33.75 litres; at 180 a litre that is 6,075, plus a
    day of driver and running. ``compute_transport_cost`` has done this since
    Stage 2.8 — a second implementation of litres times price is how two
    answers to one question start.
    """
    calc = compute_transport_cost(
        distance_km=D("270"),
        consumption_kmpl=D("8"),
        fuel_price_per_litre=D("180"),
        days=DAYS_PER_MOVEMENT,
        driver_cost_per_day=D("3000"),
        daily_operating_cost=D("2000"),
    )
    assert calc["fuel_litres"] == D("33.75")
    assert calc["fuel_cost"] == D("6075.00")
    assert calc["driver_total"] == D("3000")
    assert calc["operating_total"] == D("2000")
    assert calc["total"] == D("11075.00")


def test_a_road_read_backwards_says_it_was():
    """Distance is symmetric, so reading a row in reverse is right — and said.

    An operator reconciling a fuel figure needs to know which row it came from
    and which way round, or the check is against a route they cannot find.
    """
    forward = _road(source="routes abc · 270 km, 330 min from 2026-01-01")
    assert not forward.reversed_lookup
    backwards = _road(reversed_lookup=True)
    assert backwards.reversed_lookup
