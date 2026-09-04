"""The pipeline's rules and its arithmetic (§5.2). Pure functions.

What a pipeline is *for* is deciding what to do this morning, and what to stop
doing. So this module answers three questions and refuses a fourth.

**Which leads need attention?** The ones whose next action is due or overdue,
and the ones with no next action at all. That second set is the one that
matters: a lead nobody has scheduled anything for does not appear on any list,
does not annoy anybody, and dies quietly. It is the commonest way a CRM stops
being used.

**How long do leads sit at each stage?** From the stage history, not the
current column. "Eleven at quoted" is a number; "eleven at quoted, median
nineteen days, four past a month" is a morning's work.

**Which sources actually convert?** Counting enquiries by source flatters
whichever channel is loudest. Counting *won* ones by source is the figure that
decides where the marketing money goes — which is why a lead links to its
quotes and a quote carries its outcome (§5.1).

And the fourth, refused: **this module does not decide that a lead is dead.**
It reports how long one has been untouched against a threshold the business
sets. "Stale" is a judgement about a market — a honeymoon enquiry for next
August is not cold at three weeks — and a system that closed leads on a timer
would be deciding sales policy.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

# Why a lead is on somebody's list this morning. Ordered by how much it should
# worry them.
NO_NEXT_ACTION = "lead_no_next_action"
OVERDUE = "lead_next_action_overdue"
DUE = "lead_next_action_due"
UNOWNED = "lead_unowned"
STALE = "lead_untouched"


class StageRefused(ValueError):
    """A pipeline move the rules do not allow, with the reason."""


@dataclass(frozen=True)
class Stage:
    """One pipeline stage, as the rules see it."""

    key: str
    name: str
    sort_order: int = 0
    is_default: bool = False
    is_won: bool = False
    is_lost: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.is_won or self.is_lost


def check_pipeline(stages: Sequence[Stage]) -> list[str]:
    """What is wrong with the *shape* of a configured pipeline.

    Returned as messages rather than raised, because a client renaming their
    stages will pass through invalid states while they work, and the honest
    thing is to say so rather than to refuse the save that gets them there.
    The service raises on the ones that would break a report.
    """
    problems: list[str] = []
    if not stages:
        return ["The pipeline has no stages, so a lead has nowhere to arrive."]
    defaults = [stage for stage in stages if stage.is_default]
    if not defaults:
        problems.append(
            "No stage is marked as the one new enquiries arrive at, so a lead "
            "would have nowhere to start."
        )
    elif len(defaults) > 1:
        problems.append(
            "More than one stage is marked as where new enquiries arrive: "
            + ", ".join(stage.name for stage in defaults)
            + ". A lead can only start in one."
        )
    if not [stage for stage in stages if stage.is_won]:
        problems.append(
            "No stage means won, so nothing can ever be counted as converted."
        )
    if not [stage for stage in stages if stage.is_lost]:
        problems.append(
            "No stage means lost, so a dead lead would sit in the pipeline "
            "forever and every stage count would be wrong."
        )
    both = [stage for stage in stages if stage.is_won and stage.is_lost]
    if both:
        problems.append(
            "A stage cannot mean both won and lost: "
            + ", ".join(stage.name for stage in both)
        )
    keys = [stage.key for stage in stages]
    if len(keys) != len(set(keys)):
        problems.append("Two stages share a key, so one of them is unreachable.")
    return problems


def check_move(
    current: Stage, target: Stage, *, lost_reason: str | None = None
) -> None:
    """Whether a lead may move from ``current`` to ``target``.

    Deliberately permissive. Backwards moves are allowed — a deal cools, a
    client goes quiet — because a pipeline that only goes forwards is one where
    agents park leads at a stage they have actually left, and then the stage
    counts describe nothing. Reopening a won or lost lead is allowed for the
    same reason: clients come back a year later, and the history records both.

    The two refusals are the ones that would corrupt a report: moving a lead to
    the stage it is already at (which would put a fictitious dwell time in the
    history), and closing one as lost with no reason. The second is the whole
    value of a lost stage — "we lost eleven" is a fact nobody can act on, and
    "we lost eleven, seven on price" is a decision.
    """
    if current.key == target.key:
        raise StageRefused(
            f"This lead is already at {target.name}. Moving it there again "
            f"would record a stage change that did not happen."
        )
    if target.is_lost and not (lost_reason or "").strip():
        raise StageRefused(
            f"Say why the lead was lost before moving it to {target.name}. "
            f"'We lost eleven' is a fact nobody can act on; 'seven of them on "
            f"price' is a decision."
        )


# --------------------------------------------------------------------------- #
# What needs attention
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Attention:
    """One reason a lead is on somebody's list, and how badly."""

    code: str
    message: str
    #: Days overdue, or days untouched. Zero where the code carries no age.
    days: int = 0


