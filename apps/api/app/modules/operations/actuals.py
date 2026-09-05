"""What a trip actually cost, against what it was priced at. Pure (§8.2).

Every quote this platform has ever produced charges a drive from two numbers on
a ``vehicles`` row: a distance from the route table (§4.2) and a
``fuel_consumption_kmpl`` somebody typed once. §8.1 committed a real vehicle to
a real trip. Nothing has ever recorded what that vehicle **did** — so the
figure at the centre of every transport line has never once been checked
against a fuel receipt.

That is the gap this closes, and it is a different shape from the ones before
it. The earlier stages found columns nothing could fill; this one finds a
number nothing could ever *disprove*. A quoted 8.5 km/L that is really 6.9
under-costs every safari by a fifth, quietly, for as long as nobody measures.

So, three things.

**Distance, from the odometer.** Out and in, per vehicle per trip. Which also
gives the thing an odometer is uniquely good at: the kilometres **between**
trips. A vehicle that comes back at 84,300 and leaves next time at 84,910 did
610 km that no trip paid for, and that is either repositioning or somebody's
weekend.

**Fuel, from receipts.** Litres and money, and never a litre price derived by
division — a receipt says both, and inferring one from the other loses the
partial fill and the price change mid-trip.

**The variance, reported and never applied.** This module will tell you that a
vehicle's model says 8.5 and its last nine trips say 6.9. It will not change
the 8.5. That number is a live pricing input: moving it re-prices work in
flight, and the decision to accept a fortnight of receipts as the new truth
belongs to whoever will explain the margin.

And what is deliberately **not** measured: the driver's day rate and the daily
operating cost. Neither is observable from a trip sheet — one is payroll and
the other is amortised depreciation, tyres and insurance — and a variance
computed from a guess would be worse than the silence, because it would look
like evidence.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

#: Anything below this and a consumption figure is arithmetic on noise: a
#: 40 km airport transfer with one tankful tells you nothing about a vehicle.
MIN_KM_FOR_CONSUMPTION = Decimal("100")

#: How far out the model has to be before it is worth saying so. Below this and
#: the report would cry about a difference a hill and a headwind explain.
DEFAULT_TOLERANCE_PCT = Decimal("10")

# What is wrong, or worth knowing.
UNACCOUNTED_KM = "vehicle_unaccounted_km"
MODEL_OPTIMISTIC = "vehicle_consumption_optimistic"
MODEL_PESSIMISTIC = "vehicle_consumption_pessimistic"
NOT_ENOUGH_DATA = "vehicle_not_enough_data"


class LogRefused(ValueError):
    """A reading or a receipt the rules will not accept, with the reason."""


# --------------------------------------------------------------------------- #
# Distance
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Odometer:
    """One trip's readings, as the rules see them."""

    out_km: Decimal
    in_km: Decimal | None = None

    @property
    def distance(self) -> Decimal | None:
        """Kilometres covered, or ``None`` while the vehicle is still out."""
        if self.in_km is None:
            return None
        return self.in_km - self.out_km


def check_reading(
    reading: Odometer, *, previous_in: Decimal | None = None
) -> list[str]:
    """Whether a pair of readings can be true. Refusals, then observations.

    An odometer does not run backwards, so an ``in`` below the ``out`` is a
    typed digit rather than a fact — refused, because accepting it would put a
    negative distance into a fleet average and quietly poison every figure
    derived from it.

    A gap **above** the last trip's closing reading is not an error: vehicles
    get repositioned, serviced and taken home. It is returned as an observation
    so somebody can decide which of those it was.
    """
    if reading.out_km < 0 or (reading.in_km is not None and reading.in_km < 0):
        raise LogRefused("An odometer reading cannot be negative.")
    if reading.in_km is not None and reading.in_km < reading.out_km:
        raise LogRefused(
            f"The vehicle came back on {_plain(reading.in_km)} km having left "
            f"on {_plain(reading.out_km)}. An odometer does not run backwards "
            f"— check the digits."
        )
    if previous_in is not None and reading.out_km < previous_in:
        raise LogRefused(
            f"This trip leaves on {_plain(reading.out_km)} km and the vehicle's "
            f"last trip came back on {_plain(previous_in)}. One of the two "
            f"readings is wrong."
        )
    notes: list[str] = []
    if previous_in is not None and reading.out_km > previous_in:
        gap = reading.out_km - previous_in
        notes.append(
            f"{_plain(gap)} km since the vehicle last came back, on no trip. "
            f"Repositioning, a service run, or somebody's weekend — worth "
            f"knowing which."
        )
    return notes


