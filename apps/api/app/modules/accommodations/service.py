"""Accommodation rate selection — the deterministic pricing lookup."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.core.vat import DEFAULT_VAT_PCT, to_vat_inclusive
from app.modules.accommodations.models import AccommodationRate


class AccommodationRateService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_rate(
        self, accommodation_id: uuid.UUID, data: dict[str, Any]
    ) -> AccommodationRate:
        """Store one hand-entered rate, VAT-normalised.

        A typed-in rate goes through the same §3.2 normalisation as an ingested
        one: whoever is reading a sheet into the admin form is looking at the same
        "rates exclusive of VAT" footnote a parser would, and the invariant is
        that *stored* rates are inclusive regardless of which door they came
        through. ``vat_inclusive`` on the way in describes the source; on the row
        it is always true.
        """
        if data["effective_to"] < data["effective_from"]:
            raise AppError("effective_to must be on or after effective_from.")
        if (
            data.get("child_min_age") is not None
            and data.get("child_max_age") is not None
            and data["child_max_age"] < data["child_min_age"]
        ):
            raise AppError("child_max_age must be greater than or equal to child_min_age.")

        vat_pct = data.pop("vat_pct", DEFAULT_VAT_PCT)
        stated_inclusive = data.pop("vat_inclusive", True)
        data["rate_per_night"] = to_vat_inclusive(
            data["rate_per_night"], vat_inclusive=stated_inclusive, vat_pct=vat_pct
        )
        if data.get("child_rate") is not None:
            data["child_rate"] = to_vat_inclusive(
                data["child_rate"], vat_inclusive=stated_inclusive, vat_pct=vat_pct
            )
        if data.get("single_supplement") is not None:
            data["single_supplement"] = to_vat_inclusive(
                data["single_supplement"],
                vat_inclusive=stated_inclusive,
                vat_pct=vat_pct,
            )
        data["currency"] = str(data["currency"]).upper()

        rate = AccommodationRate(
            accommodation_id=accommodation_id,
            vat_inclusive=True,
            vat_pct=vat_pct,
            **data,
        )
        self.db.add(rate)
        await self.db.commit()
        await self.db.refresh(rate)
        return rate

    async def select_rate(
        self,
        *,
        room_type_id: uuid.UUID,
        meal_plan_id: uuid.UUID,
        residence_category_id: uuid.UUID,
        stay_date: date,
    ) -> AccommodationRate:
        """Return the single active rate whose period contains `stay_date`.

        If several overlap, the one with the latest `effective_from` wins.
        A miss raises NotFoundError — a price is never assumed.

        This lookup does not take an occupancy, so where a sheet quotes a
        separate price per occupancy it returns the **highest** one: a room
        selected without a headcount is the room as the hotel sells it, and a
        double is not priced as a single. The tiebreak is explicit rather than
        left to row order — since occupancy joined rate identity, several rows now
        share an ``effective_from``, and without it the same quote could price two
        ways on two runs. Occupancy-aware selection for a group lives in
        :class:`~app.modules.quotes.option_pricing.OptionPricingService`.
        """
        stmt = (
            select(AccommodationRate)
            .where(
                AccommodationRate.room_type_id == room_type_id,
                AccommodationRate.meal_plan_id == meal_plan_id,
                AccommodationRate.residence_category_id == residence_category_id,
                AccommodationRate.is_active.is_(True),
                AccommodationRate.effective_from <= stay_date,
                AccommodationRate.effective_to >= stay_date,
            )
            .order_by(
                AccommodationRate.effective_from.desc(),
                AccommodationRate.occupancy.desc(),
            )
            .limit(1)
        )
        rate = (await self.db.execute(stmt)).scalar_one_or_none()
        if rate is None:
            raise NotFoundError(
                "No accommodation rate found for the given room type, meal plan, "
                "residence category, and date."
            )
        return rate
