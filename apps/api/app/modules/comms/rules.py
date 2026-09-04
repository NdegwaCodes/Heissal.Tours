"""What was said, and what it means. Pure functions (§5.3).

§5.2 built a pipeline whose ``Watched.stage_since`` docstring admitted what it
was standing in for: *"the closest thing to when somebody last did something
without a full activity log."* This is that log, and the point of it is not
tidiness. Three things become answerable that a stage column cannot answer.

**When somebody last actually spoke to them.** An agent can call a client every
week without moving a stage, and a lead can be dragged across three stages
while nobody has picked up the phone. Staleness measured by stage movement is
therefore measuring the wrong thing, and it was the only thing available.

**Whether they are replying.** "Chased four times since 12 August, no reply" is
the temperature of a deal. A stage of *Negotiating* says the opposite of that
while being technically true, which is worse than saying nothing.

**How fast the first reply went out.** In travel sales this is close to the
whole game: an enquiry answered within the hour and one answered on Thursday
are not the same business. It needs the arrival time and the first outbound
contact, and until there was a log there was no second half of that pair.

And what this module refuses, as §5.2 did: it does not decide that a lead is
dead, or that silence means no. It reports attempts, dates and gaps against
thresholds the business sets, and leaves the conclusion to somebody who knows
the market.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

#: Which way the conversation went. Unlike the channel below this **is** a
#: closed set — there is no fourth direction a conversation can go — so it is a
#: CHECK constraint in the database.
#:
#: ``internal`` is the third because half of what gets logged is not a
#: conversation at all: "client's sister is the decision maker", "supplier says
#: the villa is held until Friday". Recording those as outbound would tell the
#: attention rules the client had been contacted when nobody had, which is
#: precisely the lie this module exists to stop.
INBOUND = "inbound"
OUTBOUND = "outbound"
INTERNAL = "internal"
DIRECTIONS = (INBOUND, OUTBOUND, INTERNAL)

#: How it happened. A conventional set rather than a constraint, for the reason
#: §5.2 gives about lead sources: channels multiply (a client moves to WhatsApp
#: mid-enquiry, an agent uses Instagram DMs), and a log entry refused because
#: "instagram" is not in an enum is a call nobody records.
CALL = "call"
EMAIL = "email"
WHATSAPP = "whatsapp"
SMS = "sms"
MEETING = "meeting"
NOTE = "note"
COMMON_CHANNELS = (CALL, EMAIL, WHATSAPP, SMS, MEETING, NOTE, "other")

#: The channels a length makes sense for. An email does not last nineteen
#: minutes.
TIMED_CHANNELS = (CALL, MEETING)


class LogRefused(ValueError):
    """A log entry the rules will not accept, with the reason."""


def normalise_channel(value: str | None) -> str:
    """A channel as a report should group it.

    Trimmed, lower-cased, spaces and hyphens folded to underscores, so
    "WhatsApp", "whatsapp " and "WHATSAPP" are one line in a table rather than
    three, and "Video call" and "video-call" are one rather than two.

    The same fold as ``normalise_source`` (§5.2), deliberately: one convention
    across the two CRM modules, so nobody has to remember which of them keeps
    the spaces. It does not merge words — "whats app" stays apart from
    "whatsapp" — because a normaliser that deleted separators would turn
    "phone call" into "phonecall" and start guessing.
    """
    cleaned = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return cleaned or NOTE


def normalise_direction(value: str | None) -> str:
    """One of the three directions, refusing anything else.

    Refused rather than defaulted, because a wrong direction is not a cosmetic
    error: it decides whether the lead counts as contacted, whether the client
    counts as having replied, and therefore what every figure below says.
    """
    cleaned = (value or "").strip().lower()
    if cleaned not in DIRECTIONS:
        raise LogRefused(
            f"'{value}' is not a direction. Say {', '.join(DIRECTIONS)} — "
            f"'internal' for a note to ourselves, which is not contact with "
            f"the client."
        )
    return cleaned


@dataclass(frozen=True)
class Logged:
    """One entry as the rules see it, before it is a row."""

    channel: str
    direction: str
    occurred_at: datetime
    body: str = ""
    #: Did it connect? ``None`` where the question does not apply — an email is
    #: sent, not answered — and ``False`` for the call nobody picked up, which
    #: is a fact worth keeping: three unanswered attempts is a different lead
    #: from one conversation.
    reached: bool | None = None
    duration_minutes: int | None = None


def check_logged(entry: Logged, *, now: datetime) -> None:
    """Whether an entry may be recorded. Refusals only, all of them cheap.

    Every one of these is a thing that would make the log lie, and a log that
    lies is worse than no log: the next person reads "called Tuesday" and does
    not call.
    """
    if entry.occurred_at > now:
        raise LogRefused(
            "This is dated in the future, so it has not happened yet. Log the "
            "call after making it; the next step goes on the lead as its next "
            "action."
        )
    if not (entry.body or "").strip():
        raise LogRefused(
            "Say what was said. An entry with no words records that contact "
            "happened and nothing about it, so the next person reads 'called "
            "Tuesday' and has to call again anyway."
        )
    if entry.direction == INTERNAL and entry.reached is not None:
        raise LogRefused(
            "An internal note is not a conversation with the client, so there "
            "is nobody it could have reached."
        )
    if entry.duration_minutes is not None:
        if entry.duration_minutes <= 0:
            raise LogRefused(
                "A call has to have lasted some time. Leave the length off if "
                "it is not known, and record an unanswered one as not reached."
            )
        if entry.channel not in TIMED_CHANNELS:
            raise LogRefused(
                "A length only makes sense on a call or a meeting; an email "
                "does not last nineteen minutes."
            )
        if entry.reached is False:
            raise LogRefused(
                "A call that was not answered has no length. Record the "
                "attempt with no duration — that it was attempted is the fact "
                "worth keeping."
            )


# --------------------------------------------------------------------------- #
# The history, read
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Contact:
    """One recorded entry, as the history rules see it."""

    channel: str
    direction: str
    occurred_at: datetime
    reached: bool | None = None
    #: A voided entry stays in the timeline — it is the record of what somebody
    #: thought had happened — but counts towards nothing.
    voided: bool = False

    @property
    def counts(self) -> bool:
        """Whether this is contact with the client.

        Internal notes and voided entries are not. A lead whose only activity
        is three internal notes has not been contacted, and saying otherwise is
        how an unanswered enquiry hides on a dashboard.
        """
        return not self.voided and self.direction in (INBOUND, OUTBOUND)


@dataclass
class Contacted:
    """What the log says about one lead, client, quote or booking."""

    entries: int = 0
    #: Entries that are contact with the client — internal notes excluded.
    contacts: int = 0
    last_contact_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    #: Outbound attempts since the client last said anything. The figure that
    #: says a deal has gone quiet while its stage still says Negotiating.
    chases: int = 0
    by_channel: dict[str, int] = field(default_factory=dict)
    by_direction: dict[str, int] = field(default_factory=dict)
    #: Calls placed that nobody answered. Kept separately because "we have
    #: tried four times and never got them" is a different problem from "we
    #: spoke and they went quiet", and it needs a different next step.
    unreached_calls: int = 0

    @property
    def ever_contacted(self) -> bool:
        return self.last_contact_at is not None

    @property
    def has_replied(self) -> bool:
        return self.last_inbound_at is not None


def history(entries: Iterable[Contact]) -> Contacted:
    """Fold a log into the handful of facts anything downstream wants.

    Sorted here rather than trusted, because the callers are a database query,
    a test and a timeline view, and only one of them promises an order.
    """
    rows = sorted(entries, key=lambda one: one.occurred_at)
    out = Contacted(entries=len(rows))
    for row in rows:
        out.by_channel[row.channel] = out.by_channel.get(row.channel, 0) + 1
        out.by_direction[row.direction] = out.by_direction.get(row.direction, 0) + 1
        if not row.counts:
            continue
        out.contacts += 1
        out.last_contact_at = row.occurred_at
        if row.direction == INBOUND:
            out.last_inbound_at = row.occurred_at
            # A reply resets the chase count: what matters is attempts since
            # they last said something, not attempts ever.
            out.chases = 0
        else:
            out.last_outbound_at = row.occurred_at
            out.chases += 1
            if row.channel == CALL and row.reached is False:
                out.unreached_calls += 1
    return out


def first_response_hours(
    arrived_at: datetime, entries: Iterable[Contact]
) -> Decimal | None:
    """Hours from the enquiry arriving to the first outbound word about it.

    The metric travel sales actually turns on, and the reason this module
    exists rather than a ``notes`` text field. ``None`` where nobody has
    replied yet: that is an open sore, not a zero, and reporting it as a number
    would average it away.

    Measured to the first **outbound** entry, not the first entry of any kind —
    a client's follow-up chasing us for an answer is not us answering — and an
    entry dated before the lead was recorded gives zero rather than a negative,
    because it means somebody answered the phone and typed the lead afterwards.
    """
    outbound = [
        one.occurred_at for one in entries if one.counts and one.direction == OUTBOUND
    ]
    if not outbound:
        return None
    gap = (min(outbound) - arrived_at).total_seconds() / 3600
    return Decimal(max(gap, 0.0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Silence:
    """A conversation that has stopped, described rather than judged."""

    chases: int
    days: int
    since: datetime
    ever_replied: bool

    @property
    def message(self) -> str:
        who = (
            "since they last replied"
            if self.ever_replied
            else "and they have never replied"
        )
        return (
            f"{self.chases} attempt(s) {who}, the last {self.days} day(s) ago. "
            f"Whether that is a no is a judgement about this client, not "
            f"something a report can make."
        )


def silence(
    log: Contacted, *, now: datetime, after_chases: int = 2, after_days: int = 7
) -> Silence | None:
    """Whether a conversation has gone quiet, on the caller's thresholds.

    Both thresholds must be met, and both are the caller's. Two chases in two
    days is a keen agent; two chases over three weeks is a client who has
    booked elsewhere, and no default here can tell them apart.
    """
    if log.chases < after_chases or log.last_outbound_at is None:
        return None
    days = (now - log.last_outbound_at).days
    if days < after_days:
        return None
    return Silence(
        chases=log.chases,
        days=days,
        since=log.last_outbound_at,
        ever_replied=log.has_replied,
    )


def median_hours(values: Sequence[Decimal]) -> Decimal | None:
    """Median of a list of hours, or ``None`` where there is nothing.

    Median for §5.1's reason: one enquiry answered after a fortnight's leave
    would move a mean into fiction, and the figure is supposed to describe a
    normal Tuesday.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    pair = (ordered[middle - 1] + ordered[middle]) / 2
    return pair.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


__all__ = [
    "CALL",
    "COMMON_CHANNELS",
    "DIRECTIONS",
    "EMAIL",
    "INBOUND",
    "INTERNAL",
    "MEETING",
    "NOTE",
    "OUTBOUND",
    "TIMED_CHANNELS",
    "WHATSAPP",
    "Contact",
    "Contacted",
    "LogRefused",
    "Logged",
    "Silence",
    "check_logged",
    "first_response_hours",
    "history",
    "median_hours",
    "normalise_channel",
    "normalise_direction",
    "silence",
]