# --------------------------------------------------------------------------- #
# Fuel
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Fill:
    """One fuel purchase. Litres **and** money, both from the receipt."""

    litres: Decimal
    amount: Decimal
    currency: str
    bought_on: date | None = None

    @property
    def price_per_litre(self) -> Decimal | None:
        if self.litres <= 0:
            return None
        return (self.amount / self.litres).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )


def check_fill(fill: Fill) -> None:
    """Whether a receipt can be recorded.

    Both figures are required and neither is derived from the other. A litre
    price computed by division looks like the same information and is not: it
    loses the partial fill, the price that changed halfway through the trip,
    and the pump that was charging a premium out at Voi — which is exactly the
    detail somebody is looking for when they ask why a trip cost what it did.
    """
    if fill.litres <= 0:
        raise LogRefused(
            "A fuel purchase has to have litres on it. A receipt with only a "
            "shilling figure cannot tell you anything about consumption."
        )
    if fill.amount <= 0:
        raise LogRefused("A fuel purchase has to have an amount on it.")
    if len((fill.currency or "").strip()) != 3:
        raise LogRefused(
            "A fuel purchase needs a currency. Money is an amount and a "
            "currency here as everywhere else."
        )


def fuel_total(fills: Sequence[Fill]) -> tuple[Decimal, Decimal, str]:
    """Litres and money across a trip's receipts, refusing mixed currencies.

    Refused rather than converted, for §7.1's reason about a payment: what the
    pump charged is a fact and the exchange rate is a decision. A cross-border
    run that fuelled in Tanzania needs two lines on the report, not one wrong
    one.
    """
    if not fills:
        return Decimal(0), Decimal(0), ""
    currencies = {fill.currency.upper() for fill in fills}
    if len(currencies) > 1:
        raise LogRefused(
            "This trip's fuel was bought in "
            + ", ".join(sorted(currencies))
            + ". Totalling them would mean choosing an exchange rate, which is "
            "a decision and not arithmetic."
        )
    return (
        sum((fill.litres for fill in fills), Decimal(0)),
        sum((fill.amount for fill in fills), Decimal(0)),
        currencies.pop(),
    )


# --------------------------------------------------------------------------- #
# What it actually did
# --------------------------------------------------------------------------- #


@dataclass
class Actual:
    """One trip's measured cost, beside what the model would have predicted."""

    distance_km: Decimal | None = None
    litres: Decimal = Decimal(0)
    fuel_cost: Decimal = Decimal(0)
    currency: str = ""
    #: The vehicle's configured figure — the one every quote is priced on.
    model_kmpl: Decimal | None = None
    #: What it actually managed. ``None`` where the trip is too short for the
    #: number to mean anything, or the vehicle is still out.
    actual_kmpl: Decimal | None = None

    @property
    def variance_pct(self) -> Decimal | None:
        """How far the model is out, as a percentage of itself.

        Positive means the vehicle did **better** than the model, which is the
        harmless direction: the quote over-charged for fuel. Negative is the
        one that costs money.
        """
        if not self.model_kmpl or self.actual_kmpl is None:
            return None
        return (
            (self.actual_kmpl - self.model_kmpl) / self.model_kmpl * 100
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def model_litres(self) -> Decimal | None:
        """What the model says this distance should have taken."""
        if not self.model_kmpl or self.distance_km is None:
            return None
        return (self.distance_km / self.model_kmpl).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )


def measure(
    *,
    odometer: Odometer | None,
    fills: Sequence[Fill],
    model_kmpl: Decimal | None,
    min_km: Decimal = MIN_KM_FOR_CONSUMPTION,
) -> Actual:
    """Fold a trip's readings and receipts into one row of truth.

    Consumption is left as ``None`` below ``min_km``. A 40 km airport transfer
    with one tankful is arithmetic on noise, and publishing it as "3.1 km/L"
    would put a number nobody believes next to nine that they should.
    """
    litres, cost, currency = fuel_total(fills)
    distance = odometer.distance if odometer is not None else None
    out = Actual(
        distance_km=distance,
        litres=litres,
        fuel_cost=cost,
        currency=currency,
        model_kmpl=model_kmpl,
    )
    if distance is not None and distance >= min_km and litres > 0:
        out.actual_kmpl = (distance / litres).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    return out


# --------------------------------------------------------------------------- #
# Is the model true?
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    """Something the receipts say about a vehicle, and never an instruction."""

    code: str
    message: str
    trips: int = 0
    model_kmpl: Decimal | None = None
    actual_kmpl: Decimal | None = None
    variance_pct: Decimal | None = None


