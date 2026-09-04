"""Route sequencing and itinerary scoring (§4.3). No database.

§4.2 put the roads on file; this is what that makes answerable. A package is
contiguous by construction (§3.9) — every night has a bed — and contiguity says
nothing at all about the roads between the beds. Nairobi to Amboseli to the Mara
and back is perfectly contiguous and puts an eleven-hour drive on a day the
document calls a transfer.

Two things are being defended.

**Nothing is guessed.** A hop with no route row makes every total an
understatement, so it is counted separately and no ordering is recommended on
the strength of roads we do not have.

**Nothing is reordered.** Packages are curated, not enumerated: a shorter order
is reported with its saving and left to the agent, who may have sequenced it for
a flight time or a migration crossing.
"""

from __future__ import annotations

from decimal import Decimal

from app.modules.quotes.routing import Road
from app.modules.quotes.sequencing import (
    BETTER_ORDER,
    LONG_DRIVE,
    NO_ROAD,
    SHORT_STAY,
    Hop,
    hops_of,
    order_km,
    score,
    sequence,
    shortest_order,
)

D = Decimal

# A small road network, in kilometres and minutes. Invented, in the shape of
# the real ones: Nairobi is the hub, the parks are far apart from each other.
NETWORK: dict[tuple[str, str], tuple[str, int]] = {
    ("Nairobi", "Amboseli"): ("240", 300),
    ("Nairobi", "Mara"): ("270", 330),
    ("Nairobi", "Diani"): ("490", 600),
    ("Amboseli", "Mara"): ("560", 720),
    ("Amboseli", "Diani"): ("420", 540),
    ("Mara", "Diani"): ("700", 840),
}


def _road(a: str, b: str) -> Road | None:
    """The test network, symmetric like the real table's reverse lookup."""
    found = NETWORK.get((a, b)) or NETWORK.get((b, a))
    if found is None:
        return None
    km, minutes = found
    return Road(
        label=f"{a} to {b}",
        distance_km=D(km),
        drive_time_minutes=minutes,
        reversed_lookup=(a, b) not in NETWORK,
    )


def _sparse(a: str, b: str) -> Road | None:
    """A network with the Amboseli–Mara road missing, which is the common case.

    A hand-entered table is complete for the routes an operator has actually
    driven and sold, and the gaps are the pairs nobody has quoted yet.
    """
    if {a, b} == {"Amboseli", "Mara"}:
        return None
    return _road(a, b)


# --------------------------------------------------------------------------- #
# The hops a package implies
# --------------------------------------------------------------------------- #


def test_hops_are_the_drives_between_consecutive_legs():
    hops = hops_of(["Nairobi", "Mara", "Nairobi"], road_for=_road)
    assert [(hop.from_place, hop.to_place) for hop in hops] == [
        ("Nairobi", "Mara"),
        ("Mara", "Nairobi"),
    ]
    assert all(hop.road is not None for hop in hops)


def test_two_legs_in_one_place_are_not_a_drive():
    """A city hotel then a lodge in the same place is a change of bed.

    Inventing a zero-kilometre hop would make it indistinguishable from a road
    with no row on file, which is a real fault this module reports.
    """
    hops = hops_of(["Nairobi", "Nairobi", "Mara"], road_for=_road)
    assert [(hop.from_place, hop.to_place) for hop in hops] == [("Nairobi", "Mara")]


def test_a_hop_with_no_road_is_kept_rather_than_dropped():
    hops = hops_of(["Amboseli", "Mara"], road_for=_sparse)
    assert len(hops) == 1
    assert hops[0].road is None
    assert hops[0].minutes == 0 and hops[0].km == D(0)


def test_a_single_leg_package_has_no_hops():
    assert hops_of(["Diani"], road_for=_road) == []
    assert hops_of([], road_for=_road) == []


# --------------------------------------------------------------------------- #
# The score
# --------------------------------------------------------------------------- #


def test_the_score_adds_up_the_driving():
    """Nairobi to the Mara and back: 540 km, 11 hours, longest leg 5.5.

    Both directions of one road, which is what a safari out of Nairobi is.
    """
    result = score(hops_of(["Nairobi", "Mara", "Nairobi"], road_for=_road))
    assert result.total_km == D("540")
    assert result.total_minutes == 660
    assert result.total_hours == D("11.0")
    assert result.longest_minutes == 330
    assert result.hops == 2
    assert result.is_complete


