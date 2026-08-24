"""Pure option-pricing rules for the Stage 3 quotation (design doc §3.3-§3.6).

Every function here is deterministic and free of I/O, so the arithmetic that
decides what a client is charged can be tested exhaustively against the worked
examples in the design doc. The service layer does the lookups and calls into
this module; nothing here reads the database.

All money is ``Decimal`` and every stored figure is already VAT-inclusive (§3.2),
so nothing in this module adds tax.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, Decimal

# The chain to walk when a hotel has no rate for the plan the client asked for
# (§3.4). Bed and breakfast is the end of the line: below it a chef and a manual
# meal cost are needed, which is a human decision rather than a fallback.
MEAL_PLAN_FALLBACK: dict[str, tuple[str, ...]] = {
    "FB": ("FB", "HB", "BB"),
    "HB": ("HB", "FB", "BB"),
    "BB": ("BB", "HB", "FB"),
    "AI": ("AI", "FB", "HB", "BB"),
    "RO": ("RO", "BB", "HB", "FB"),
}

# Plans that already include the meals a guest needs, so no chef fee or manual
# food cost belongs on the option (§3.4).
PLANS_WITH_MEALS = frozenset({"FB", "AI"})


# --------------------------------------------------------------------------- #
# Rooming
# --------------------------------------------------------------------------- #


def rooms_required(pax: int, capacity: int) -> int:
    """``ceil(pax / capacity)`` — the §3.3 rule.

    25 guests in twins is 13 rooms; in a 4-guest villa it is 7.
    """
    if pax <= 0:
        raise ValueError("pax must be at least 1")
    if capacity <= 0:
        raise ValueError("room capacity must be at least 1")
    return -(-pax // capacity)


def room_plan(pax: int, capacity: int) -> list[int]:
    """How many guests sleep in each room, largest rooms first.

    Returns one entry per room, because the price of a room depends on how many
    people are in it (§3.3): 25 guests in twins is twelve rooms of two and one
    room of one, and that last room is charged at the supplier's single rate
    rather than half a double.
    """
    count = rooms_required(pax, capacity)
    full, remainder = divmod(pax, capacity)
    plan = [capacity] * full
    if remainder:
        plan.append(remainder)
    # Defensive: the two derivations must agree.
    assert len(plan) == count, (plan, count)
    return plan


# --------------------------------------------------------------------------- #
# Meal plans
# --------------------------------------------------------------------------- #


def meal_plan_chain(requested: str) -> tuple[str, ...]:
    """The plans to try, best first, for a requested plan."""
    return MEAL_PLAN_FALLBACK.get(requested.upper(), (requested.upper(),))


def resolve_meal_plan(requested: str, available: set[str]) -> tuple[str | None, bool]:
    """Pick the plan to price, and say whether it is a fallback.

    The flag matters as much as the choice: an option priced on a different board
    basis from the one asked for is not comparable with the others, and the sales
    agent has to be told rather than left to notice (§3.4).
    """
    wanted = requested.upper()
    for candidate in meal_plan_chain(wanted):
        if candidate in available:
            return candidate, candidate != wanted
    return None, False


def needs_chef(plan: str) -> bool:
    """Whether an option priced on ``plan`` needs a chef and a manual food cost.

    Only bed-and-breakfast and room-only options do. A chef is never added to a
    half-board or full-board option (§3.4).
    """
    return plan.upper() not in PLANS_WITH_MEALS and plan.upper() != "HB"


# --------------------------------------------------------------------------- #
# Supplier discounts and STO rates (§3.5)
# --------------------------------------------------------------------------- #


def supplier_paid(rate: Decimal, discount_pct: Decimal | None) -> Decimal:
    """What Heissal pays the hotel: the sheet rate less the whole discount.

    The full stated percentage always applies here, whatever the rate kind —
    that is what makes the sheet "our rate".
    """
    if discount_pct is None or discount_pct == 0:
        return rate
    return rate * (Decimal(1) - discount_pct / Decimal(100))


def costed_rate(rate: Decimal, discount_pct: Decimal | None, rate_kind: str) -> Decimal:
    """The figure that enters the client-facing build-up (§3.5).

    An STO sheet is already an operator rate, so the paid figure is used as-is.
    A rack rate with a stated discount passes **half** the concession to the
    client and keeps the other half as margin.
    """
    if rate_kind == "sto":
        return supplier_paid(rate, discount_pct)
    if discount_pct is None or discount_pct == 0:
        return rate
    return rate * (Decimal(1) - (discount_pct / Decimal(2)) / Decimal(100))


def retained_discount(
    rate: Decimal, discount_pct: Decimal | None, rate_kind: str, *, units: int = 1
) -> Decimal:
    """The part of a discount kept rather than passed on, as margin.

    Tracked separately because realised margin on a discounted rack option is
    the profit percentage *plus* contingency *plus* this (§3.6).
    """
    paid = supplier_paid(rate, discount_pct)
    costed = costed_rate(rate, discount_pct, rate_kind)
    return (costed - paid) * units


# --------------------------------------------------------------------------- #
# Supplements (§3.5a)
# --------------------------------------------------------------------------- #


def nights_within(
    stay_start: date, stay_end: date, window_start: date, window_end: date
) -> int:
    """Nights of a stay that fall inside a supplement's window.

    A stay is counted by nights, so the night of ``stay_end`` is not one: a
    guest checking out on the 26th did not sleep there that night.
    """
    first = max(stay_start, window_start)
    last = min(stay_end, window_end + _ONE_DAY)
    return max(0, (last - first).days)


_ONE_DAY = date(2000, 1, 2) - date(2000, 1, 1)


def supplement_cost(
    *,
    amount: Decimal,
    basis: str,
    pax: int,
    rooms: int,
    nights: int,
) -> Decimal:
    """What a supplement adds, given its charging basis.

    The basis is not decoration: the same 3,300 is a very different number per
    person per night than per room, and the sheets use both.
    """
    if nights <= 0:
        return Decimal(0)
    if basis == "per_person_per_night":
        return amount * pax * nights
    if basis == "per_person":
        return amount * pax
    if basis == "per_room_per_night":
        return amount * rooms * nights
    if basis == "per_room":
        return amount * rooms
    raise ValueError(f"unknown supplement basis: {basis!r}")


# --------------------------------------------------------------------------- #
# Minimum stay (§3.3a)
# --------------------------------------------------------------------------- #


def meets_minimum_stay(nights: int, min_nights: int | None) -> bool:
    """Whether a stay satisfies a rate's minimum.

    Checked against the whole stay rather than the nights inside the restricted
    window: a three-night booking fails a four-night minimum even if only one of
    those nights falls in the restricted period.
    """
    return min_nights is None or nights >= min_nights


def minimum_stay_reason(nights: int, min_nights: int) -> str:
    """Client-facing wording for a property declined on minimum stay.

    This text is printed on the quotation (§3.3a), so it says only what is safe
    to show a client — never a cost or a commercial reason.
    """
    plural = "night" if min_nights == 1 else "nights"
    return (
        f"Requires a minimum stay of {min_nights} {plural}; "
        f"this itinerary is {nights} {'night' if nights == 1 else 'nights'}."
    )


# --------------------------------------------------------------------------- #
# The build-up (§3.6)
# --------------------------------------------------------------------------- #


def round_up_to(value: Decimal, step: Decimal) -> Decimal:
    """Round ``value`` up to the next multiple of ``step``."""
    if step <= 0:
        raise ValueError("rounding step must be positive")
    return (value / step).quantize(Decimal(1), rounding=ROUND_CEILING) * step


@dataclass(frozen=True)
class BuildUp:
    """Every figure behind one option's price. All backend-only except the last two."""

    cost_subtotal: Decimal
    contingency_value: Decimal
    cost_basis: Decimal
    profit_value: Decimal
    after_profit: Decimal
    agent_cover_fee: Decimal
    selling_total: Decimal
    per_person: Decimal | None
    group_total: Decimal
    components: dict[str, Decimal] = field(default_factory=dict)