@dataclass(frozen=True)
class Watched:
    """A lead as the attention rules see it. Not the ORM row — see §5.1's Decided."""

    name: str
    stage: Stage
    #: When the lead last moved stage, which is the closest thing to "when
    #: somebody last did something" without a full activity log.
    stage_since: date | None = None
    next_action_on: date | None = None
    owner: str | None = None


def attention(
    lead: Watched, *, today: date, stale_after_days: int = 14
) -> list[Attention]:
    """Why this lead needs looking at, worst first.

    A **terminal** lead needs nothing: a won deal has no next action and a lost
    one is not a task. Reporting them would fill the list with work nobody has
    to do, which is how a list stops being read.
    """
    if lead.stage.is_terminal:
        return []

    out: list[Attention] = []
    if lead.next_action_on is None:
        # First, and deliberately: a lead with no next action appears on no
        # other list, annoys nobody, and dies quietly.
        out.append(
            Attention(
                NO_NEXT_ACTION,
                f"{lead.name} has no next action, so nothing will bring it back "
                f"to anybody's attention.",
            )
        )
    elif lead.next_action_on < today:
        late = (today - lead.next_action_on).days
        out.append(
            Attention(
                OVERDUE,
                f"{lead.name}: the next action was due {late} day(s) ago.",
                days=late,
            )
        )
    elif lead.next_action_on == today:
        out.append(Attention(DUE, f"{lead.name}: the next action is due today."))

    if lead.owner is None:
        out.append(
            Attention(
                UNOWNED,
                f"{lead.name} has no owner, so it is nobody's to lose.",
            )
        )

    if lead.stage_since is not None:
        idle = (today - lead.stage_since).days
        if idle >= stale_after_days:
            out.append(
                Attention(
                    STALE,
                    f"{lead.name} has been at {lead.stage.name} for {idle} "
                    f"day(s). Not necessarily cold — a honeymoon enquiry for "
                    f"next August is not stale at three weeks — but worth a "
                    f"decision.",
                    days=idle,
                )
            )
    return out


# --------------------------------------------------------------------------- #
# The pipeline, counted
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Counted:
    """One lead as the funnel sees it."""

    stage: Stage
    source: str = "other"
    stage_since: date | None = None
    #: Their stated budget, in their own currency. Never summed across
    #: currencies, for the reason §5.1 gives.
    budget_amount: Decimal | None = None
    budget_currency: str | None = None
    #: How many quotes the lead produced, and whether any was accepted. This
    #: is what links a source to money rather than to activity.
    quotes: int = 0
    accepted: bool = False


@dataclass
class StageCount:
    stage: str
    name: str
    leads: int = 0
    #: Median days at this stage, over the leads currently in it. Median for
    #: the §5.1 reason: one forgotten lead would move a mean into fiction.
    median_days: int | None = None


