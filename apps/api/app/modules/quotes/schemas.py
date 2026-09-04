from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.quotes.models import QUOTE_STATUSES

TRAVELLER_TYPES = ("adult", "child", "infant")


# --------------------------------------------------------------------------- #
# Input (assembly) schemas
# --------------------------------------------------------------------------- #

class TravellerIn(BaseModel):
    traveller_type: str
    age: int | None = Field(default=None, ge=0, le=120)

    @model_validator(mode="after")
    def _check_type(self) -> TravellerIn:
        if self.traveller_type not in TRAVELLER_TYPES:
            raise ValueError(f"traveller_type must be one of {TRAVELLER_TYPES}")
        return self


class CohortIn(BaseModel):
    """One (residency, traveller type) headcount — the group vector's unit (§3.8).

    No currency field. Which currency a residency bills in is a property of the
    residence category, so accepting one per quote would let two quotes disagree
    about it.
    """

    residence_category_id: uuid.UUID
    traveller_type: str
    headcount: int = Field(ge=1)

    @model_validator(mode="after")
    def _check_type(self) -> CohortIn:
        if self.traveller_type not in TRAVELLER_TYPES:
            raise ValueError(f"traveller_type must be one of {TRAVELLER_TYPES}")
        return self


class CohortRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    residence_category_id: uuid.UUID
    traveller_type: str
    headcount: int


class AccommodationSelectionIn(BaseModel):
    accommodation_id: uuid.UUID
    room_type_id: uuid.UUID
    meal_plan_id: uuid.UUID
    rooms: int = Field(default=1, ge=1)
    nights: int = Field(default=1, ge=1)


class ActivitySelectionIn(BaseModel):
    activity_id: uuid.UUID
    day: int | None = Field(default=None, ge=1)
    adults: int = Field(default=0, ge=0)
    children: int = Field(default=0, ge=0)


class LegIn(BaseModel):
    destination_id: uuid.UUID
    nights: int = Field(default=1, ge=1)
    check_in: date | None = None
    check_out: date | None = None
    accommodations: list[AccommodationSelectionIn] = Field(default_factory=list)
    activities: list[ActivitySelectionIn] = Field(default_factory=list)


class TransportIn(BaseModel):
    vehicle_id: uuid.UUID
    estimated_km: Decimal = Field(default=Decimal("0"), ge=0)
    days: int = Field(default=1, ge=1)


class TransportSegmentIn(BaseModel):
    """One movement on the quote: a line haul or a transfer leg (§3.10).

    Distinct from :class:`TransportIn`, which is the Stage 2 km-and-fuel safari
    vehicle model and stays for game-drive costing.

    ``kind`` and ``mode`` are not constrained to literals here on purpose. The
    vocabulary and the reasons behind it — why ``air`` is named and never
    priced, why rail drags transfers with it — live in
    :mod:`app.modules.quotes.transport`, and a second copy in the schema would
    be a second thing to keep in step. Creation refuses an unsellable mode with
    that module's own wording, which says *why* rather than listing the
    permitted values.
    """

    sequence: int = Field(default=0, ge=0)
    kind: str = Field(max_length=20)
    mode: str = Field(max_length=10)
    travel_class: str = Field(default="", max_length=20)
    destination_id: uuid.UUID | None = None
    # Where the drive starts (§4.2). Needed only for a movement on our own
    # vehicle, which is costed from the route table — a route is a pair, and
    # the far end cannot be inferred from an option's legs when transport is
    # priced once for the whole quote.
    origin_id: uuid.UUID | None = None
    # Set for a movement run on our own or a hired vehicle, which is costed by
    # the fleet model rather than from a transfer tariff.
    vehicle_id: uuid.UUID | None = None
    vehicle_type: str | None = Field(default=None, max_length=40)
    units: int = Field(default=1, ge=1)
    # NULL prices at the quote's arrival date. Given, it prices at the tariff
    # effective that day, so a return leg after a fare revision is not charged
    # at the outbound fare.
    travel_date: date | None = None
    is_optional: bool = False
    is_vvip: bool = False
    description: str | None = None


class TransportSegmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sequence: int
    kind: str
    mode: str
    travel_class: str
    destination_id: uuid.UUID | None
    origin_id: uuid.UUID | None
    vehicle_id: uuid.UUID | None
    vehicle_type: str | None
    units: int
    travel_date: date | None
    is_optional: bool
    is_vvip: bool
    description: str | None


