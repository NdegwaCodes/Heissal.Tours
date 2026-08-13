"""Health / readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.core.redis import redis_client
from app.db.session import AsyncSessionLocal

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness + dependency checks."""
    db_ok = "ok"
    redis_ok = "ok"

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = "error"

    try:
        await redis_client.ping()
    except Exception:
        redis_ok = "error"

    status_str = "ok" if db_ok == "ok" and redis_ok == "ok" else "degraded"
    return {"status": status_str, "db": db_ok, "redis": redis_ok}
