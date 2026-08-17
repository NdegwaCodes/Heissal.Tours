"""Pure pricing-math unit tests (no DB, no event loop).

These grow as the pricing engine (Stage 2.8) adds pure calculation helpers.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.integrations.exchange_rate import apply_rate
from app.modules.activities.service import compute_activity_cost
from app.modules.park_fees.service import classify_age, compute_park_fee
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


def test_classify_age_bounds():
    # child band 3..11
    assert classify_age(2, 3, 11) == "infant"
    assert classify_age(3, 3, 11) == "child"
    assert classify_age(11, 3, 11) == "child"
    assert classify_age(12, 3, 11) == "adult"
    # a park that defines child as 5..17
    assert classify_age(4, 5, 17) == "infant"
    assert classify_age(17, 5, 17) == "child"
    assert classify_age(18, 5, 17) == "adult"


def test_compute_park_fee_math():
    # 2 adults (no ages) + ages [5, 1] with child band 3..11 => 2 adult, 1 child, 1 infant
    result = compute_park_fee(
        adult_fee=Decimal("70"),
        child_fee=Decimal("40"),
        infant_fee=Decimal("0"),
        adults=2,
        ages=[5, 1],
        days=3,
        child_min_age=3,
        child_max_age=11,
    )
    assert result["counts"] == {"adult": 2, "child": 1, "infant": 1}
    assert result["adult_total"] == Decimal("420")  # 70*2*3
    assert result["child_total"] == Decimal("120")  # 40*1*3
    assert result["infant_total"] == Decimal("0")
    assert result["total"] == Decimal("540")


def test_compute_activity_cost_math():
    result = compute_activity_cost(
        adult_price=Decimal("450"),
        child_price=Decimal("250"),
        adults=2,
        children=1,
    )
    assert result["adult_total"] == Decimal("900")  # 450*2
    assert result["child_total"] == Decimal("250")  # 250*1
    assert result["total"] == Decimal("1150")


def test_compute_transport_cost_math():
    # 210 km / 7 kmpl = 30 L; 30 * 1.5 = 45 fuel; +driver 35*2 +operating 20*2
    r = compute_transport_cost(
        distance_km=Decimal("210"),
        consumption_kmpl=Decimal("7"),
        fuel_price_per_litre=Decimal("1.5"),
        days=2,
        driver_cost_per_day=Decimal("35"),
        daily_operating_cost=Decimal("20"),
    )
    assert r["fuel_litres"] == Decimal("30")
    assert r["fuel_cost"] == Decimal("45.0")
    assert r["total"] == Decimal("155")

    # Game-drive multiplier halves effective km/L -> doubles fuel used (60 L)
    r2 = compute_transport_cost(
        distance_km=Decimal("210"),
        consumption_kmpl=Decimal("7"),
        fuel_price_per_litre=Decimal("1.5"),
        days=2,
        driver_cost_per_day=Decimal("35"),
        daily_operating_cost=Decimal("20"),
        consumption_multiplier=Decimal("2"),
    )
    assert r2["fuel_litres"] == Decimal("60")


# --- Stage 2.6: FX + pricing math --------------------------------------------

def test_apply_rate_multiplies():
    assert apply_rate(Decimal("100"), Decimal("130")) == Decimal("13000")


def test_apply_markup_and_tax():
    assert apply_markup(Decimal("1000"), Decimal("20")) == Decimal("1200.0")
    assert apply_tax(Decimal("1000"), Decimal("16")) == Decimal("160.00")
    with pytest.raises(ValueError):
        apply_markup(Decimal("-1"), Decimal("10"))


def test_apply_discount_pct_amount_and_clamp():
    assert apply_discount(Decimal("1000"), discount_pct=Decimal("10")) == Decimal("100.0")
    assert apply_discount(Decimal("1000"), discount_amount=Decimal("250")) == Decimal("250")
    # A discount larger than the subtotal is clamped, never negative.
    assert apply_discount(Decimal("100"), discount_amount=Decimal("500")) == Decimal("100")
    with pytest.raises(ValueError):
        apply_discount(Decimal("100"), discount_pct=Decimal("5"), discount_amount=Decimal("5"))


def test_compute_price_breakdown_full():
    # internal 1000, +20% markup = 1200; -10% discount = 120 -> 1080; +16% tax = 172.8
    r = compute_price_breakdown(
        Decimal("1000"),
        markup_pct=Decimal("20"),
        discount_pct=Decimal("10"),
        tax_pct=Decimal("16"),
        discount_approval_threshold_pct=Decimal("10"),
    )
    assert r["selling_subtotal"] == Decimal("1200.0")
    assert r["discount_value"] == Decimal("120.00")
    assert r["after_discount"] == Decimal("1080.00")
    assert r["tax"] == Decimal("172.8000")
    assert r["selling_price"] == Decimal("1252.8000")
    assert r["gross_profit"] == Decimal("252.8000")
    # margin = 252.8 / 1252.8
    assert r["gross_margin"] == Decimal("252.8000") / Decimal("1252.8000")
    # 10% discount meets the 10% approval threshold
    assert r["needs_approval"] is True


def test_compute_price_breakdown_no_discount_no_tax():
    r = compute_price_breakdown(Decimal("500"), markup_pct=Decimal("0"))
    assert r["selling_price"] == Decimal("500")
    assert r["gross_profit"] == Decimal("0")
    assert r["gross_margin"] == Decimal("0")
    assert r["needs_approval"] is False


def test_compute_price_breakdown_zero_internal_cost():
    # No divide-by-zero when everything is zero.
    r = compute_price_breakdown(Decimal("0"), markup_pct=Decimal("20"))
    assert r["selling_price"] == Decimal("0")
    assert r["gross_margin"] == Decimal("0")


# --- Stage 2.8: engine pure helpers ------------------------------------------

def test_compute_accommodation_cost():
    assert compute_accommodation_cost(
        rate_per_night=Decimal("500"), rooms=2, nights=3
    ) == Decimal("3000")


def test_classify_group_uses_age_then_type():
    travellers = [
        TravellerInput("adult"),            # no age -> declared adult
        TravellerInput("adult", 40),        # age 40 -> adult by bounds
        TravellerInput("child", 8),         # age 8 in 3..11 -> child
        TravellerInput("child", 1),         # age 1 below 3 -> infant (age wins over type)
        TravellerInput("infant"),           # no age -> declared infant
    ]
    counts = classify_group(travellers, 3, 11)
    assert counts == {"adult": 2, "child": 1, "infant": 2}