class OptionLegIn(BaseModel):
    """One leg of a curated multi-destination package (§3.9).

    Dates rather than a night count: the two say the same thing only while
    nothing is edited, and the moment a middle leg moves by a day only dates
    record what happened.
    """

    sequence: int = Field(ge=1)
    destination_id: uuid.UUID
    accommodation_id: uuid.UUID
    check_in: date
    check_out: date
    # The plan for THIS leg. A day out of the hotel makes half board the right
    # choice rather than a fallback from full board, and the document has to be
    # able to tell those apart. NULL uses the quote's own requested plan.
    requested_meal_plan_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _check_dates(self) -> OptionLegIn:
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class OptionLegRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sequence: int
    destination_id: uuid.UUID
    accommodation_id: uuid.UUID
    check_in: date
    check_out: date
    requested_meal_plan_id: uuid.UUID | None
    room_type_id: uuid.UUID | None
    meal_plan_id: uuid.UUID | None
    rooms_required: int | None
    meal_plan_fallback_from: str | None


class QuoteOptionIn(BaseModel):
    """One property to offer on the quote (§3.7).

    Only the property and the agent's manual money are given. The room type, meal
    plan and rooming are *resolved* by pricing — the agent picks hotels, the
    engine picks the cheapest eligible room within each one.
    """

    accommodation_id: uuid.UUID
    is_recommended: bool = False
    sort_order: int = 0
    # Backend-only, added after profit and never marked up (§3.6).
    agent_cover_fee: Decimal = Field(default=Decimal("0"), ge=0)
    # Group fee per meal for bed-and-breakfast options only (§3.4).
    chef_fee_per_meal: Decimal | None = Field(default=None, ge=0)
    manual_meal_cost: Decimal | None = Field(default=None, ge=0)
    # An agent may mark an option non-comparable (a villa against resorts); the
    # engine can add that flag but never remove it.
    is_comparable: bool = True
    notes: str | None = None
    # A curated package: one property per leg (§3.9). Empty means the
    # single-property option `accommodation_id` already describes, which is
    # most quotes. Legs take precedence when present.
    legs: list[OptionLegIn] = Field(default_factory=list)


class QuoteCreate(BaseModel):
    client_id: uuid.UUID
    # The enquiry this quote answers (§5.2). Optional: a repeat client rings up
    # and asks for a quote, and refusing to price one without a lead record
    # would make the CRM an obstacle. Given, it is what lets §5.1's funnel say
    # which SOURCES convert rather than only how many quotes do.
    lead_id: uuid.UUID | None = None
    # Optional — default from the client's residence category / its currency.
    presentation_currency: str | None = Field(default=None, min_length=3, max_length=3)
    residence_category_id: uuid.UUID | None = None
    arrival_date: date
    departure_date: date
    markup_pct: Decimal | None = Field(default=None, ge=0)
    discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    tax_pct: Decimal | None = Field(default=None, ge=0)
    travellers: list[TravellerIn] = Field(default_factory=list)
    # The group vector. Takes precedence over pax_count when present, because it
    # is the only form that can express a mixed-residency group (§3.8).
    cohorts: list[CohortIn] = Field(default_factory=list)
    legs: list[LegIn] = Field(default_factory=list)
    transport: list[TransportIn] = Field(default_factory=list)
    # The journey (§3.10) — line hauls and transfer legs, priced from the
    # destination tariffs and charged into every option.
    transport_segments: list[TransportSegmentIn] = Field(default_factory=list)

    # -- Stage 3 group quoting (design doc §3.3-§3.7) --------------------- #
    # A 25-person corporate booking is a headcount, not 25 traveller rows.
    pax_count: int | None = Field(default=None, ge=1)
    # NULL uses the business defaults from pricing config (profit 24%,
    # contingency 5%) — never a literal in code.
    profit_pct: Decimal | None = Field(default=None, ge=0, le=100)
    contingency_pct: Decimal | None = Field(default=None, ge=0, le=100)
    # The plan the client asked for; options may fall back from it (§3.4).
    requested_meal_plan_id: uuid.UUID | None = None
    options: list[QuoteOptionIn] = Field(default_factory=list)
    # The document's cover copy (§3.11). NULL falls back to a title derived from
    # the destination, so a quote is never blank-covered.
    document_title: str | None = Field(default=None, max_length=160)
    document_subtitle: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def _check_dates(self) -> QuoteCreate:
        if self.departure_date <= self.arrival_date:
            raise ValueError("departure_date must be after arrival_date")
        return self


