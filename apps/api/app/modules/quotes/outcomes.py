"""What happened to a quote, and what that adds up to (§5.1). Pure functions.

``QUOTE_STATUSES`` has listed ``accepted``, ``declined`` and ``expired`` since
Stage 2 and **nothing has ever been able to set them**. Every quote in the
system is a draft or was sent; no quote can be won or lost. So the CRM's first
question — how many of the proposals we send turn into bookings — has had no
data at all, and would have reported nothing forever.

This module is the rules half of closing that. Three ideas.

**Expiry is derived, not stored.** A quote is expired the moment somebody looks
at it past its validity date, not when a nightly job last ran. Storing it needs
a clock and a scheduler and then has two answers whenever the job is late,
which on the one report the business actually reads is the wrong trade. So the
column keeps what a person decided and the expiry is computed against a date
the caller passes in.

**Accepting is accepting an option.** A quote offers between three and nine of
them (§3.7). "The client said yes" without saying yes to *what* leaves the
revenue figure ambiguous and the operations team with nothing to book, so the
option is required at the moment of acceptance.

**An expired quote cannot be accepted.** It is the case where a client comes
back to a six-week-old proposal at rates that have since moved, and the honest
answer is a re-issue rather than a booking at last season's prices. That is a
commercial rule with money attached, which is why it lives here beside the
arithmetic rather than in a router.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

DRAFT = "draft"
SENT = "sent"
ACCEPTED = "accepted"
DECLINED = "declined"
EXPIRED = "expired"

#: What a person can record. ``expired`` is absent on purpose: nobody decides
#: an expiry, a calendar does.
OUTCOMES = (ACCEPTED, DECLINED)

#: A quote in one of these has been decided and does not move again. Re-quoting
#: is a new quote, which keeps the record of what was decided intact.
TERMINAL = (ACCEPTED, DECLINED)


class OutcomeRefused(ValueError):
    """A transition the sales process does not allow, with the reason."""


def effective_status(
    status: str, valid_until: date | None, *, today: date
) -> str:
    """The status as it reads today, expiry included.

    Only a **sent** quote expires. A draft that is past a validity date it was
    given by an earlier version has not expired: it was never in front of
    anybody, and calling it expired would make the funnel count proposals that
    were never sent. A decided quote does not expire either — the client
    answered, and the answer is not undone by a date.
    """
    if status != SENT or valid_until is None:
        return status
    return EXPIRED if valid_until < today else SENT


def check_outcome(
    status: str, valid_until: date | None, *, outcome: str, today: date
) -> None:
    """Whether ``outcome`` may be recorded, raising with the reason if not."""
    if outcome not in OUTCOMES:
        raise OutcomeRefused(
            f"'{outcome}' is not an outcome anybody records. A quote is "
            f"accepted or declined; expiry is a date, not a decision."
        )
    if status == DRAFT:
        raise OutcomeRefused(
            "This quote is still a draft, so nothing has been in front of the "
            "client to accept or decline. Issue it first."
        )
    if status in TERMINAL:
        raise OutcomeRefused(
            f"This quote was already {status}. Recording a second outcome "
            f"would overwrite what the client actually decided — raise a new "
            f"quote instead, which keeps both."
        )
    if effective_status(status, valid_until, today=today) == EXPIRED:
        if outcome == ACCEPTED:
            raise OutcomeRefused(
                f"This quote expired on {valid_until}. Accepting it would book "
                f"the trip at rates that have since moved — re-issue it, which "
                f"appends a version at today's prices, and accept that."
            )
        # Declining an expired quote is allowed and worth recording: "they went
        # elsewhere" and "we let it lapse" are different losses, and only the
        # first one has a reason attached.


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Decided:
    """One quote as the funnel sees it. Deliberately not the ORM row.

    Everything the arithmetic below needs and nothing else, so the aggregation
    is testable without a database — and so that adding a column to ``quotes``
    cannot silently change what the business's headline number means.
    """

    status: str
    currency: str
    #: The version's selling price. ``None`` for a draft, which has none.
    value: Decimal | None = None
    valid_until: date | None = None
    issued_on: date | None = None
    decided_on: date | None = None
    #: Whether the option the client chose was the one we recommended. ``None``
    #: where nothing was chosen or nothing was recommended.
    took_recommendation: bool | None = None


@dataclass
class Conversion:
    """The funnel, and the money in each part of it.

    **Value is never summed across currencies.** A quote is presented in one
    currency (§3.8) and a single "total won" spanning shillings and dollars is
    a number with no meaning; converting them here would bake today's rate into
    a historical report. So the money is per currency and the counts are not.
    """

    counts: dict[str, int] = field(default_factory=dict)
    #: ``{currency: amount}`` for each of won, lost and still outstanding.
    won: dict[str, Decimal] = field(default_factory=dict)
    lost: dict[str, Decimal] = field(default_factory=dict)
    outstanding: dict[str, Decimal] = field(default_factory=dict)
    #: Accepted as a share of everything decided — won over won-plus-lost.
    #: Outstanding and expired quotes are excluded: a quote nobody has
    #: answered is not a loss, and counting it as one makes every rate look
    #: like a crisis in a busy month.
    win_rate: Decimal | None = None
    #: Median days from issue to decision. Median rather than mean because one
    #: quote accepted after eight months would move a mean into fiction.
    median_days_to_decide: int | None = None
    #: How often the client took the option we recommended — the single most
    #: valuable thing this table can say about how we sell (§3.7).
    recommendation_taken: int = 0
    recommendation_declined: int = 0

    @property
    def recommendation_rate(self) -> Decimal | None:
        total = self.recommendation_taken + self.recommendation_declined
        if not total:
            return None
        return (
            Decimal(self.recommendation_taken) / Decimal(total)
        ).quantize(Decimal("0.0001"))


def _add(bucket: dict[str, Decimal], currency: str, amount: Decimal | None) -> None:
    if amount is None:
        return
    bucket[currency] = bucket.get(currency, Decimal(0)) + amount


def convert(quotes: Iterable[Decided], *, today: date) -> Conversion:
    """Aggregate decided and outstanding quotes into one report.

    Statuses are read through :func:`effective_status`, so a sent quote past
    its validity counts as expired here without anything having written that
    to a row.
    """
    out = Conversion()
    gaps: list[int] = []
    for quote in quotes:
        status = effective_status(quote.status, quote.valid_until, today=today)
        out.counts[status] = out.counts.get(status, 0) + 1
        currency = quote.currency.upper()
        if status == ACCEPTED:
            _add(out.won, currency, quote.value)
        elif status == DECLINED:
            _add(out.lost, currency, quote.value)
        elif status in (SENT, EXPIRED):
            # Expired sits in outstanding rather than lost: nobody said no, and
            # the pipeline value of a quote that lapsed is a follow-up call
            # rather than a write-off.
            _add(out.outstanding, currency, quote.value)

        if (
            status in TERMINAL
            and quote.issued_on is not None
            and quote.decided_on is not None
        ):
            gaps.append((quote.decided_on - quote.issued_on).days)

        if status == ACCEPTED and quote.took_recommendation is not None:
            if quote.took_recommendation:
                out.recommendation_taken += 1
            else:
                out.recommendation_declined += 1

    decided = out.counts.get(ACCEPTED, 0) + out.counts.get(DECLINED, 0)
    if decided:
        out.win_rate = (
            Decimal(out.counts.get(ACCEPTED, 0)) / Decimal(decided)
        ).quantize(Decimal("0.0001"))
    if gaps:
        out.median_days_to_decide = median(gaps)
    return out


def median(values: Sequence[int]) -> int:
    """The middle value, rounding a two-sided middle down.

    Its own function because "average time to decide" is the figure most often
    quoted from a report like this, and a mean is the wrong one: a single quote
    accepted eight months later drags a mean somewhere no quote has ever been.
    """
    if not values:
        raise ValueError("no values to take a median of")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2
