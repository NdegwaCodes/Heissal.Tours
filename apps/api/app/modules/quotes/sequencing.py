"""Route sequencing and itinerary scoring, as pure functions (§4.3).

§4.2 put the road network on file. This is what it makes answerable: **is this
package drivable in the order the agent built it, and is there a better order?**

Both questions were unanswerable before, and the first one matters more than it
sounds. A package is contiguous by construction (§3.9) — every night has a bed
— but contiguity says nothing about the roads between the beds. Nairobi to
Amboseli to the Mara and back is a perfectly contiguous itinerary that puts an
eleven-hour drive on a day the document calls a transfer, and arrives at a park
gate after it has closed. Nothing in the quote shows it: the price is right, the
nights add up, and the failure happens on the road.

**Nothing here reorders anything.** Packages are curated, not enumerated (§3.9):
the agent chooses what to offer and the engine prices it. So a shorter ordering
is *reported*, with the saving named, and left as the agent's decision — they
may have sequenced it that way for a flight time, a lodge's availability or a
migration crossing, and none of those are facts this module holds.

Everything is derived from the route table and the leg dates. No I/O, no clock:
the same package scores the same twice, which is what lets a score be frozen
into a version.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import permutations

from app.modules.quotes.packages import Problem
from app.modules.quotes.routing import Road, plain

# Problem codes, so a caller acts on the fault rather than parsing a sentence.
NO_ROAD = "sequence_road_not_on_file"
LONG_DRIVE = "sequence_drive_too_long"
SHORT_STAY = "sequence_stay_shorter_than_drive"
BETTER_ORDER = "sequence_shorter_order_exists"

#: Permutations are factorial, so the search is capped rather than left to
#: discover its own limit on a fifteen-leg itinerary. Five middle legs is 120
#: orderings, which is instant; the cap is on the *middle*, so a seven-leg
#: package is still searched.
MAX_LEGS_TO_REORDER = 7


@dataclass(frozen=True)
class Hop:
    """One drive between two consecutive legs of a package.

    ``road`` is ``None`` where the route table has no row for the pair. That is
    reported rather than estimated: the whole point of §4.2 is that nobody can
    derive a Kenyan road's length from two coordinates.
    """

    from_place: str
    to_place: str
    road: Road | None = None
    #: Nights spent at ``to_place`` after arriving, from the leg that holds it.
    nights_after: int = 0

    @property
    def minutes(self) -> int:
        return self.road.drive_time_minutes if self.road else 0

    @property
    def km(self) -> Decimal:
        return self.road.distance_km if self.road else Decimal(0)


@dataclass(frozen=True)
class Score:
    """What driving this package costs the client in time and road.

    Not a single number. A "score" that collapses distance, the longest day and
    the unknown hops into one figure would be comparable and meaningless: an
    agent choosing between two packages needs to know *which* of them has the
    eleven-hour day.
    """

    total_km: Decimal = Decimal(0)
    total_minutes: int = 0
    longest_minutes: int = 0
    #: Hops with no road on file. Held apart because every other figure here is
    #: an understatement by exactly that much.
    unknown_hops: int = 0
    hops: int = 0

    @property
    def total_hours(self) -> Decimal:
        return (Decimal(self.total_minutes) / Decimal(60)).quantize(Decimal("0.1"))

    @property
    def is_complete(self) -> bool:
        """Whether every hop had a road, and the totals therefore mean anything."""
        return self.unknown_hops == 0


@dataclass
class Sequenced:
    """A package's hops, its score, and what is wrong with the order."""

    hops: list[Hop] = field(default_factory=list)
    score: Score = field(default_factory=Score)
    problems: list[Problem] = field(default_factory=list)
    #: The shortest ordering found, as place names, where it beats the given
    #: one. Empty when the order is already shortest or could not be searched.
    better_order: tuple[str, ...] = ()
    #: What that ordering would save, in kilometres.
    saving_km: Decimal = Decimal(0)


def hops_of(
    places: Sequence[str],
    *,
    road_for: Callable[[str, str], Road | None],
    nights: Sequence[int] | None = None,
) -> list[Hop]:
    """The drives implied by visiting ``places`` in order.

    Consecutive duplicates produce no hop: two legs at one destination — a city
    hotel and then a lodge in the same place — is a change of bed and not a
    drive, and inventing a zero-kilometre hop would make the leg look like a
    road with no row on file.
    """
    out: list[Hop] = []
    stay = list(nights or [])
    for index in range(len(places) - 1):
        here, there = places[index], places[index + 1]
        if here == there:
            continue
        out.append(
            Hop(
                from_place=here,
                to_place=there,
                road=road_for(here, there),
                nights_after=(
                    stay[index + 1] if index + 1 < len(stay) else 0
                ),
            )
        )
    return out


