"""QuoteAssemblyService — building, checking and issuing a multi-option quote.

Stage 3.3 answered "what does each option cost". This is the layer above it:
which properties are on the quote, which one is recommended, which were
considered and declined, whether the whole thing is fit to send, and the
immutable version that freezes it (§1, §3.7, §3.11).

Two ideas do most of the work here.

**Readiness is graded, not binary.** A quote can be wrong in ways that would put
a bad number in front of a client — an option that failed to price, a
bed-and-breakfast option with no chef cost, no recommendation to make — and it
can be *thin* in ways that only cost a sale, like offering two hotels instead of
five. The first kind blocks issuing; the second is advice. Collapsing them into
one boolean would either let an under-priced quote out or refuse a perfectly
correct one.

**Issuing freezes, it never mutates.** Re-issuing appends another version, so a
quote already sent cannot silently re-price itself when a supplier rate moves.
The version's headline figures come from the **recommended** option, because that
is the one being proposed; every option's figures are kept alongside in
``quote_version_options`` so "what did the client actually see" stays answerable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.modules.accommodations.models import Accommodation
from app.modules.pricing.config import PricingConfig
from app.modules.pricing.service import PricingConfigService
from app.modules.quotes.models import (
    Quote,
    QuoteOption,
    QuoteRejectedCandidate,
    QuoteVersion,
    QuoteVersionOption,
)
from app.modules.quotes.option_pricing import (
    OptionCosting,
    OptionPricingResult,
    OptionPricingService,
)
from app.modules.quotes.options import needs_chef
from app.modules.quotes.schemas import (
    QuoteOptionIn,
    QuoteOptionUpdate,
    RejectedCandidateIn,
)

BLOCKING = "blocking"
ADVISORY = "advisory"

# Option fields a caller may not null out, because the column is NOT NULL.
_NOT_NULLABLE = frozenset({"sort_order", "is_comparable", "agent_cover_fee"})


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a quote, and how badly."""

    severity: str
    code: str
    message: str


@dataclass
class Readiness:
    """Whether a quote can be issued, and what is wrong with it either way."""

    is_ready: bool
    catered_options: int
    self_catering_options: int
    problems: list[Problem]


class QuoteAssemblyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- the options on a quote ---------------------------------------------- #

    async def add_option(
        self, quote_id: uuid.UUID, payload: QuoteOptionIn
    ) -> QuoteOption:
        quote = await self._load(quote_id)
        self._refuse_if_issued(quote)
        accommodation = await self.db.get(Accommodation, payload.accommodation_id)
        if accommodation is None:
            raise NotFoundError("Accommodation not found.")
        if any(o.accommodation_id == payload.accommodation_id for o in quote.options):
            raise AppError(
                f"{accommodation.name} is already an option on this quote. A client "
                f"choosing between two entries for the same property is a mistake, "
                f"not a choice."
            )
        highest = max((o.sort_order for o in quote.options), default=0)
        option = QuoteOption(
            quote_id=quote.id,
            accommodation_id=payload.accommodation_id,
            is_recommended=payload.is_recommended,
            sort_order=payload.sort_order or highest + 1,
            agent_cover_fee=payload.agent_cover_fee,
            chef_fee_per_meal=payload.chef_fee_per_meal,
            manual_meal_cost=payload.manual_meal_cost,
            is_comparable=payload.is_comparable,
            notes=payload.notes,
        )
        self.db.add(option)
        await self.db.flush()
        if payload.is_recommended:
            await self._make_sole_recommendation(quote.id, option.id)
        await self.db.commit()
        return option

    async def update_option(
        self, quote_id: uuid.UUID, option_id: uuid.UUID, payload: QuoteOptionUpdate
    ) -> QuoteOption:
        quote = await self._load(quote_id)
        self._refuse_if_issued(quote)
        option = await self._option(quote, option_id)
        fields = payload.model_dump(exclude_unset=True)
        recommended = fields.pop("is_recommended", None)
        for name, value in fields.items():
            # An explicit null clears the nullable money and notes fields, which
            # is a real thing to want (an option that stops needing a chef). It
            # must not reach the NOT NULL ones, where it would be a 500.
            if value is None and name in _NOT_NULLABLE:
                continue
            setattr(option, name, value)
        if recommended is True:
            # Exactly one recommendation, always: setting one clears the rest in
            # the same transaction rather than leaving the caller to unset the
            # old one and risk a quote with two.
            await self._make_sole_recommendation(quote.id, option.id)
        elif recommended is False:
            option.is_recommended = False
        await self.db.commit()
        return option

    async def remove_option(self, quote_id: uuid.UUID, option_id: uuid.UUID) -> None:
        quote = await self._load(quote_id)
        self._refuse_if_issued(quote)
        option = await self._option(quote, option_id)
        await self.db.delete(option)
        await self.db.commit()

    async def _make_sole_recommendation(
        self, quote_id: uuid.UUID, option_id: uuid.UUID
    ) -> None:
        rows = (
            (
                await self.db.execute(
                    select(QuoteOption).where(QuoteOption.quote_id == quote_id)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.is_recommended = row.id == option_id

    # -- properties considered and declined (§3.3a) -------------------------- #

    async def add_rejected_candidate(
        self, quote_id: uuid.UUID, payload: RejectedCandidateIn
    ) -> QuoteRejectedCandidate:
        """Record a property the agent considered and ruled out by hand.

        The reference document's Diani Cottages entry — declined because it caps
        at 16 guests — is this. ``reason`` prints on the quotation verbatim, so it
        may only hold something safe to show a client (§3.3a); that is the
        agent's judgement to make, and the API does not try to police prose.
        """
        quote = await self._load(quote_id)
        self._refuse_if_issued(quote)
        if payload.accommodation_id is not None:
            accommodation = await self.db.get(Accommodation, payload.accommodation_id)
            if accommodation is None:
                raise NotFoundError("Accommodation not found.")
            if any(
                o.accommodation_id == payload.accommodation_id for o in quote.options
            ):
                raise AppError(
                    f"{accommodation.name} is offered as an option on this quote, so "
                    f"it cannot also be listed as considered and declined."
                )
        highest = max(
            (c.sort_order for c in quote.rejected_candidates), default=0
        )
        candidate = QuoteRejectedCandidate(
            quote_id=quote.id,
            accommodation_id=payload.accommodation_id,
            name=payload.name,
            reason=payload.reason,
            sort_order=payload.sort_order or highest + 1,
            source="manual",
        )
        self.db.add(candidate)
        await self.db.commit()
        return candidate

    async def remove_rejected_candidate(
        self, quote_id: uuid.UUID, candidate_id: uuid.UUID
    ) -> None:
        quote = await self._load(quote_id)
        self._refuse_if_issued(quote)
        candidate = next(
            (c for c in quote.rejected_candidates if c.id == candidate_id), None
        )
        if candidate is None:
            raise NotFoundError("Rejected candidate not found on this quote.")
        if candidate.source != "manual":
            raise AppError(
                "That refusal came from the rates, not from a person — the property "
                "does not meet its own minimum stay for these dates. Change the "
                "dates or the property's rates; deleting the line would only hide "
                "it until the next re-price."
            )
        await self.db.delete(candidate)
        await self.db.commit()

    # -- readiness ----------------------------------------------------------- #

    async def readiness(self, quote_id: uuid.UUID) -> Readiness:
        quote = await self._load(quote_id)
        cfg = await PricingConfigService(self.db).get()
        try:
            priced = await OptionPricingService(self.db).compute(quote)
        except AppError as exc:
            return Readiness(
                is_ready=False,
                catered_options=0,
                self_catering_options=0,
                problems=[Problem(BLOCKING, "not_priceable", str(exc))],
            )
        return self._grade(quote, priced, cfg)

    def _grade(
        self, quote: Quote, priced: OptionPricingResult, cfg: PricingConfig
    ) -> Readiness:
        problems: list[Problem] = []

        # An option the engine could not price would simply be missing from the
        # document, which reads as an oversight rather than as the data gap it is.
        for warning in priced.warnings:
            problems.append(Problem(BLOCKING, "unpriced_option", warning))

        catered = [o for o in priced.options if not needs_chef(o.meal_plan_code)]
        self_catering = [o for o in priced.options if needs_chef(o.meal_plan_code)]

        for option in self_catering:
            # Silence is not zero: an unpriced chef and food cost undercuts the
            # option by the whole cost of feeding the group (§3.4).
            if "chef" not in option.components or "meals" not in option.components:
                problems.append(
                    Problem(
                        BLOCKING,
                        "missing_meal_cost",
                        f"{option.accommodation_name} is priced on "
                        f"{option.meal_plan_code}, so it needs a chef fee and a food "
                        f"cost. Without them the option is under-priced by the whole "
                        f"cost of feeding the group.",
                    )
                )

        recommended = [o for o in quote.options if o.is_recommended]
        if not recommended:
            problems.append(
                Problem(
                    BLOCKING,
                    "no_recommendation",
                    "No option is flagged as recommended. The document leads on the "
                    "recommendation, and the engine does not choose between hotels.",
                )
            )
        elif len(recommended) > 1:
            problems.append(
                Problem(
                    BLOCKING,
                    "multiple_recommendations",
                    f"{len(recommended)} options are flagged as recommended; the "
                    f"document can only lead on one.",
                )
            )
        elif not any(
            o.accommodation_id == recommended[0].accommodation_id
            for o in priced.options
        ):
            problems.append(
                Problem(
                    BLOCKING,
                    "recommendation_not_priced",
                    "The recommended property is not among the priced options, so "
                    "the document would lead on a property with no price.",
                )
            )

        problems.extend(
            self._count_problems(
                len(catered),
                cfg.min_catered_options,
                cfg.max_catered_options,
                label="catered",
                noun="hotel option",
            )
        )
        problems.extend(
            self._count_problems(
                len(self_catering),
                cfg.min_self_catering_options,
                cfg.max_self_catering_options,
                label="self_catering",
                noun="self-catering option",
            )
        )

        return Readiness(
            is_ready=not any(p.severity == BLOCKING for p in problems),
            catered_options=len(catered),
            self_catering_options=len(self_catering),
            problems=problems,
        )

    @staticmethod
    def _count_problems(
        found: int, minimum: int, maximum: int, *, label: str, noun: str
    ) -> list[Problem]:
        """Grade an option count against its configured bounds.

        Too many blocks: the document's comparison table is built for a range,
        and past it the layout stops being readable. Too few is advice — a thin
        quote is a weaker sale, not a wrong one, and there are real cases (a
        single property the client already named) where it is exactly right.
        """
        if found > maximum:
            return [
                Problem(
                    BLOCKING,
                    f"too_many_{label}_options",
                    f"{found} {noun}s; the document is built for at most {maximum}.",
                )
            ]
        if found < minimum:
            return [
                Problem(
                    ADVISORY,
                    f"few_{label}_options",
                    f"{found} {noun}s; {minimum} to {maximum} gives the client a real "
                    f"choice.",
                )
            ]
        return []

    # -- issuing ------------------------------------------------------------- #

    async def issue(
        self, quote_id: uuid.UUID, *, actor_id: uuid.UUID | None
    ) -> QuoteVersion:
        """Freeze the quote into an immutable version and mark it sent.

        Prices the options first, so a version can never be a snapshot of stale
        figures, then refuses on any blocking problem — listing all of them,
        because fixing one at a time through a sequence of 400s is how a quote
        goes out with the second problem still in it.
        """
        quote = await self._load(quote_id)
        cfg = await PricingConfigService(self.db).get()
        priced = await OptionPricingService(self.db).price_options(quote_id)
        quote = await self._load(quote_id)
        # The session does not expire on commit, so the collections pricing just
        # rewrote are still the pre-pricing ones in the identity map. Without
        # this the snapshot would record the refusals as they were *before* the
        # engine derived them — that is, none at all.
        await self.db.refresh(quote, ["options", "rejected_candidates"])

        readiness = self._grade(quote, priced, cfg)
        blocking = [p for p in readiness.problems if p.severity == BLOCKING]
        if blocking:
            raise AppError(
                "This quote is not ready to issue: "
                + " ".join(f"({p.code}) {p.message}" for p in blocking)
            )

        recommended_id = next(o.accommodation_id for o in quote.options if o.is_recommended)
        headline = next(
            o for o in priced.options if o.accommodation_id == recommended_id
        )

        next_number = (
            await self.db.execute(
                select(func.coalesce(func.max(QuoteVersion.version_number), 0) + 1).where(
                    QuoteVersion.quote_id == quote_id
                )
            )
        ).scalar_one()

        version = QuoteVersion(
            quote_id=quote.id,
            version_number=int(next_number),
            snapshot=_snapshot(quote, priced, readiness),
            currency=headline.currency,
            created_by=actor_id,
            **_headline_money(headline),
        )
        options_by_accommodation = {o.accommodation_id: o for o in quote.options}
        for order, costing in enumerate(priced.options, start=1):
            option = options_by_accommodation[costing.accommodation_id]
            version.options.append(
                QuoteVersionOption(
                    option_id=option.id,
                    accommodation_id=costing.accommodation_id,
                    accommodation_name=costing.accommodation_name,
                    meal_plan_label=costing.meal_plan_name,
                    rooms_required=costing.rooms_required,
                    cost_subtotal=costing.build_up.cost_subtotal,
                    contingency_value=costing.build_up.contingency_value,
                    profit_value=costing.build_up.profit_value,
                    agent_cover_fee=costing.build_up.agent_cover_fee,
                    supplier_paid_total=costing.supplier_paid_total,
                    retained_discount=costing.retained_discount,
                    selling_total=costing.build_up.group_total,
                    per_person=costing.build_up.per_person,
                    currency=costing.currency,
                    is_recommended=option.is_recommended,
                    is_comparable=costing.is_comparable,
                    sort_order=option.sort_order or order,
                )
            )
        self.db.add(version)
        await self.db.flush()

        quote.current_version_id = version.id
        # Counted from the day it is issued, not from when the quote was drafted:
        # a proposal built three weeks ago is still good for its full 30 days
        # once it is actually sent (§3.11).
        quote.valid_until = date.today() + timedelta(days=cfg.quotation_validity_days)
        quote.status = "sent"
        await self.db.commit()
        return (
            await self.db.execute(select(QuoteVersion).where(QuoteVersion.id == version.id))
        ).scalar_one()

    async def select_option(self, quote_id: uuid.UUID, option_id: uuid.UUID) -> Quote:
        """Record which option the client chose.

        The single most valuable field for the CRM (§7): it is what says whether
        the *Recommended* flag matches how clients actually behave. Deliberately
        does not change the quote's status — choosing an option and accepting a
        quotation are different events, and conflating them would lose the gap
        between them.
        """
        quote = await self._load(quote_id)
        await self._option(quote, option_id)
        quote.selected_option_id = option_id
        quote.selected_at = datetime.now(UTC)
        await self.db.commit()
        return await self._load(quote_id)

    # -- helpers ------------------------------------------------------------- #

    def _refuse_if_issued(self, quote: Quote) -> None:
        """Guard the assembly against edits after the quote has gone out.

        Versions are immutable, but the quote they point at is not, and an option
        quietly added after a client received the document would make the stored
        version disagree with what the client is looking at. Re-issuing is the
        supported path: it appends a new version.
        """
        if quote.status != "draft":
            raise AppError(
                f"This quote is {quote.status}, not a draft. Editing the options of a "
                f"quote already sent would make the client's copy disagree with ours "
                f"— re-issue it instead, which appends a new version."
            )

    async def _load(self, quote_id: uuid.UUID) -> Quote:
        quote = (
            await self.db.execute(select(Quote).where(Quote.id == quote_id))
        ).scalar_one_or_none()
        if quote is None:
            raise NotFoundError("Quote not found.")
        return quote

    @staticmethod
    async def _option(quote: Quote, option_id: uuid.UUID) -> QuoteOption:
        option = next((o for o in quote.options if o.id == option_id), None)
        if option is None:
            raise NotFoundError("Option not found on this quote.")
        return option


# --------------------------------------------------------------------------- #
# Version money
# --------------------------------------------------------------------------- #


def _headline_money(headline: OptionCosting) -> dict[str, Decimal]:
    """The version's summary figures, taken from the recommended option.

    ``internal_cost`` is what Heissal actually pays out, not the costed subtotal:
    on a discounted rack rate those differ by the retained half, and calling the
    costed figure "cost" would understate realised margin by exactly that amount.
    So margin here is the profit percentage *plus* the contingency *plus* the
    retained half — the three numbers §3.5 insists on tracking apart, correctly
    added back together.
    """
    # Quantized to the precision the columns actually hold, so the figures a
    # caller reads back are the figures that were stored — an unrounded margin
    # returned before the round trip would not match the row afterwards.
    selling = _money(headline.build_up.group_total)
    internal = _money(headline.build_up.cost_subtotal - headline.retained_discount)
    profit = selling - internal
    return {
        "internal_cost": internal,
        "selling_price": selling,
        "gross_profit": profit,
        "gross_margin": (
            (profit / selling).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            if selling
            else Decimal(0)
        ),
    }


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _snapshot(
    quote: Quote, priced: OptionPricingResult, readiness: Readiness
) -> dict:
    """The whole computed quote as JSON, for reconstructing it years later.

    Denormalised on purpose: a property renamed or a rate superseded must not
    change what this version says was quoted.
    """
    recommended = {
        o.accommodation_id: o.is_recommended for o in quote.options
    }
    order = {o.accommodation_id: o.sort_order for o in quote.options}
    return {
        "quote_number": quote.quote_number,
        "currency": quote.presentation_currency.upper(),
        "arrival_date": quote.arrival_date.isoformat(),
        "departure_date": quote.departure_date.isoformat(),
        "pax_count": quote.pax_count,
        "options": [
            {
                "accommodation_id": str(o.accommodation_id),
                "accommodation_name": o.accommodation_name,
                "room_type_name": o.room_type_name,
                "meal_plan_code": o.meal_plan_code,
                "meal_plan_name": o.meal_plan_name,
                "meal_plan_fallback_from": o.meal_plan_fallback_from,
                "rooms_required": o.rooms_required,
                "nights": o.nights,
                "is_comparable": o.is_comparable,
                # Frozen so the document leads on the option that was actually
                # recommended when it went out, not on whatever is flagged today.
                "is_recommended": recommended.get(o.accommodation_id, False),
                "sort_order": order.get(o.accommodation_id, 0),
                "components": {k: str(v) for k, v in o.components.items()},
                "supplements": [
                    {
                        "label": s.label,
                        "basis": s.basis,
                        "nights": s.nights,
                        "cost": str(s.cost),
                    }
                    for s in o.supplements
                ],
                "supplier_paid_total": str(o.supplier_paid_total),
                "retained_discount": str(o.retained_discount),
                "cost_subtotal": str(o.build_up.cost_subtotal),
                "contingency_value": str(o.build_up.contingency_value),
                "profit_value": str(o.build_up.profit_value),
                "agent_cover_fee": str(o.build_up.agent_cover_fee),
                "per_person": (
                    str(o.build_up.per_person)
                    if o.build_up.per_person is not None
                    else None
                ),
                "group_total": str(o.build_up.group_total),
                "warnings": list(o.warnings),
            }
            for o in priced.options
        ],
        # Every property shown as considered-and-declined, engine-derived and
        # agent-typed alike: the client saw both, so the snapshot holds both.
        "rejected": [
            {
                "accommodation_id": (
                    str(r.accommodation_id) if r.accommodation_id else None
                ),
                "name": r.name,
                "reason": r.reason,
                "source": r.source,
            }
            for r in sorted(quote.rejected_candidates, key=lambda r: r.sort_order)
        ],
        "readiness": {
            "catered_options": readiness.catered_options,
            "self_catering_options": readiness.self_catering_options,
            "problems": [
                {"severity": p.severity, "code": p.code, "message": p.message}
                for p in readiness.problems
            ],
        },
    }
