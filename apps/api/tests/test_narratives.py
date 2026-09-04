"""Proposal copy and the gate it has to pass (§4.4).

Stage 4's last item is an "AI-generated narrative". What is built here is the
half that matters and the half that does not depend on which model: a brief of
**facts**, a draft that carries its own provenance, and one rule —

    nothing reaches a client document until a person approves it.

That is the same rule ``rate_extraction`` applies to money, for the same
reason. A wrong figure on a proposal is a commercial incident; a confidently
wrong sentence about a hotel is a smaller version of the same thing, and it is
harder to spot because it reads well.

No provider ships. There is no model configured for this project, and an HTTP
client for a vendor nobody has chosen would be worse than a seam — so the
default refuses out loud and the tests below inject a stub. The review gate
works today either way, which is the honest order to build it in.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.integrations.narrative import (
    ACCOMMODATION,
    DESTINATION,
    HAND,
    Brief,
    Draft,
    NarrativeUnavailable,
    UnavailableProvider,
)
from app.modules.accommodations.models import Accommodation
from app.modules.narratives.models import APPROVED, DRAFT, REJECTED, Narrative
from app.modules.narratives.service import NarrativeService
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal


class StubProvider:
    """A provider that writes something recognisable from the brief.

    Deliberately not a template that produces plausible marketing: the test
    needs to prove the brief reached the provider and the draft came back with
    its provenance, and prose would only make the assertions vaguer.
    """

    name = "stub"

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.briefs: list[Brief] = []

    async def write(self, brief: Brief) -> Draft:
        if self.fail:
            raise NarrativeUnavailable("the stub is having a day off")
        self.briefs.append(brief)
        return Draft(
            text=f"STUB<{brief.name}|{brief.place}|{'/'.join(brief.meal_plans)}>",
            provider=self.name,
            model="stub-1",
        )


@pytest_asyncio.fixture(loop_scope="session")
async def written_off():
    """Remove every narrative this module wrote, whatever it was about."""
    yield
    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(Narrative).where(Narrative.provider.in_(["stub", HAND, "stub+hand"]))
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            await db.delete(row)
        await db.commit()


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# --------------------------------------------------------------------------- #
# The brief
# --------------------------------------------------------------------------- #


async def test_the_brief_carries_facts_from_the_catalogue(
    client, admin_tokens, sample_catalogue
):
    """Name, category, place, and the board bases we have RATES for.

    That last one is the point: a description promising half board we cannot
    sell is a description that costs a booking the moment a client asks for
    it. So the plans come from ``accommodation_rates`` rather than from the
    property's own marketing.
    """
    async with AsyncSessionLocal() as db:
        brief = await NarrativeService(db).brief_for(
            ACCOMMODATION,
            uuid.UUID(sample_catalogue["acc_sto_full_board"]),
            steer="Ask about the reef.",
        )
    assert brief.subject == ACCOMMODATION
    assert "Coral Sands" in brief.name
    assert brief.category
    assert "Diani" in brief.place
    assert brief.meal_plans, brief
    assert all(plan for plan in brief.meal_plans)
    assert brief.room_types
    assert brief.steer == "Ask about the reef."


async def test_a_brief_refuses_to_be_about_nothing():
    """The dataclass guards itself, so no provider is called with an empty name."""
    with pytest.raises(ValueError, match="something to be about"):
        Brief(subject=ACCOMMODATION, name="")
    with pytest.raises(ValueError, match="unknown narrative subject"):
        Brief(subject="hotel_chain", name="Somewhere")
    with pytest.raises(ValueError, match="words must be positive"):
        Brief(subject=DESTINATION, name="Diani", words=0)


async def test_a_draft_must_say_what_produced_it():
    """Provenance is not optional. An untraceable sentence is undefendable."""
    with pytest.raises(ValueError, match="must say what produced it"):
        Draft(text="Lovely.", provider="")
    with pytest.raises(ValueError, match="no text is not a draft"):
        Draft(text="   ", provider="stub")


async def test_the_default_provider_refuses_out_loud():
    """No model is configured, and filler would be worse than nothing.

    A template stitching the brief together would produce "Coral Sands Resort
    is a resort in Diani offering full board" — the facts panel above it,
    retyped, going out on client documents looking like something nobody
    wrote.
    """
    with pytest.raises(NarrativeUnavailable, match="No narrative provider"):
        await UnavailableProvider().write(Brief(subject=DESTINATION, name="Diani"))


async def test_generating_with_no_provider_configured_is_refused(
    client, admin_tokens, sample_catalogue
):
    """Over the API too, with the reason and the alternative in the message."""
    h = _h(admin_tokens)
    refused = await client.post(
        f"{API}/accommodations/{sample_catalogue['acc_sto_full_board']}"
        f"/narratives/generate",
        headers=h,
        json={"steer": "Mention the reef."},
    )
    assert refused.status_code == 400, refused.text
    message = refused.json()["error"]["message"]
    assert "No narrative provider is configured" in message
    # And it says what to do instead, because an agent who is only told "no"
    # writes nothing.
    assert "same review" in message


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


async def test_a_generated_draft_is_a_draft_and_carries_its_provenance(
    client, admin_tokens, sample_catalogue, written_off
):
    """Provider, model and the brief it was given, stored beside the text.

    The same instinct as the source strings on the costing worksheet (§3.12):
    a sentence on a two-year-old proposal has to be attributable.
    """
    stub = StubProvider()
    async with AsyncSessionLocal() as db:
        row = await NarrativeService(db, provider=stub).generate(
            ACCOMMODATION,
            uuid.UUID(sample_catalogue["acc_sto_full_board"]),
            actor_id=None,
            steer="Mention the reef.",
        )
    assert row.status == DRAFT
    assert row.provider == "stub"
    assert row.model == "stub-1"
    assert row.brief["steer"] == "Mention the reef."
    assert row.brief["meal_plans"]
    assert not row.is_printable
    # The provider was given the brief, not free text.
    assert stub.briefs and stub.briefs[0].name == row.brief["name"]


async def test_an_unapproved_draft_never_reaches_the_document(
    client, admin_tokens, sample_catalogue, written_off
):
    """The whole feature, in one assertion.

    A draft exists, it is about the recommended property, and the rendered
    proposal does not contain a word of it.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    async with AsyncSessionLocal() as db:
        await NarrativeService(db, provider=StubProvider()).generate(
            ACCOMMODATION,
            uuid.UUID(ids["acc_sto_full_board"]),
            actor_id=None,
        )
    html = await _issued_document(client, h, ids)
    assert "STUB<" not in html


