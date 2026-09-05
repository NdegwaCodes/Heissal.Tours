"""The other side of a booking: what we owe, and whether the room is held (§8.3).

§7.1 built the client side completely — what they owe, when, and what has
arrived. There has never been a counterpart. Every quote is costed from
supplier rates and every version snapshot carries a ``supplier_paid_total``,
and yet nothing in this system has ever told a hotel that a group is coming,
recorded that they said yes, or noticed that they invoiced more than we
budgeted.

Two failures follow from that, and they are the two that end an operator's
career.

**The room was never actually booked.** A trip departs, everything on the
departure board is green — vehicle, driver, seats — and the hotel has no
reservation. §8.1 asked "who is driving"; it never asked "did anyone ring the
lodge". So an unconfirmed supplier is a gap on the same board, on a threshold
the business sets, because how late is too late differs between a Diani hotel
in May and a Mara camp in August.

**The margin quietly went.** A package costed at 180,000 that gets invoiced at
195,000 has eaten a fifth of the profit, and today nothing would ever compare
the two. This is §8.2's argument again in a different currency: an estimate
with no actual beside it is a number that cannot be wrong.

What this module does **not** do is decide the expected figure. The snapshot's
``supplier_paid_total`` is one number for a whole option across every supplier
on it — there is no per-hotel split in there to read — so splitting it would be
a guess dressed as a fact, and an operator typing the figure off the contract
is both more accurate and more honest. Same instinct as §8.2 refusing to
estimate a driver's day rate.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

#: Where a supplier booking has got to. Four states and no more: an operator
#: needs to know whether they have asked, whether they have an answer, and
#: whether it is off. Anything finer is a note.
TO_REQUEST = "to_request"
REQUESTED = "requested"
CONFIRMED = "confirmed"
CANCELLED = "cancelled"
SUPPLY_STATUSES = (TO_REQUEST, REQUESTED, CONFIRMED, CANCELLED)

#: Statuses that still hold something. A cancelled row owes nobody anything.
LIVE_STATUSES = (TO_REQUEST, REQUESTED, CONFIRMED)

# What is wrong, or worth knowing.
NOT_REQUESTED = "supplier_not_requested"
NOT_CONFIRMED = "supplier_not_confirmed"
OVER_BUDGET = "supplier_invoiced_over"
UNDER_BUDGET = "supplier_invoiced_under"
UNSETTLED = "supplier_unsettled"

#: How far out an unconfirmed supplier stops being a plan and starts being a
#: problem. A default, and the caller's to override — a Diani hotel in May and
#: a Mara camp in August are not the same conversation.
DEFAULT_CONFIRM_BY_DAYS = 14

#: How far an invoice may differ from the budget before it is worth saying so.
DEFAULT_TOLERANCE_PCT = Decimal("2")


class SupplyRefused(ValueError):
    """A supplier booking change the rules will not allow, with the reason."""


def normalise_status(value: str | None) -> str:
    cleaned = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned not in SUPPLY_STATUSES:
        raise SupplyRefused(
            f"'{value}' is not a state a supplier booking can be in. Say "
            f"{', '.join(SUPPLY_STATUSES)}."
        )
    return cleaned


def check_transition(
    current: str,
    target: str,
    *,
    their_reference: str | None = None,
    reason: str | None = None,
) -> None:
    """Whether a supplier booking may move from ``current`` to ``target``.

    Two refusals, and both are things an operator would otherwise discover at
    a reception desk.

    **Confirming needs their reference.** A confirmation with no booking number
    is somebody's recollection of a phone call, and it is exactly the row that
    turns out to be wrong on the day. The reference is what a hotel can look
    up while a family stands in the lobby.

    **Cancelling needs a reason.** §5.2's argument about a lost lead: "we
    cancelled the Watamu rooms" is a fact nobody can act on, and "the client
    moved the dates" is the next agent's answer to a supplier who rings up.
    """
    if current == target:
        raise SupplyRefused(
            f"This is already {target.replace('_', ' ')}."
        )
    if target == CONFIRMED and not (their_reference or "").strip():
        raise SupplyRefused(
            "A confirmation needs the supplier's own reference. Without one "
            "this is somebody's recollection of a phone call, and there is "
            "nothing for the hotel to look up while the family stands in the "
            "lobby."
        )
    if target == CANCELLED and not (reason or "").strip():
        raise SupplyRefused(
            "Say why this is being cancelled. The next person to talk to this "
            "supplier needs to know whether we moved the dates or they let us "
            "down."
        )
    if current == CANCELLED and target != TO_REQUEST:
        raise SupplyRefused(
            "This was cancelled. Put it back to 'to request' and ask again — "
            "reviving a cancelled reservation without asking is how two groups "
            "end up in one room."
        )


# --------------------------------------------------------------------------- #
# What is owed
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Committed:
    """One supplier commitment, as the rules see it."""

    supplier: str
    status: str = TO_REQUEST
    #: What the quote was costed at. Typed off the contract, not derived — see
    #: the module docstring for why the snapshot cannot be split per supplier.
    expected: Decimal = Decimal(0)
    #: What they actually billed, once they have. ``None`` until the invoice
    #: arrives, which is a different thing from zero.
    invoiced: Decimal | None = None
    #: What has been paid out. Also ``None`` rather than zero until something
    #: has been.
    settled: Decimal | None = None
    currency: str = ""
    check_in: date | None = None
    confirm_by: date | None = None
    their_reference: str = ""

    @property
    def owed(self) -> Decimal:
        """What is still to be paid: the invoice where there is one, else the budget.

        Falling back to the budget rather than to zero matters. A supplier who
        has not invoiced yet is not a supplier who is owed nothing, and a
        payables figure that quietly ignored them would be exactly wrong in the
        direction that hurts.
        """
        if self.status == CANCELLED:
            return Decimal(0)
        due = self.invoiced if self.invoiced is not None else self.expected
        return max(due - (self.settled or Decimal(0)), Decimal(0))


@dataclass
class Exposure:
    """What is owed to suppliers, per currency and never summed across them."""

    expected: dict[str, Decimal] = field(default_factory=dict)
    invoiced: dict[str, Decimal] = field(default_factory=dict)
    settled: dict[str, Decimal] = field(default_factory=dict)
    owed: dict[str, Decimal] = field(default_factory=dict)
    suppliers: int = 0
    unconfirmed: int = 0

    @property
    def all_confirmed(self) -> bool:
        return self.suppliers > 0 and self.unconfirmed == 0


def exposure(items: Iterable[Committed]) -> Exposure:
    """Total what is owed, keeping the currencies apart.

    Never summed across them, for §5.1's reason and §7.1's: a KES figure and a
    USD figure added together is a number that is wrong in a way nobody can
    see. A lodge billing in dollars and a transfer company billing in shillings
    is the normal case here, not the exotic one.
    """
    out = Exposure()
    for item in items:
        if item.status == CANCELLED:
            continue
        currency = (item.currency or "").upper()
        out.suppliers += 1
        if item.status != CONFIRMED:
            out.unconfirmed += 1
        if not currency:
            continue
        out.expected[currency] = out.expected.get(currency, Decimal(0)) + item.expected
        if item.invoiced is not None:
            out.invoiced[currency] = (
                out.invoiced.get(currency, Decimal(0)) + item.invoiced
            )
        if item.settled is not None:
            out.settled[currency] = out.settled.get(currency, Decimal(0)) + item.settled
        owed = item.owed
        if owed:
            out.owed[currency] = out.owed.get(currency, Decimal(0)) + owed
    return out


# --------------------------------------------------------------------------- #
# What is wrong
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Concern:
    """Something about a supplier commitment worth somebody's attention."""

    code: str
    message: str
    supplier: str = ""
    #: Days until the trip, so the board reads soonest first.
    days: int = 0
    variance_pct: Decimal | None = None


