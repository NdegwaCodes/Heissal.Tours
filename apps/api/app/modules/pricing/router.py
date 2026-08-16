from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.pricing.schemas import PricingConfigRead, PricingConfigUpdate
from app.modules.pricing.service import PricingConfigService
from app.modules.users.models import User

router = APIRouter(tags=["pricing"])


@router.get("/pricing-config", response_model=PricingConfigRead)
async def get_pricing_config(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("pricing:read")),
):
    return await PricingConfigService(db).get()


@router.patch("/pricing-config", response_model=PricingConfigRead)
async def update_pricing_config(
    body: PricingConfigUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("pricing:manage")),
):
    patch = body.model_dump(exclude_unset=True)
    return await PricingConfigService(db).update(patch, updated_by=actor.id)
