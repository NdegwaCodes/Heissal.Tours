"""Clashes, licences and what is missing before Thursday. Pure rules (§8.1).

The overlap rule is the one worth reading. A vehicle dropping a group at the
airport on the 5th and collecting another that afternoon is a normal Tuesday at
a coast operator; a vehicle on two trips over the 5th and 6th is a Tuesday that
does not happen. Telling those apart correctly at every edge — including the
single-day trips, which is where the first version of this got it wrong — is
what stops the calendar being either a nuisance or a lie.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.operations.roster import (
    DRIVER,
    DRIVER_GUIDE,
    GUIDE,
    LICENCE_EXPIRING,
    NO_DRIVER,
    NO_VEHICLE,
    NOT_ENOUGH_SEATS,
    TIGHT_TURNAROUND,
    AssignmentRefused,
    Crew,
    Fleet,
    Held,
    Roster,
    Window,
    check_crew,
    check_ready,
    check_vehicle,
    clashes,
    seats,
    sort_gaps,
    window_for,
)

TODAY = date(2026, 9, 5)


def _w(first: int, last: int, month: int = 11) -> Window:
    return Window(starts_on=date(2026, month, first), ends_on=date(2026, month, last))


# --------------------------------------------------------------------------- #
# The window
# --------------------------------------------------------------------------- #


def test_both_ends_are_inclusive():
    """An operator says "the 4th to the 8th" and means five days.

    A half-open convention would be defensible and would also mean every
    conversation about a clash starts by agreeing what the dates mean.
    """
    assert _w(4, 8).days == 5
    assert _w(4, 4).days == 1


def test_a_window_cannot_end_before_it_starts():
    with pytest.raises(AssignmentRefused):
        Window(starts_on=date(2026, 11, 8), ends_on=date(2026, 11, 4))


def test_two_trips_over_the_same_days_clash():
    assert _w(1, 5).overlaps(_w(4, 9)) is True
    assert _w(1, 5).overlaps(_w(1, 5)) is True
    # Contained.
    assert _w(1, 9).overlaps(_w(3, 4)) is True


def test_trips_that_do_not_meet_do_not_clash():
    assert _w(1, 5).overlaps(_w(6, 9)) is False


def test_a_same_day_handover_is_not_a_clash():
    """Drop one group at the airport in the morning, collect another after lunch."""
    assert _w(1, 5).overlaps(_w(5, 9)) is False
    assert _w(1, 5).is_handover(_w(5, 9)) is True
    # And it reads the same in either direction.
    assert _w(5, 9).is_handover(_w(1, 5)) is True


def test_two_single_day_trips_on_the_same_date_are_a_clash():
    """The edge the first version of this rule got wrong.

    A handover needs the shared day to be one window's *last* and the other's
    *first*. Without that second half, two one-day trips on the 5th read as a
    handover — which is two groups and one vehicle.
    """
    assert _w(5, 5).overlaps(_w(5, 5)) is True
    assert _w(5, 5).is_handover(_w(5, 5)) is False


def test_a_single_day_trip_against_a_longer_one_is_a_clash_either_way():
    """And symmetrically, which the first version was not.

    A vehicle out all of the 5th cannot also start a trip on the 5th, and it
    cannot also finish one — the two are the same situation seen from opposite
    ends, so they have to give the same answer.
    """
    assert _w(5, 5).overlaps(_w(5, 9)) is True
    assert _w(1, 5).overlaps(_w(5, 5)) is True


# --------------------------------------------------------------------------- #
# Clashes, reported
# --------------------------------------------------------------------------- #


def test_a_clash_names_the_trip_and_blocks():
    found = clashes(
        _w(4, 9),
        [Held(window=_w(1, 5), reference="HTB-2026-0007")],
        subject="KBZ 123A",
    )
    assert len(found) == 1
    assert found[0].blocking is True
    assert "KBZ 123A is already out" in found[0].message
    assert "HTB-2026-0007" in found[0].message
    assert "one trip that does not happen" in found[0].message


def test_a_handover_is_advice_and_not_a_refusal():
    found = clashes(
        _w(5, 9),
        [Held(window=_w(1, 5), reference="HTB-2026-0007")],
        subject="Joseph",
    )
    assert [one.code for one in found] == [TIGHT_TURNAROUND]
    assert found[0].blocking is False
    assert "same day" in found[0].message


def test_nothing_is_reported_where_nothing_meets():
    assert clashes(_w(6, 9), [Held(window=_w(1, 5))]) == []


def test_re_dating_an_assignment_does_not_clash_with_itself():
    """Without this every move would be refused by the thing being moved."""
    import uuid

    same = uuid.uuid4()
    held = [Held(window=_w(1, 5), booking_id=same, reference="HTB-2026-0007")]
    assert clashes(_w(2, 6), held) != []
    assert clashes(_w(2, 6), held, ignore=same) == []


# --------------------------------------------------------------------------- #
# Who may be sent out
# --------------------------------------------------------------------------- #


def _person(**over):
    fields = {
        "name": "Joseph",
        "roles": (DRIVER_GUIDE,),
        "is_active": True,
        "licence_expires_on": date(2027, 6, 30),
    }
    fields.update(over)
    return Crew(**fields)


def test_a_driver_guide_satisfies_both_needs():
    """One person, one row — which is why the role is a list.

    Two rows would mean assigning the same human twice and double-booking them
    against themselves.
    """
    member = _person()
    assert member.can(DRIVER) is True
    assert member.can(GUIDE) is True


def test_a_guide_who_does_not_drive_cannot_be_sent_with_the_vehicle():
    with pytest.raises(AssignmentRefused) as raised:
        check_crew(_person(roles=(GUIDE,)), DRIVER, _w(1, 5))
    assert "not down as a driver" in str(raised.value)


def test_somebody_off_the_roster_cannot_be_put_on_a_trip():
    with pytest.raises(AssignmentRefused) as raised:
        check_crew(_person(is_active=False), DRIVER, _w(1, 5))
    assert "not on the active roster" in str(raised.value)


def test_a_licence_expiring_mid_safari_is_the_case_worth_catching():
    """It passes every check made against today, and the group is in Tsavo.

    Which is the whole reason the expiry is a date on the row rather than a
    valid/invalid flag somebody updates.
    """
    with pytest.raises(AssignmentRefused) as raised:
        check_crew(
            _person(licence_expires_on=date(2026, 11, 3)), DRIVER, _w(1, 5)
        )
    assert "in the middle of this trip" in str(raised.value)
    assert "03 November 2026" in str(raised.value)


def test_a_licence_expiring_the_day_the_trip_ends_is_still_refused():
    """It has to be valid for the last drive, which happens on the last day."""
    with pytest.raises(AssignmentRefused):
        check_crew(
            _person(licence_expires_on=date(2026, 11, 5)), DRIVER, _w(1, 5)
        )


def test_a_licence_lasting_past_the_trip_is_fine():
    check_crew(_person(licence_expires_on=date(2026, 11, 6)), DRIVER, _w(1, 5))


def test_a_guide_who_does_not_drive_is_not_asked_for_a_driving_licence():
    check_crew(
        _person(roles=(GUIDE,), licence_expires_on=date(2020, 1, 1)),
        GUIDE,
        _w(1, 5),
    )


def test_an_unknown_role_is_refused_with_the_three_that_work():
    with pytest.raises(AssignmentRefused) as raised:
        check_crew(_person(), "porter", _w(1, 5))
    assert "driver_guide" in str(raised.value)


def test_a_vehicle_off_the_fleet_cannot_be_put_on_a_trip():
    with pytest.raises(AssignmentRefused) as raised:
        check_vehicle(Fleet(name="KBZ 123A", is_active=False))
    assert "not in the active fleet" in str(raised.value)


def test_seats_are_counted_across_the_vehicles_not_per_vehicle():
    """Twelve people in two Land Cruisers is the normal answer, not a problem."""
    pair = [
        Fleet(name="A", passenger_capacity=6),
        Fleet(name="B", passenger_capacity=6),
    ]
    assert seats(pair) == 12
    assert seats([]) == 0


# --------------------------------------------------------------------------- #
# What is missing before Thursday
# --------------------------------------------------------------------------- #


def _roster(**over):
    fields = {
        "reference": "HTB-2026-0007",
        "departs_on": date(2026, 9, 12),
        "pax_count": 4,
        "vehicles": [Fleet(name="KBZ 123A", passenger_capacity=6)],
        "drivers": [_person()],
        "guides": [],
    }
    fields.update(over)
    return Roster(**fields)


def test_a_crewed_trip_has_nothing_missing():
    assert check_ready(_roster(), today=TODAY) == []


def test_a_trip_with_no_vehicle_says_how_long_there_is():
    found = check_ready(_roster(vehicles=[]), today=TODAY)
    gap = next(one for one in found if one.code == NO_VEHICLE)
    assert gap.days == 7
    assert "leaves in 7 day(s)" in gap.message


def test_a_trip_with_no_driver_is_reported():
    found = check_ready(_roster(drivers=[]), today=TODAY)
    assert NO_DRIVER in {one.code for one in found}


def test_a_missing_guide_is_deliberately_not_reported():
    """Whether a trip needs one depends on what the client asked and paid for.

    A board that complained about every self-drive booking is a board nobody
    opens — the §5.2 lesson about closing leads on a timer, applied to
    departures.
    """
    assert check_ready(_roster(guides=[]), today=TODAY) == []


def test_a_group_too_big_for_the_vehicles_says_how_short():
    found = check_ready(_roster(pax_count=9), today=TODAY)
    gap = next(one for one in found if one.code == NOT_ENOUGH_SEATS)
    assert "9 and the assigned vehicles seat 6 — 3 short" in gap.message


def test_seats_are_not_counted_before_there_is_a_vehicle():
    """One problem, reported once. "No vehicle" already says it."""
    found = check_ready(_roster(vehicles=[], pax_count=9), today=TODAY)
    assert NOT_ENOUGH_SEATS not in {one.code for one in found}


def test_a_licence_expiring_just_after_a_trip_is_a_warning_not_a_gap():
    """It will not stop this trip; it will stop the next one."""
    found = check_ready(
        _roster(drivers=[_person(licence_expires_on=date(2026, 9, 20))]),
        today=TODAY,
    )
    gap = next(one for one in found if one.code == LICENCE_EXPIRING)
    assert "8 day(s) after" in gap.message
    assert "it will stop the next one" in gap.message


def test_the_board_is_sorted_soonest_first_then_worst():
    """An unsorted departure board is one nobody works through."""
    gaps = check_ready(_roster(vehicles=[], drivers=[]), today=TODAY)
    assert [one.code for one in sort_gaps(gaps)] == [NO_VEHICLE, NO_DRIVER]


# --------------------------------------------------------------------------- #
# The window a trip actually commits
# --------------------------------------------------------------------------- #


def test_a_vehicle_leaving_the_night_before_is_out_that_night():
    """A fleet calendar that says otherwise hands it to somebody else."""
    window = window_for(
        date(2026, 11, 4), date(2026, 11, 8), before_days=1, after_days=1
    )
    assert window.starts_on == date(2026, 11, 3)
    assert window.ends_on == date(2026, 11, 9)


def test_the_default_window_is_the_clients_own_dates():
    window = window_for(date(2026, 11, 4), date(2026, 11, 8))
    assert (window.starts_on, window.ends_on) == (
        date(2026, 11, 4),
        date(2026, 11, 8),
    )
