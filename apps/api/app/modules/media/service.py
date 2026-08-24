"""Image upload and lookup for properties and destinations.

Bytes go through the content-addressed store the supplier documents already use
(:mod:`app.core.storage`), so an image uploaded twice occupies one file and the
stored name is derived from content rather than from an attacker-supplied
filename. Only metadata lands in Postgres.

**No server-side crop derivatives.** The design doc calls for images centre-
cropped to the template's aspect ratios; the template does that with a fixed
aspect box and ``object-fit: cover``, which is a centre crop, and it renders
identically in the browser and in print. Storing pre-cropped copies would mean
re-deriving every image whenever a layout changed, for a result the renderer
produces for free — so the originals are kept and cropping stays a presentation
concern. If a future layout needs a genuinely different crop *per image* (a
subject off-centre), that is when a stored derivative earns its place.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.core.storage import save_bytes
from app.modules.accommodations.models import Accommodation
from app.modules.destinations.models import Destination
from app.modules.media.models import DestinationImage, PropertyImage

# Only formats a print renderer and every browser handle. SVG is excluded on
# purpose: it is a script-bearing document, not a photograph.
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


class MediaService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- property images ----------------------------------------------------- #

    async def add_property_image(
        self,
        accommodation_id: uuid.UUID,
        *,
        content: bytes,
        filename: str,
        content_type: str,
        alt_text: str | None,
        is_hero: bool,
        sort_order: int | None,
        uploaded_by: uuid.UUID | None,
    ) -> PropertyImage:
        accommodation = await self.db.get(Accommodation, accommodation_id)
        if accommodation is None:
            raise NotFoundError("Accommodation not found.")
        stored = self._store(content, filename=filename, content_type=content_type,
                             subdir="property-images")
        existing = (
            await self.db.execute(
                select(PropertyImage).where(
                    PropertyImage.accommodation_id == accommodation_id,
                    PropertyImage.checksum == stored.checksum,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            # The same photograph, uploaded again. Returning the row already
            # there beats a 409: the caller's intent is satisfied, and a gallery
            # upload of five files where two are repeats should not half-fail.
            return existing

        highest = await self._highest_sort(
            PropertyImage, PropertyImage.accommodation_id == accommodation_id
        )
        image = PropertyImage(
            accommodation_id=accommodation_id,
            storage_path=stored.storage_path,
            content_type=content_type,
            byte_size=stored.byte_size,
            checksum=stored.checksum,
            alt_text=alt_text or f"{accommodation.name}",
            is_hero=is_hero,
            sort_order=sort_order if sort_order is not None else highest + 1,
            uploaded_by=uploaded_by,
        )
        self.db.add(image)
        await self.db.flush()
        if is_hero:
            await self._make_sole_flag(
                PropertyImage,
                PropertyImage.accommodation_id == accommodation_id,
                "is_hero",
                image.id,
            )
        await self.db.commit()
        return image

    async def list_property_images(
        self, accommodation_id: uuid.UUID
    ) -> list[PropertyImage]:
        stmt = (
            select(PropertyImage)
            .where(PropertyImage.accommodation_id == accommodation_id)
            # Hero first, then the gallery in the order an editor arranged it.
            .order_by(PropertyImage.is_hero.desc(), PropertyImage.sort_order)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    # -- destination images -------------------------------------------------- #

    async def add_destination_image(
        self,
        destination_id: uuid.UUID,
        *,
        content: bytes,
        filename: str,
        content_type: str,
        alt_text: str | None,
        is_cover: bool,
        sort_order: int | None,
        uploaded_by: uuid.UUID | None,
    ) -> DestinationImage:
        destination = await self.db.get(Destination, destination_id)
        if destination is None:
            raise NotFoundError("Destination not found.")
        stored = self._store(content, filename=filename, content_type=content_type,
                             subdir="destination-images")
        existing = (
            await self.db.execute(
                select(DestinationImage).where(
                    DestinationImage.destination_id == destination_id,
                    DestinationImage.checksum == stored.checksum,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        highest = await self._highest_sort(
            DestinationImage, DestinationImage.destination_id == destination_id
        )
        image = DestinationImage(
            destination_id=destination_id,
            storage_path=stored.storage_path,
            content_type=content_type,
            byte_size=stored.byte_size,
            checksum=stored.checksum,
            alt_text=alt_text or destination.name,
            is_cover=is_cover,
            sort_order=sort_order if sort_order is not None else highest + 1,
            uploaded_by=uploaded_by,
        )
        self.db.add(image)
        await self.db.flush()
        if is_cover:
            await self._make_sole_flag(
                DestinationImage,
                DestinationImage.destination_id == destination_id,
                "is_cover",
                image.id,
            )
        await self.db.commit()
        return image

    async def list_destination_images(
        self, destination_id: uuid.UUID
    ) -> list[DestinationImage]:
        stmt = (
            select(DestinationImage)
            .where(DestinationImage.destination_id == destination_id)
            .order_by(DestinationImage.is_cover.desc(), DestinationImage.sort_order)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    # -- shared -------------------------------------------------------------- #

    async def get_property_image(self, image_id: uuid.UUID) -> PropertyImage:
        image = await self.db.get(PropertyImage, image_id)
        if image is None:
            raise NotFoundError("Image not found.")
        return image

    async def get_destination_image(self, image_id: uuid.UUID) -> DestinationImage:
        image = await self.db.get(DestinationImage, image_id)
        if image is None:
            raise NotFoundError("Image not found.")
        return image

    async def delete_property_image(self, image_id: uuid.UUID) -> None:
        await self.db.delete(await self.get_property_image(image_id))
        await self.db.commit()

    async def delete_destination_image(self, image_id: uuid.UUID) -> None:
        await self.db.delete(await self.get_destination_image(image_id))
        await self.db.commit()

    @staticmethod
    def _store(content: bytes, *, filename: str, content_type: str, subdir: str):
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise AppError(
                f"{content_type or 'that file'} is not an accepted image format. "
                f"Use {', '.join(sorted(ALLOWED_IMAGE_TYPES))}."
            )
        if not content:
            raise AppError("That file is empty.")
        # The extension comes from the declared type, never from the filename:
        # the stored name must not be influenced by the upload at all.
        return save_bytes(
            content,
            filename=f"image.{ALLOWED_IMAGE_TYPES[content_type]}",
            subdir=subdir,
        )

    async def _highest_sort(self, model: Any, where: Any) -> int:
        rows = (
            (await self.db.execute(select(model.sort_order).where(where)))
            .scalars()
            .all()
        )
        return max(rows, default=0)

    async def _make_sole_flag(
        self, model: Any, where: Any, field: str, keep: uuid.UUID
    ) -> None:
        """Exactly one hero / one cover per owner.

        Set on the way in rather than checked on the way out: a property with two
        heroes renders an arbitrary one, and which one it is would change between
        runs.
        """
        rows = (await self.db.execute(select(model).where(where))).scalars().all()
        for row in rows:
            setattr(row, field, row.id == keep)