async def test_approved_copy_reaches_the_document_and_wins(
    client, admin_tokens, sample_catalogue, written_off
):
    """Approved copy beats the hand-written column beneath it.

    It is the newest editorial decision about the property and somebody other
    than its author signed it off, which is more than the older column can say.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    subject_id = uuid.UUID(ids["acc_sto_full_board"])
    async with AsyncSessionLocal() as db:
        # A hand-written blurb on the property, so the precedence is a real
        # choice rather than the only text available.
        accommodation = await db.get(Accommodation, subject_id)
        accommodation.blurb = "THE OLD BLURB."
        await db.commit()
        service = NarrativeService(db, provider=StubProvider())
        draft = await service.generate(ACCOMMODATION, subject_id, actor_id=None)
        await service.approve(draft.id, actor_id=None)

    html = await _issued_document(client, h, ids)
    assert "STUB&lt;" in html or "STUB<" in html
    assert "THE OLD BLURB" not in html

    async with AsyncSessionLocal() as db:
        accommodation = await db.get(Accommodation, subject_id)
        accommodation.blurb = None
        await db.commit()


async def test_approval_supersedes_the_standing_copy_and_keeps_it(
    client, admin_tokens, sample_catalogue, written_off
):
    """An issued proposal said what it said, so the old row stays.

    A client asking why this year's description differs from last year's is
    asking a question the table can answer — but only if approving a
    replacement does not delete what it replaced.
    """
    subject_id = uuid.UUID(sample_catalogue["acc_sto_full_board"])
    async with AsyncSessionLocal() as db:
        service = NarrativeService(db)
        first = await service.compose(
            ACCOMMODATION, subject_id, text="The first description.", actor_id=None
        )
        await service.approve(first.id, actor_id=None)
        second = await service.compose(
            ACCOMMODATION, subject_id, text="The second description.", actor_id=None
        )
        await service.approve(second.id, actor_id=None)

        standing = await service.printable(ACCOMMODATION, subject_id)
        assert standing is not None
        assert standing.text == "The second description."

        replaced = await db.get(Narrative, first.id)
        assert replaced.status == APPROVED
        assert replaced.superseded_at is not None
        assert not replaced.is_printable
        # And it is still in the history, which is the point of keeping it.
        history = await service.history(ACCOMMODATION, subject_id)
        assert first.id in [row.id for row in history]


async def test_an_agents_own_writing_takes_the_same_path(
    client, admin_tokens, sample_catalogue, written_off
):
    """It is usually the better paragraph, and it still gets reviewed.

    The gate is not about who wrote it. It is about somebody other than the
    author reading it before a client does.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    written = await client.post(
        f"{API}/accommodations/{ids['acc_sto_full_board']}/narratives",
        headers=h,
        json={"text": "A HAND WRITTEN PARAGRAPH."},
    )
    assert written.status_code == 201, written.text
    body = written.json()
    assert body["status"] == DRAFT
    assert body["provider"] == HAND

    html = await _issued_document(client, h, ids)
    assert "A HAND WRITTEN PARAGRAPH" not in html


