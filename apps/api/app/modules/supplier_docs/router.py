"""Supplier-document ingestion routes: upload, extract, review, confirm.

Split across two permissions on purpose. Uploading a rate sheet is clerical;
confirming one writes prices that end up on client quotations, so it is guarded
separately (``supplier_doc:confirm``) and can be granted to fewer people.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import require_permission
from app.core.errors import AppError
from app.db.session import get_db
from app.modules.supplier_docs.models import EXTRACTION_STATUSES, SupplierDocument
from app.modules.supplier_docs.schemas import (
    ConfirmRequest,
    ConfirmResult,
    ExtractHint,
    ExtractionRead,
    ExtractionSummary,
    SupplierDocumentRead,
)
from app.modules.supplier_docs.service import IngestionService
from app.modules.users.models import User

router = APIRouter(tags=["supplier-documents"])

READ = "supplier_doc:read"
MANAGE = "supplier_doc:manage"
CONFIRM = "supplier_doc:confirm"

_ALLOWED_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
}


@router.get("/supplier-documents", response_model=list[SupplierDocumentRead])
async def list_documents(
    accommodation_id: uuid.UUID | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    stmt = select(SupplierDocument)
    if accommodation_id:
        stmt = stmt.where(SupplierDocument.accommodation_id == accommodation_id)
    if status:
        stmt = stmt.where(SupplierDocument.status == status)
    rows = (
        await db.execute(stmt.order_by(SupplierDocument.created_at.desc()))
    ).scalars().all()
    return rows


@router.get("/supplier-documents/{document_id}", response_model=SupplierDocumentRead)
async def get_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await IngestionService(db).get_document(document_id)


@router.post("/supplier-documents", response_model=SupplierDocumentRead, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    accommodation_id: uuid.UUID | None = Form(default=None),
    supplier_id: uuid.UUID | None = Form(default=None),
    notes: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(MANAGE)),
):
    content_type = (file.content_type or "").lower()
    if content_type and content_type not in _ALLOWED_TYPES:
        raise AppError(
            f"{content_type} is not an accepted rate-sheet format. "
            "Upload a PDF or a scanned image."
        )
    content = await file.read()
    # Read-then-check rather than streaming: the cap is small enough that the
    # bytes are already in memory, and refusing after the fact is clearer than a
    # truncated file that parses into half a rate sheet.
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise AppError(
            f"That file is {len(content) // (1024 * 1024)} MB; the limit is "
            f"{settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )
    return await IngestionService(db).store_document(
        content=content,
        filename=file.filename or "upload.pdf",
        content_type=content_type or "application/pdf",
        accommodation_id=accommodation_id,
        supplier_id=supplier_id,
        notes=notes,
        uploaded_by=user.id,
    )


@router.post(
    "/supplier-documents/{document_id}/extract", response_model=ExtractionSummary
)
async def extract_document(
    document_id: uuid.UUID,
    hint: ExtractHint | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    """Parse the document into candidate rows. Writes no rates.

    Safe to re-run with a better hint; pending proposals are replaced and
    already-reviewed rows are left untouched.
    """
    return await IngestionService(db).extract(document_id, hint or ExtractHint())


@router.get(
    "/supplier-documents/{document_id}/extractions",
    response_model=list[ExtractionRead],
)
async def list_extractions(
    document_id: uuid.UUID,
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    if status and status not in EXTRACTION_STATUSES:
        raise AppError(f"status must be one of {', '.join(EXTRACTION_STATUSES)}")
    return await IngestionService(db).list_extractions(document_id, status)


@router.post(
    "/supplier-documents/{document_id}/confirm", response_model=ConfirmResult
)
async def confirm_extractions(
    document_id: uuid.UUID,
    body: ConfirmRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CONFIRM)),
):
    """Apply review decisions. This is the only path that creates rates."""
    return await IngestionService(db).confirm(
        document_id, body.rows, reviewer=user.id, defaults=body.defaults
    )
