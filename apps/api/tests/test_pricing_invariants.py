"""Stage 2.9 — invariant & edge-case tests for the pure pricing math.

These pin properties that must hold for *every* input, not one worked example:
money stays exact ``Decimal``, the breakdown identities always balance, discounts
can never go negative or exceed the subtotal, and age classification is correct
on its exact boundaries. No DB, no I/O — these run anywhere.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.activities.service import compute_activity_cost
from app.modules.park_fees.service import classify_age
from app.modules.pricing.service import (
    apply_discount,
    apply_markup,
    apply_tax,
    compute_price_breakdown,
)
from app.modules.quotes.engine import (
    TravellerInput,
    classify_group,
    compute_accommodation_cost,
)
from app.modules.vehicles.service import compute_transport_cost

D = Decimal

# A spread of costs/percentages the identities below must hold for.
COSTS = [D("0"), D("0.01"), D("1"), D("3400"), D("1234567.8901")]
MARKUPS = [D("0"), D("10"), D("25"), D("33.333"), D("100")]
DISCOUNTS = [None, D("0"), D("5"), D("50"), D("100")]
TAXES = [D("0"), D("16"), D("7.5")]


# --------------------------------------------------------------------------- #
# Breakdown identities — must balance for every combination.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("cost", COSTS)
@pytest.mark.parametrize("markup", MARKUPS)
@pytest.mark.parametrize("discount", DISCOUNTS)
@pytest.mark.parametrize("tax", TAXES)
def test_breakdown_identities_hold(cost, markup, discount, tax):
    b = compute_price_breakdown(
        cost, markup_pct=markup, discount_pct=discount, tax_pct=tax
    )
    # Every step composes exactly — no rounding is applied in the math layer.
    assert b["selling_subtotal"] == cost * (D(1) + markup / D(100))
    assert b["after_discount"] == b["selling_subtotal"] - b["discount_value"]
    assert b["tax"] == b["after_discount"] * tax / D(100)
    assert b["selling_price"] == b["after_discount"] + b["tax"]
    assert b["gross_profit"] == b["selling_price"] - b["internal_cost"]
    # A discount is never negative and never exceeds what it discounts.
    assert D(0) <= b["discount_value"] <= b["selling_subtotal"]
    # Margin is a fraction, and is 0 (not an error) when nothing is being sold.
    assert b["gross_margin"] <= 1
    if b["selling_price"] == 0:
        assert b["gross_margin"] == 0
    else:
        assert b["gross_margin"] == b["gross_profit"] / b["selling_price"]


@pytest.mark.parametrize("cost", COSTS)
@pytest.mark.parametrize("markup", MARKUPS)
@pytest.mark.parametrize("discount", DISCOUNTS)
@pytest.mark.parametrize("tax", TAXES)
def test_every_money_field_is_decimal(cost, markup, discount, tax):
    """Money must never degrade to float anywhere in the breakdown."""
    b = compute_price_breakdown(
        cost, markup_pct=markup, discount_pct=discount, tax_pct=tax
    )
    for key, value in b.items():
        if key == "needs_approval":
            assert isinstance(value, bool)
        else:
            assert isinstance(value, Decimal), f"{key} is {type(value).__name__}, not Decimal"


def test_decimal_math_is_exact_where_float_would_drift():
    """0.1 x 3 must be exactly 0.3 — the reason money is Decimal, not float."""
    assert compute_accommodation_cost(rate_per_night=D("0.1"), rooms=1, nights=3) == D("0.3")
    assert 0.1 * 3 != 0.3  # the float trap this project avoids
    assert apply_tax(D("0.05"), D("10")) == D("0.005")
    assert apply_markup(D("100"), D("33.333")) == D("133.333")


# --------------------------------------------------------------------------- #
# Discount edges
# --------------------------------------------------------------------------- #

def test_full_discount_zeroes_the_sale_without_error():
    b = compute_price_breakdown(
        D("3400"), markup_pct=D("25"), discount_pct=D("100"), tax_pct=D("16")
    )
    assert b["discount_value"] == D("4250")
    assert b["after_discount"] == 0
    assert b["tax"] == 0
    assert b["selling_price"] == 0
    # Selling at zero makes the whole internal cost a loss, and margin guards /0.
    assert b["gross_profit"] == D("-3400")
    assert b["gross_margin"] == 0


def test_fixed_discount_is_clamped_to_the_subtotal():
    # A 9 999 discount on a 1 250 subtotal cannot produce a negative price.
    assert apply_discount(D("1250"), discount_amount=D("9999")) == D("1250")
    assert apply_discount(D("1250"), discount_amount=D("0")) == 0
    assert apply_discount(D("0"), discount_amount=D("50")) == 0


def test_discount_pct_and_amount_are_mutually_exclusive():
    with pytest.raises(ValueError):
        apply_discount(D("100"), discount_pct=D("10"), discount_amount=D("10"))


@pytest.mark.parametrize(
    ("fn", "kwargs"),
    [
        (apply_markup, {"internal_cost": D("-1"), "markup_pct": D("10")}),
        (apply_markup, {"internal_cost": D("10"), "markup_pct": D("-1")}),
        (apply_tax, {"after_discount": D("10"), "tax_pct": D("-1")}),
    ],
)
def test_negative_inputs_are_rejected(fn, kwargs):
    with pytest.raises(ValueError):
        fn(**kwargs)


def test_negative_discount_is_rejected():
    with pytest.raises(ValueError):
        apply_discount(D("100"), discount_pct=D("-5"))
    with pytest.raises(ValueError):
        apply_discount(D("100"), discount_amount=D("-5"))


# --------------------------------------------------------------------------- #
# Discount-approval threshold — an exact boundary, so pin both sides of it.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("discount", "expected"),
    [(D("9.99"), False), (D("10"), True), (D("10.01"), True)],
)
def test_needs_approval_triggers_at_and_above_the_threshold(discount, expected):
    b = compute_price_breakdown(
        D("1000"),
        markup_pct=D("20"),
        discount_pct=discount,
        discount_approval_threshold_pct=D("10"),
    )
    assert b["needs_approval"] is expected


def test_no_threshold_never_needs_approval():
    b = compute_price_breakdown(D("1000"), markup_pct=D("20"), discount_pct=D("90"))
    assert b["needs_approval"] is False


# --------------------------------------------------------------------------- #
# Age classification — boundaries are where fee disputes come from.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0, "infant"), (2, "infant"),                # below child_min_age
        (3, "child"), (7, "child"), (11, "child"),   # inclusive on both bounds
        (12, "adult"), (65, "adult"),                # above child_max_age
    ],
)
def test_classify_age_is_inclusive_on_both_bounds(age, expected):
    assert classify_age(age, 3, 11) == expected


def test_classify_group_prefers_age_over_declared_type():
    # Declared adult but aged 8 -> billed as a child; declared child aged 30 -> adult.
    group = [
        TravellerInput("adult", 8),
        TravellerInput("child", 30),
        TravellerInput("adult", 1),
    ]
    assert classify_group(group, 3, 11) == {"adult": 1, "child": 1, "infant": 1}


def test_classify_group_falls_back_to_type_without_an_age():
    group = [TravellerInput("adult"), TravellerInput("child"), TravellerInput("infant")]
    assert classify_group(group, 3, 11) == {"adult": 1, "child": 1, "infant": 1}


def test_classify_group_treats_an_unknown_type_as_an_adult():
    """Unknown types bill at the highest rate rather than silently going free."""
    assert classify_group([TravellerInput("teenager")], 3, 11) == {
        "adult": 1, "child": 0, "infant": 0
    }


def test_classify_group_of_nobody_is_all_zeroes():
    assert classify_group([], 3, 11) == {"adult": 0, "child": 0, "infant": 0}


# --------------------------------------------------------------------------- #
# Cost-line edges
# --------------------------------------------------------------------------- #

def test_accommodation_cost_scales_linearly():
    base = compute_accommodation_cost(rate_per_night=D("500"), rooms=1, nights=3)
    assert base == D("1500")
    assert compute_accommodation_cost(rate_per_night=D("500"), rooms=2, nights=3) == base * 2
    assert compute_accommodation_cost(rate_per_night=D("500"), rooms=1, nights=6) == base * 2
    assert compute_accommodation_cost(rate_per_night=D("500"), rooms=0, nights=3) == 0


def test_activity_cost_with_no_children_charges_no_child_price():
    r = compute_activity_cost(
        adult_price=D("450"), child_price=D("250"), adults=2, children=0
    )
    assert r["child_total"] == 0
    assert r["total"] == D("900")


def test_transport_cost_rejects_zero_or_negative_consumption():
    """A 0 km/L vehicle would divide by zero — it must raise, not price at infinity."""
    for bad in (D("0"), D("-7")):
        with pytest.raises(ValueError):
            compute_transport_cost(
                distance_km=D("210"),
                consumption_kmpl=bad,
                fuel_price_per_litre=D("1.5"),
                days=3,
                driver_cost_per_day=D("35"),
                daily_operating_cost=D("20"),
            )


def test_game_drive_multiplier_raises_fuel_burn():
    kwargs = {
        "distance_km": D("210"),
        "consumption_kmpl": D("7"),
        "fuel_price_per_litre": D("1.5"),
        "days": 3,
        "driver_cost_per_day": D("35"),
        "daily_operating_cost": D("20"),
    }
    plain = compute_transport_cost(**kwargs)
    rough = compute_transport_cost(**kwargs, consumption_multiplier=D("1.4"))
    # Same distance, worse effective economy -> strictly more fuel, same crew cost.
    assert rough["fuel_litres"] > plain["fuel_litres"]
    assert rough["driver_total"] == plain["driver_total"]
    assert rough["total"] > plain["total"]


def test_zero_distance_still_charges_the_crew():
    r = compute_transport_cost(
        distance_km=D("0"),
        consumption_kmpl=D("7"),
        fuel_price_per_litre=D("1.5"),
        days=2,
        driver_cost_per_day=D("35"),
        daily_operating_cost=D("20"),
    )
    assert r["fuel_cost"] == 0
    assert r["total"] == D("110")  # (35 + 20) x 2
