"""Stage 2.1 — reference data: currencies, residence categories, destinations, FX."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import settings
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_currencies_seeded(client, admin_tokens):
    h = {"Authorization": f"Bearer {admin_tokens['access_token']}"}
    resp = await client.get(f"{API}/currencies", headers=h)
    assert resp.status_code == 200
    codes = {c["code"] for c in resp.json()}
    assert {"KES", "USD", "EUR", "GBP"} <= codes


async def test_residence_categories_seeded(client, admin_tokens):
    h = {"Authorization": f"Bearer {admin_tokens['access_token']}"}
    resp = await client.get(f"{API}/residence-categories", headers=h)
    assert resp.status_code == 200
    keys = {c["key"] for c in resp.json()}
    assert {"citizen", "ea_resident", "resident", "non_resident"} <= keys


async def test_destination_crud_and_rbac(client, admin_tokens):
    admin_h = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

    # Admin creates a destination (park). Unique name keeps re-runs isolated on
    # the shared dev DB (a per-test transactional DB is a later test-harness item).
    name = f"Test Park {uuid.uuid4().hex[:8]}"
    resp = await client.post(
        f"{API}/destinations",
        headers=admin_h,
        json={"name": name, "type": "park", "region": "Narok"},
    )
    assert resp.status_code == 201, resp.text
    dest = resp.json()
    assert dest["slug"].startswith("test-park-")
    assert dest["type"] == "park"

    # Make a viewer (read-only) user.
    email = unique_email("ref-viewer")
    r = await client.post(
        f"{API}/users",
        headers=admin_h,
        json={"email": email, "password": "ViewerPass123", "role_keys": ["viewer"]},
    )
    assert r.status_code == 201
    r = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "ViewerPass123"}
    )
    viewer_h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # Viewer can read...
    resp = await client.get(f"{API}/destinations", headers=viewer_h)
    assert resp.status_code == 200
    assert any(d["id"] == dest["id"] for d in resp.json())

    # ...but cannot create (needs destination:manage).
    resp = await client.post(
        f"{API}/destinations", headers=viewer_h, json={"name": "Amboseli", "type": "park"}
    )
    assert resp.status_code == 403


async def test_exchange_rate_create_and_list(client, admin_tokens):
    h = {"Authorization": f"Bearer {admin_tokens['access_token']}"}
    resp = await client.post(
        f"{API}/exchange-rates",
        headers=h,
        json={
            "base_currency": "USD",
            "quote_currency": "KES",
            "rate": "129.50",
            "effective_from": "2026-01-01",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["base_currency"] == "USD"

    resp = await client.get(f"{API}/exchange-rates", headers=h)
    assert resp.status_code == 200
    assert any(
        r["base_currency"] == "USD" and r["quote_currency"] == "KES" for r in resp.json()
    )
