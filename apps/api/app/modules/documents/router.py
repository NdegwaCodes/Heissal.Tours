"""The quotation document: rendered HTML, and the brand copy it prints.

The proposal is guarded by ``quote:read`` rather than ``quote:read_cost``. It is
the client-facing artefact by definition — its view model has no cost or margin
field at all — so a sales agent who can read a quote can render one.

The **costing worksheet** on the same quote is the mirror of it (§3.12) and is
guarded by ``quote:read_cost``, because it is the half of the same information
the client must never see.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, Response
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
    inline_assets: bool = Query(
        default=True,
        description=(
            "Embed the photographs in the document. False links them instead, "
            "for a preview whose fetcher can authenticate."
        ),
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
        quote_id, version_number=version, inline_assets=inline_assets
    )
    return HTMLResponse(content=html)


@router.get(
    "/quotes/{quote_id}/worksheet.html",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}}},
)
async def render_costing_worksheet(
    quote_id: uuid.UUID,
    version: int | None = Query(
        default=None,
        ge=1,
        description="Version number to explain. Defaults to the latest issued one.",
    ),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:read_cost")),
):
    """The internal costing worksheet: the mirror of the client document (§3.12).

    Every line with its basis, the multiplier applied and the row it came from,
    plus the build-up and the three numbers realised margin is made of. Gated
    on ``quote:read_cost`` — the same permission that gates the internal
    pricing read — because this is the document the client must never see.
    """
    html = await QuotationDocumentService(db).render_worksheet_html(
        quote_id, version_number=version
    )
    return HTMLResponse(content=html)


@router.get(
    "/quotes/{quote_id}/document.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def render_quotation_pdf(
    quote_id: uuid.UUID,
    version: int | None = Query(
        default=None,
        ge=1,
        description="Version number to print. Defaults to the latest issued one.",
    ),
    download: bool = Query(
        default=True,
        description="Send as an attachment. False renders inline in the browser.",
    ),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:read")),
):
    """Print an issued quotation to PDF (§3.11).

    The same document as ``document.html``, through a headless browser, with
    every photograph embedded so the file stands alone once it leaves here. The
    filename carries the quote number and version, because two versions of one
    quote are two different documents and a client will quote the name back.
    """
    pdf, filename = await QuotationDocumentService(db).render_pdf(
        quote_id, version_number=version
    )
    disposition = "attachment" if download else "inline"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )
