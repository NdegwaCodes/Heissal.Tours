"""Route selection (§4.2).

One question, asked the same way everywhere: **what is the road between these
two places on this date?** The answer has to survive three facts about
hand-entered reference data —

* a route may be entered in either direction, and distance is symmetric;
* a route may be re-entered for a season, and the later row wins;
* a route may not be there at all, which is a gap to report and never a figure
  to invent.

Effective dating is read the way every other rate in this codebase is (§3.1):
the latest row whose window covers the date. ``effective_to`` may be NULL,
meaning "until further notice", which is what an unchanging road is.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.quotes.routing import Road, normalise_types, plain
from app.modules.transport.models import Route


class RouteService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def find(
        self,
        *,
        origin_id: uuid.UUID | None,
        destination_id: uuid.UUID | None,
        on: date,
    ) -> Road | None:
        """The road between two places on ``on``, or ``None`` if none is on file.

        The forward row wins where both directions are entered — a one-way
        road, or a ferry queue that only bites southbound, is a real difference
        and the operator recorded it deliberately. Otherwise the reverse row is
        read backwards and says so, because refusing a route somebody has
        already entered would make every itinerary need its return typed twice.
        """
        if origin_id is None or destination_id is None:
            return None
        rows = (
            (
                await self.db.execute(
                    select(Route).where(
                        or_(
                            (Route.origin_id == origin_id)
                            & (Route.destination_id == destination_id),
                            (Route.origin_id == destination_id)
                            & (Route.destination_id == origin_id),
                        ),
                        Route.is_active.is_(True),
                        Route.effective_from <= on,
                        or_(Route.effective_to.is_(None), Route.effective_to >= on),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return None
        forward = [row for row in rows if row.origin_id == origin_id]
        # Later season first, so a row loaded to supersede another does.
        chosen = max(
            forward or rows, key=lambda row: (row.effective_from, row.created_at)
        )
        return _road(chosen, reversed_lookup=chosen.origin_id != origin_id)


def _road(row: Route, *, reversed_lookup: bool) -> Road:
    return Road(
        label=row.label or "",
        distance_km=Decimal(row.distance_km),
        drive_time_minutes=int(row.drive_time_minutes),
        required_vehicle_types=normalise_types(row.required_vehicle_types),
        reversed_lookup=reversed_lookup,
        notes=row.notes or "",
        # The row and its season, as an operator would look it up — the same
        # shape as every other source string on the worksheet (§3.12).
        source=(
            f"routes {row.id} · {plain(Decimal(row.distance_km))} km, "
            f"{row.drive_time_minutes} min from {row.effective_from}"
            + (" (read in reverse)" if reversed_lookup else "")
        ),
    )
