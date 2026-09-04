"""What is owed, when, and what is left (§7.1). Pure functions.

The arithmetic a booking turns on, kept away from the database so it can be
tested exhaustively — because these are the figures on an invoice, and the two
ways they go wrong are both silent.

**Instalments must sum to the total, exactly.** A 30% deposit on 223,749 is
67,124.70, and a schedule whose parts add up to 223,749.01 is a schedule that
leaves a client owing a cent forever or being refunded one. The last instalment
takes the remainder — the same rule §3.6 uses for splitting a shared cost
across cohorts, and for the same reason.

**A balance is never negative to a client.** An overpayment is real (clients
round up, pay twice, pay in the wrong currency) and it is a credit to be dealt
with, not a negative bill. So the balance floors at zero and the overpayment is
reported separately, where somebody can see it.

**Nothing here computes a cancellation charge.** The ladder — "inside 30 days,
50% retained" — is commercial policy nobody has given us, and a plausible
invented figure on a refund is worse than no figure: it looks like it came from
a contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

#: The precision every stored money column here uses. Quantised at each step
#: rather than at the end, because an instalment is a figure somebody pays and
#: a bank does not accept four decimal places.
CENTS = Decimal("0.01")

DEPOSIT = "Deposit"
BALANCE = "Balance"
FULL = "Full payment"


class ScheduleRefused(ValueError):
    """A schedule that cannot be built, with the reason."""


@dataclass(frozen=True)
class Instalment:
    """One thing the client owes on one date."""

    label: str
    due_on: date
    amount: Decimal
    currency: str
    sort_order: int = 0


@dataclass
class Owed:
    """Where a booking stands, in money.

    ``balance`` is what to put on a statement; ``overpaid`` is the credit that
    is not on it. Keeping them apart is the difference between "you owe nothing"
    and "you owe minus four thousand shillings", and only one of those is a
    sentence a client should ever read.
    """

    total: Decimal = Decimal(0)
    paid: Decimal = Decimal(0)
    balance: Decimal = Decimal(0)
    overpaid: Decimal = Decimal(0)
    currency: str = ""
    #: Instalments whose due date has passed and which the payments do not
    #: cover. Ordered oldest first — the one to ring about is the first.
    overdue: list[Instalment] = field(default_factory=list)
    #: The next thing due, past or future, or ``None`` when it is all paid.
    next_due: Instalment | None = None

    @property
    def is_settled(self) -> bool:
        return self.balance <= 0


def build(
    total: Decimal,
    currency: str,
    *,
    deposit_pct: Decimal,
    travel_from: date,
    balance_due_days_before: int,
    today: date,
) -> list[Instalment]:
    """The instalments a booking of ``total`` implies.

    Two instalments in the ordinary case: a deposit due now and the balance due
    a set number of days before travel. The percentages are policy and live in
    config; this resolves them to dated rows, which is what freezes them — a
    deposit rule changed next month must not restate an invoice already sent.

    **A late booking is one payment, not two.** Where the balance date has
    already passed — or falls before the deposit is even due — the schedule
    collapses to a single payment due today. The alternative is an invoice
    asking for a balance before the deposit, which is the kind of thing that
    gets a booking form ignored rather than corrected.

    **A full-deposit booking is one payment too.** At 100% there is no balance,
    and a second instalment of zero is a line on a statement that means
    nothing.
    """
    if total <= 0:
        raise ScheduleRefused(
            "A booking with nothing to pay has no schedule. Check the version's "
            "selling price before booking it."
        )
    if not 0 < deposit_pct <= 100:
        raise ScheduleRefused(
            f"A deposit of {deposit_pct}% is not a deposit. Set a percentage "
            f"above zero and no more than a hundred."
        )
    amount = total.quantize(CENTS, rounding=ROUND_HALF_UP)
    code = currency.upper()
    balance_on = travel_from - timedelta(days=max(0, balance_due_days_before))

    deposit = (amount * deposit_pct / Decimal(100)).quantize(
        CENTS, rounding=ROUND_HALF_UP
    )
    # The remainder, not a second percentage: two rounded halves of a rounded
    # whole do not add up to it, and an invoice that is a cent out is an
    # invoice somebody has to write off by hand.
    balance = amount - deposit

    if balance <= 0 or balance_on <= today:
        return [
            Instalment(
                label=FULL, due_on=today, amount=amount, currency=code, sort_order=1
            )
        ]
    return [
        Instalment(
            label=DEPOSIT, due_on=today, amount=deposit, currency=code, sort_order=1
        ),
        Instalment(
            label=BALANCE,
            due_on=balance_on,
            amount=balance,
            currency=code,
            sort_order=2,
        ),
    ]


def owed(
    instalments: Sequence[Instalment],
    payments: Iterable[Decimal],
    *,
    total: Decimal | None = None,
    currency: str = "",
    today: date,
) -> Owed:
    """Where a booking stands: paid, owed, overpaid, and what is overdue.

    Payments are applied against the instalments **oldest first** rather than
    matched to them. Real payments do not line up — clients pay round numbers,
    pay late, and pay two instalments at once — and a system that insisted on
    matching would leave an operator unable to record what the bank plainly
    shows.
    """
    schedule = sorted(instalments, key=lambda one: (one.due_on, one.sort_order))
    billed = sum((one.amount for one in schedule), Decimal(0))
    expected = (total if total is not None else billed).quantize(CENTS)
    received = sum(
        (amount for amount in payments), Decimal(0)
    ).quantize(CENTS)

    out = Owed(
        total=expected,
        paid=received,
        # Floored at zero: an overpayment is a credit, not a negative bill.
        balance=max(Decimal(0), expected - received),
        overpaid=max(Decimal(0), received - expected),
        currency=(currency or (schedule[0].currency if schedule else "")).upper(),
    )

    running = received
    for instalment in schedule:
        if running >= instalment.amount:
            running -= instalment.amount
            continue
        # The first instalment the payments do not cover.
        if out.next_due is None:
            out.next_due = instalment
        if instalment.due_on < today:
            out.overdue.append(instalment)
        running = Decimal(0)
    return out


def is_confirmable(
    schedule: Sequence[Instalment], payments: Iterable[Decimal]
) -> bool:
    """Whether enough has arrived to confirm the trip.

    The **deposit**, not the whole balance: confirming is the act of telling
    the suppliers it is happening, and that is what a deposit buys. Waiting for
    the balance would mean nothing is ever confirmed until a fortnight before
    travel, which is not how any of this works.
    """
    if not schedule:
        return False
    first = min(schedule, key=lambda one: (one.due_on, one.sort_order))
    received = sum((amount for amount in payments), Decimal(0))
    return received >= first.amount
