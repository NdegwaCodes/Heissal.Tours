"""Assemble the v1 API router from module routers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import health
from app.modules.accommodations.router import router as accommodations_router
from app.modules.activities.router import router as activities_router
from app.modules.auth.router import router as auth_router
from app.modules.currency.router import router as currency_router
from app.modules.destinations.router import router as destinations_router
from app.modules.park_fees.router import router as park_fees_router
from app.modules.rbac.router import router as rbac_router
from app.modules.residence.router import router as residence_router
from app.modules.suppliers.router import router as suppliers_router
from app.modules.users.router import router as users_router

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(rbac_router)
# Stage 2 — reference / catalogue
api_router.include_router(residence_router)
api_router.include_router(currency_router)
api_router.include_router(suppliers_router)
api_router.include_router(destinations_router)
api_router.include_router(accommodations_router)
api_router.include_router(park_fees_router)
api_router.include_router(activities_router)