class QuoteUpdate(BaseModel):
    """Partial edit of a quote's own fields. Only what is sent is changed."""

    document_title: str | None = Field(default=None, max_length=160)
    document_subtitle: str | None = Field(default=None, max_length=240)


class QuoteStatusUpdate(BaseModel):
    status: str

    @model_validator(mode="after")
    def _check_status(self) -> QuoteStatusUpdate:
        if self.status not in QUOTE_STATUSES:
            raise ValueError(f"status must be one of {QUOTE_STATUSES}")
        return self


# --------------------------------------------------------------------------- #
# Output schemas
# --------------------------------------------------------------------------- #

class TravellerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    traveller_type: str
    age: int | None


class AccommodationSelectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    accommodation_id: uuid.UUID
    room_type_id: uuid.UUID
    meal_plan_id: uuid.UUID
    rooms: int
    nights: int


class ActivitySelectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    activity_id: uuid.UUID
    day: int | None
    adults: int
    children: int


class LegRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sequence: int
    destination_id: uuid.UUID
    nights: int
    check_in: date | None
    check_out: date | None
    accommodations: list[AccommodationSelectionRead]
    activities: list[ActivitySelectionRead]


class TransportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    vehicle_id: uuid.UUID
    estimated_km: Decimal
    days: int


class QuoteOptionResolvedRead(BaseModel):
    """An option as stored: the property plus whatever pricing resolved.

    Room type, meal plan and rooming are NULL until the options are priced. No
    money appears here — the figures live in the pricing result and the version
    snapshot, so a quote read can never expose cost or margin (§2).
    """

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    accommodation_id: uuid.UUID
    room_type_id: uuid.UUID | None
    meal_plan_id: uuid.UUID | None
    rooms_required: int | None
    meal_plan_fallback_from: str | None
    is_comparable: bool
    is_recommended: bool
    sort_order: int
    # The curated package, in itinerary order. Empty for a single-property
    # option, which is most quotes (§3.9).
    legs: list[OptionLegRead] = Field(default_factory=list)


class QuoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    quote_number: str
    client_id: uuid.UUID
    status: str
    presentation_currency: str
    residence_category_id: uuid.UUID
    arrival_date: date
    departure_date: date
    markup_pct: Decimal | None
    discount_pct: Decimal | None
    tax_pct: Decimal | None
    current_version_id: uuid.UUID | None
    created_at: datetime
    pax_count: int | None
    profit_pct: Decimal | None
    contingency_pct: Decimal | None
    requested_meal_plan_id: uuid.UUID | None
    lead_id: uuid.UUID | None = None
    valid_until: date | None
    # The status as it reads today (§5.1): a sent quote past its validity is
    # expired without anything having written that to the row. Exposed beside
    # the stored one rather than instead of it, because "sent and lapsed" and
    # "sent and waiting" are the same row and different pipeline entries.
    effective_status: str
    decided_at: datetime | None = None
    decision_note: str | None = None
    # Which option the client actually chose — the CRM's most valuable field (§7).
    selected_option_id: uuid.UUID | None
    selected_at: datetime | None
    travellers: list[TravellerRead]
    cohorts: list[CohortRead]
    legs: list[LegRead]
    transport: list[TransportRead]
    transport_segments: list[TransportSegmentRead]
    options: list[QuoteOptionResolvedRead]
    rejected_candidates: list[RejectedCandidateFullRead]


