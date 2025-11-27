from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from . import models, schemas
from ..core.db import get_db
from ..core.security import get_current_user


def slugify(text: str) -> str:
    # simple slugifier; you can later replace with a better lib
    return (
        text.strip()
        .lower()
        .replace(" ", "-")
        .replace("/", "-")
    )


router = APIRouter(prefix="/locations", tags=["locations"])


@router.post("/", response_model=schemas.LocationRead, status_code=201)
async def create_location(
    loc_in: schemas.LocationCreate,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),  # require auth, maybe admin later
):
    slug = slugify(f"{loc_in.city}-{loc_in.country}")
    loc = models.Location(
        city=loc_in.city,
        country=loc_in.country,
        lat=loc_in.lat,
        lon=loc_in.lon,
        slug=slug,
    )
    db.add(loc)
    await db.commit()
    await db.refresh(loc)
    return loc


@router.get("/", response_model=list[schemas.LocationRead])
async def list_locations(
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(models.Location))
    return result.scalars().all()


@router.get("/{location_id}", response_model=schemas.LocationRead)
async def get_location(
    location_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.Location).where(models.Location.id == location_id)
    )
    loc = result.scalar_one_or_none()
    if not loc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return loc
