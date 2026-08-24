"""Supplier-document ingestion: store, extract, confirm.

The business rule this module exists to enforce is that **no parsed number
becomes a rate without a person agreeing to it**. Extraction writes proposals to
``supplier_document_extractions``; only :meth:`IngestionService.confirm` writes
to ``accommodation_rates``, and only for rows a reviewer accepted. A parser or a
vision model is treated as a suggestion, never as a source of truth, because a
wrong money value that reaches a client is a commercial incident.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.storage import save_bytes
from app.integrations.rate_extraction import (
    ExtractedRateRow,
    ExtractionHint,
    ExtractionResult,
    RateExtractionProvider,
)
from app.modules.accommodations.models import (
    Accommodation,
    AccommodationRate,
    MealPlan,
    RoomType,
)
from app.modules.residence.models import ResidenceCategory
from app.modules.supplier_docs.extraction import default_extractor
from app.modules.supplier_docs.models import (
    SupplierDocument,
    SupplierDocumentExtraction,
)
from app.modules.supplier_docs.schemas import (
    ConfirmResult,
    ConfirmResultRow,
    ConfirmRow,
    ExtractHint,
    ExtractionSummary,
)

_SUBDIR = "supplier-docs"


def _serialise(row: ExtractedRateRow) -> dict[str, Any]:
    """JSON-safe form of a candidate row, stored verbatim for the reviewer.

    Money is kept as a string so the proposal in the database reads exactly as
    the parser understood it; a float here would be a rounding error waiting to
    be confirmed by someone who trusted the screen.
    """
    return {
        "room_type": row.room_type,
        "meal_plan": row.meal_plan,
        "occupancy": row.occupancy,
        "season_name": row.season_name,
        "effective_from": row.effective_from.isoformat() if row.effective_from else None,
        "effective_to": row.effective_to.isoformat() if row.effective_to else None,
        "currency": row.currency,
        "rate_per_night": str(row.rate_per_night) if row.rate_per_night is not None else None,
        "warnings": list(row.warnings),
        "source_note": row.source_note,
        "page": row.page,
        "is_complete": row.is_complete,
    }


class IngestionService:
    def __init__(
        self, db: AsyncSession, provider: RateExtractionProvider | None = None
    ) -> None:
        self.db = db
        self.provider = provider or default_extractor()

    # -- upload ----------------------------------------------------------- #

    async def store_document(
        self,
        *,
        content: bytes,
        filename: str,
        content_type: str,
        accommodation_id: uuid.UUID | None,
        supplier_id: uuid.UUID | None,
        notes: str | None,
        uploaded_by: uuid.UUID | None,
    ) -> SupplierDocument:
        if not content:
            raise AppError("The uploaded file is empty.")
        if accommodation_id is not None:
            if await self.db.get(Accommodation, accommodation_id) is None:
                raise NotFoundError("Accommodation not found.")

        stored = save_bytes(content, filename=filename, subdir=_SUBDIR)

        # The same rate sheet gets sent twice routinely (the corpus contains a
        # file literally named "... - Copy.pdf"), so a re-upload is reported
        # rather than quietly creating a second document with its own review
        # queue for identical rates.
        existing = (
            await self.db.execute(
                select(SupplierDocument).where(
                    SupplierDocument.checksum == stored.checksum,
                    SupplierDocument.accommodation_id == accommodation_id,
                )
            )
        ).scalars().first()
        if existing is not None:
            raise ConflictError(
                "This document has already been uploaded for this property "
                f"(as {existing.original_filename!r}). Review that upload instead."
            )

        doc = SupplierDocument(
            accommodation_id=accommodation_id,
            supplier_id=supplier_id,
            original_filename=filename[:300],
            storage_path=stored.storage_path,
            content_type=content_type or "application/octet-stream",
            byte_size=stored.byte_size,
            checksum=stored.checksum,
            status="uploaded",
            notes=notes,
            uploaded_by=uploaded_by,
        )
        self.db.add(doc)
        await self.db.commit()
        await self.db.refresh(doc)
        return doc

    async def get_document(self, document_id: uuid.UUID) -> SupplierDocument:
        doc = await self.db.get(SupplierDocument, document_id)
        if doc is None:
            raise NotFoundError("Supplier document not found.")
        return doc

    # -- extract ---------------------------------------------------------- #

    async def extract(
        self, document_id: uuid.UUID, hint: ExtractHint
    ) -> ExtractionSummary:
        """Run extraction and replace this document's pending proposals.

        Re-running is allowed and is the normal response to a bad first pass with
        a better hint. Rows already reviewed are left alone: re-extraction must
        not silently undo a decision a person made.
        """
        doc = await self.get_document(document_id)

        pending = (
            await self.db.execute(
                select(SupplierDocumentExtraction).where(
                    SupplierDocumentExtraction.document_id == doc.id,
                    SupplierDocumentExtraction.status == "pending",
                )
            )
        ).scalars().all()
        for stale in pending:
            await self.db.delete(stale)

        doc.status = "extracting"
        await self.db.flush()

        result = self._run_provider(doc, hint)

        if result.rows:
            doc.status = "extracted"
            doc.extraction_error = None
        else:
            doc.status = "failed"
            doc.extraction_error = "; ".join(result.warnings)[:2000] or "No rates found."

        complete = 0
        for index, row in enumerate(result.rows):
            complete += bool(row.is_complete)
            self.db.add(
                SupplierDocumentExtraction(
                    document_id=doc.id,
                    row_index=index,
                    status="pending",
                    proposed=_serialise(row),
                    confidence=row.confidence,
                )
            )
        await self.db.commit()

        return ExtractionSummary(
            document_id=doc.id,
            provider=result.provider,
            page_count=result.page_count,
            total_rows=len(result.rows),
            complete_rows=complete,
            incomplete_rows=len(result.rows) - complete,
            warnings=result.warnings,
            needs_other_provider=result.needs_other_provider,
        )

    def _run_provider(self, doc: SupplierDocument, hint: ExtractHint) -> ExtractionResult:
        if not self.provider.supports(doc.content_type, doc.original_filename):
            return ExtractionResult(
                warnings=[
                    f"{doc.content_type} is not something the {self.provider.name} "
                    f"provider can read"
                ],
                provider=self.provider.name,
            )
        return self.provider.extract(
            doc.storage_path,
            ExtractionHint(
                residence_category=hint.residence_category,
                rate_kind=hint.rate_kind,
                default_currency=hint.default_currency,
                default_meal_plan=hint.default_meal_plan,
                supplier_discount_pct=hint.supplier_discount_pct,
                vat_inclusive=hint.vat_inclusive,
                vat_pct=hint.vat_pct,
            ),
        )

    async def list_extractions(
        self, document_id: uuid.UUID, status: str | None = None
    ) -> list[SupplierDocumentExtraction]:
        await self.get_document(document_id)
        stmt = select(SupplierDocumentExtraction).where(
            SupplierDocumentExtraction.document_id == document_id
        )
        if status:
            stmt = stmt.where(SupplierDocumentExtraction.status == status)
        rows = (
            await self.db.execute(stmt.order_by(SupplierDocumentExtraction.row_index))
        ).scalars().all()
        return list(rows)

    # -- confirm ---------------------------------------------------------- #

    async def confirm(
        self, document_id: uuid.UUID, decisions: list[ConfirmRow], *, reviewer: uuid.UUID
    ) -> ConfirmResult:
        """Apply a reviewer's decisions. Accepted rows become stored rates.

        Each row is resolved independently and a failure is reported against that
        row rather than aborting the batch, because one unresolvable room name
        should not discard twenty good decisions the reviewer just made.
        """
        doc = await self.get_document(document_id)
        if doc.accommodation_id is None:
            raise AppError(
                "Attach this document to a property before confirming rates: a "
                "rate cannot exist without knowing whose it is."
            )

        results: list[ConfirmResultRow] = []
        confirmed = rejected = failed = 0
        now = datetime.now(UTC)

        for decision in decisions:
            row = await self.db.get(SupplierDocumentExtraction, decision.extraction_id)
            if row is None or row.document_id != doc.id:
                failed += 1
                results.append(
                    ConfirmResultRow(
                        extraction_id=decision.extraction_id,
                        status="failed",
                        error="No such proposed row on this document.",
                    )
                )
                continue
            if row.status != "pending":
                failed += 1
                results.append(
                    ConfirmResultRow(
                        extraction_id=row.id,
                        status="failed",
                        error=f"Already {row.status}; re-extract to review it again.",
                    )
                )
                continue

            if not decision.accept:
                row.status = "rejected"
                row.reviewer_note = decision.reviewer_note
                row.reviewed_by = reviewer
                row.reviewed_at = now
                rejected += 1
                results.append(ConfirmResultRow(extraction_id=row.id, status="rejected"))
                continue

            try:
                rate = await self._build_rate(doc, row, decision)
            except AppError as exc:
                failed += 1
                results.append(
                    ConfirmResultRow(
                        extraction_id=row.id, status="failed", error=str(exc)
                    )
                )
                continue

            self.db.add(rate)
            await self.db.flush()
            row.status = "confirmed"
            row.reviewer_note = decision.reviewer_note
            row.reviewed_by = reviewer
            row.reviewed_at = now
            row.created_rate_id = rate.id
            confirmed += 1
            results.append(
                ConfirmResultRow(
                    extraction_id=row.id, status="confirmed", rate_id=rate.id
                )
            )

        still_pending = (
            await self.db.execute(
                select(SupplierDocumentExtraction.id).where(
                    SupplierDocumentExtraction.document_id == doc.id,
                    SupplierDocumentExtraction.status == "pending",
                )
            )
        ).first()
        if still_pending is None:
            doc.status = "reviewed"

        await self.db.commit()
        return ConfirmResult(
            confirmed=confirmed, rejected=rejected, failed=failed, rows=results
        )

    async def _build_rate(
        self,
        doc: SupplierDocument,
        row: SupplierDocumentExtraction,
        decision: ConfirmRow,
    ) -> AccommodationRate:
        """Resolve one accepted row into a rate, or explain why it cannot be.

        The reviewer's value always wins over the parser's; the proposal is only
        a default. Anything still missing is an error rather than a guess.
        """
        proposed = row.proposed or {}

        room_type_id = decision.room_type_id
        if room_type_id is None:
            room_type_id = await self._match_room_type(
                doc.accommodation_id, proposed.get("room_type")
            )
        if room_type_id is None:
            raise AppError(
                "Choose a room type: the sheet's "
                f"{proposed.get('room_type')!r} does not match one on this property."
            )
        room = await self.db.get(RoomType, room_type_id)
        if room is None or room.accommodation_id != doc.accommodation_id:
            raise AppError("That room type does not belong to this property.")

        meal_plan_id = decision.meal_plan_id or await self._code_id(
            MealPlan, proposed.get("meal_plan")
        )
        if meal_plan_id is None:
            raise AppError("Choose a meal plan: the sheet did not state one clearly.")

        residence_id = decision.residence_category_id
        if residence_id is None:
            residence_id = await self._residence_id(proposed.get("residence_category"))
        if residence_id is None:
            raise AppError(
                "Choose a residence category: resident and non-resident rates "
                "differ materially and must not be merged."
            )

        occupancy = decision.occupancy or proposed.get("occupancy")
        if not occupancy:
            raise AppError(
                "Set the occupancy: this column does not say how many guests the "
                "price covers, and the rate is meaningless without it."
            )

        amount = decision.rate_per_night
        if amount is None and proposed.get("rate_per_night"):
            amount = Decimal(str(proposed["rate_per_night"]))
        if amount is None or amount <= 0:
            raise AppError("Set the nightly rate.")

        currency = decision.currency or proposed.get("currency")
        if not currency:
            raise AppError("Set the currency: the sheet does not state one.")

        start = decision.effective_from or _as_date(proposed.get("effective_from"))
        end = decision.effective_to or _as_date(proposed.get("effective_to"))
        if start is None or end is None:
            raise AppError("Set the season dates: the sheet's window could not be read.")
        if end < start:
            raise AppError("The season end date is before its start date.")

        if (
            decision.child_min_age is not None
            and decision.child_max_age is not None
            and decision.child_max_age < decision.child_min_age
        ):
            raise AppError("child_max_age must be greater than or equal to child_min_age.")

        # An identical rate already on file means this document is being
        # confirmed twice; the uniqueness constraint would raise deep inside the
        # flush, so it is caught here with something a reviewer can act on.
        clash = (
            await self.db.execute(
                select(AccommodationRate.id).where(
                    AccommodationRate.room_type_id == room_type_id,
                    AccommodationRate.meal_plan_id == meal_plan_id,
                    AccommodationRate.residence_category_id == residence_id,
                    AccommodationRate.occupancy == int(occupancy),
                    AccommodationRate.effective_from == start,
                )
            )
        ).first()
        if clash is not None:
            raise AppError(
                "A rate already exists for that room, meal plan, residence, "
                "occupancy and start date. Edit the existing rate instead."
            )

        return AccommodationRate(
            accommodation_id=doc.accommodation_id,
            room_type_id=room_type_id,
            meal_plan_id=meal_plan_id,
            residence_category_id=residence_id,
            season_name=decision.season_name or proposed.get("season_name") or "Standard",
            occupancy=int(occupancy),
            effective_from=start,
            effective_to=end,
            currency=currency.upper(),
            rate_per_night=amount,
            child_rate=decision.child_rate,
            child_min_age=decision.child_min_age,
            child_max_age=decision.child_max_age,
            rate_kind=decision.rate_kind or "rack",
            supplier_discount_pct=decision.supplier_discount_pct,
            vat_inclusive=True if decision.vat_inclusive is None else decision.vat_inclusive,
            vat_pct=Decimal("16") if decision.vat_pct is None else decision.vat_pct,
            source_document_id=doc.id,
        )

    async def _match_room_type(
        self, accommodation_id: uuid.UUID | None, name: str | None
    ) -> uuid.UUID | None:
        """Match a sheet's room wording to a room type on this property.

        Only an exact case-insensitive match on name or code counts. Fuzzy
        matching here would put "Superior Room" prices on a "Standard Room", so
        anything less than certain is left for the reviewer to pick.
        """
        if not accommodation_id or not name:
            return None
        wanted = " ".join(name.split()).casefold()
        rooms = (
            await self.db.execute(
                select(RoomType).where(RoomType.accommodation_id == accommodation_id)
            )
        ).scalars().all()
        for room in rooms:
            if room.name.casefold() == wanted or (room.code or "").casefold() == wanted:
                return room.id
        return None

    async def _code_id(self, model: type[MealPlan], code: str | None) -> uuid.UUID | None:
        if not code:
            return None
        found = (
            await self.db.execute(select(model).where(model.code == code))
        ).scalars().first()
        return found.id if found else None

    async def _residence_id(self, key: str | None) -> uuid.UUID | None:
        if not key:
            return None
        found = (
            await self.db.execute(
                select(ResidenceCategory).where(ResidenceCategory.key == key)
            )
        ).scalars().first()
        return found.id if found else None


def _as_date(value: Any) -> Any:
    from datetime import date as _date

    if value in (None, ""):
        return None
    if isinstance(value, _date):
        return value
    try:
        return _date.fromisoformat(str(value))
    except ValueError:
        return None
