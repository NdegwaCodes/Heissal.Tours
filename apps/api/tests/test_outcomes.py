"""What happened to a quote, and what it adds up to (§5.1). No database.

``QUOTE_STATUSES`` has listed ``accepted``, ``declined`` and ``expired`` since
Stage 2 and **nothing could ever set them**. Every quote in the system was a
draft or was sent, so the CRM's first question — how many of the proposals we
send become bookings — had no data and would have reported nothing forever.

Three rules are defended here, and each one has money attached.

**Expiry is derived.** A quote is expired when somebody looks at it past its
validity, not when a nightly job last ran.

**An expired quote cannot be accepted.** That is the case where a client comes
back to a six-week-old proposal at rates that have since moved.

**Value is never summed across currencies.** A single "total won" spanning
shillings and dollars is a figure with no meaning.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.quotes.outcomes import (
    ACCEPTED,
    DECLINED,
    DRAFT,
    EXPIRED,
    SENT,
    Decided,
    OutcomeRefused,
    check_outcome,
    convert,
    effective_status,
    median,
)

D = Decimal

TODAY = date(2026, 9, 4)
YESTERDAY = date(2026, 9, 3)
NEXT_MONTH = date(2026, 10, 4)


# --------------------------------------------------------------------------- #
# Derived expiry
# --------------------------------------------------------------------------- #


def test_a_sent_quote_past_its_validity_reads_as_expired():
    assert effective_status(SENT, YESTERDAY, today=TODAY) == EXPIRED
    assert effective_status(SENT, NEXT_MONTH, today=TODAY) == SENT
    # The day itself is still good: a quote valid until the 4th is valid on it.
    assert effective_status(SENT, TODAY, today=TODAY) == SENT


def test_a_sent_quote_with_no_validity_date_never_expires():
    """Nothing to expire against, and inventing a default would expire quotes
    on a rule nobody set."""
    assert effective_status(SENT, None, today=TODAY) == SENT


def test_a_draft_does_not_expire():
    """It was never in front of anybody.

    Calling it expired would put proposals in the funnel that were never sent,
    which is the fastest way to make a win rate meaningless.
    """
    assert effective_status(DRAFT, YESTERDAY, today=TODAY) == DRAFT


def test_a_decided_quote_does_not_expire():
    """The client answered, and a date does not undo the answer."""
    assert effective_status(ACCEPTED, YESTERDAY, today=TODAY) == ACCEPTED
    assert effective_status(DECLINED, YESTERDAY, today=TODAY) == DECLINED


# --------------------------------------------------------------------------- #
# What may be recorded
# --------------------------------------------------------------------------- #


def test_a_sent_quote_can_be_accepted_or_declined():
    check_outcome(SENT, NEXT_MONTH, outcome=ACCEPTED, today=TODAY)
    check_outcome(SENT, NEXT_MONTH, outcome=DECLINED, today=TODAY)


def test_a_draft_cannot_be_decided():
    """Nothing has been in front of the client to say yes or no to."""
    with pytest.raises(OutcomeRefused, match="still a draft"):
        check_outcome(DRAFT, None, outcome=ACCEPTED, today=TODAY)


def test_an_expired_quote_cannot_be_accepted_and_the_message_says_why():
    """Rates have moved, so the honest answer is a re-issue.

    The refusal names the date and the fix. A message that only says "no"
    produces a workaround; one that says "re-issue it, which appends a version
    at today's prices" produces the right booking.
    """
    with pytest.raises(OutcomeRefused, match="expired on 2026-09-03") as raised:
        check_outcome(SENT, YESTERDAY, outcome=ACCEPTED, today=TODAY)
    assert "re-issue" in str(raised.value)
    assert "rates that have since moved" in str(raised.value)


def test_an_expired_quote_can_still_be_declined():
    """"They went elsewhere" and "we let it lapse" are different losses.

    Only the first has a reason attached, and a pipeline that cannot record it
    loses the difference.
    """
    check_outcome(SENT, YESTERDAY, outcome=DECLINED, today=TODAY)


def test_a_decided_quote_cannot_be_decided_again():
    for status in (ACCEPTED, DECLINED):
        with pytest.raises(OutcomeRefused, match="already"):
            check_outcome(status, NEXT_MONTH, outcome=ACCEPTED, today=TODAY)


def test_expiry_is_not_something_anybody_records():
    """A calendar decides it, so it is not on the list of outcomes."""
    with pytest.raises(OutcomeRefused, match="expiry is a date, not a decision"):
        check_outcome(SENT, NEXT_MONTH, outcome=EXPIRED, today=TODAY)
    with pytest.raises(OutcomeRefused, match="not an outcome"):
        check_outcome(SENT, NEXT_MONTH, outcome="maybe", today=TODAY)


# --------------------------------------------------------------------------- #
# The funnel
# --------------------------------------------------------------------------- #


def _quote(status, value, **over):
    fields = {
        "status": status,
        "currency": "KES",
        "value": D(value) if value is not None else None,
        "valid_until": NEXT_MONTH,
    }
    fields.update(over)
    return Decided(**fields)


def test_the_funnel_counts_and_totals_each_part():
    report = convert(
        [
            _quote(ACCEPTED, "400000"),
            _quote(ACCEPTED, "250000"),
            _quote(DECLINED, "300000"),
            _quote(SENT, "180000"),
        ],
        today=TODAY,
    )
    assert report.counts == {ACCEPTED: 2, DECLINED: 1, SENT: 1}
    assert report.won == {"KES": D("650000")}
    assert report.lost == {"KES": D("300000")}
    assert report.outstanding == {"KES": D("180000")}


def test_the_win_rate_ignores_quotes_nobody_has_answered():
    """Two of three decided, with two more still out: 66.67%, not 40%.

    A quote nobody has answered is not a loss, and counting it as one makes
    every win rate look like a crisis in a busy month.
    """
    report = convert(
        [
            _quote(ACCEPTED, "100000"),
            _quote(ACCEPTED, "100000"),
            _quote(DECLINED, "100000"),
            _quote(SENT, "100000"),
            _quote(SENT, "100000"),
        ],
        today=TODAY,
    )
    assert report.win_rate == D("0.6667")


def test_a_lapsed_quote_is_outstanding_rather_than_lost():
    """Nobody said no. The pipeline value of a lapsed quote is a phone call.

    Writing it off would flatter the win rate — it leaves the denominator —
    and hide the follow-up that is actually owed.
    """
    report = convert([_quote(SENT, "220000", valid_until=YESTERDAY)], today=TODAY)
    assert report.counts == {EXPIRED: 1}
    assert report.outstanding == {"KES": D("220000")}
    assert report.lost == {}
    assert report.win_rate is None


def test_money_is_kept_per_currency():
    """A total spanning shillings and dollars is a figure with no meaning.

    And converting them here would bake today's exchange rate into a report
    about last quarter (§3.8).
    """
    report = convert(
        [
            _quote(ACCEPTED, "400000"),
            _quote(ACCEPTED, "3200", currency="USD"),
        ],
        today=TODAY,
    )
    assert report.won == {"KES": D("400000"), "USD": D("3200")}
    assert report.counts == {ACCEPTED: 2}


def test_there_is_no_win_rate_before_anything_is_decided():
    """``None``, not zero. "No data" and "we lose everything" are different."""
    report = convert([_quote(SENT, "100000")], today=TODAY)
    assert report.win_rate is None
    assert report.median_days_to_decide is None
    assert report.recommendation_rate is None


def test_the_time_to_decide_is_a_median():
    """Days 1, 2, 3 and 240: the median is 2, the mean would be 61.

    One quote accepted after eight months would move a mean somewhere no quote
    has ever been, and this is the figure most often quoted from a report like
    this.
    """
    issued = date(2026, 1, 1)
    report = convert(
        [
            _quote(ACCEPTED, "1", issued_on=issued, decided_on=date(2026, 1, 2)),
            _quote(DECLINED, "1", issued_on=issued, decided_on=date(2026, 1, 3)),
            _quote(ACCEPTED, "1", issued_on=issued, decided_on=date(2026, 1, 4)),
            _quote(ACCEPTED, "1", issued_on=issued, decided_on=date(2026, 8, 29)),
        ],
        today=TODAY,
    )
    assert report.median_days_to_decide == 2


def test_the_median_of_an_even_count_rounds_down():
    assert median([1, 2, 3, 4]) == 2
    assert median([5]) == 5
    with pytest.raises(ValueError, match="no values"):
        median([])


def test_whether_clients_take_the_recommendation_is_counted():
    """The most valuable thing this report says about how we sell (§3.7).

    Two of three accepted quotes took the recommended option. If that number
    is low, the flag is not describing what clients want — and nothing else in
    the system would ever have said so.
    """
    report = convert(
        [
            _quote(ACCEPTED, "1", took_recommendation=True),
            _quote(ACCEPTED, "1", took_recommendation=True),
            _quote(ACCEPTED, "1", took_recommendation=False),
            # A declined quote says nothing about which option was preferred.
            _quote(DECLINED, "1", took_recommendation=False),
            # Nor does an accepted one with nothing recorded.
            _quote(ACCEPTED, "1", took_recommendation=None),
        ],
        today=TODAY,
    )
    assert report.recommendation_taken == 2
    assert report.recommendation_declined == 1
    assert report.recommendation_rate == D("0.6667")


def test_a_quote_with_no_version_has_no_value_and_still_counts():
    """A quote can be decided without a priced version behind it.

    The count is the honest part and the money is absent rather than zero: a
    zero in the won column would report a booking worth nothing.
    """
    report = convert([_quote(ACCEPTED, None)], today=TODAY)
    assert report.counts == {ACCEPTED: 1}
    assert report.won == {}


def test_the_same_quotes_report_the_same_twice():
    quotes = [_quote(ACCEPTED, "100"), _quote(DECLINED, "200")]
    assert convert(quotes, today=TODAY) == convert(quotes, today=TODAY)
