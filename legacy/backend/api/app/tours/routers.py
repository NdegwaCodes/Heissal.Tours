from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from . import models, schemas
from ..core.db import get_db
from ..core.security import get_current_user


def slugify(text: str) -> str:
    return (
        text.strip()
        .lower()
        .replace(" ", "-")
        .replace("/", "-")
    )


router = APIRouter(prefix="/tours", tags=["tours"])


@router.post("/", response_model=schemas.TourRead, status_code=201)
async def create_tour(
    tour_in: schemas.TourCreate,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),  # require auth for now
):
    slug = slugify(tour_in.title)
    # ensure slug uniqueness (basic)
    result = await db.execute(select(models.Tour).where(models.Tour.slug == slug))
    if result.scalar_one_or_none():
        slug = f"{slug}-dup"

    tour = models.Tour(
        **tour_in.model_dump(),
        slug=slug,
    )
    db.add(tour)
    await db.commit()
    await db.refresh(tour)
    return tour


@router.get("/", response_model=list[schemas.TourRead])
async def list_tours(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    location_id: UUID | None = None,
    tour_type: str | None = None,
):
    query = select(models.Tour).where(models.Tour.status == models.TourStatus.published)
    if location_id:
        query = query.where(models.Tour.location_id == location_id)
    if tour_type:
        query = query.where(models.Tour.tour_type == tour_type)

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{tour_id}", response_model=schemas.TourRead)
async def get_tour(
    tour_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(models.Tour).where(models.Tour.id == tour_id))
    tour = result.scalar_one_or_none()
    if not tour:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return tour