def test_an_unknown_hop_is_counted_and_never_estimated():
    """Every other figure is then an understatement by exactly that much.

    So ``is_complete`` exists: a total of 510 km across a trip with a missing
    road is not a total, and anything comparing two packages has to know.
    """
    result = score(
        hops_of(["Nairobi", "Amboseli", "Mara", "Nairobi"], road_for=_sparse)
    )
    assert result.unknown_hops == 1
    assert not result.is_complete
    # 240 there and 270 back, with the 560 between them unknown.
    assert result.total_km == D("510")


def test_the_longest_drive_is_reported_separately():
    """An agent choosing between packages needs to know which has the long day.

    A score that collapsed distance and the longest leg into one number would
    be comparable and useless: two trips of 1,000 km are different trips if one
    of them is a single fourteen-hour push.
    """
    result = score(hops_of(["Nairobi", "Mara", "Diani"], road_for=_road))
    assert result.total_km == D("970")
    assert result.longest_minutes == 840


# --------------------------------------------------------------------------- #
# A shorter order
# --------------------------------------------------------------------------- #


def test_the_shortest_order_keeps_the_ends_fixed():
    """The client lands in Nairobi and flies home from Nairobi.

    Nairobi → Mara → Amboseli → Nairobi is 270 + 560 + 240 = 1,070.
    Nairobi → Amboseli → Mara → Nairobi is 240 + 560 + 270 = 1,070 as well —
    a symmetric network makes the two mirror orders equal, which is exactly why
    the recommendation has to compare figures rather than assume a direction.
    """
    best = shortest_order(
        ["Nairobi", "Mara", "Amboseli", "Nairobi"], road_for=_road
    )
    assert best is not None
    ordering, total = best
    assert ordering[0] == "Nairobi" and ordering[-1] == "Nairobi"
    assert total == D("1070")


def test_a_genuinely_shorter_order_is_found():
    """Diani → Mara → Amboseli → Nairobi against the sensible order.

    As sequenced: 700 + 560 + 240 = 1,500.
    Diani → Amboseli → Mara → Nairobi: 420 + 560 + 270 = 1,250.
    250 km saved, which is four hours nobody has to sit in a vehicle.
    """
    given = ["Diani", "Mara", "Amboseli", "Nairobi"]
    assert order_km(given, road_for=_road) == D("1500")
    best = shortest_order(given, road_for=_road)
    assert best is not None
    assert best[0] == ("Diani", "Amboseli", "Mara", "Nairobi")
    assert best[1] == D("1250")


def test_no_order_is_recommended_when_a_road_is_missing():
    """A recommendation resting on data we do not have is worse than none.

    Otherwise the shortest ordering is always the one whose roads are least
    known, because a missing road contributes nothing to a partial sum.
    """
    assert (
        shortest_order(
            ["Nairobi", "Amboseli", "Mara", "Nairobi"], road_for=_sparse
        )
        is None
    )
    assert order_km(["Amboseli", "Mara"], road_for=_sparse) is None


def test_a_package_with_nothing_to_reorder_is_not_searched():
    """Two or three legs have no middle to permute."""
    assert shortest_order(["Nairobi", "Mara"], road_for=_road) is None
    assert shortest_order(["Nairobi", "Mara", "Nairobi"], road_for=_road) is None


def test_the_search_is_capped():
    """Permutations are factorial, so the limit is stated rather than found.

    A fifteen-leg itinerary is not a quote anybody sends, but it is a request
    somebody can make, and a fifteen-leg search is not a request anybody
    survives.
    """
    long_trip = ["Nairobi"] + ["Mara"] * 8 + ["Nairobi"]
    assert shortest_order(long_trip, road_for=_road) is None


# --------------------------------------------------------------------------- #
# What it tells the agent
# --------------------------------------------------------------------------- #


def test_a_missing_road_is_advisory_and_says_both_consequences():
    """It cannot be totalled and it cannot be costed.

    Advisory here because the blocking version of this lives where the money
    is: a movement on our own vehicle with no route refuses to be issued
    (§4.2). This is the package-level note that the itinerary itself has a gap.
    """
    result = sequence(
        ["Nairobi", "Amboseli", "Mara", "Nairobi"], road_for=_sparse
    )
    faults = [p for p in result.problems if p.code == NO_ROAD]
    assert len(faults) == 1
    assert not faults[0].blocking
    assert "cannot be totalled" in faults[0].message
    assert "cannot be costed" in faults[0].message


