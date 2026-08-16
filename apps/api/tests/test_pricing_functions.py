"""Pure pricing-math unit tests (no DB, no event loop).

These grow as the pricing engine (Stage 2.8) adds pure calculation helpers.
"""

from __future__ import annotations

from decimal import Decimal

from app.modules.activities.service import compute_activity_cost
from app.modules.park_fees.service import classify_age, compute_park_fee


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
