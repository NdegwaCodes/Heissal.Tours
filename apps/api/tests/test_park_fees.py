"""Stage 2.3 — park & conservation fees: API + DB selection.

Pure age-classification / fee-math tests live in test_pricing_functions.py.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.config import settings

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_park_fee_create_and_resolve(client, admin_tokens):
    h = await _auth(admin_tokens)

    dest = (
        await client.post(
            f"{API}/destinations",
            headers=h,
            json={"name": f"Fee Park {uuid.uuid4().hex[:8]}", "type": "park"},
        )
    ).json()

    cats = (await client.get(f"{API}/residence-categories", headers=h)).json()
    non_resident = next(c for c in cats if c["key"] == "non_resident")

    created = await client.post(
        f"{API}/destinations/{dest['id']}/park-fees",
        headers=h,
        json={
            "fee_type": "park_entry",
            "residence_category_id": non_resident["id"],
            "currency": "USD",
            "adult": "70",
            "child": "40",
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
        },
    )
    assert created.status_code == 201, created.text

    # Resolve in range
    resolved = await client.get(
        f"{API}/destinations/{dest['id']}/resolve-park-fee",
        headers=h,
        params={
            "fee_type": "park_entry",
            "residence_category_id": non_resident["id"],
            "on_date": "2026-07-01",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["adult"] == "70.0000"

    # Out of range -> explicit 404
    miss = await client.get(
        f"{API}/destinations/{dest['id']}/resolve-park-fee",
        headers=h,
        params={
            "fee_type": "park_entry",
            "residence_category_id": non_resident["id"],
            "on_date": "2027-07-01",
        },
    )
    assert miss.status_code == 404


async def test_park_fee_bad_child_bounds_rejected(client, admin_tokens):
    h = await _auth(admin_tokens)
    dest = (
        await client.post(
            f"{API}/destinations",
            headers=h,
            json={"name": f"BadBounds {uuid.uuid4().hex[:8]}", "type": "park"},
        )
    ).json()
    cats = (await client.get(f"{API}/residence-categories", headers=h)).json()
    cat = cats[0]
    resp = await client.post(
        f"{API}/destinations/{dest['id']}/park-fees",
        headers=h,
        json={
            "residence_category_id": cat["id"],
            "currency": "KES",
            "adult": "1000",
            "child": "500",
            "child_min_age": 12,
            "child_max_age": 5,  # invalid
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
        },
    )
    assert resp.status_code == 400