def test_a_drive_past_the_working_day_is_reported_with_the_gate_in_mind():
    """Amboseli to the Mara is twelve hours, and park gates close.

    The threshold is configuration, not a literal: "too long to drive in a day"
    is a commercial judgement about what this operation will sell, and it is
    the kind that changes the first time a client complains.
    """
    result = sequence(
        ["Nairobi", "Amboseli", "Mara", "Nairobi"],
        road_for=_road,
        max_drive_minutes=600,
    )
    fault = next(p for p in result.problems if p.code == LONG_DRIVE)
    assert not fault.blocking
    assert "12.0 hours" in fault.message
    assert "gate closes" in fault.message

    # Raise what the operation is willing to sell and the note goes away.
    relaxed = sequence(
        ["Nairobi", "Amboseli", "Mara", "Nairobi"],
        road_for=_road,
        max_drive_minutes=780,
    )
    assert not [p for p in relaxed.problems if p.code == LONG_DRIVE]


def test_one_night_after_a_long_drive_is_flagged():
    """Most of two days on the road for a single evening there.

    The shape of trip a client remembers badly, and the one an agent building
    to a night count rather than to a map produces by accident.
    """
    result = sequence(
        ["Nairobi", "Mara", "Nairobi"],
        road_for=_road,
        nights=[1, 1, 0],
        max_drive_minutes=600,
    )
    fault = next(p for p in result.problems if p.code == SHORT_STAY)
    assert not fault.blocking
    assert "one night after about 5.5 hours" in fault.message


def test_a_comfortable_stay_after_a_long_drive_is_not_flagged():
    """Three nights in the Mara after the drive up is an ordinary safari."""
    result = sequence(
        ["Nairobi", "Mara", "Nairobi"],
        road_for=_road,
        nights=[1, 3, 0],
        max_drive_minutes=600,
    )
    assert not [p for p in result.problems if p.code == SHORT_STAY]


def test_a_shorter_order_is_a_note_and_not_a_correction():
    """Reported with the saving, and with why it may be deliberate.

    Packages are curated, not enumerated (§3.9). The agent spoke to the client;
    a system that silently reorders their itinerary is a system arguing with
    the person who has the information.
    """
    result = sequence(
        ["Diani", "Mara", "Amboseli", "Nairobi"], road_for=_road, nights=[3, 2, 2, 1]
    )
    assert result.better_order == ("Diani", "Amboseli", "Mara", "Nairobi")
    assert result.saving_km == D("250")
    fault = next(p for p in result.problems if p.code == BETTER_ORDER)
    assert not fault.blocking
    assert "250 km less" in fault.message
    assert "may be deliberate" in fault.message


def test_an_order_that_is_already_shortest_gets_no_note():
    result = sequence(
        ["Diani", "Amboseli", "Mara", "Nairobi"], road_for=_road
    )
    assert result.better_order == ()
    assert result.saving_km == D(0)
    assert not [p for p in result.problems if p.code == BETTER_ORDER]


def test_every_sequencing_fault_is_advisory():
    """None of these is a reason to refuse a quote.

    Each is a trip that can be sold and a trip somebody should look at twice.
    The blocking rules in this area are about money or deliverability — a
    movement with no tariff (§3.10), a road the vehicle cannot take (§4.2) —
    and a blocking sequencing rule would have the system arguing with the
    agent who actually spoke to the client.
    """
    result = sequence(
        ["Diani", "Mara", "Amboseli", "Nairobi"],
        road_for=_sparse,
        nights=[3, 1, 2, 1],
        max_drive_minutes=480,
    )
    assert result.problems
    assert all(not p.blocking for p in result.problems)


def test_the_same_package_scores_the_same_twice():
    """Frozen into a version, so it cannot depend on anything ambient."""
    places = ["Nairobi", "Mara", "Diani", "Nairobi"]
    first = sequence(places, road_for=_road)
    second = sequence(places, road_for=_road)
    assert first.score == second.score
    assert first.better_order == second.better_order
    assert [p.message for p in first.problems] == [p.message for p in second.problems]


def test_a_hop_carries_its_own_figures():
    """Small, but it is what the worksheet prints per drive."""
    hop = Hop(
        from_place="Nairobi",
        to_place="Mara",
        road=_road("Nairobi", "Mara"),
        nights_after=3,
    )
    assert hop.km == D("270")
    assert hop.minutes == 330
    assert hop.nights_after == 3