class QuoteSummary(BaseModel):
    """Lightweight list row (no nested assembly)."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    quote_number: str
    client_id: uuid.UUID
    status: str
    presentation_currency: str
    arrival_date: date
    departure_date: date
    created_at: datetime


# --------------------------------------------------------------------------- #
# Pricing (Stage 2.8)
# --------------------------------------------------------------------------- #

class CalculateRequest(BaseModel):
    """A transient quote to price without persisting (live quote builder)."""

    residence_category_id: uuid.UUID
    presentation_currency: str = Field(min_length=3, max_length=3)
    arrival_date: date
    departure_date: date
    markup_pct: Decimal | None = Field(default=None, ge=0)
    discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    tax_pct: Decimal | None = Field(default=None, ge=0)
    travellers: list[TravellerIn] = Field(default_factory=list)
    legs: list[LegIn] = Field(default_factory=list)
    transport: list[TransportIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_dates(self) -> CalculateRequest:
        if self.departure_date <= self.arrival_date:
            raise ValueError("departure_date must be after arrival_date")
        return self


class PricingLineClient(BaseModel):
    """A costed line as the client sees it — price only, never cost."""

    category: str
    description: str
    quantity: Decimal
    client_price: Decimal


class PricingLineInternal(PricingLineClient):
    """Staff view — adds the internal cost and its source currency."""

    source_currency: str
    internal_cost: Decimal


class PricingResultClient(BaseModel):
    presentation_currency: str
    lines: list[PricingLineClient]
    selling_price: Decimal


class PricingResultInternal(BaseModel):
    presentation_currency: str
    lines: list[PricingLineInternal]
    markup_pct: Decimal
    discount_pct: Decimal
    tax_pct: Decimal
    internal_cost: Decimal
    selling_subtotal: Decimal
    discount_value: Decimal
    after_discount: Decimal
    tax: Decimal
    selling_price: Decimal
    gross_profit: Decimal
    gross_margin: Decimal
    needs_approval: bool


class QuoteItemClient(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    category: str
    description: str
    quantity: Decimal
    unit_price: Decimal


class QuoteItemInternal(QuoteItemClient):
    source_currency: str
    internal_cost: Decimal


class QuoteVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_number: int
    currency: str
    selling_price: Decimal
    created_at: datetime


class QuoteVersionClientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_number: int
    currency: str
    selling_price: Decimal
    created_at: datetime
    items: list[QuoteItemClient]
    options: list[QuoteVersionOptionClientRead] = Field(default_factory=list)


class QuoteVersionInternalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_number: int
    currency: str
    internal_cost: Decimal
    selling_price: Decimal
    gross_profit: Decimal
    gross_margin: Decimal
    created_at: datetime
    items: list[QuoteItemInternal]
    options: list[QuoteVersionOptionInternalRead] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Stage 3 option pricing (design doc §2, §3.3-§3.7)
# --------------------------------------------------------------------------- #
# The internal/client split is enforced here rather than by filtering in the
# renderer. A client model that simply has no field for cost, margin, supplier
# payments or the agent cover fee cannot leak one, however the document template
# changes later.


class OptionBuildUpInternal(BaseModel):
    """Every step of the margin build-up. Backend only, in full."""

    model_config = ConfigDict(from_attributes=True)
    cost_subtotal: Decimal
    contingency_value: Decimal
    cost_basis: Decimal
    profit_value: Decimal
    after_profit: Decimal
    agent_cover_fee: Decimal
    selling_total: Decimal
    per_person: Decimal | None
    group_total: Decimal
    components: dict[str, Decimal]


class SupplementChargeInternal(BaseModel):
    """A festive loading or compulsory gala dinner as priced onto one option."""

    model_config = ConfigDict(from_attributes=True)
    label: str
    kind: str
    basis: str
    amount: Decimal
    currency: str
    nights: int
    cost: Decimal


class TransportChargeInternal(BaseModel):
    """One movement as it was costed (§3.10). Internal: these are tariffs.

    The unit amount is kept beside the multiplied-out cost because a fare that
    cannot be checked against the operator's own published figure cannot be
    reconciled at all.
    """

    model_config = ConfigDict(from_attributes=True)
    sequence: int
    kind: str
    mode: str
    label: str
    basis: str
    units: int
    unit_amount: Decimal
    currency: str
    cost: Decimal
    is_optional: bool
    is_vvip: bool
    #: The row behind it — the tariff, or the route and the vehicle for a drive
    #: on our own fleet (§4.2). Internal by definition: it is what an operator
    #: reconciles a fuel invoice against, and it names our own tables.
    source: str = ""


class PricedLegRead(BaseModel):
    """One leg of a priced package, as the client sees it (§3.9).

    No ``meal_plan_fallback_from``. That the property had no rate on the plan
    the client asked for is an internal fact — it says something about our data
    and our negotiation, not about their holiday — and the option-level field is
    already restricted to ``quote:read_cost``. Putting it on the client schema
    per leg leaked it, which the cost-leak sweep caught.
    """

    sequence: int
    accommodation_name: str
    room_type_name: str
    meal_plan_code: str
    rooms_required: int
    nights: int


class PricedLegInternalRead(PricedLegRead):
    """The same leg for staff, plus why it is not what was asked for."""

    meal_plan_fallback_from: str | None = None


class CohortPriceRead(BaseModel):
    """What one cohort pays, in its own billing currency (§3.8).

    Client-facing: these are prices, not costs. For a mixed group they are the
    only meaningful per-person figures, since ``per_person`` below is NULL
    whenever residency or traveller type varies.
    """

    residence: str
    traveller_type: str
    headcount: int
    currency: str
    per_person: Decimal
    total: Decimal


class QuoteOptionClientRead(BaseModel):
    """What the client is shown for one option: a price, and nothing behind it."""

    model_config = ConfigDict(from_attributes=True)
    accommodation_id: uuid.UUID
    accommodation_name: str
    room_type_name: str
    meal_plan_code: str
    rooms_required: int
    nights: int
    currency: str
    # NULL when the group is not uniform, in which case only the total is shown.
    per_person: Decimal | None = None
    group_total: Decimal
    is_comparable: bool
    # One row per cohort. Residents in KES beside non-residents in USD, adults
    # apart from children — what the client asked to be able to show.
    cohorts: list[CohortPriceRead] = Field(default_factory=list)
    # What the destination includes and this price therefore covers, by name.
    # Client-facing on purpose: these are the words the proposal lists under
    # Included, and a client who is charged for an excursion has to be able to
    # read that they are getting it.
    activities: list[str] = Field(default_factory=list)
    # One entry per leg of the package. A single-property option has one; the
    # top-level room and plan fields above describe the first leg only.
    #
    # Sequence rather than list so the internal schema can narrow it to
    # PricedLegInternalRead — a list is invariant, so overriding it with a
    # subclass is unsound and mypy is right to say so.
    legs: Sequence[PricedLegRead] = Field(default_factory=list)
    # The rates used to reach a group total spanning currencies. A converted
    # total with an unstated rate is a dispute waiting to happen.
    conversions: dict[str, Decimal] = Field(default_factory=dict)


class QuoteOptionInternalRead(QuoteOptionClientRead):
    room_type_id: uuid.UUID
    meal_plan_id: uuid.UUID
    meal_plan_name: str
    # Set when the property had no rate on the plan the client asked for.
    meal_plan_fallback_from: str | None = None
    supplier_paid_total: Decimal
    retained_discount: Decimal
    supplements: list[SupplementChargeInternal]
    build_up: OptionBuildUpInternal
    warnings: list[str]
    # Overrides the client's leg rows with the fallback reason included.
    legs: list[PricedLegInternalRead] = Field(default_factory=list)



class RejectedCandidateRead(BaseModel):
    """A property considered but not offered. ``reason`` prints verbatim (§3.3a)."""

    model_config = ConfigDict(from_attributes=True)
    accommodation_id: uuid.UUID | None = None
    name: str
    reason: str


class OptionPricingClientResult(BaseModel):
    options: list[QuoteOptionClientRead]
    rejected: list[RejectedCandidateRead]
    # The journey (§3.10), which belongs to the quote and not to an option: it
    # is the same journey whichever hotel is chosen. Flights are named because
    # the fare is an exclusion, and a client who is not told to book their own
    # ticket is a client who arrives without one; the tariffs behind the rest
    # are internal, since the price is already in each option's total.
    transport_named: list[str] = Field(default_factory=list)
    # What the add-ons sell for. A price, not the cost behind it, and never
    # part of any option's group_total.
    optional_transport_price: Decimal = Decimal(0)


class OptionPricingInternalResult(BaseModel):
    options: list[QuoteOptionInternalRead]
    rejected: list[RejectedCandidateRead]
    # Why a property on the quote could not be priced at all. Internal because it
    # describes gaps in our own rate data, not anything about the hotel.
    warnings: list[str]
    # The journey as costed (§3.10).
    transport: list[TransportChargeInternal] = Field(default_factory=list)
    transport_optional: list[TransportChargeInternal] = Field(default_factory=list)
    transport_total: Decimal = Decimal(0)
    optional_transport_total: Decimal = Decimal(0)
    optional_transport_price: Decimal = Decimal(0)
    transport_named: list[str] = Field(default_factory=list)
    # Movements with no tariff on file. Blocking at readiness: a movement
    # priced at zero reads on a document as one the client is not charged for.
    unpriced_transport: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Stage 3.4 assembly: options, refusals, readiness, issued versions
# --------------------------------------------------------------------------- #


class QuoteOptionUpdate(BaseModel):
    """Partial edit of one option. Only the fields sent are changed.

    Setting ``is_recommended`` to true clears it on every other option in the
    same transaction — a document that leads on two properties leads on neither.
    """

    is_recommended: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)
    agent_cover_fee: Decimal | None = Field(default=None, ge=0)
    chef_fee_per_meal: Decimal | None = Field(default=None, ge=0)
    manual_meal_cost: Decimal | None = Field(default=None, ge=0)
    is_comparable: bool | None = None
    notes: str | None = None


class AcceptQuoteIn(BaseModel):
    """The client said yes — to which option, and anything worth recording.

    ``option_id`` may be omitted only where one was already chosen through
    ``/select``: a quote offers several options (§3.7), so an acceptance
    without one leaves the booking and the revenue figure undecided.
    """

    option_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=2000)


class DeclineQuoteIn(BaseModel):
    """The client said no, and why if they said.

    The reason is optional in the schema and asked for in the wording. A funnel
    that counts losses without reasons says you are losing and nothing about
    what to change; insisting on one would only produce "n/a".
    """

    note: str | None = Field(default=None, max_length=2000)


class ConversionRead(BaseModel):
    """The funnel (§5.1).

    Money is **per currency** and never summed across them: a quote is
    presented in one currency (§3.8), and a single "total won" spanning
    shillings and dollars is a figure with no meaning — converting them would
    bake today's rate into a historical report.
    """

    counts: dict[str, int]
    won: dict[str, Decimal]
    lost: dict[str, Decimal]
    outstanding: dict[str, Decimal]
    #: Accepted over accepted-plus-declined. Outstanding quotes are excluded:
    #: one nobody has answered is not a loss.
    win_rate: Decimal | None = None
    #: Median, not mean — one quote accepted after eight months would move a
    #: mean somewhere no quote has ever been.
    median_days_to_decide: int | None = None
    recommendation_taken: int = 0
    recommendation_declined: int = 0
    #: How often the client took the option we recommended (§3.7). The most
    #: valuable thing this report says about how we sell.
    recommendation_rate: Decimal | None = None


class RejectedCandidateIn(BaseModel):
    """A property the agent considered and ruled out (§3.3a).

    ``accommodation_id`` is optional because a candidate may never have been in
    the catalogue — the reference document's Diani Cottages is a name and a
    reason, nothing more. ``reason`` is printed on the quotation verbatim, so it
    must say only what is safe to show a client.
    """

    accommodation_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1)
    sort_order: int | None = Field(default=None, ge=0)


class RejectedCandidateFullRead(RejectedCandidateRead):
    """As stored: adds the id and who put it there. Staff-facing."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sort_order: int
    # engine | manual — an engine refusal is rewritten on every re-price.
    source: str


class ReadinessProblem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    # blocking | advisory — only blocking problems stop a quote being issued.
    severity: str
    code: str
    message: str


class ReadinessRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    is_ready: bool
    catered_options: int
    self_catering_options: int
    problems: list[ReadinessProblem]


class SelectOptionIn(BaseModel):
    option_id: uuid.UUID


class QuoteVersionOptionClientRead(BaseModel):
    """One option as frozen into a version: price only."""

    model_config = ConfigDict(from_attributes=True)
    accommodation_id: uuid.UUID | None
    accommodation_name: str
    meal_plan_label: str | None
    rooms_required: int | None
    currency: str
    per_person: Decimal | None
    selling_total: Decimal
    is_recommended: bool
    is_comparable: bool
    sort_order: int


class QuoteVersionOptionInternalRead(QuoteVersionOptionClientRead):
    option_id: uuid.UUID | None
    cost_subtotal: Decimal
    contingency_value: Decimal
    profit_value: Decimal
    agent_cover_fee: Decimal
    supplier_paid_total: Decimal | None
    retained_discount: Decimal | None
