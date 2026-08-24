"""Stage 2.6 — FX conversion service + pricing configuration (API-level)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.config import settings

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _make_rate(client, h, base, quote, rate, effective_from):
    resp = await client.post(
        f"{API}/exchange-rates",
        headers=h,
        json={
            "base_currency": base,
            "quote_currency": quote,
            "rate": rate,
            "effective_from": effective_from,
            "source": "test",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_fx_convert_identity(client, admin_tokens):
    h = await _auth(admin_tokens)
    r = await client.get(
        f"{API}/exchange-rates/convert",
        headers=h,
        params={"amount": "100", "from": "USD", "to": "USD"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["rate"]) == Decimal("1")
    assert Decimal(body["converted"]) == Decimal("100")


async def test_fx_convert_direct_and_effective_dating(client, admin_tokens):
    h = await _auth(admin_tokens)
    # Use unique synthetic currency codes so this test is isolated.
    base = "T" + uuid.uuid4().hex[:2].upper()
    quote = "Q" + uuid.uuid4().hex[:2].upper()
    await _make_rate(client, h, base, quote, "120", "2026-01-01")
    await _make_rate(client, h, base, quote, "130", "2026-06-01")

    # A date after the later rate -> the 130 rate wins.
    r = await client.get(
        f"{API}/exchange-rates/convert",
        headers=h,
        params={"amount": "100", "from": base, "to": quote, "on_date": "2026-07-01"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["rate"]) == Decimal("130")
    assert Decimal(body["converted"]) == Decimal("13000")

    # A date between the two -> the earlier 120 rate wins.
    r2 = await client.get(
        f"{API}/exchange-rates/convert",
        headers=h,
        params={"amount": "100", "from": base, "to": quote, "on_date": "2026-03-01"},
    )
    assert Decimal(r2.json()["rate"]) == Decimal("120")


async def test_fx_convert_inverse(client, admin_tokens):
    h = await _auth(admin_tokens)
    base = "T" + uuid.uuid4().hex[:2].upper()
    quote = "Q" + uuid.uuid4().hex[:2].upper()
    # 1 base = 4 quote (chosen to divide evenly for an exact reciprocal).
    await _make_rate(client, h, base, quote, "4", "2026-01-01")
    # Only base->quote exists; converting quote->base uses the reciprocal (1/4).
    r = await client.get(
        f"{API}/exchange-rates/convert",
        headers=h,
        params={"amount": "100", "from": quote, "to": base, "on_date": "2026-07-01"},
    )
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["rate"]) == Decimal("0.25")
    assert Decimal(r.json()["converted"]) == Decimal("25")


async def test_fx_convert_missing_rate_is_404(client, admin_tokens):
    h = await _auth(admin_tokens)
    miss_a = "X" + uuid.uuid4().hex[:2].upper()
    miss_b = "Z" + uuid.uuid4().hex[:2].upper()
    r = await client.get(
        f"{API}/exchange-rates/convert",
        headers=h,
        params={"amount": "100", "from": miss_a, "to": miss_b},
    )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"


async def test_fx_convert_requires_permission(client):
    # No auth header -> 401, never a silent conversion.
    r = await client.get(
        f"{API}/exchange-rates/convert",
        params={"amount": "100", "from": "USD", "to": "KES"},
    )
    assert r.status_code == 401


async def test_pricing_config_get_and_update_roundtrip(
    client, admin_tokens, restore_pricing_config
):
    h = await _auth(admin_tokens)
    # GET always returns a valid config (defaults if never saved).
    r = await client.get(f"{API}/pricing-config", headers=h)
    assert r.status_code == 200, r.text
    assert "default_markup_pct" in r.json()

    # PATCH a known set of values, then read them back.
    patch = await client.patch(
        f"{API}/pricing-config",
        headers=h,
        json={"default_markup_pct": "22.5", "default_tax_pct": "16", "quote_validity_days": 21},
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["default_markup_pct"] == "22.5"
    assert body["default_tax_pct"] == "16"
    assert body["quote_validity_days"] == 21

    again = await client.get(f"{API}/pricing-config", headers=h)
    assert again.json()["default_markup_pct"] == "22.5"
    assert again.json()["quote_validity_days"] == 21


async def test_pricing_config_rejects_invalid(
    client, admin_tokens, restore_pricing_config
):
    h = await _auth(admin_tokens)
    r = await client.patch(
        f"{API}/pricing-config",
        headers=h,
        json={"default_markup_pct": "-5"},
    )
    assert r.status_code == 422


async def test_pricing_config_requires_permission(client):
    r = await client.get(f"{API}/pricing-config")
    assert r.status_code == 401


async def test_the_stage_three_build_up_defaults_are_readable_and_editable(
    client, admin_tokens, restore_pricing_config
):
    """"Profit is a fixed 24%, in pricing config, not hard-coded" (design §3.6).

    It was configurable in name only until 3.4: the values existed on the config
    model but were absent from the read and update schemas, so no admin could see
    or change them through the API.
    """
    h = await _auth(admin_tokens)
    body = (await client.get(f"{API}/pricing-config", headers=h)).json()
    assert body["profit_pct"] == "24"
    assert body["contingency_pct"] == "5"
    assert body["per_person_rounding"] == "100"
    assert body["quotation_validity_days"] == 30
    # The quote-shape bounds: "3-9 hotels plus 1-2 BnB options" (§1).
    assert body["min_catered_options"] == 3
    assert body["max_catered_options"] == 9
    assert body["min_self_catering_options"] == 1
    assert body["max_self_catering_options"] == 2

    patched = await client.patch(
        f"{API}/pricing-config",
        headers=h,
        json={"profit_pct": "18", "contingency_pct": "0", "max_catered_options": 6},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["profit_pct"] == "18"
    assert patched.json()["contingency_pct"] == "0"
    assert patched.json()["max_catered_options"] == 6

    again = (await client.get(f"{API}/pricing-config", headers=h)).json()
    assert again["profit_pct"] == "18"
