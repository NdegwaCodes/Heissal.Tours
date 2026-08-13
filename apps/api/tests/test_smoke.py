"""Stage 1 acceptance smoke tests: health, auth, RBAC matrix, refresh, audit."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from tests.conftest import unique_email

API = settings.API_V1_STR

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_health_ok(client):
    resp = await client.get(f"{API}/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["redis"] == "ok"


async def test_login_and_me(client, admin_tokens):
    assert admin_tokens["access_token"]
    assert admin_tokens["refresh_token"]
    resp = await client.get(
        f"{API}/auth/me",
        headers={"Authorization": f"Bearer {admin_tokens['access_token']}"},
    )
    assert resp.status_code == 200
    me = resp.json()
    assert me["email"] == settings.FIRST_SUPERUSER_EMAIL
    assert me["is_superuser"] is True
    assert me["permissions"] == ["*"]


async def test_login_wrong_password(client):
    resp = await client.post(
        f"{API}/auth/login",
        data={"username": settings.FIRST_SUPERUSER_EMAIL, "password": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_protected_requires_auth(client):
    resp = await client.get(f"{API}/users")
    assert resp.status_code == 401


async def test_rbac_matrix(client, admin_tokens):
    admin_h = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

    # Admin creates a viewer-only user.
    email = unique_email("viewer")
    resp = await client.post(
        f"{API}/users",
        headers=admin_h,
        json={"email": email, "password": "ViewerPass123", "role_keys": ["viewer"]},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["permissions"] == ["audit:read", "role:read", "settings:read", "user:read"]

    # Viewer logs in.
    resp = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "ViewerPass123"}
    )
    assert resp.status_code == 200
    viewer_h = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    # Viewer HAS user:read -> 200
    resp = await client.get(f"{API}/users", headers=viewer_h)
    assert resp.status_code == 200

    # Viewer LACKS user:create -> 403
    resp = await client.post(
        f"{API}/users",
        headers=viewer_h,
        json={"email": unique_email(), "password": "whatever123"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"

    # No token -> 401
    resp = await client.post(f"{API}/users", json={"email": unique_email(), "password": "x"})
    assert resp.status_code == 401


async def test_refresh_rotation(client, admin_tokens):
    old_refresh = admin_tokens["refresh_token"]
    # Rotate: old refresh -> new pair
    resp = await client.post(f"{API}/auth/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 200
    new_pair = resp.json()
    assert new_pair["refresh_token"] != old_refresh

    # Reusing the old (now revoked) refresh token -> 401
    resp = await client.post(f"{API}/auth/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 401


async def test_audit_written_on_user_create(client, admin_tokens):
    admin_h = {"Authorization": f"Bearer {admin_tokens['access_token']}"}
    email = unique_email("audited")
    resp = await client.post(
        f"{API}/users",
        headers=admin_h,
        json={"email": email, "password": "AuditPass123", "role_keys": []},
    )
    assert resp.status_code == 201
    user_id = resp.json()["id"]

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                sa.text(
                    "SELECT action, entity_type, new_value FROM audit_log "
                    "WHERE entity_id = :eid ORDER BY created_at DESC LIMIT 1"
                ),
                {"eid": user_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "USER_CREATE"
    assert row[1] == "user"
