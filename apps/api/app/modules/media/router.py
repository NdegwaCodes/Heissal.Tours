"""Image upload and delivery for properties and destinations.

Bytes are served through the API rather than from a public directory, so access
stays permission-checked instead of resting on a guessable path (design doc §6).
The quotation renderer references these same URLs.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import require_permission
from app.core.errors import AppError
from app.core.storage import resolve
from app.db.session import get_db
from app.modules.media.schemas import DestinationImageRead, PropertyImageRead
from app.modules.media.service import MediaService
from app.modules.users.models import User

router = APIRouter(tags=["media"])

READ = "media:read"
MANAGE = "media:manage"


async def _payload(file: UploadFile) -> bytes:
    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise AppError(
            f"That image is {len(content) // (1024 * 1024)} MB; the limit is "
            f"{settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )
    return content


@router.get(
    "/accommodations/{accommodation_id}/images",
    response_model=list[PropertyImageRead],
)
async def list_property_images(
    accommodation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await MediaService(db).list_property_images(accommodation_id)


@router.post(
    "/accommodations/{accommodation_id}/images",
    response_model=PropertyImageRead,
    status_code=201,
)
async def add_property_image(
    accommodation_id: uuid.UUID,
    file: UploadFile = File(...),
    alt_text: str | None = Form(default=None),
    is_hero: bool = Form(default=False),
    sort_order: int | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(MANAGE)),
):
    """Add a photograph. Re-uploading the same file returns the existing row."""
    return await MediaService(db).add_property_image(
        accommodation_id,
        content=await _payload(file),
        filename=file.filename or "image",
        content_type=(file.content_type or "").lower(),
        alt_text=alt_text,
        is_hero=is_hero,
        sort_order=sort_order,
        uploaded_by=user.id,
    )


@router.get("/property-images/{image_id}/file")
async def serve_property_image(
    image_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    image = await MediaService(db).get_property_image(image_id)
    return FileResponse(resolve(image.storage_path), media_type=image.content_type)


@router.delete("/property-images/{image_id}", status_code=204)
async def delete_property_image(
    image_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    await MediaService(db).delete_property_image(image_id)


@router.get(
    "/destinations/{destination_id}/images", response_model=list[DestinationImageRead]
)
async def list_destination_images(
    destination_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await MediaService(db).list_destination_images(destination_id)


@router.post(
    "/destinations/{destination_id}/images",
    response_model=DestinationImageRead,
    status_code=201,
)
async def add_destination_image(
    destination_id: uuid.UUID,
    file: UploadFile = File(...),
    alt_text: str | None = Form(default=None),
    is_cover: bool = Form(default=False),
    sort_order: int | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(MANAGE)),
):
    """Add destination imagery. `is_cover` marks the quotation cover (§3.11)."""
    return await MediaService(db).add_destination_image(
        destination_id,
        content=await _payload(file),
        filename=file.filename or "image",
        content_type=(file.content_type or "").lower(),
        alt_text=alt_text,
        is_cover=is_cover,
        sort_order=sort_order,
        uploaded_by=user.id,
    )


@router.get("/destination-images/{image_id}/file")
async def serve_destination_image(
    image_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    image = await MediaService(db).get_destination_image(image_id)
    return FileResponse(resolve(image.storage_path), media_type=image.content_type)


@router.delete("/destination-images/{image_id}", status_code=204)
async def delete_destination_image(
    image_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    await MediaService(db).delete_destination_image(image_id)
