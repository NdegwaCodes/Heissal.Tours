"""The internal costing worksheet — the mirror of the client document (§3.12).

The proposal says what a trip costs. This says **why**, in the form somebody can
check against the supplier's own paper: every line with its amount, the basis it
is charged on, the multiplier that was actually applied, and the row it came
from. A cost you cannot trace to a document is a cost you cannot defend when a
supplier invoices something else, and a margin you cannot decompose is a margin
nobody can be held to.

Deliberately **not** the client view model with cost fields added. That class is
the internal/client boundary made structural (§2) — it has no field for cost,
margin or supplier payments, so a template cannot print what it was never
handed — and bolting the worksheet onto it would dissolve the one mechanism that
makes the boundary hold. Two view models, two templates, two permissions: the
proposal needs ``quote:read``, this needs ``quote:read_cost``.

Both read the **same frozen version**. That is the point of the mirror: if the
worksheet and the proposal could disagree, the worksheet would not be evidence
of anything. So nothing here is recomputed — the lines, their sources and the
figures they add up to were frozen when the quote was issued, because a rate
superseded next month must not change what this version says it was costed at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.clients.models import Client
from app.modules.documents.viewmodel import money
from app.modules.quotes.models import Quote, QuoteVersion

# The order the build-up reads its components in (§3.6). Lines are grouped by
# component rather than left in insertion order so the worksheet reads down the
# same sequence as the arithmetic it explains.
COMPONENT_ORDER = (
    "accommodation",
    "supplements",
    "park_fees",
    "chef",
    "meals",
    "transport",
    "transport_optional",
)

COMPONENT_LABELS = {
    "accommodation": "Accommodation",
    "supplements": "Mandatory supplements",
    "park_fees": "Park & conservation entry",
    "chef": "Chef",
    "meals": "Group food",
    "transport": "Transport",
    "transport_optional": "Optional upgrades — outside the package price",
}


@dataclass(frozen=True)
class WorksheetLine:
    """One costed line, as an operator reconciles it."""

    label: str
    basis: str
    unit_amount: str
    quantity: int
    extended: str
    source: str
    who: str
    leg: int | None
    # The sheet rate and what the supplier is actually paid, where a discounted
    # rack rate makes those three different numbers (§3.5).
    sheet_amount: str | None = None
    paid_amount: str | None = None


@dataclass(frozen=True)
class WorksheetGroup:
    """One component's lines and their subtotal in the source currencies."""

    component: str
    label: str
    lines: list[WorksheetLine]
    #: The component total from the build-up, in the presentation currency.
    total: str | None


@dataclass(frozen=True)
class WorksheetFigure:
    label: str
    value: str
    #: Set on the three figures that are margin, so the template can group them.
    is_margin: bool = False
    note: str | None = None


@dataclass(frozen=True)
class WorksheetCohort:
    label: str
    headcount: int
    per_person: str
    total: str