async def test_a_model_draft_edited_by_a_person_records_both(
    client, admin_tokens, sample_catalogue, written_off
):
    """"stub+hand": a model proposed it and a person is answerable for it.

    Not vanity. The provenance decides who can be asked what a sentence meant,
    and after an edit the answer is the editor.
    """
    subject_id = uuid.UUID(sample_catalogue["acc_sto_full_board"])
    async with AsyncSessionLocal() as db:
        service = NarrativeService(db, provider=StubProvider())
        draft = await service.generate(ACCOMMODATION, subject_id, actor_id=None)
        revised = await service.revise(
            draft.id, text="The version a person actually wrote.", actor_id=None
        )
    assert revised.provider == f"stub+{HAND}"
    assert revised.text == "The version a person actually wrote."
    assert revised.status == DRAFT


async def test_approved_copy_cannot_be_edited(
    client, admin_tokens, sample_catalogue, written_off
):
    """Approval is of a TEXT, not of a row.

    Editing an approved narrative would put words in front of a client that
    nobody approved — the exact hole the gate exists to close — so a change to
    approved copy is a new draft and a new review.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    written = await client.post(
        f"{API}/accommodations/{ids['acc_sto_full_board']}/narratives",
        headers=h,
        json={"text": "Approved and final."},
    )
    narrative_id = written.json()["id"]
    approved = await client.post(
        f"{API}/narratives/{narrative_id}/approve", headers=h
    )
    assert approved.status_code == 200, approved.text

    refused = await client.patch(
        f"{API}/narratives/{narrative_id}",
        headers=h,
        json={"text": "Quietly changed afterwards."},
    )
    assert refused.status_code == 400, refused.text
    assert "not a draft" in refused.json()["error"]["message"]


async def test_a_rejected_draft_is_kept_with_its_reason(
    client, admin_tokens, sample_catalogue, written_off
):
    """The record of what was nearly sent, and why it was not.

    Which is what stops the next writer making the same mistake.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    written = await client.post(
        f"{API}/accommodations/{ids['acc_sto_full_board']}/narratives",
        headers=h,
        json={"text": "Claims a spa the property does not have."},
    )
    narrative_id = written.json()["id"]
    rejected = await client.post(
        f"{API}/narratives/{narrative_id}/reject",
        headers=h,
        json={"note": "No spa. Check the facilities list before writing."},
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == REJECTED
    assert "No spa" in body["review_note"]
    assert body["reviewed_at"] is not None

    listed = await client.get(
        f"{API}/accommodations/{ids['acc_sto_full_board']}/narratives", headers=h
    )
    assert narrative_id in [row["id"] for row in listed.json()]

    # And it cannot be revived: a new draft is written instead.
    revived = await client.post(
        f"{API}/narratives/{narrative_id}/approve", headers=h
    )
    assert revived.status_code == 400, revived.text
    assert "already turned down" in revived.json()["error"]["message"]


async def test_an_approved_narrative_cannot_be_rejected_out_from_under_a_proposal(
    client, admin_tokens, sample_catalogue, written_off
):
    """It may already be on an issued document, and those are immutable."""
    h, ids = _h(admin_tokens), sample_catalogue
    written = await client.post(
        f"{API}/accommodations/{ids['acc_sto_full_board']}/narratives",
        headers=h,
        json={"text": "On a proposal already."},
    )
    narrative_id = written.json()["id"]
    await client.post(f"{API}/narratives/{narrative_id}/approve", headers=h)
    refused = await client.post(
        f"{API}/narratives/{narrative_id}/reject", headers=h, json={}
    )
    assert refused.status_code == 400, refused.text
    assert "Approve a replacement" in refused.json()["error"]["message"]


async def test_the_review_queue_is_readable(
    client, admin_tokens, sample_catalogue, written_off
):
    """A gate nobody can see the queue for becomes a rubber stamp."""
    h, ids = _h(admin_tokens), sample_catalogue
    written = await client.post(
        f"{API}/accommodations/{ids['acc_sto_full_board']}/narratives",
        headers=h,
        json={"text": "Waiting for somebody to read it."},
    )
    assert written.status_code == 201, written.text
    queue = await client.get(f"{API}/narratives", headers=h, params={"status": "draft"})
    assert queue.status_code == 200, queue.text
    assert written.json()["id"] in [row["id"] for row in queue.json()]

    bad = await client.get(
        f"{API}/narratives", headers=h, params={"status": "published"}
    )
    assert bad.status_code == 400, bad.text


async def test_a_destination_narrative_works_the_same_way(
    client, admin_tokens, sample_catalogue, written_off
):
    """One pipeline for both, because a second copy of the gate is a second
    place for it to be got wrong."""
    h, ids = _h(admin_tokens), sample_catalogue
    written = await client.post(
        f"{API}/destinations/{ids['destination_diani']}/narratives",
        headers=h,
        json={"text": "A description of the coast."},
    )
    assert written.status_code == 201, written.text
    assert written.json()["subject"] == DESTINATION
    approved = await client.post(
        f"{API}/narratives/{written.json()['id']}/approve", headers=h
    )
    assert approved.status_code == 200, approved.text


async def test_approval_is_its_own_permission(
    client, admin_tokens, sample_catalogue, written_off
):
    """Writing copy and publishing it are different levels of trust.

    The same split as issuing a quotation (§3.4). A role that may write must be
    able to exist without being able to publish, or the gate is decoration.
    """
    from app.modules.rbac.permissions import PERMISSIONS, ROLE_DEFINITIONS

    assert "narrative:manage" in PERMISSIONS
    assert "narrative:approve" in PERMISSIONS
    assert "narrative:approve" in ROLE_DEFINITIONS["admin"]["permissions"]
    # Read access travels with the rest of the reference data; approval does not.
    assert "narrative:read" in PERMISSIONS


async def _issued_document(client, h, ids) -> str:
    """Issue a one-option quote on the demo property and render it."""
    quote_id = await _issued_quote(client, h, ids)
    page = await client.get(f"{API}/quotes/{quote_id}/document.html", headers=h)
    assert page.status_code == 200, page.text
    return page.text


async def _issued_quote(client, h, ids) -> str:
    """Issue a one-option quote on the demo property, and return its id."""
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Narrative Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("narrative"),
            "residence_category_id": ids["residence_citizen"],
        },
    )
    quote = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record.json()["id"],
            "presentation_currency": "KES",
            "residence_category_id": ids["residence_citizen"],
            "arrival_date": "2026-07-01",
            "departure_date": "2026-07-04",
            "pax_count": 2,
            "requested_meal_plan_id": ids["meal_plan_fb"],
            "options": [
                {
                    "accommodation_id": ids["acc_sto_full_board"],
                    "is_recommended": True,
                }
            ],
        },
    )
    assert quote.status_code == 201, quote.text
    quote_id = quote.json()["id"]
    priced = await client.post(f"{API}/quotes/{quote_id}/options/price", headers=h)
    assert priced.status_code == 200, priced.text
    issued = await client.post(f"{API}/quotes/{quote_id}/issue", headers=h)
    assert issued.status_code == 200, issued.text
    return quote_id


