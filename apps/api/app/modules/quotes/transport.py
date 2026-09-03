"""Transport rules, as pure functions (§3.8, stage 3.10).

Accommodation is what a client compares; transport is what makes the trip
possible, and it is the half of a quote that goes wrong quietly. A missing
movement does not fail to price — it prices perfectly, one road leg short, and
the shortfall is invisible on a finished document.

Four rules carry this module.

**A journey is one movement per transition, plus arrival and departure.** Two
destinations is not one transfer: it is airport → hotel one, hotel one → hotel
two, hotel two → airport. So the expected number of movements is derived from
the package (``legs + 1``) rather than trusted from what somebody happened to
type.

**Flights are named, never priced.** Heissal holds no ticketing licence, so a
flight is itinerary text and an exclusion — never a line in the build-up. That
makes ``air`` unpriceable rather than merely unpriced, which is why it is not a
tariff lookup that happens to be empty: an empty lookup would one day be filled
in and start selling something we cannot sell.

**Rail always drags four transfers with it.** A train leaves from a terminus
nobody sleeps at: pickup → terminus and terminus → hotel, and the same in
reverse. An SGR quote without them is not a cheaper quote, it is an under-priced
one, so a rail line-haul with its transfers missing is **blocking** (§3.8).

**VVIP is an add-on, not part of the package.** It is quoted separately so the
comparison between options stays a comparison of the same journey.

Everything here is deterministic and free of I/O: these rules decide whether a
quote is issuable, and they need to be testable exhaustively rather than through
the database. :class:`~app.modules.quotes.packages.Problem` is reused verbatim —
the readiness check already speaks that vocabulary, and a second identical
dataclass would only be a second thing to keep in step.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.quotes.packages import Problem

LINE_HAUL = "line_haul"
TRANSFER = "transfer"
KINDS = (LINE_HAUL, TRANSFER)

#: Line-haul modes with a tariff behind them.
PRICED_MODES = ("road", "rail")
#: Modes that appear on the itinerary and never in the money. See the docstring.
NAMED_ONLY_MODES = ("air",)

#: Transfers a single rail line-haul implies: pickup to terminus, terminus to
#: hotel. A return journey is two line-haul segments and therefore four.
TRANSFERS_PER_RAIL_LEG = 2

# How a tariff's cost basis multiplies out against the group. ``per_vehicle``
# and ``per_leg`` are both group charges — a Coaster costs what it costs whoever
# is aboard — and the vehicle or leg count travels in ``units`` rather than
# being folded into the amount, so the document can still say "2 vehicles".
_LINE_BASIS = {
    "per_person": "per_person",
    "per_vehicle": "per_group",
    "per_leg": "per_group",
}

NO_SEGMENTS = "transport_none"
UNKNOWN_KIND = "transport_unknown_kind"
UNKNOWN_MODE = "transport_unknown_mode"
FLIGHT_NAMED = "transport_flight_named_not_priced"
RAIL_WITHOUT_TRANSFERS = "transport_rail_without_transfers"
MISSING_MOVEMENTS = "transport_movements_short"
VVIP_NOT_OPTIONAL = "transport_vvip_not_optional"


@dataclass(frozen=True)
class Segment:
    """One movement on a quote, as the rules need to see it.

    A projection of ``quote_transport_segments`` rather than the row itself, so
    the rules can be tested without a database and cannot accidentally reach for
    a relationship.
    """

    sequence: int
    kind: str
    mode: str
    travel_class: str = ""
    vehicle_type: str | None = None
    #: True when the segment runs on our own or a hired vehicle, which is
    #: costed per vehicle per day by the Stage 2 fleet model.
    has_vehicle: bool = False
    units: int = 1
    is_optional: bool = False
    is_vvip: bool = False
    label: str = ""


def line_basis(cost_basis: str) -> str:
    """The :mod:`~app.modules.quotes.cohorts` basis a tariff charges on."""
    try:
        return _LINE_BASIS[cost_basis]
    except KeyError:
        raise ValueError(f"unknown transport cost basis: {cost_basis!r}") from None


def movements_needed(legs: int) -> int:
    """Road/rail movements a package of ``legs`` stays implies.

    One per transition, plus arrival and departure. A single-property trip needs
    two; three destinations need four.
    """
    return max(0, legs) + 1


def priceable(segments: list[Segment]) -> list[Segment]:
    """The segments that belong in the package price.

    Optional extras and flights are both excluded, for opposite reasons: an
    extra is not part of the package, and a flight is not ours to sell.
    """
    return [s for s in _order(segments) if s.mode in PRICED_MODES and not s.is_optional]


def optional_extras(segments: list[Segment]) -> list[Segment]:
    """Priceable segments the client can add on — VVIP transport and the rest."""
    return [s for s in _order(segments) if s.mode in PRICED_MODES and s.is_optional]


def named_only(segments: list[Segment]) -> list[Segment]:
    """Segments that appear on the itinerary with no figure against them."""
    return [s for s in _order(segments) if s.mode in NAMED_ONLY_MODES]


def check(segments: list[Segment], *, legs: int = 1) -> list[Problem]:
    """Every fault in a quote's transport, blocking ones included.

    ``legs`` is the number of stays the itinerary contains, which is what says
    how many movements it takes to get through it.
    """
    if not segments:
        return [
            Problem(
                NO_SEGMENTS,
                "This quote carries no transport at all. If the client is "
                "driving themselves, say so on the document; otherwise the road "
                "cost is missing from every option.",
                blocking=False,
            )
        ]

    problems: list[Problem] = []
    ordered = _order(segments)

    for segment in ordered:
        if segment.kind not in KINDS:
            problems.append(
                Problem(
                    UNKNOWN_KIND,
                    f"Segment {segment.sequence} is a {segment.kind!r}, which is "
                    f"neither a line haul nor a transfer, so nothing knows how "
                    f"to price it.",
                    sequence=segment.sequence,
                )
            )
        if segment.mode not in PRICED_MODES and segment.mode not in NAMED_ONLY_MODES:
            modes = ", ".join(PRICED_MODES)
            problems.append(
                Problem(
                    UNKNOWN_MODE,
                    f"Segment {segment.sequence} travels by {segment.mode!r}, "
                    f"which is not a mode we sell ({modes}).",
                    sequence=segment.sequence,
                )
            )

    for segment in named_only(ordered):
        problems.append(
            Problem(
                FLIGHT_NAMED,
                f"Segment {segment.sequence} is a flight, so it is named on the "
                f"itinerary and carries no price: Heissal does not ticket air "
                f"travel. List the fare as an exclusion so the client knows to "
                f"book it themselves.",
                sequence=segment.sequence,
                blocking=False,
            )
        )

    rail = [s for s in ordered if s.kind == LINE_HAUL and s.mode == "rail"]
    transfers = [s for s in ordered if s.kind == TRANSFER and s.mode in PRICED_MODES]
    required = len(rail) * TRANSFERS_PER_RAIL_LEG
    if rail and len(transfers) < required:
        problems.append(
            Problem(
                RAIL_WITHOUT_TRANSFERS,
                f"{len(rail)} rail line haul(s) need {required} transfer legs "
                f"(pickup to terminus, terminus to hotel, and the same in "
                f"reverse) and this quote has {len(transfers)}. A train leaves "
                f"from a terminus nobody sleeps at, so the missing legs are a "
                f"cost the client would not be charged for.",
            )
        )

    # A trip run on our own or a hired vehicle is one segment covering every
    # movement — per vehicle per day, not per leg — so counting movements would
    # report a shortfall on the commonest case in the fleet.
    movements = [s for s in ordered if s.mode in PRICED_MODES and not s.is_optional]
    needed = movements_needed(legs)
    if not any(s.has_vehicle for s in ordered) and len(movements) < needed:
        problems.append(
            Problem(
                MISSING_MOVEMENTS,
                f"An itinerary of {legs} stay(s) takes {needed} movements — one "
                f"per transition plus arrival and departure — and this quote "
                f"prices {len(movements)}. Either the missing legs are unpriced "
                f"or the client is arranging them, and the document should say "
                f"which.",
                blocking=False,
            )
        )

    for segment in ordered:
        if segment.is_vvip and not segment.is_optional:
            problems.append(
                Problem(
                    VVIP_NOT_OPTIONAL,
                    f"Segment {segment.sequence} is VVIP transport inside the "
                    f"package price. It is quoted as an add-on, so that the "
                    f"options stay a comparison of the same journey.",
                    sequence=segment.sequence,
                    blocking=False,
                )
            )

    return problems


def _order(segments: list[Segment]) -> list[Segment]:
    """Segments in itinerary order — by sequence, which is what was typed."""
    return sorted(segments, key=lambda segment: segment.sequence)