@dataclass
class FleetTruth:
    """One vehicle's measured consumption over a run of trips."""

    vehicle: str = ""
    trips: int = 0
    distance_km: Decimal = Decimal(0)
    litres: Decimal = Decimal(0)
    fuel_cost: Decimal = Decimal(0)
    currency: str = ""
    model_kmpl: Decimal | None = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def actual_kmpl(self) -> Decimal | None:
        if self.litres <= 0 or self.distance_km <= 0:
            return None
        return (self.distance_km / self.litres).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )


def audit(
    vehicle: str,
    actuals: Iterable[Actual],
    *,
    model_kmpl: Decimal | None,
    tolerance_pct: Decimal = DEFAULT_TOLERANCE_PCT,
    min_trips: int = 3,
) -> FleetTruth:
    """What a run of trips says about the number every quote is priced on.

    Pooled rather than averaged per trip: total kilometres over total litres is
    the figure a fleet manager means, and a mean of per-trip ratios would let
    one 120 km transfer weigh as much as a 1,400 km circuit.

    It **reports**. It does not change ``fuel_consumption_kmpl``: that is a
    live pricing input, moving it re-prices work in flight, and deciding that a
    fortnight of receipts is the new truth belongs to whoever will have to
    explain the margin. Under ``min_trips`` it says so rather than concluding
    from two.
    """
    measured = [
        one
        for one in actuals
        if one.distance_km is not None and one.distance_km > 0 and one.litres > 0
    ]
    out = FleetTruth(vehicle=vehicle, model_kmpl=model_kmpl)
    currencies = {one.currency for one in measured if one.currency}
    out.currency = currencies.pop() if len(currencies) == 1 else ""
    for one in measured:
        out.trips += 1
        out.distance_km += one.distance_km or Decimal(0)
        out.litres += one.litres
        if out.currency and one.currency == out.currency:
            out.fuel_cost += one.fuel_cost

    actual = out.actual_kmpl
    if out.trips < min_trips or actual is None:
        out.findings.append(
            Finding(
                NOT_ENOUGH_DATA,
                f"{vehicle} has {out.trips} measured trip(s). Not enough to say "
                f"anything about the model figure — two trips and a hill is not "
                f"a pattern.",
                trips=out.trips,
                model_kmpl=model_kmpl,
                actual_kmpl=actual,
            )
        )
        return out

    if not model_kmpl:
        return out
    variance = ((actual - model_kmpl) / model_kmpl * 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    if abs(variance) < tolerance_pct:
        return out

    shortfall = abs(variance).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if variance < 0:
        out.findings.append(
            Finding(
                MODEL_OPTIMISTIC,
                f"{vehicle} is priced at {_plain(model_kmpl)} km/L and has "
                f"managed {_plain(actual)} over {out.trips} trips "
                f"({_plain(out.distance_km)} km). Every quote using it is "
                f"under-costing fuel by about {_plain(shortfall)}%. Somebody "
                f"has to decide whether that is the new truth — nothing here "
                f"changes it.",
                trips=out.trips,
                model_kmpl=model_kmpl,
                actual_kmpl=actual,
                variance_pct=variance,
            )
        )
    else:
        out.findings.append(
            Finding(
                MODEL_PESSIMISTIC,
                f"{vehicle} is priced at {_plain(model_kmpl)} km/L and has "
                f"managed {_plain(actual)} over {out.trips} trips. Quotes using "
                f"it carry about {_plain(shortfall)}% more fuel than it burns — "
                f"harmless to the margin, and it is losing work on price.",
                trips=out.trips,
                model_kmpl=model_kmpl,
                actual_kmpl=actual,
                variance_pct=variance,
            )
        )
    return out


def _plain(value: Decimal) -> str:
    """A decimal without its trailing zeros. §4.2's ``routing.plain``, again.

    Not imported from there: a fuel report reaching into the routing module for
    a formatter would be a dependency between two things that have nothing to
    do with each other.
    """
    return format(value.normalize(), "f")


__all__ = [
    "DEFAULT_TOLERANCE_PCT",
    "MIN_KM_FOR_CONSUMPTION",
    "MODEL_OPTIMISTIC",
    "MODEL_PESSIMISTIC",
    "NOT_ENOUGH_DATA",
    "UNACCOUNTED_KM",
    "Actual",
    "Fill",
    "Finding",
    "FleetTruth",
    "LogRefused",
    "Odometer",
    "audit",
    "check_fill",
    "check_reading",
    "fuel_total",
    "measure",
]