@dataclass
class SourceCount:
    source: str
    leads: int = 0
    quoted: int = 0
    won: int = 0

    @property
    def win_rate(self) -> Decimal | None:
        """Won over *all* leads from this source, not over the quoted ones.

        The stricter denominator on purpose: a source that produces twenty
        enquiries and two quotes is not a 100% source because both quotes
        converted. What a marketing decision needs is enquiries in, bookings
        out.
        """
        if not self.leads:
            return None
        return (Decimal(self.won) / Decimal(self.leads)).quantize(Decimal("0.0001"))


@dataclass
class Pipeline:
    stages: list[StageCount] = field(default_factory=list)
    sources: list[SourceCount] = field(default_factory=list)
    #: Open leads' stated budgets, per currency. A soft figure and labelled as
    #: one: it is what clients said, not what anything is worth.
    open_budget: dict[str, Decimal] = field(default_factory=dict)
    open_leads: int = 0
    won_leads: int = 0
    lost_leads: int = 0

    @property
    def win_rate(self) -> Decimal | None:
        """Won over everything decided. Open leads are excluded, as in §5.1."""
        decided = self.won_leads + self.lost_leads
        if not decided:
            return None
        return (Decimal(self.won_leads) / Decimal(decided)).quantize(
            Decimal("0.0001")
        )


def _median(values: Sequence[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def summarise(
    leads: Iterable[Counted], stages: Sequence[Stage], *, today: date
) -> Pipeline:
    """The pipeline by stage and by source.

    Every configured stage appears, including the empty ones: a stage with no
    leads in it is the most interesting cell in the table — either nobody uses
    it or everything is skipping it — and dropping it hides that.
    """
    out = Pipeline()
    dwell: dict[str, list[int]] = {}
    by_source: dict[str, SourceCount] = {}
    counts: dict[str, int] = {stage.key: 0 for stage in stages}
    # Materialised once: the caller may hand us a generator, and a function
    # that silently reports zeros for a second pass over an exhausted one is a
    # function that lies quietly.
    watched = list(leads)

    for lead in watched:
        counts[lead.stage.key] = counts.get(lead.stage.key, 0) + 1
        key = lead.stage.key
        if lead.stage_since is not None:
            dwell.setdefault(key, []).append((today - lead.stage_since).days)

        if lead.stage.is_won:
            out.won_leads += 1
        elif lead.stage.is_lost:
            out.lost_leads += 1
        else:
            out.open_leads += 1
            if lead.budget_amount is not None and lead.budget_currency:
                currency = lead.budget_currency.upper()
                out.open_budget[currency] = (
                    out.open_budget.get(currency, Decimal(0)) + lead.budget_amount
                )

        source = by_source.setdefault(
            lead.source, SourceCount(source=lead.source)
        )
        source.leads += 1
        if lead.quotes:
            source.quoted += 1
        if lead.accepted or lead.stage.is_won:
            source.won += 1

    out.stages = [
        StageCount(
            stage=stage.key,
            name=stage.name,
            leads=counts.get(stage.key, 0),
            median_days=_median(dwell.get(stage.key, [])),
        )
        for stage in sorted(stages, key=lambda one: one.sort_order)
    ]
    out.sources = sorted(by_source.values(), key=lambda one: -one.leads)
    return out


def since_days(when: datetime | date | None, *, today: date) -> int | None:
    """Days from ``when`` to ``today``, taking a date or a timestamp.

    One place, because the stage history stores timestamps and the reports work
    in dates, and a conversion written at each call site is one that will
    eventually be off by a day in one of them.
    """
    if when is None:
        return None
    day = when.date() if isinstance(when, datetime) else when
    return (today - day).days


def next_action_default(*, today: date, days: int = 3) -> date:
    """A default next action, for a lead created without one.

    Three working-ish days: soon enough that a new enquiry is not left, far
    enough that it is not noise on the morning it arrives. Configurable by the
    caller, and a *default* rather than a requirement — the point is that no
    lead is created with nothing scheduled, not that somebody has to guess a
    date at the moment the phone rings.
    """
    return today + timedelta(days=max(0, days))
