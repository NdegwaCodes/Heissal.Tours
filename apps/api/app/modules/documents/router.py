"""The quotation document: rendered HTML, and the brand copy it prints.

Guarded by ``quote:read`` rather than ``quote:read_cost``. The document is the
client-facing artefact by definition — its view model has no cost or margin field
at all — so a sales agent who can read a quote can render one.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.documents.schemas import DocumentConfigRead, DocumentConfigUpdate
from app.modules.documents.service import (
    DocumentConfigService,
    QuotationDocumentService,
)
from app.modules.users.models import User

router = APIRouter(tags=["documents"])


@router.get("/document-config", response_model=DocumentConfigRead)
async def get_document_config(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("settings:read")),
):
    return await DocumentConfigService(db).get()


@router.patch("/document-config", response_model=DocumentConfigRead)
async def update_document_config(
    body: DocumentConfigUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("settings:update")),
):
    """Reword the standing copy, or swap the placeholder fonts for the real ones."""
    patch = body.model_dump(exclude_unset=True)
    return await DocumentConfigService(db).update(patch, updated_by=actor.id)


@router.get(
    "/quotes/{quote_id}/document.html",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}}},
)
async def render_quotation_html(
    quote_id: uuid.UUID,
    version: int | None = Query(
        default=None,
        ge=1,
        description="Version number to render. Defaults to the latest issued one.",
    ),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:read")),
):
    """Render an issued quotation as a self-contained HTML document.

    Only an *issued* quote has a document: the version is what freezes the
    figures, and rendering live ones would produce a proposal whose numbers move
    between reloads. Passing ``version`` renders an earlier one exactly as the
    client received it.
    """
    html = await QuotationDocumentService(db).render_html(
        quote_id, version_number=version
    )
    return HTMLResponse(content=html)