def check_supply(
    items: Sequence[Committed],
    *,
    departs_on: date | None,
    today: date,
    confirm_by_days: int = DEFAULT_CONFIRM_BY_DAYS,
) -> list[Concern]:
    """Whether anything about this trip's suppliers should stop it leaving.

    The failure this exists for: a departure board showing a vehicle, a driver
    and enough seats, all green, and no reservation at the lodge. §8.1 asked
    who was driving and never asked whether anybody had rung the hotel.

    The threshold is the caller's. A Diani hotel in May will take a booking on
    the Thursday; a Mara camp in August wanted it in February, and no default
    here can tell those apart.
    """
    out: list[Concern] = []
    until = (departs_on - today).days if departs_on else 0
    for item in items:
        if item.status == CANCELLED:
            continue
        deadline = item.confirm_by
        close = until <= confirm_by_days if departs_on else False
        if deadline is not None:
            close = close or deadline <= today
        if item.status == TO_REQUEST and close:
            out.append(
                Concern(
                    NOT_REQUESTED,
                    f"{item.supplier} has not been asked yet and the trip "
                    f"leaves in {until} day(s). Nobody has told them a group "
                    f"is coming.",
                    supplier=item.supplier,
                    days=until,
                )
            )
        elif item.status == REQUESTED and close:
            out.append(
                Concern(
                    NOT_CONFIRMED,
                    f"{item.supplier} was asked and has not confirmed, and the "
                    f"trip leaves in {until} day(s). Chase it — an unanswered "
                    f"request is not a reservation.",
                    supplier=item.supplier,
                    days=until,
                )
            )
    return out


