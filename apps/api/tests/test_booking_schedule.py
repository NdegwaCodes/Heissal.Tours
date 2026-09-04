"""What is owed, when, and what is left (§7.1). No database.

These are the figures on an invoice, and the two ways they go wrong are both
silent — so they are tested away from the database, exhaustively.

**Instalments must sum to the total, exactly.** A schedule whose parts add up
to a cent more than the whole leaves a client owing a cent forever, or being
refunded one.

**A balance is never negative to a client.** Overpayments are real; "you owe
minus four thousand shillings" is not a sentence anybody should read.

**A late booking is one payment, not two.** An invoice asking for a balance
before the deposit gets a booking form ignored rather than corrected.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.modules.bookings.schedule import (
    BALANCE,
    DEPOSIT,
    FULL,
    Instalment,
    ScheduleRefused,
    build,
    is_confirmable,
    owed,
)

D = Decimal
TODAY = date(2026, 9, 4)
NEXT_YEAR = date(2027, 7, 1)


def _build(total, *, deposit=D("30"), travel=NEXT_YEAR, days_before=30, today=TODAY):
    return build(
        D(total),
        "KES",
        deposit_pct=deposit,
        travel_from=travel,
        balance_due_days_before=days_before,
        today=today,
    )


# --------------------------------------------------------------------------- #
# Building the schedule
# --------------------------------------------------------------------------- #


def test_a_deposit_and_a_balance_on_their_own_dates():
    """30% now, the rest thirty days before travel."""
    schedule = _build("400000")
    assert [one.label for one in schedule] == [DEPOSIT, BALANCE]
    assert schedule[0].amount == D("120000.00")
    assert schedule[0].due_on == TODAY
    assert schedule[1].amount == D("280000.00")
    assert schedule[1].due_on == date(2027, 6, 1)
    assert all(one.currency == "KES" for one in schedule)


def test_the_instalments_add_up_to_the_total_exactly():
    """The case that makes this worth a pure test.

    30% of 223,749 is 67,124.70 and the balance is the **remainder**, not a
    second percentage: two rounded parts of a rounded whole do not add up to
    it, and an invoice a cent out is one somebody writes off by hand.
    """
    for total in ("223749", "223749.01", "0.03", "999999.99", "7", "1234.56"):
        schedule = _build(total)
        assert sum(one.amount for one in schedule) == D(total).quantize(D("0.01")), total


def test_odd_percentages_still_add_up():
    for pct in ("1", "7.5", "33.333", "49.99", "99"):
        schedule = _build("223749", deposit=D(pct))
        assert sum(one.amount for one in schedule) == D("223749.00"), pct


def test_a_full_deposit_is_one_payment():
    """At 100% there is no balance, and an instalment of zero means nothing."""
    schedule = _build("400000", deposit=D("100"))
    assert [one.label for one in schedule] == [FULL]
    assert schedule[0].amount == D("400000.00")


def test_a_late_booking_is_one_payment_due_now():
    """The balance date has passed, so asking for a deposit first is nonsense.

    An invoice asking for a balance before the deposit is the kind of thing
    that gets a booking form ignored rather than corrected.
    """
    schedule = _build("400000", travel=TODAY + timedelta(days=10))
    assert [one.label for one in schedule] == [FULL]
    assert schedule[0].due_on == TODAY
    assert schedule[0].amount == D("400000.00")


def test_a_booking_exactly_on_the_balance_date_is_one_payment():
    """The boundary: due today and due now are the same thing."""
    schedule = _build("400000", travel=TODAY + timedelta(days=30))
    assert [one.label for one in schedule] == [FULL]


def test_a_booking_one_day_clear_of_it_is_two():
    schedule = _build("400000", travel=TODAY + timedelta(days=31))
    assert [one.label for one in schedule] == [DEPOSIT, BALANCE]
    assert schedule[1].due_on == TODAY + timedelta(days=1)


def test_a_booking_with_nothing_to_pay_is_refused():
    """A schedule for zero is not a schedule; it is a version priced wrongly."""
    with pytest.raises(ScheduleRefused, match="nothing to pay"):
        _build("0")
    with pytest.raises(ScheduleRefused):
        _build("-100")


def test_an_impossible_deposit_is_refused():
    with pytest.raises(ScheduleRefused, match="is not a deposit"):
        _build("400000", deposit=D("0"))
    with pytest.raises(ScheduleRefused):
        _build("400000", deposit=D("140"))


def test_the_currency_is_carried_onto_every_line():
    """An invoice line with no currency on it is a number."""
    schedule = build(
        D("3200"),
        "usd",
        deposit_pct=D("50"),
        travel_from=NEXT_YEAR,
        balance_due_days_before=30,
        today=TODAY,
    )
    assert [one.currency for one in schedule] == ["USD", "USD"]


# --------------------------------------------------------------------------- #
# Where a booking stands
# --------------------------------------------------------------------------- #


def _owed(paid, *, schedule=None, total="400000", today=TODAY):
    return owed(
        schedule if schedule is not None else _build(total),
        [D(one) for one in paid],
        total=D(total),
        currency="KES",
        today=today,
    )


def test_nothing_paid_owes_everything():
    position = _owed([])
    assert position.paid == D("0.00")
    assert position.balance == D("400000.00")
    assert not position.is_settled
    assert position.next_due is not None
    assert position.next_due.label == DEPOSIT


def test_the_deposit_paid_leaves_the_balance():
    position = _owed(["120000"])
    assert position.balance == D("280000.00")
    assert position.next_due.label == BALANCE
    assert position.overdue == []


def test_paid_in_full_is_settled():
    position = _owed(["120000", "280000"])
    assert position.balance == D("0.00")
    assert position.is_settled
    assert position.next_due is None


def test_an_overpayment_is_a_credit_and_not_a_negative_bill():
    """Clients round up, pay twice, and pay the wrong figure.

    "You owe minus four thousand shillings" is not a sentence a client should
    read, so the balance floors at zero and the credit is reported where
    somebody can see it.
    """
    position = _owed(["404000"])
    assert position.balance == D("0.00")
    assert position.overpaid == D("4000.00")
    assert position.is_settled


def test_a_part_payment_of_the_deposit_still_owes_the_deposit():
    """Payments are applied oldest first, not matched to instalments.

    Real payments do not line up — a client sends a round 100,000 against a
    120,000 deposit — and a system that insisted on matching would leave an
    operator unable to record what the bank plainly shows.
    """
    position = _owed(["100000"])
    assert position.balance == D("300000.00")
    assert position.next_due.label == DEPOSIT


def test_one_payment_can_settle_two_instalments():
    position = _owed(["400000"])
    assert position.is_settled
    assert position.next_due is None


def test_an_overdue_instalment_is_reported_oldest_first():
    """The one to ring about is the first."""
    schedule = [
        Instalment(DEPOSIT, date(2026, 6, 1), D("120000"), "KES", 1),
        Instalment(BALANCE, date(2026, 8, 1), D("280000"), "KES", 2),
    ]
    position = _owed([], schedule=schedule)
    assert [one.label for one in position.overdue] == [DEPOSIT, BALANCE]
    assert position.next_due.label == DEPOSIT


def test_a_future_instalment_is_not_overdue():
    position = _owed(["120000"])
    assert position.overdue == []
    assert position.next_due.due_on > TODAY


def test_the_total_wins_over_the_schedule_when_they_disagree():
    """The booking's own figure is the invoice; the schedule is how it is split.

    They should never differ — the schedule is built from the total — but if a
    row is edited by hand, what the client owes is the total, and reporting the
    sum of the lines instead would quietly change the bill.
    """
    schedule = [Instalment(DEPOSIT, TODAY, D("100000"), "KES", 1)]
    position = _owed([], schedule=schedule, total="400000")
    assert position.total == D("400000.00")
    assert position.balance == D("400000.00")


def test_a_booking_confirms_on_the_deposit_not_the_balance():
    """Confirming is telling the suppliers it is happening.

    Waiting for the balance would mean nothing is ever confirmed until a
    fortnight before travel, which is not how any of this works.
    """
    schedule = _build("400000")
    assert not is_confirmable(schedule, [D("119999.99")])
    assert is_confirmable(schedule, [D("120000")])
    assert is_confirmable(schedule, [D("60000"), D("60000")])
    assert not is_confirmable([], [D("400000")])


def test_the_position_is_the_same_twice():
    schedule = _build("223749")
    first = owed(schedule, [D("60000")], total=D("223749"), currency="KES", today=TODAY)
    second = owed(schedule, [D("60000")], total=D("223749"), currency="KES", today=TODAY)
    assert first == second