@dataclass
class WorksheetOption:
    number: str
    name: str
    route: str
    is_recommended: bool
    is_comparable: bool
    room_type: str
    meal_plan: str
    meal_plan_fallback_from: str | None
    rooms_required: int | None
    nights: int | None
    groups: list[WorksheetGroup]
    build_up: list[WorksheetFigure]
    margin: list[WorksheetFigure]
    cohorts: list[WorksheetCohort] = field(default_factory=list)
    conversions: list[WorksheetFigure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Worksheet:
    quote_number: str
    client_name: str
    version_number: int
    issued_on: date
    arrival: date | None
    departure: date | None
    nights: int
    pax: int
    currency: str
    options: list[WorksheetOption]
    journey: list[WorksheetLine]
    journey_note: str | None
    rejected: list[tuple[str, str, str]]
    problems: list[tuple[str, str, str]]


class WorksheetBuilder:
    """Assembles the worksheet for one issued version."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build(self, quote: Quote, version: QuoteVersion) -> Worksheet:
        snapshot = version.snapshot or {}
        currency = version.currency.upper()
        client = await self.db.get(Client, quote.client_id)
        journey = snapshot.get("transport") or {}

        raw_options = sorted(
            snapshot.get("options", []), key=lambda o: o.get("sort_order") or 0
        )
        return Worksheet(
            quote_number=quote.quote_number,
            client_name=client.name if client else "",
            version_number=version.version_number,
            issued_on=version.created_at.date(),
            arrival=quote.arrival_date,
            departure=quote.departure_date,
            nights=max(0, (quote.departure_date - quote.arrival_date).days),
            pax=int(snapshot.get("pax_count") or quote.pax_count or 0),
            currency=currency,
            options=[
                self._option(raw, index, currency)
                for index, raw in enumerate(raw_options, start=1)
            ],
            # The journey once, not once per option: it is the same journey
            # whichever option the client takes, and repeating it under each
            # would make the worksheet look as though it were charged more than
            # once (§3.10).
            journey=[
                self._line(one, currency)
                for one in self._journey_lines(raw_options)
            ],
            journey_note=(
                "Flights named and not priced: "
                + "; ".join(str(one) for one in journey.get("named") or [])
                if journey.get("named")
                else None
            ),
            rejected=[
                (
                    str(row.get("name") or ""),
                    str(row.get("reason") or ""),
                    str(row.get("source") or ""),
                )
                for row in snapshot.get("rejected") or []
            ],
            problems=[
                (
                    str(row.get("severity") or ""),
                    str(row.get("code") or ""),
                    str(row.get("message") or ""),
                )
                for row in (snapshot.get("readiness") or {}).get("problems") or []
            ],
        )

    # -- one option ---------------------------------------------------------- #

    def _option(self, raw: dict, index: int, currency: str) -> WorksheetOption:
        components = {
            label: Decimal(str(value))
            for label, value in (raw.get("components") or {}).items()
        }
        lines = raw.get("lines") or []
        groups: list[WorksheetGroup] = []
        # Known components in build-up order, then anything the snapshot has
        # that this module has not been taught — an unlisted component must not
        # vanish from the worksheet, which is the one place every cost has to
        # appear.
        seen_components = [one for one in COMPONENT_ORDER if self._any(lines, one)]
        for extra in dict.fromkeys(str(one.get("component") or "") for one in lines):
            if extra and extra not in seen_components:
                seen_components.append(extra)
        for component in seen_components:
            mine = [one for one in lines if one.get("component") == component]
            groups.append(
                WorksheetGroup(
                    component=component,
                    label=COMPONENT_LABELS.get(component, component.title()),
                    lines=[self._line(one, currency) for one in mine],
                    total=(
                        money(components[component], currency)
                        if component in components
                        # A component the build-up does not carry — the
                        # optional upgrades — is still totalled, so the ledger
                        # adds up, but only where its lines share one currency:
                        # summing across currencies here would invent a
                        # conversion the quote never made.
                        else self._own_total(mine)
                    ),
                )
            )

        def figure(name: str) -> Decimal:
            return Decimal(str(raw.get(name) or "0"))

        cost_subtotal = figure("cost_subtotal")
        contingency = figure("contingency_value")
        profit = figure("profit_value")
        agent_fee = figure("agent_cover_fee")
        retained = figure("retained_discount")
        group_total = figure("group_total")
        per_person = raw.get("per_person")

        build_up = [
            WorksheetFigure("Cost subtotal", money(cost_subtotal, currency) or ""),
            WorksheetFigure(
                "Contingency",
                money(contingency, currency) or "",
                note="inside the cost basis, so profit accrues on it",
            ),
            WorksheetFigure(
                "Cost basis", money(cost_subtotal + contingency, currency) or ""
            ),
            WorksheetFigure("Profit", money(profit, currency) or ""),
            WorksheetFigure(
                "Agent cover fee",
                money(agent_fee, currency) or "",
                note="added after profit and never marked up",
            ),
            WorksheetFigure(
                "Selling total",
                money(cost_subtotal + contingency + profit + agent_fee, currency)
                or "",
            ),
            WorksheetFigure(
                "Per person",
                money(per_person, currency) or "—",
                note=(
                    None
                    if per_person
                    else "not shown: the group is not uniform, so no single "
                    "figure is true for everyone"
                ),
            ),
            WorksheetFigure(
                "Group total",
                money(group_total, currency) or "",
                note="per person rounded up first, then multiplied out",
            ),
        ]
        # Realised margin is the three numbers §3.5 insists on tracking apart,
        # correctly added back together: on a discounted rack rate the retained
        # half is margin on top of the profit percentage, and calling the costed
        # figure "cost" would understate it by exactly that amount.
        margin = [
            WorksheetFigure("Profit", money(profit, currency) or "", is_margin=True),
            WorksheetFigure(
                "Contingency", money(contingency, currency) or "", is_margin=True
            ),
            WorksheetFigure(
                "Retained half-discount",
                money(retained, currency) or "",
                is_margin=True,
                note="half the supplier concession, passed to no one (§3.5)",
            ),
            WorksheetFigure(
                "Realised margin",
                money(profit + contingency + retained, currency) or "",
                is_margin=True,
            ),
            WorksheetFigure(
                "Paid to the properties",
                money(figure("supplier_paid_total"), currency) or "",
                # Accommodation only, and saying so matters: this is the figure
                # a discounted rack rate makes differ from the costed one
                # (§3.5). Fees, transport and the chef are paid at the stated
                # cost, so they carry no such gap and are not in it.
                note="accommodation only — the rate the property invoices",
            ),
        ]

        return WorksheetOption(
            number=f"{index:02d}",
            name=str(raw.get("accommodation_name") or ""),
            route=" → ".join(
                str(leg.get("destination_name") or leg.get("accommodation_name") or "")
                for leg in sorted(
                    raw.get("legs") or [], key=lambda leg: leg.get("sequence") or 0
                )
            ),
            is_recommended=bool(raw.get("is_recommended")),
            is_comparable=bool(raw.get("is_comparable", True)),
            room_type=str(raw.get("room_type_name") or ""),
            meal_plan=str(raw.get("meal_plan_name") or raw.get("meal_plan_code") or ""),
            meal_plan_fallback_from=raw.get("meal_plan_fallback_from"),
            rooms_required=raw.get("rooms_required"),
            nights=raw.get("nights"),
            groups=groups,
            build_up=build_up,
            margin=margin,
            cohorts=[
                WorksheetCohort(
                    label=(
                        f"{row.get('residence_label') or row.get('residence')} · "
                        f"{row.get('traveller_type')}"
                    ),
                    headcount=int(row.get("headcount") or 0),
                    per_person=money(
                        row.get("per_person"), str(row.get("currency") or "")
                    )
                    or "",
                    total=money(row.get("total"), str(row.get("currency") or "")) or "",
                )
                for row in raw.get("cohorts") or []
            ],
            conversions=[
                WorksheetFigure(pair, str(rate))
                for pair, rate in (raw.get("conversions") or {}).items()
            ],
            warnings=[str(one) for one in raw.get("warnings") or []],
        )

    # -- helpers ------------------------------------------------------------- #

    @staticmethod
    def _own_total(lines: list[dict]) -> str | None:
        currencies = {str(one.get("currency") or "").upper() for one in lines}
        if len(currencies) != 1:
            return None
        total = sum(
            (Decimal(str(one.get("extended") or "0")) for one in lines), Decimal(0)
        )
        return money(total, currencies.pop())

    @staticmethod
    def _any(lines: list, component: str) -> bool:
        return any(one.get("component") == component for one in lines)

    @staticmethod
    def _journey_lines(raw_options: list[dict]) -> list[dict]:
        """The transport lines, taken from one option rather than every option.

        They are identical across options by construction, and printing them
        once is what stops the worksheet reading as though the journey were
        charged several times.
        """
        for raw in raw_options:
            # Both components: the journey as arranged includes the upgrades,
            # which carry "(optional)" in their own label. What they must not
            # do is sit inside the package subtotal, which is why they are a
            # separate component down in the option's ledger.
            journey = [
                one
                for one in (raw.get("lines") or [])
                if one.get("component") in {"transport", "transport_optional"}
            ]
            if journey:
                return journey
        return []

    @staticmethod
    def _line(raw: dict, currency: str) -> WorksheetLine:
        source_currency = str(raw.get("currency") or currency).upper()
        who = " · ".join(
            part
            for part in (raw.get("residence"), raw.get("traveller_type"))
            if part
        )
        return WorksheetLine(
            label=str(raw.get("label") or ""),
            basis=str(raw.get("basis") or ""),
            unit_amount=money(raw.get("unit_amount"), source_currency) or "",
            quantity=int(raw.get("quantity") or 0),
            extended=money(raw.get("extended"), source_currency) or "",
            source=str(raw.get("source") or ""),
            who=who or "whole group",
            leg=raw.get("leg"),
            sheet_amount=money(raw.get("sheet_amount"), source_currency),
            paid_amount=money(raw.get("paid_amount"), source_currency),
        )


def worksheet_filename(quote_number: str, version_number: int) -> str:
    return f"{quote_number}-v{version_number}-worksheet.html"


__all__ = [
    "Worksheet",
    "WorksheetBuilder",
    "WorksheetCohort",
    "WorksheetFigure",
    "WorksheetGroup",
    "WorksheetLine",
    "WorksheetOption",
    "worksheet_filename",
]