def build_up(
    *,
    components: dict[str, Decimal],
    pax: int,
    contingency_pct: Decimal,
    profit_pct: Decimal,
    agent_cover_fee: Decimal = Decimal(0),
    rounding_step: Decimal = Decimal("100"),
    uniform_group: bool = True,
) -> BuildUp:
    """Turn cost components into a selling price (§3.6).

    Two orderings matter and both are deliberate:

    * **Contingency is inside the cost basis**, so profit accrues on it.
    * **The agent cover fee is added after profit**, so it reaches the client at
      face value and is never marked up.

    The per-person figure is computed and rounded up *first*, then multiplied
    back out to the group total. Rounding a group total and dividing instead is
    what makes the sample quotation contradict itself — page 6 says 28,800 per
    person against a 720,000 total that implies 28,400.

    ``uniform_group=False`` suppresses the per-person figure entirely, because it
    is meaningless when travellers are not all paying the same: a mixed-residency
    or adult-plus-child group is quoted as a total (§3.6).
    """
    if pax <= 0:
        raise ValueError("pax must be at least 1")
    for name, value in components.items():
        if value < 0:
            raise ValueError(f"cost component {name!r} is negative")
    if agent_cover_fee < 0:
        raise ValueError("agent cover fee cannot be negative")

    cost_subtotal = sum(components.values(), Decimal(0))
    contingency_value = cost_subtotal * contingency_pct / Decimal(100)
    cost_basis = cost_subtotal + contingency_value
    profit_value = cost_basis * profit_pct / Decimal(100)
    after_profit = cost_basis + profit_value
    selling_total = after_profit + agent_cover_fee

    if uniform_group:
        per_person = round_up_to(selling_total / pax, rounding_step)
        group_total = per_person * pax
    else:
        per_person = None
        group_total = round_up_to(selling_total, rounding_step)

    return BuildUp(
        cost_subtotal=cost_subtotal,
        contingency_value=contingency_value,
        cost_basis=cost_basis,
        profit_value=profit_value,
        after_profit=after_profit,
        agent_cover_fee=agent_cover_fee,
        selling_total=selling_total,
        per_person=per_person,
        group_total=group_total,
        components=dict(components),
    )
