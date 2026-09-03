"""Multi-destination packages: the leg rules, as pure functions (§3.9).

An option stopped being "one hotel" the moment the client asked for **2 or 3
destinations in a single 7–30 day trip**. A package is an ordered set of legs,
each one a destination, a property, a meal plan and a date range — and the thing
that makes a package either coherent or quietly wrong is its *dates*.

Packages are **curated, not enumerated**. Three legs against four hotels against
three transport modes is 192 combinations, and a matrix of those is not a
document anybody reads. The agent picks the packages worth offering; this module
only checks that each one is a trip a person could actually take.

Everything here is deterministic and free of I/O, because these are the rules
that decide whether a quote is issuable and they need to be testable
exhaustively rather than through the database.

**Why contiguity is blocking rather than a warning.** A one-day gap between two
legs is a night the client has no bed and no line on the invoice: they arrive at
hotel two a day late, or sleep somewhere nobody has paid for. A one-day overlap
is a night paid for twice, in two towns. Neither is visible on a finished
document — the per-person figure looks entirely normal — so it has to be caught
before the quote can be issued, not flagged for someone to notice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# Problem codes, so a caller can act on the specific fault rather than parse a
# sentence. These are the codes the readiness check reports.
NO_LEGS = "package_no_legs"
BAD_RANGE = "package_leg_dates_reversed"
GAP = "package_leg_gap"
OVERLAP = "package_leg_overlap"
LATE_START = "package_starts_after_arrival"
EARLY_START = "package_starts_before_arrival"
SHORT_END = "package_ends_before_departure"
LONG_END = "package_ends_after_departure"
DUPLICATE_SEQUENCE = "package_duplicate_sequence"
REPEATED_DESTINATION = "package_destination_repeated"
TOO_SHORT = "package_leg_below_minimum_stay"


@dataclass(frozen=True)
class Leg:
    """One stay within a package. ``key`` identifies the destination.

    Deliberately holds dates rather than a night count. A count plus an implied
    start is the same information only while nothing is ever edited; the moment
    an agent moves the middle leg by a day, dates say what happened and counts
    do not.
    """

    sequence: int
    destination: str
    check_in: date
    check_out: date
    property_name: str = ""

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


@dataclass(frozen=True)
class Problem:
    """One fault in a package. ``blocking`` decides whether it can be issued."""

    code: str
    message: str
    sequence: int | None = None
    blocking: bool = True


def order(legs: list[Leg]) -> list[Leg]:
    """Legs in itinerary order.

    Sorted by ``sequence`` and not by date: the sequence is what the agent typed,
    and sorting by date would silently *repair* a mis-sequenced package into a
    valid-looking one, hiding the mistake instead of reporting it.
    """
    return sorted(legs, key=lambda leg: leg.sequence)


def total_nights(legs: list[Leg]) -> int:
    return sum(leg.nights for leg in legs)


def check(
    legs: list[Leg],
    *,
    arrival: date,
    departure: date,
    minimum_stay: dict[int, int] | None = None,
) -> list[Problem]:
    """Every fault in a package, blocking ones included.

    ``minimum_stay`` maps a leg's sequence to the nights its property requires,
    which the caller looks up from the rates. A leg below its minimum is
    **blocking**, unlike the single-property case where the property is simply
    dropped from the comparison (§3.3a): a package is one offer, so a leg that
    cannot be booked makes the whole package unbookable rather than shorter.
    """
    problems: list[Problem] = []
    if not legs:
        return [
            Problem(
                NO_LEGS,
                "This package has no legs, so there is nothing to price.",
                blocking=True,
            )
        ]

    ordered = order(legs)

    seen: dict[int, int] = {}
    for leg in ordered:
        seen[leg.sequence] = seen.get(leg.sequence, 0) + 1
    for sequence, count in sorted(seen.items()):
        if count > 1:
            problems.append(
                Problem(
                    DUPLICATE_SEQUENCE,
                    f"Two legs share position {sequence}, so the order of the "
                    f"itinerary is undefined.",
                    sequence=sequence,
                )
            )

    for leg in ordered:
        if leg.check_out <= leg.check_in:
            problems.append(
                Problem(
                    BAD_RANGE,
                    f"Leg {leg.sequence} ({leg.destination}) checks out on "
                    f"{leg.check_out}, which is not after its check-in of "
                    f"{leg.check_in}.",
                    sequence=leg.sequence,
                )
            )

    # Contiguity. The check-out date of one leg IS the check-in date of the next:
    # the guest sleeps at the first hotel up to that morning and at the second
    # from that afternoon, so the date belongs to both and is counted as a night
    # by neither twice.
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if later.check_in > earlier.check_out:
            missing = (later.check_in - earlier.check_out).days
            problems.append(
                Problem(
                    GAP,
                    f"{missing} night(s) between leg {earlier.sequence} "
                    f"({earlier.destination}, out {earlier.check_out}) and leg "
                    f"{later.sequence} ({later.destination}, in "
                    f"{later.check_in}) have no accommodation.",
                    sequence=later.sequence,
                )
            )
        elif later.check_in < earlier.check_out:
            doubled = (earlier.check_out - later.check_in).days
            problems.append(
                Problem(
                    OVERLAP,
                    f"Leg {later.sequence} ({later.destination}) starts "
                    f"{doubled} night(s) before leg {earlier.sequence} "
                    f"({earlier.destination}) ends, so those nights are paid "
                    f"for in two places at once.",
                    sequence=later.sequence,
                )
            )

    first, last = ordered[0], ordered[-1]
    if first.check_in > arrival:
        problems.append(
            Problem(
                LATE_START,
                f"The package starts on {first.check_in} but the group arrives "
                f"on {arrival}, leaving "
                f"{(first.check_in - arrival).days} night(s) unaccounted for.",
                sequence=first.sequence,
            )
        )
    elif first.check_in < arrival:
        problems.append(
            Problem(
                EARLY_START,
                f"The package starts on {first.check_in}, before the group "
                f"arrives on {arrival}.",
                sequence=first.sequence,
            )
        )
    if last.check_out < departure:
        problems.append(
            Problem(
                SHORT_END,
                f"The package ends on {last.check_out} but the group departs on "
                f"{departure}, leaving "
                f"{(departure - last.check_out).days} night(s) unaccounted for.",
                sequence=last.sequence,
            )
        )
    elif last.check_out > departure:
        problems.append(
            Problem(
                LONG_END,
                f"The package ends on {last.check_out}, after the group departs "
                f"on {departure}.",
                sequence=last.sequence,
            )
        )

    for leg in ordered:
        needed = (minimum_stay or {}).get(leg.sequence)
        if needed and leg.nights < needed:
            problems.append(
                Problem(
                    TOO_SHORT,
                    f"Leg {leg.sequence} ({leg.property_name or leg.destination}) "
                    f"is {leg.nights} night(s) but the property requires "
                    f"{needed}.",
                    sequence=leg.sequence,
                )
            )

    # A destination visited twice is legal — Nairobi at both ends of a safari is
    # the commonest itinerary in the country — so this is a note and not a fault.
    # It is reported because the *other* reason it happens is an agent copying a
    # leg and forgetting to change the destination.
    counts: dict[str, list[int]] = {}
    for leg in ordered:
        counts.setdefault(leg.destination, []).append(leg.sequence)
    for destination, sequences in sorted(counts.items()):
        if len(sequences) > 1:
            problems.append(
                Problem(
                    REPEATED_DESTINATION,
                    f"{destination} appears on legs "
                    f"{', '.join(str(s) for s in sequences)}. That is normal for "
                    f"a city at both ends of a safari; check it is not a copied "
                    f"leg.",
                    sequence=sequences[-1],
                    blocking=False,
                )
            )

    return problems


def blocking(problems: list[Problem]) -> list[Problem]:
    return [problem for problem in problems if problem.blocking]


def nights_of(leg: Leg) -> list[date]:
    """The nights a leg is charged for, as dates.

    One entry per night slept, so ``check_out`` is absent: a 29–31 October leg
    is the nights of the 29th and the 30th. Rates and park fees are both
    selected per night (§3.1), and this is the list they are selected over.
    """
    return [leg.check_in + timedelta(days=n) for n in range(leg.nights)]
