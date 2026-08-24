"""Stage 3.2 — upload, extract, confirm, end to end through the API.

The rule under test is the one the milestone exists to enforce: **a parsed
number never becomes a rate until a person accepts it.** Extraction writes
proposals; only confirmation writes to ``accommodation_rates``.

The document is a PDF built by the test itself (``tests/pdf_fixture.py``) in the
same shape as the Swahili Beach contract. The real sheets are confidential
supplier contracts and are deliberately not in this repository.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.modules.accommodations.models import Accommodation, AccommodationRate
from tests.conftest import auth_headers
from tests.pdf_fixture import (
    make_pdf,
    swahili_beach_shaped_pdf,
    temple_point_shaped_pdf,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = settings.API_V1_STR
D = Decimal


@pytest_asyncio.fixture(loop_scope="session")
async def property_for_ingestion(client: AsyncClient, admin_tokens, sample_catalogue):
    """A property with the two room types the fixture sheet names.

    Its own property, not a seeded one: these tests create rates, and a shared
    read-mostly catalogue must not be mutated by execution order.
    """
    h = auth_headers(admin_tokens)
    tag = uuid.uuid4().hex[:8]
    resp = await client.post(
        f"{API}/accommodations",
        headers=h,
        json={
            "name": f"Ingestion Test Resort {tag}",
            "slug": f"ingestion-test-{tag}",
            "destination_id": sample_catalogue["destination_diani"],
            "category": "resort",
        },
    )
    assert resp.status_code == 201, resp.text
    accommodation_id = resp.json()["id"]

    rooms = {}
    for name, code in (("Standard Room", "STD"), ("Superior Room", "SUP")):
        r = await client.post(
            f"{API}/accommodations/{accommodation_id}/room-types",
            headers=h,
            json={"name": name, "code": code, "max_occupancy": 3},
        )
        assert r.status_code == 201, r.text
        rooms[name] = r.json()["id"]

    yield {"accommodation_id": accommodation_id, "rooms": rooms}

    # Removed through the ORM, not the API: there is no DELETE route for an
    # accommodation, and calling one returned 405 while looking like cleanup.
    async with AsyncSessionLocal() as db:
        acc = await db.get(Accommodation, uuid.UUID(accommodation_id))
        if acc is not None:
            await db.delete(acc)  # cascades to room types, rates and documents
            await db.commit()


async def _upload(client, h, *, content: bytes, accommodation_id=None, filename="rates.pdf"):
    data = {}
    if accommodation_id:
        data["accommodation_id"] = str(accommodation_id)
    return await client.post(
        f"{API}/supplier-documents",
        headers=h,
        files={"file": (filename, content, "application/pdf")},
        data=data,
    )


async def test_upload_extract_confirm_creates_rates(
    client: AsyncClient, admin_tokens, property_for_ingestion, sample_catalogue
):
    """The whole path, and the counts at each step."""
    h = auth_headers(admin_tokens)
    accommodation_id = property_for_ingestion["accommodation_id"]

    resp = await _upload(
        client, h, content=swahili_beach_shaped_pdf(), accommodation_id=accommodation_id
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["status"] == "uploaded"
    assert len(doc["checksum"]) == 64
    assert doc["byte_size"] > 0

    # Extraction alone must not create a single rate.
    async with AsyncSessionLocal() as db:
        before = len(
            (
                await db.execute(
                    select(AccommodationRate).where(
                        AccommodationRate.accommodation_id == uuid.UUID(accommodation_id)
                    )
                )
            ).scalars().all()
        )

    resp = await client.post(
        f"{API}/supplier-documents/{doc['id']}/extract",
        headers=h,
        json={"residence_category": "citizen", "rate_kind": "sto"},
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    # The composite names the reader that won, so a surprising result can be
    # traced to the code that produced it.
    assert summary["provider"] == "pdf-composite:pdf-grid"
    assert summary["total_rows"] == 12
    assert summary["complete_rows"] == 12
    assert summary["needs_other_provider"] is False

    async with AsyncSessionLocal() as db:
        after_extract = len(
            (
                await db.execute(
                    select(AccommodationRate).where(
                        AccommodationRate.accommodation_id == uuid.UUID(accommodation_id)
                    )
                )
            ).scalars().all()
        )
    assert after_extract == before, "extraction must not write rates"

    resp = await client.get(
        f"{API}/supplier-documents/{doc['id']}/extractions", headers=h
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 12
    assert all(r["status"] == "pending" for r in rows)

    # The parser read the Swahili Beach figures, dot-thousands and all.
    proposals = {
        (
            r["proposed"]["room_type"],
            r["proposed"]["occupancy"],
            r["proposed"]["season_name"],
        ): r
        for r in rows
    }
    high_standard_single = proposals[("Standard Room", 1, "High")]
    assert high_standard_single["proposed"]["rate_per_night"] == "23920"
    assert high_standard_single["proposed"]["currency"] == "KES"
    assert high_standard_single["proposed"]["meal_plan"] == "BB"
    assert high_standard_single["proposed"]["effective_from"] == "2026-01-04"

    # Confirm two rows: one accepted, one rejected.
    accept = proposals[("Standard Room", 1, "High")]
    reject = proposals[("Superior Room", 2, "Low")]
    resp = await client.post(
        f"{API}/supplier-documents/{doc['id']}/confirm",
        headers=h,
        json={
            "rows": [
                {
                    "extraction_id": accept["id"],
                    "accept": True,
                    "residence_category_id": sample_catalogue["residence_citizen"],
                    "rate_kind": "sto",
                },
                {
                    "extraction_id": reject["id"],
                    "accept": False,
                    "reviewer_note": "superseded by a later contract",
                },
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert (result["confirmed"], result["rejected"], result["failed"]) == (1, 1, 0)

    rate_id = next(r["rate_id"] for r in result["rows"] if r["status"] == "confirmed")
    async with AsyncSessionLocal() as db:
        rate = await db.get(AccommodationRate, uuid.UUID(rate_id))
        assert rate is not None
        # The stored rate is the sheet's own figure, with provenance.
        assert rate.rate_per_night == D("23920.0000")
        assert rate.occupancy == 1
        assert rate.currency == "KES"
        assert rate.season_name == "High"
        assert rate.rate_kind == "sto"
        assert str(rate.source_document_id) == doc["id"]
        # VAT-inclusive by default, so the engine never taxes it twice.
        assert rate.vat_inclusive is True
        assert rate.vat_pct == D("16.00")


async def test_a_rejected_row_creates_nothing_and_stays_rejected(
    client: AsyncClient, admin_tokens, property_for_ingestion, sample_catalogue
):
    """Rejection is a decision that must survive, not a skipped row."""
    h = auth_headers(admin_tokens)
    resp = await _upload(
        client,
        h,
        content=swahili_beach_shaped_pdf(),
        accommodation_id=property_for_ingestion["accommodation_id"],
        filename="reject-case.pdf",
    )
    doc_id = resp.json()["id"]
    await client.post(f"{API}/supplier-documents/{doc_id}/extract", headers=h, json={})
    rows = (
        await client.get(f"{API}/supplier-documents/{doc_id}/extractions", headers=h)
    ).json()

    target = rows[0]
    resp = await client.post(
        f"{API}/supplier-documents/{doc_id}/confirm",
        headers=h,
        json={"rows": [{"extraction_id": target["id"], "accept": False}]},
    )
    assert resp.json()["rejected"] == 1
    assert resp.json()["rows"][0]["rate_id"] is None

    # Deciding twice on the same row is refused rather than silently repeated.
    resp = await client.post(
        f"{API}/supplier-documents/{doc_id}/confirm",
        headers=h,
        json={"rows": [{"extraction_id": target["id"], "accept": True}]},
    )
    body = resp.json()
    assert body["failed"] == 1
    assert "rejected" in body["rows"][0]["error"]


async def test_reviewer_values_override_what_the_parser_read(
    client: AsyncClient, admin_tokens, property_for_ingestion, sample_catalogue
):
    """The reviewer is the authority; the proposal is only a default.

    A confirm screen that cannot correct a misread number teaches people to
    click through, which defeats the point of confirming at all.
    """
    h = auth_headers(admin_tokens)
    resp = await _upload(
        client,
        h,
        content=swahili_beach_shaped_pdf(),
        accommodation_id=property_for_ingestion["accommodation_id"],
        filename="override-case.pdf",
    )
    doc_id = resp.json()["id"]
    await client.post(f"{API}/supplier-documents/{doc_id}/extract", headers=h, json={})
    rows = (
        await client.get(f"{API}/supplier-documents/{doc_id}/extractions", headers=h)
    ).json()
    target = next(r for r in rows if r["proposed"]["occupancy"] == 2)

    resp = await client.post(
        f"{API}/supplier-documents/{doc_id}/confirm",
        headers=h,
        json={
            "rows": [
                {
                    "extraction_id": target["id"],
                    "accept": True,
                    "residence_category_id": sample_catalogue["residence_non_resident"],
                    "room_type_id": property_for_ingestion["rooms"]["Superior Room"],
                    "occupancy": 3,
                    "rate_per_night": "44000",
                    "currency": "usd",
                    "season_name": "Corrected",
                    "supplier_discount_pct": "15",
                    "reviewer_note": "sheet misread; taken from page 4",
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["confirmed"] == 1
    rate_id = resp.json()["rows"][0]["rate_id"]

    async with AsyncSessionLocal() as db:
        rate = await db.get(AccommodationRate, uuid.UUID(rate_id))
        assert rate.rate_per_night == D("44000.0000")
        assert rate.occupancy == 3
        assert rate.currency == "USD"  # normalised, not stored as typed
        assert rate.season_name == "Corrected"
        assert rate.supplier_discount_pct == D("15.000")
        assert str(rate.room_type_id) == property_for_ingestion["rooms"]["Superior Room"]


async def test_an_incomplete_row_is_refused_with_a_reason_not_guessed(
    client: AsyncClient, admin_tokens, property_for_ingestion, sample_catalogue
):
    """A row missing something essential must fail loudly on confirm.

    The alternative — inventing an occupancy or a currency — is how a rate the
    supplier never quoted reaches a client.
    """
    h = auth_headers(admin_tokens)
    resp = await _upload(
        client,
        h,
        content=swahili_beach_shaped_pdf(column_without_occupancy=True),
        accommodation_id=property_for_ingestion["accommodation_id"],
        filename="ambiguous.pdf",
    )
    doc_id = resp.json()["id"]
    summary = (
        await client.post(
            f"{API}/supplier-documents/{doc_id}/extract", headers=h, json={}
        )
    ).json()
    # The four labelled columns still read cleanly; the unlabelled one does not.
    assert summary["total_rows"] == 15
    assert summary["complete_rows"] == 12
    assert summary["incomplete_rows"] == 3

    rows = (
        await client.get(f"{API}/supplier-documents/{doc_id}/extractions", headers=h)
    ).json()
    row = next(r for r in rows if r["proposed"]["occupancy"] is None)
    assert row["proposed"]["rate_per_night"] is not None, "the price was still read"
    assert any("how many guests" in w for w in row["proposed"]["warnings"])

    # The reviewer picks the room type, which the sheet's heading did not match.
    # Occupancy is then the only thing still unknown, and it is unknowable from
    # this document, so confirming must refuse and say why.
    resp = await client.post(
        f"{API}/supplier-documents/{doc_id}/confirm",
        headers=h,
        json={
            "rows": [
                {
                    "extraction_id": row["id"],
                    "accept": True,
                    "room_type_id": property_for_ingestion["rooms"]["Standard Room"],
                    "residence_category_id": sample_catalogue["residence_citizen"],
                }
            ]
        },
    )
    body = resp.json()
    assert body["confirmed"] == 0
    assert body["failed"] == 1
    assert "occupancy" in body["rows"][0]["error"].lower()


async def test_the_same_document_cannot_be_uploaded_twice(
    client: AsyncClient, admin_tokens, property_for_ingestion
):
    """Re-sending a sheet is routine; two review queues for it are not."""
    h = auth_headers(admin_tokens)
    pdf = make_pdf([(40, 700, "DUPLICATE CHECK 2026"), (40, 675, "nothing to parse")])
    first = await _upload(
        client,
        h,
        content=pdf,
        accommodation_id=property_for_ingestion["accommodation_id"],
        filename="dup.pdf",
    )
    assert first.status_code == 201

    second = await _upload(
        client,
        h,
        content=pdf,
        accommodation_id=property_for_ingestion["accommodation_id"],
        filename="dup-renamed.pdf",
    )
    assert second.status_code == 409, second.text
    assert "already been uploaded" in second.text


async def test_a_scan_is_reported_rather_than_treated_as_empty(
    client: AsyncClient, admin_tokens, property_for_ingestion
):
    """An image-only sheet must ask for the vision provider, not read as zero rates.

    Three documents in the real corpus are scans. Reporting them as "no rates
    found" would quietly lose a property's whole price list.
    """
    h = auth_headers(admin_tokens)
    resp = await _upload(
        client,
        h,
        content=make_pdf([]),  # a valid PDF with no text at all
        accommodation_id=property_for_ingestion["accommodation_id"],
        filename="scan.pdf",
    )
    doc_id = resp.json()["id"]
    summary = (
        await client.post(
            f"{API}/supplier-documents/{doc_id}/extract", headers=h, json={}
        )
    ).json()
    assert summary["total_rows"] == 0
    assert summary["needs_other_provider"] is True
    assert any("no text layer" in w for w in summary["warnings"])

    doc = (await client.get(f"{API}/supplier-documents/{doc_id}", headers=h)).json()
    assert doc["status"] == "failed"
    assert "no text layer" in doc["extraction_error"]


async def test_re_extraction_replaces_pending_but_keeps_decisions(
    client: AsyncClient, admin_tokens, property_for_ingestion, sample_catalogue
):
    """Re-running with a better hint must not undo what a person decided."""
    h = auth_headers(admin_tokens)
    resp = await _upload(
        client,
        h,
        content=swahili_beach_shaped_pdf(),
        accommodation_id=property_for_ingestion["accommodation_id"],
        filename="reextract.pdf",
    )
    doc_id = resp.json()["id"]
    await client.post(f"{API}/supplier-documents/{doc_id}/extract", headers=h, json={})
    rows = (
        await client.get(f"{API}/supplier-documents/{doc_id}/extractions", headers=h)
    ).json()

    decided = rows[0]
    await client.post(
        f"{API}/supplier-documents/{doc_id}/confirm",
        headers=h,
        json={"rows": [{"extraction_id": decided["id"], "accept": False}]},
    )

    await client.post(f"{API}/supplier-documents/{doc_id}/extract", headers=h, json={})
    after = (
        await client.get(f"{API}/supplier-documents/{doc_id}/extractions", headers=h)
    ).json()

    surviving = {r["id"]: r for r in after}
    assert decided["id"] in surviving, "a reviewed row must not be deleted"
    assert surviving[decided["id"]]["status"] == "rejected"
    assert sum(1 for r in after if r["status"] == "pending") == 12


async def test_confirming_needs_its_own_permission(
    client: AsyncClient, admin_tokens, property_for_ingestion
):
    """Uploading is clerical; confirming writes prices onto client quotations.

    A sales agent has neither permission, so both are refused for them.
    """
    h = auth_headers(admin_tokens)
    email = f"agent+{uuid.uuid4().hex[:8]}@heissaltest.com"
    resp = await client.post(
        f"{API}/users",
        headers=h,
        json={
            "email": email,
            "full_name": "Ingest Agent",
            "password": "AgentPass123!",
            "role_keys": ["sales_agent"],
        },
    )
    assert resp.status_code == 201, resp.text

    tokens = (
        await client.post(
            f"{API}/auth/login", data={"username": email, "password": "AgentPass123!"}
        )
    ).json()
    agent_h = auth_headers(tokens)

    resp = await _upload(
        client,
        agent_h,
        content=make_pdf([(40, 700, "AGENT UPLOAD 2026")]),
        accommodation_id=property_for_ingestion["accommodation_id"],
    )
    assert resp.status_code == 403

    resp = await client.post(
        f"{API}/supplier-documents/{uuid.uuid4()}/confirm",
        headers=agent_h,
        json={"rows": [{"extraction_id": str(uuid.uuid4()), "accept": True}]},
    )
    assert resp.status_code == 403


async def test_a_document_with_no_property_cannot_confirm_rates(
    client: AsyncClient, admin_tokens
):
    """A rate cannot exist without knowing whose it is."""
    h = auth_headers(admin_tokens)
    # Unique content: an unattached document is deduplicated on
    # (checksum, NULL property), so identical bytes would collide with whatever a
    # previous run of this test left behind.
    resp = await _upload(
        client,
        h,
        content=swahili_beach_shaped_pdf(marker=f"ref {uuid.uuid4().hex}"),
        filename="orphan.pdf",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]
    await client.post(f"{API}/supplier-documents/{doc_id}/extract", headers=h, json={})
    rows = (
        await client.get(f"{API}/supplier-documents/{doc_id}/extractions", headers=h)
    ).json()

    resp = await client.post(
        f"{API}/supplier-documents/{doc_id}/confirm",
        headers=h,
        json={"rows": [{"extraction_id": rows[0]["id"], "accept": True}]},
    )
    assert resp.status_code == 400
    assert "attach this document to a property" in resp.text.lower()


async def test_the_transposed_layout_is_read_by_the_block_reader(
    client: AsyncClient, admin_tokens, property_for_ingestion, sample_catalogue
):
    """Meal plans as columns, occupancy as the row label, two season blocks.

    Temple Point's shape. The grid reader returns nothing for it, so this proves
    the composite picks the reader by result rather than by guessing the layout,
    and that a price is matched to the meal plan above it and the season block
    above that.
    """
    h = auth_headers(admin_tokens)
    resp = await _upload(
        client,
        h,
        content=temple_point_shaped_pdf(),
        accommodation_id=property_for_ingestion["accommodation_id"],
        filename="transposed.pdf",
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]

    summary = (
        await client.post(
            f"{API}/supplier-documents/{doc_id}/extract", headers=h, json={}
        )
    ).json()
    assert summary["provider"] == "pdf-composite:pdf-block"
    assert summary["total_rows"] == 32
    assert summary["complete_rows"] == 32

    rows = (
        await client.get(f"{API}/supplier-documents/{doc_id}/extractions", headers=h)
    ).json()
    by_key = {
        (
            r["proposed"]["room_type"],
            r["proposed"]["occupancy"],
            r["proposed"]["meal_plan"],
            r["proposed"]["season_name"],
        ): r["proposed"]
        for r in rows
    }

    # The sheet's own figures: Creek Deluxe full board is 28,400 single in high
    # season and 37,000 in the festive season, and the windows differ.
    high = by_key[("CREEK DELUXE", 1, "FB", "High")]
    festive = by_key[("CREEK DELUXE", 1, "FB", "Festive")]
    assert high["rate_per_night"] == "28400"
    assert festive["rate_per_night"] == "37000"
    assert high["effective_from"] == "2027-01-11"
    assert festive["effective_from"] == "2027-12-20"

    # "BO" (bed only) maps onto the seeded RO plan rather than becoming a new one.
    assert by_key[("CREEK DELUXE", 1, "RO", "High")]["rate_per_night"] == "21600"

    # Occupancy is the row label here, and both rooms carry their own prices.
    assert by_key[("CREEK DELUXE", 2, "FB", "High")]["rate_per_night"] == "37600"
    assert by_key[("BOUTIQUE", 3, "FB", "High")]["rate_per_night"] == "39900"

    # These rows are confirmable without a reviewer filling anything in.
    resp = await client.post(
        f"{API}/supplier-documents/{doc_id}/confirm",
        headers=h,
        json={
            "rows": [
                {
                    "extraction_id": next(
                        r["id"]
                        for r in rows
                        if r["proposed"]["room_type"] == "CREEK DELUXE"
                        and r["proposed"]["occupancy"] == 1
                        and r["proposed"]["meal_plan"] == "FB"
                        and r["proposed"]["season_name"] == "High"
                    ),
                    "accept": True,
                    "room_type_id": property_for_ingestion["rooms"]["Standard Room"],
                    "residence_category_id": sample_catalogue["residence_citizen"],
                    "rate_kind": "sto",
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["confirmed"] == 1
    async with AsyncSessionLocal() as db:
        rate = await db.get(
            AccommodationRate, uuid.UUID(resp.json()["rows"][0]["rate_id"])
        )
        assert rate.rate_per_night == D("28400.0000")
        assert rate.occupancy == 1


async def test_shared_defaults_make_a_partly_read_sheet_confirmable(
    client: AsyncClient, admin_tokens, property_for_ingestion, sample_catalogue
):
    """One value set once, applied to every row that lacks it.

    This is what makes a half-read document usable. Real sheets commonly omit
    the same field on every row — the residence category is never printed at all
    — and without shared defaults a reviewer retypes it for every rate. A row's
    own value still wins, so a default cannot overwrite what was read.
    """
    h = auth_headers(admin_tokens)
    resp = await _upload(
        client,
        h,
        content=swahili_beach_shaped_pdf(column_without_occupancy=True),
        accommodation_id=property_for_ingestion["accommodation_id"],
        filename="defaults.pdf",
    )
    doc_id = resp.json()["id"]
    await client.post(f"{API}/supplier-documents/{doc_id}/extract", headers=h, json={})
    rows = (
        await client.get(f"{API}/supplier-documents/{doc_id}/extractions", headers=h)
    ).json()

    unlabelled = [r for r in rows if r["proposed"]["occupancy"] is None]
    labelled = next(r for r in rows if r["proposed"]["occupancy"] == 2)
    assert len(unlabelled) == 3

    resp = await client.post(
        f"{API}/supplier-documents/{doc_id}/confirm",
        headers=h,
        json={
            "defaults": {
                "residence_category_id": sample_catalogue["residence_citizen"],
                "room_type_id": property_for_ingestion["rooms"]["Standard Room"],
                "occupancy": 2,
                "rate_kind": "sto",
            },
            "rows": [
                # Takes occupancy 2 from the defaults.
                {"extraction_id": unlabelled[0]["id"], "accept": True},
                # States its own occupancy, which must survive the defaults.
                {"extraction_id": labelled["id"], "accept": True, "occupancy": 3},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["confirmed"], body["failed"]) == (2, 0)

    by_extraction = {r["extraction_id"]: r["rate_id"] for r in body["rows"]}
    async with AsyncSessionLocal() as db:
        from_default = await db.get(
            AccommodationRate, uuid.UUID(by_extraction[unlabelled[0]["id"]])
        )
        from_row = await db.get(
            AccommodationRate, uuid.UUID(by_extraction[labelled["id"]])
        )
    assert from_default.occupancy == 2, "the default supplied the missing occupancy"
    assert from_row.occupancy == 3, "the row's own value must win over the default"
    assert from_default.rate_kind == "sto" and from_row.rate_kind == "sto"
