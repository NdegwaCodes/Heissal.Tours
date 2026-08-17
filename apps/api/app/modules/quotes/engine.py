"""PricingEngine (Stage 2.8) — turns an assembled quote request into a costed,
priced breakdown.

Algorithm (design §4): resolve each cost line in its **source** currency from
configurable rate data (never a guessed price), convert every line to the quote's
**presentation** currency via the ExchangeRate service, sum the internal cost,
then apply markup → discount → tax to get the selling price, profit and margin.

DB access is confined to rate/FX lookups; the money math lives in pure helpers
(:mod:`app.modules.pricing.service` + the small helpers here) so it is unit
testable and deterministic. Missing rates raise — a line is never silently zeroed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.accommodations.service import AccommodationRateService
from app.modules.activities.service import ActivityRateService, compute_activity_cost
from app.modules.currency.fx import AdminExchangeRateProvider
from app.modules.park_fees.service import ParkFeeService, classify_age
from app.modules.pricing.service import PricingConfigService, compute_price_breakdown
from app.modules.vehicles.models import Vehicle
from app.modules.vehicles.service import FuelPriceService, compute_transport_cost

# --------------------------------------------------------------------------- #
# Normalised, source-agnostic inputs (built from a request OR a saved quote).
# --------------------------------------------------------------------------- #

@dataclass
class TravellerInput:
    traveller_type: str
    age: int | None = None


@dataclass
class AccommodationInput:
    accommodation_id: uuid.UUID
    room_type_id: uuid.UUID
    meal_plan_id: uuid.UUID
    rooms: int
    nights: int


@dataclass
class ActivityInput:
    activity_id: uuid.UUID
    adults: int
    children: int


@dataclass
class LegInput:
    destination_id: uuid.UUID
    nights: int
    check_in: date | None
    accommodations: list[AccommodationInput] = field(default_factory=list)
    activities: list[ActivityInput] = field(default_factory=list)


@dataclass
class TransportInput:
    vehicle_id: uuid.UUID
    estimated_km: Decimal
    days: int


@dataclass
class PricingInputs:
    residence_category_id: uuid.UUID
    presentation_currency: str
    arrival_date: date
    departure_date: date
    markup_pct: Decimal | None
    discount_pct: Decimal | None
    tax_pct: Decimal | None
    travellers: list[TravellerInput]
    legs: list[LegInput]
    transport: list[TransportInput]


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #

def compute_accommodation_cost(
    *, rate_per_night: Decimal, rooms: int, nights: int
) -> Decimal:
    """Core accommodation cost: rooms × nights × rate_per_night.

    child_rate / single_supplement are deliberately not applied here: the quote's
    accommodation selection captures rooms + nights, not per-room occupancy, so
    applying them would require occupancy data the model does not yet carry. That
    refinement is tracked for a later iteration rather than guessed at.
    """
    return rate_per_night * rooms * nights


def classify_group(
    travellers: list[TravellerInput], child_min_age: int, child_max_age: int
) -> dict[str, int]:
    """Count adults/children/infants using a fee's own age bounds.

    A traveller with a known age is classified by the bounds; one without an age
    falls back to its declared type.
    """
    counts = {"adult": 0, "child": 0, "infant": 0}
    for t in travellers:
        if t.age is not None:
            counts[classify_age(t.age, child_min_age, child_max_age)] += 1
        elif t.traveller_type in counts:
            counts[t.traveller_type] += 1
        else:
            counts["adult"] += 1
    return counts


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #

class PricingEngine:
    #: Park-entry fee type auto-derived per leg destination (skipped if none set).
    PARK_FEE_TYPE = "park_entry"

    def __init__(self, db: AsyncSession):
        self.db = db
        self.fx = AdminExchangeRateProvider(db)
        self.acc_rates = AccommodationRateService(db)
        self.park_fees = ParkFeeService(db)
        self.activity_rates = ActivityRateService(db)
        self.fuel_prices = FuelPriceService(db)

    async def compute(self, inputs: PricingInputs) -> dict:
        cfg = await PricingConfigService(self.db).get()
        currency = inputs.presentation_currency.upper()
        fx_date = inputs.arrival_date

        raw_lines: list[dict] = []
        for leg in inputs.legs:
            rate_date = leg.check_in or inputs.arrival_date
            raw_lines.extend(await self._accommodation_lines(leg, inputs, rate_date))
            park_line = await self._park_fee_line(leg, inputs, rate_date)
            if park_line is not None:
                raw_lines.append(park_line)
            raw_lines.extend(await self._activity_lines(leg, inputs, rate_date))
        raw_lines.extend(await self._transport_lines(inputs))

        # Convert every line to the presentation currency.
        internal_cost = Decimal("0")
        lines: list[dict] = []
        for ln in raw_lines:
            converted = await self.fx.convert(
                ln["internal_cost_source"], ln["source_currency"], currency, fx_date
            )
            internal_cost += converted
            lines.append({**ln, "internal_cost": converted})

        markup_pct = inputs.markup_pct if inputs.markup_pct is not None else cfg.default_markup_pct
        discount_pct = (
            inputs.discount_pct if inputs.discount_pct is not None else cfg.default_discount_pct
        )
        tax_pct = inputs.tax_pct if inputs.tax_pct is not None else cfg.default_tax_pct

        breakdown = compute_price_breakdown(
            internal_cost,
            markup_pct=markup_pct,
            discount_pct=discount_pct,
            tax_pct=tax_pct,
            discount_approval_threshold_pct=cfg.discount_approval_threshold_pct,
        )

        # Per-line client price = line internal × (1 + markup); sums to subtotal.
        markup_factor = Decimal(1) + markup_pct / Decimal("100")
        for ln in lines:
            ln["client_price"] = ln["internal_cost"] * markup_factor

        return {
            "presentation_currency": currency,
            "lines": lines,
            "markup_pct": markup_pct,
            "discount_pct": discount_pct,
            "tax_pct": tax_pct,
            "internal_cost": breakdown["internal_cost"],
            "selling_subtotal": breakdown["selling_subtotal"],
            "discount_value": breakdown["discount_value"],
            "after_discount": breakdown["after_discount"],
            "tax": breakdown["tax"],
            "selling_price": breakdown["selling_price"],
            "gross_profit": breakdown["gross_profit"],
            "gross_margin": breakdown["gross_margin"],
            "needs_approval": breakdown["needs_approval"],
        }

    # -- line builders ------------------------------------------------------- #

    async def _accommodation_lines(
        self, leg: LegInput, inputs: PricingInputs, rate_date: date
    ) -> list[dict]:
        out: list[dict] = []
        for sel in leg.accommodations:
            rate = await self.acc_rates.select_rate(
                room_type_id=sel.room_type_id,
                meal_plan_id=sel.meal_plan_id,
                residence_category_id=inputs.residence_category_id,
                stay_date=rate_date,
            )
            cost = compute_accommodation_cost(
                rate_per_night=rate.rate_per_night, rooms=sel.rooms, nights=sel.nights
            )
            out.append(
                {
                    "category": "accommodation",
                    "description": f"{sel.rooms} room(s) × {sel.nights} night(s) "
                    f"@ {rate.rate_per_night} {rate.currency} ({rate.season_name})",
                    "quantity": Decimal(sel.rooms * sel.nights),
                    "source_currency": rate.currency.upper(),
                    "internal_cost_source": cost,
                }
            )
        return out

    async def _park_fee_line(
        self, leg: LegInput, inputs: PricingInputs, rate_date: date
    ) -> dict | None:
        try:
            fee = await self.park_fees.select_fee(
                destination_id=leg.destination_id,
                fee_type=self.PARK_FEE_TYPE,
                residence_category_id=inputs.residence_category_id,
                on_date=rate_date,
            )
        except NotFoundError:
            return None  # Not every destination charges a park fee.

        counts = classify_group(inputs.travellers, fee.child_min_age, fee.child_max_age)
        total = (
            fee.adult * counts["adult"]
            + fee.child * counts["child"]
            + fee.infant * counts["infant"]
        ) * leg.nights
        return {
            "category": "park_fee",
            "description": f"Park fees × {leg.nights} day(s) "
            f"(A{counts['adult']}/C{counts['child']}/I{counts['infant']})",
            "quantity": Decimal(leg.nights),
            "source_currency": fee.currency.upper(),
            "internal_cost_source": total,
        }

    async def _activity_lines(
        self, leg: LegInput, inputs: PricingInputs, rate_date: date
    ) -> list[dict]:
        out: list[dict] = []
        for sel in leg.activities:
            rate = await self.activity_rates.select_rate(
                activity_id=sel.activity_id,
                residence_category_id=inputs.residence_category_id,
                on_date=rate_date,
            )
            result = compute_activity_cost(
                adult_price=rate.adult_price,
                child_price=rate.child_price,
                adults=sel.adults,
                children=sel.children,
            )
            out.append(
                {
                    "category": "activity",
                    "description": f"Activity for {sel.adults} adult(s), "
                    f"{sel.children} child(ren)",
                    "quantity": Decimal(sel.adults + sel.children),
                    "source_currency": rate.currency.upper(),
                    "internal_cost_source": result["total"],
                }
            )
        return out

    async def _transport_lines(self, inputs: PricingInputs) -> list[dict]:
        out: list[dict] = []
        for tr in inputs.transport:
            vehicle = await self.db.get(Vehicle, tr.vehicle_id)
            if vehicle is None:
                raise NotFoundError("Vehicle not found for transport line.")
            fuel = await self.fuel_prices.select_price(
                fuel_type=vehicle.fuel_type, on_date=inputs.arrival_date
            )
            calc = compute_transport_cost(
                distance_km=tr.estimated_km,
                consumption_kmpl=vehicle.fuel_consumption_kmpl,
                fuel_price_per_litre=fuel.price_per_litre,
                days=tr.days,
                driver_cost_per_day=vehicle.driver_cost_per_day,
                daily_operating_cost=vehicle.daily_operating_cost,
            )
            # Fuel is priced in the fuel-price currency; driver/operating in the
            # vehicle currency — kept as separate lines so FX is exact.
            out.append(
                {
                    "category": "transport_fuel",
                    "description": f"Fuel: {tr.estimated_km} km @ "
                    f"{vehicle.fuel_consumption_kmpl} km/L",
                    "quantity": calc["fuel_litres"],
                    "source_currency": fuel.currency.upper(),
                    "internal_cost_source": calc["fuel_cost"],
                }
            )
            out.append(
                {
                    "category": "transport_service",
                    "description": f"Driver + operating × {tr.days} day(s)",
                    "quantity": Decimal(tr.days),
                    "source_currency": vehicle.currency.upper(),
                    "internal_cost_source": calc["driver_total"] + calc["operating_total"],
                }
            )
        return out