def score(hops: Sequence[Hop]) -> Score:
    """Add up the driving. Unknown hops are counted, never guessed at."""
    known = [hop for hop in hops if hop.road is not None]
    return Score(
        total_km=sum((hop.km for hop in known), Decimal(0)),
        total_minutes=sum(hop.minutes for hop in known),
        longest_minutes=max((hop.minutes for hop in known), default=0),
        unknown_hops=sum(1 for hop in hops if hop.road is None),
        hops=len(hops),
    )


def order_km(
    places: Sequence[str], *, road_for: Callable[[str, str], Road | None]
) -> Decimal | None:
    """Total kilometres for one ordering, or ``None`` if any hop is unknown.

    ``None`` rather than a partial sum on purpose: comparing an ordering whose
    roads are all on file against one missing two of them would recommend the
    one we know least about, every time.
    """
    total = Decimal(0)
    for index in range(len(places) - 1):
        here, there = places[index], places[index + 1]
        if here == there:
            continue
        road = road_for(here, there)
        if road is None:
            return None
        total += road.distance_km
    return total


def shortest_order(
    places: Sequence[str], *, road_for: Callable[[str, str], Road | None]
) -> tuple[tuple[str, ...], Decimal] | None:
    """The shortest ordering that keeps the first and last places fixed.

    Fixed ends because they are not ours to move: the first leg is where the
    client lands and the last is where they fly home from, and a "better" trip
    that starts in the wrong city is not better. What is left is the middle,
    which is exactly the part an agent picked an order for.

    Returns ``None`` when there is nothing to search or when any ordering has a
    road missing — a recommendation resting on data we do not have is worse
    than none.
    """
    if len(places) < 4 or len(places) > MAX_LEGS_TO_REORDER:
        return None
    first, last = places[0], places[-1]
    middle = list(places[1:-1])
    best: tuple[tuple[str, ...], Decimal] | None = None
    for candidate in permutations(middle):
        ordering = (first, *candidate, last)
        total = order_km(ordering, road_for=road_for)
        if total is None:
            return None
        if best is None or total < best[1]:
            best = (ordering, total)
    return best


def sequence(
    places: Sequence[str],
    *,
    road_for: Callable[[str, str], Road | None],
    nights: Sequence[int] | None = None,
    max_drive_minutes: int = 600,
) -> Sequenced:
    """Everything §4.3 can say about one package's order.

    ``max_drive_minutes`` is configuration rather than a literal, because "too
    long to drive in a day" is a commercial judgement about what Heissal is
    willing to sell — and it is the kind of judgement that changes once a
    client complains.

    Every fault here is **advisory**. Each is a trip that can be sold and a
    trip somebody should look at twice, which is the definition of advice: an
    agent may know the eleven-hour day is what the client asked for, and a
    blocking rule would make the system argue with the person who spoke to
    them. The blocking faults in this area are elsewhere and are about money
    or deliverability — a movement with no tariff (§3.10), a road the vehicle
    cannot take (§4.2).
    """
    hops = hops_of(places, road_for=road_for, nights=nights)
    out = Sequenced(hops=hops, score=score(hops))

    for hop in hops:
        if hop.road is None:
            out.problems.append(
                Problem(
                    NO_ROAD,
                    f"No route is on file between {hop.from_place} and "
                    f"{hop.to_place}, so this itinerary's driving cannot be "
                    f"totalled and that drive cannot be costed. Enter the "
                    f"route.",
                    blocking=False,
                )
            )
            continue
        if hop.minutes > max_drive_minutes:
            hours = (Decimal(hop.minutes) / Decimal(60)).quantize(Decimal("0.1"))
            out.problems.append(
                Problem(
                    LONG_DRIVE,
                    f"{hop.from_place} to {hop.to_place} is about {hours} hours "
                    f"of driving, past the {max_drive_minutes // 60}-hour day "
                    f"this operation sells. Check the arrival is before the "
                    f"gate closes, or break the drive.",
                    blocking=False,
                )
            )
        # A long drive for one night is the shape of trip clients remember
        # badly: most of two days on the road for a single evening there.
        if hop.nights_after == 1 and hop.minutes >= max_drive_minutes // 2:
            hours = (Decimal(hop.minutes) / Decimal(60)).quantize(Decimal("0.1"))
            out.problems.append(
                Problem(
                    SHORT_STAY,
                    f"{hop.to_place} is one night after about {hours} hours of "
                    f"driving. Most of two days on the road for one evening "
                    f"there — worth confirming the client wants it that way.",
                    blocking=False,
                )
            )

    given = order_km(places, road_for=road_for)
    best = shortest_order(places, road_for=road_for)
    if given is not None and best is not None and best[1] < given:
        out.better_order = best[0]
        out.saving_km = given - best[1]
        out.problems.append(
            Problem(
                BETTER_ORDER,
                f"Visiting {' → '.join(best[0])} instead drives "
                f"{plain(out.saving_km)} km less. That may be deliberate — a flight "
                f"time, a lodge's availability, a migration crossing — so it is "
                f"a note and not a correction.",
                blocking=False,
            )
        )
    return out
