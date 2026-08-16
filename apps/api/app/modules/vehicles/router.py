from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDService, slugify
from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.users.models import User
from app.modules.vehicles.models import FuelPrice, Vehicle
from app.modules.vehicles.schemas import (
    FuelPriceCreate,
    FuelPriceRead,
    VehicleCreate,
    VehicleRead,
    VehicleUpdate,
)
from app.modules.vehicles.service import FuelPriceService

router = APIRouter(prefix="/vehicles", tags=["fleet"])

READ = "vehicle:read"
MANAGE = "vehicle:manage"


@router.get("", response_model=list[VehicleRead])
async def list_vehicles(db: AsyncSession = Depends(get_db), _=Depends(require_permission(READ))):
    return await CRUDService(db, Vehicle).list()


@router.post("", response_model=VehicleRead, status_code=201)
async def create_vehicle(
    body: VehicleCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    data = body.model_dump()
    data["slug"] = slugify(data.get("slug") or data["name"])
    data["currency"] = data["currency"].upper()
    return await CRUDService(db, Vehicle).create(data)


@router.get("/{vehicle_id}", response_model=VehicleRead)
async def get_vehicle(
    vehicle_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await CRUDService(db, Vehicle).get(vehicle_id)


@router.patch("/{vehicle_id}", response_model=VehicleRead)
async def update_vehicle(
    vehicle_id: uuid.UUID,
    body: VehicleUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    return await CRUDService(db, Vehicle).update(vehicle_id, body.model_dump(exclude_unset=True))


# --- Fuel prices (separate path to avoid /vehicles/{id} collision) ---
fuel_router = APIRouter(prefix="/fuel-prices", tags=["fleet"])


@fuel_router.get("", response_model=list[FuelPriceRead])
async def list_fuel_prices(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    rows = (
        await db.execute(select(FuelPrice).order_by(FuelPrice.effective_from.desc()))
    ).scalars().all()
    return rows


@fuel_router.post("", response_model=FuelPriceRead, status_code=201)
async def create_fuel_price(
    body: FuelPriceCreate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(MANAGE)),
):
    price = FuelPrice(
        fuel_type=body.fuel_type,
        price_per_litre=body.price_per_litre,
        currency=body.currency.upper(),
        effective_from=body.effective_from,
        source=body.source,
        created_by=actor.id,
    )
    db.add(price)
    await db.commit()
    await db.refresh(price)
    return price


@fuel_router.get("/resolve", response_model=FuelPriceRead)
async def resolve_fuel_price(
    fuel_type: str,
    on_date: date,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await FuelPriceService(db).select_price(fuel_type=fuel_type, on_date=on_date)