async def test_an_issued_proposal_keeps_the_words_it_went_out_with(
    client, admin_tokens, sample_catalogue, written_off
):
    """Approving a replacement must not rewrite a document already sent.

    §4.4 makes replacing a description a routine act rather than a rare edit,
    so resolving the paragraph at render time would have an old version quietly
    re-describing its hotels — which is the same failure the frozen money
    prevents, in prose. The text is frozen into the version at issue.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    subject_id = uuid.UUID(ids["acc_sto_full_board"])
    async with AsyncSessionLocal() as db:
        service = NarrativeService(db)
        first = await service.compose(
            ACCOMMODATION, subject_id, text="AS ISSUED, THE FIRST WORDS.", actor_id=None
        )
        await service.approve(first.id, actor_id=None)

    quote_id = await _issued_quote(client, h, ids)
    page = await client.get(f"{API}/quotes/{quote_id}/document.html", headers=h)
    assert "AS ISSUED, THE FIRST WORDS." in page.text

    # A new description is written and approved after the proposal went out.
    async with AsyncSessionLocal() as db:
        service = NarrativeService(db)
        second = await service.compose(
            ACCOMMODATION, subject_id, text="WRITTEN AFTERWARDS.", actor_id=None
        )
        await service.approve(second.id, actor_id=None)

    again = await client.get(f"{API}/quotes/{quote_id}/document.html", headers=h)
    assert "AS ISSUED, THE FIRST WORDS." in again.text
    assert "WRITTEN AFTERWARDS." not in again.text

    # And a proposal issued now does use the new words.
    fresh = await _issued_quote(client, h, ids)
    latest = await client.get(f"{API}/quotes/{fresh}/document.html", headers=h)
    assert "WRITTEN AFTERWARDS." in latest.text
