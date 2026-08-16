"""Stage 2.4 — activities and activity rates."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import settings
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_activity_rate_flow(client, admin_tokens):
    h = await _auth(admin_tokens)

    act = (
        await client.post(
            f"{API}/activities",
            headers=h,
            json={"name": f"Balloon Safari {uuid.uuid4().hex[:6]}", "duration_minutes": 60},
        )
    ).json()
    assert act["slug"].startswith("balloon-safari-")

    cats = (await client.get(f"{API}/residence-categories", headers=h)).json()
    non_resident = next(c for c in cats if c["key"] == "non_resident")

    created = await client.post(
        f"{API}/activities/{act['id']}/rates",
        headers=h,
        json={
            "residence_category_id": non_resident["id"],
            "currency": "USD",
            "adult_price": "450",
            "child_price": "250",
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
        },
    )
    assert created.status_code == 201, created.text

    resolved = await client.get(
        f"{API}/activities/{act['id']}/resolve-rate",
        headers=h,
        params={"residence_category_id": non_resident["id"], "on_date": "2026-08-01"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["adult_price"] == "450.0000"

    miss = await client.get(
        f"{API}/activities/{act['id']}/resolve-rate",
        headers=h,
        params={"residence_category_id": non_resident["id"], "on_date": "2025-01-01"},
    )
    assert miss.status_code == 404


async def test_activity_rbac(client, admin_tokens):
    admin_h = await _auth(admin_tokens)
    email = unique_email("act-viewer")
    await client.post(
        f"{API}/users",
        headers=admin_h,
        json={"email": email, "password": "ViewerPass123", "role_keys": ["viewer"]},
    )
    r = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "ViewerPass123"}
    )
    viewer_h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    assert (await client.get(f"{API}/activities", headers=viewer_h)).status_code == 200
    resp = await client.post(
        f"{API}/activities", headers=viewer_h, json={"name": "Nope"}
    )
    assert resp.status_code == 403