def check_invoices(
    items: Sequence[Committed], *, tolerance_pct: Decimal = DEFAULT_TOLERANCE_PCT
) -> list[Concern]:
    """Where a supplier billed something other than what the quote assumed.

    §8.2's argument in a different currency: an estimate with no actual beside
    it is a number that cannot be wrong. A package costed at 180,000 and
    invoiced at 195,000 has eaten a fifth of the profit, and until these two
    figures sat on the same row nothing would ever have compared them.

    Under-billing is reported too, and not as good news: a supplier who has
    short-invoiced will send the rest later, usually after the trip has been
    reconciled and closed.
    """
    out: list[Concern] = []
    for item in items:
        if item.status == CANCELLED or item.invoiced is None or not item.expected:
            continue
        variance = (
            (item.invoiced - item.expected) / item.expected * 100
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if abs(variance) < tolerance_pct:
            continue
        gap = abs(item.invoiced - item.expected)
        if variance > 0:
            out.append(
                Concern(
                    OVER_BUDGET,
                    f"{item.supplier} invoiced {_plain(item.invoiced)} "
                    f"{item.currency} against a budget of "
                    f"{_plain(item.expected)} — {_plain(gap)} more than the "
                    f"quote was costed on. That comes straight out of the "
                    f"margin.",
                    supplier=item.supplier,
                    variance_pct=variance,
                )
            )
        else:
            out.append(
                Concern(
                    UNDER_BUDGET,
                    f"{item.supplier} invoiced {_plain(item.invoiced)} "
                    f"{item.currency} against a budget of "
                    f"{_plain(item.expected)} — {_plain(gap)} less. Worth "
                    f"checking they have not left something off: the rest "
                    f"usually arrives after the trip is closed.",
                    supplier=item.supplier,
                    variance_pct=variance,
                )
            )
    return out


def unsettled(
    items: Sequence[Committed], *, departed_on: date | None, today: date
) -> list[Concern]:
    """Suppliers still owed money after the group has gone home."""
    if departed_on is None or departed_on >= today:
        return []
    since = (today - departed_on).days
    return [
        Concern(
            UNSETTLED,
            f"{item.supplier} is still owed {_plain(item.owed)} "
            f"{item.currency}, {since} day(s) after the group left.",
            supplier=item.supplier,
            days=since,
        )
        for item in items
        if item.status != CANCELLED and item.owed > 0
    ]


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


__all__ = [
    "CANCELLED",
    "CONFIRMED",
    "DEFAULT_CONFIRM_BY_DAYS",
    "DEFAULT_TOLERANCE_PCT",
    "LIVE_STATUSES",
    "NOT_CONFIRMED",
    "NOT_REQUESTED",
    "OVER_BUDGET",
    "REQUESTED",
    "SUPPLY_STATUSES",
    "TO_REQUEST",
    "UNDER_BUDGET",
    "UNSETTLED",
    "Committed",
    "Concern",
    "Exposure",
    "SupplyRefused",
    "check_invoices",
    "check_supply",
    "check_transition",
    "exposure",
    "normalise_status",
    "unsettled",
]
