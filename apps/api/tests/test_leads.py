"""Leads, the pipeline and the morning list, end to end (§5.2).

The rules are in ``test_pipeline.py``. This is the wiring, and three things
about it are worth more than the rest:

* the stages are **rows**, so a client who calls "Negotiating" something else
  renames it and no report changes;
* every move writes **history**, which is what makes it a pipeline rather than
  a status column;
* and a lead's quotes carry their outcome back to its **source** (§5.1), which
  is the join that answers where the marketing money should go.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.modules.leads.models import Lead
from app.modules.quotes.models import Quote
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal
TODAY = date.today()


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest_asyncio.fixture(loop_scope="session")
async def swept():
    """Delete the leads (and their quotes) this module created."""
    made: list[str] = []
    yield made
    async with AsyncSessionLocal() as db:
        for lead_id in made:
            quotes = (
                (
                    await db.execute(
                        select(Quote).where(Quote.lead_id == uuid.UUID(lead_id))
                    )
                )
                .scalars()
                .all()
            )
            for quote in quotes:
                await db.delete(quote)
            await db.flush()
            row = await db.get(Lead, uuid.UUID(lead_id))
            if row is not None:
                await db.delete(row)
        await db.commit()


async def _stages(client, h) -> dict[str, dict]:
    listed = await client.get(f"{API}/lead-stages", headers=h)
    assert listed.status_code == 200, listed.text
    return {row["key"]: row for row in listed.json()}


async def _lead(client, h, swept, **over):
    body = {
        "contact_name": f"Enquirer {uuid.uuid4().hex[:6]}",
        "contact_email": unique_email("lead"),
        "source": "Website",
        "destination_interest": "Somewhere on the coast",
        "pax_estimate": 6,
    }
    body.update(over)
    created = await client.post(f"{API}/leads", headers=h, json=body)
    assert created.status_code == 201, created.text
    swept.append(created.json()["id"])
    return created.json()


# --------------------------------------------------------------------------- #
# The pipeline is configuration
# --------------------------------------------------------------------------- #


async def test_a_generic_pipeline_is_seeded_and_is_coherent(client, admin_tokens):
    """A fresh install gets something usable, and the first rename makes it theirs.

    Seeded lazily rather than in the reference seeder because the pipeline is
    the client's to own — and they have not told us their stages, which is the
    whole reason these are rows.
    """
    h = _h(admin_tokens)
    stages = await _stages(client, h)
    assert set(stages) >= {"new", "quoted", "won", "lost"}
    assert stages["new"]["is_default"] is True
    assert stages["won"]["is_won"] is True
    assert stages["lost"]["is_lost"] is True

    report = await client.get(f"{API}/leads/pipeline", headers=h)
    assert report.status_code == 200, report.text
    # Nothing wrong with the shape: one entry stage, one won, one lost.
    assert report.json()["problems"] == []


async def test_a_stage_can_be_renamed_without_changing_a_figure(
    client, admin_tokens, swept
):
    """"Won" becomes "Booked and deposit paid" and every report still works.

    Because they ask which stage *means* won rather than comparing a name —
    which is the property that lets the client own their own sales process.
    """
    h = _h(admin_tokens)
    stages = await _stages(client, h)
    won = stages["won"]

    lead = await _lead(client, h, swept)
    await client.post(
        f"{API}/leads/{lead['id']}/move",
        headers=h,
        json={"stage_id": won["id"], "note": "Deposit received."},
    )
    before = (await client.get(f"{API}/leads/pipeline", headers=h)).json()

    renamed = await client.patch(
        f"{API}/lead-stages/{won['id']}",
        headers=h,
        json={"name": "Booked and deposit paid"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Booked and deposit paid"
    assert renamed.json()["key"] == "won"
    assert renamed.json()["is_won"] is True

    after = (await client.get(f"{API}/leads/pipeline", headers=h)).json()
    assert after["won_leads"] == before["won_leads"]
    assert after["win_rate"] == before["win_rate"]
    # The table shows the new name against the same key.
    row = next(one for one in after["stages"] if one["stage"] == "won")
    assert row["name"] == "Booked and deposit paid"

    # Put it back, so the rest of the suite reads what it expects.
    await client.patch(
        f"{API}/lead-stages/{won['id']}", headers=h, json={"name": "Won"}
    )


# --------------------------------------------------------------------------- #
# Recording an enquiry
# --------------------------------------------------------------------------- #


async def test_an_enquiry_lands_at_the_entry_stage_with_a_next_action(
    client, admin_tokens, swept
):
    """Both halves matter.

    The stage is where new enquiries arrive, and the next action is defaulted a
    few days out — demanding one would make the form an obstacle while the
    phone is ringing, and leaving it empty is how a lead disappears.
    """
    h = _h(admin_tokens)
    stages = await _stages(client, h)
    lead = await _lead(client, h, swept)
    assert lead["stage_id"] == stages["new"]["id"]
    assert lead["next_action_on"] is not None
    assert date.fromisoformat(lead["next_action_on"]) > TODAY
    # The arrival itself is in the history, or the time spent in the entry
    # stage would be invisible.
    assert len(lead["events"]) == 1
    assert lead["events"][0]["from_stage_id"] is None
    assert "Enquiry received" in lead["events"][0]["note"]


async def test_a_source_is_normalised_so_a_report_has_one_row_per_channel(
    client, admin_tokens, swept
):
    """"Website", "walk-in" and "Walk In" are not three channels."""
    h = _h(admin_tokens)
    first = await _lead(client, h, swept, source="Website")
    second = await _lead(client, h, swept, source="  WEB-SITE ")
    third = await _lead(client, h, swept, source="Walk In")
    assert first["source"] == "website"
    assert second["source"] == "web_site"
    assert third["source"] == "walk_in"


async def test_a_lead_needs_no_client_record(client, admin_tokens, swept):
    """An enquiry arrives as a name and a phone number.

    The client record is created when there is something to quote; refusing the
    lead until then would mean typing a client for every call that goes nowhere.
    """
    h = _h(admin_tokens)
    lead = await _lead(client, h, swept, contact_phone="+254700000000")
    assert lead["client_id"] is None
    assert lead["contact_phone"] == "+254700000000"


async def test_a_budget_needs_an_amount_and_a_currency(
    client, admin_tokens, swept
):
    """Money is NUMERIC plus a currency here as everywhere else (§3.2).

    Even for a figure as soft as "they said about four hundred thousand".
    """
    h = _h(admin_tokens)
    refused = await client.post(
        f"{API}/leads",
        headers=h,
        json={
            "contact_name": "Half a budget",
            "contact_email": unique_email("budget"),
            "budget_amount": "400000",
        },
    )
    assert refused.status_code == 400, refused.text
    assert "amount and a currency" in refused.json()["error"]["message"]

    ok = await _lead(
        client, h, swept, budget_amount="400000", budget_currency="kes"
    )
    assert ok["budget_currency"] == "KES"


async def test_travel_dates_the_wrong_way_round_are_refused(
    client, admin_tokens, swept
):
    h = _h(admin_tokens)
    refused = await client.post(
        f"{API}/leads",
        headers=h,
        json={
            "contact_name": "Backwards",
            "contact_email": unique_email("backwards"),
            "travel_from": "2026-08-10",
            "travel_to": "2026-08-01",
        },
    )
    assert refused.status_code == 400, refused.text


# --------------------------------------------------------------------------- #
# Moving through the pipeline
# --------------------------------------------------------------------------- #


async def test_every_move_is_recorded(client, admin_tokens, swept):
    """The history is the pipeline; the current stage is a convenience.

    "Eleven at quoted" is a number. "Eleven at quoted, median nineteen days" is
    a morning's work, and only the events can say the second.
    """
    h = _h(admin_tokens)
    stages = await _stages(client, h)
    lead = await _lead(client, h, swept)

    for key, note in (("qualified", "Spoke to them."), ("quoted", "Sent option A.")):
        moved = await client.post(
            f"{API}/leads/{lead['id']}/move",
            headers=h,
            json={"stage_id": stages[key]["id"], "note": note},
        )
        assert moved.status_code == 200, moved.text

    final = (await client.get(f"{API}/leads/{lead['id']}", headers=h)).json()
    assert final["stage_id"] == stages["quoted"]["id"]
    assert [one["note"] for one in final["events"]] == [
        "Enquiry received.",
        "Spoke to them.",
        "Sent option A.",
    ]
    # And each event knows who moved it.
    assert all(one["by"] is not None for one in final["events"][1:])


async def test_losing_a_lead_requires_a_reason(client, admin_tokens, swept):
    """"We lost eleven" is a fact nobody can act on."""
    h = _h(admin_tokens)
    stages = await _stages(client, h)
    lead = await _lead(client, h, swept)

    refused = await client.post(
        f"{API}/leads/{lead['id']}/move",
        headers=h,
        json={"stage_id": stages["lost"]["id"]},
    )
    assert refused.status_code == 400, refused.text
    assert "Say why the lead was lost" in refused.json()["error"]["message"]

    lost = await client.post(
        f"{API}/leads/{lead['id']}/move",
        headers=h,
        json={
            "stage_id": stages["lost"]["id"],
            "lost_reason": "Went with a competitor on price.",
        },
    )
    assert lost.status_code == 200, lost.text
    assert "competitor" in lost.json()["lost_reason"]


async def test_closing_a_lead_clears_its_next_action(client, admin_tokens, swept):
    """A won deal has no follow-up call and a lost one is not a task.

    Leaving the date behind would keep both on somebody's morning list forever,
    which is how a list stops being read.
    """
    h = _h(admin_tokens)
    stages = await _stages(client, h)
    lead = await _lead(client, h, swept)
    assert lead["next_action_on"] is not None

    won = await client.post(
        f"{API}/leads/{lead['id']}/move",
        headers=h,
        json={"stage_id": stages["won"]["id"]},
    )
    assert won.status_code == 200, won.text
    assert won.json()["next_action_on"] is None


async def test_moving_a_lead_where_it_already_is_is_refused(
    client, admin_tokens, swept
):
    """It would write a stage change that did not happen into the history."""
    h = _h(admin_tokens)
    stages = await _stages(client, h)
    lead = await _lead(client, h, swept)
    refused = await client.post(
        f"{API}/leads/{lead['id']}/move",
        headers=h,
        json={"stage_id": stages["new"]["id"]},
    )
    assert refused.status_code == 400, refused.text
    assert "already at" in refused.json()["error"]["message"]


# --------------------------------------------------------------------------- #
# The morning list
# --------------------------------------------------------------------------- #


async def test_a_lead_with_no_next_action_is_first_on_the_list(
    client, admin_tokens, swept
):
    """The one that would otherwise die quietly."""
    h = _h(admin_tokens)
    lead = await _lead(client, h, swept)
    cleared = await client.patch(
        f"{API}/leads/{lead['id']}", headers=h, json={"next_action_on": None}
    )
    assert cleared.status_code == 200, cleared.text

    listed = await client.get(f"{API}/leads/attention", headers=h)
    assert listed.status_code == 200, listed.text
    mine = next(
        row for row in listed.json() if row["lead"]["id"] == lead["id"]
    )
    assert mine["reasons"][0]["code"] == "lead_no_next_action"
    # And it is at the top of the whole list, not merely of its own reasons.
    assert listed.json()[0]["reasons"][0]["code"] == "lead_no_next_action"


async def test_an_overdue_lead_says_how_late_it_is(client, admin_tokens, swept):
    h = _h(admin_tokens)
    lead = await _lead(client, h, swept)
    await client.post(
        f"{API}/leads/{lead['id']}/next-action",
        headers=h,
        params={"on": (TODAY - timedelta(days=6)).isoformat(), "note": "Call back"},
    )
    listed = await client.get(f"{API}/leads/attention", headers=h)
    mine = next(row for row in listed.json() if row["lead"]["id"] == lead["id"])
    overdue = next(
        one for one in mine["reasons"] if one["code"] == "lead_next_action_overdue"
    )
    assert overdue["days"] == 6


async def test_a_closed_lead_is_off_the_list(client, admin_tokens, swept):
    h = _h(admin_tokens)
    stages = await _stages(client, h)
    lead = await _lead(client, h, swept)
    await client.patch(
        f"{API}/leads/{lead['id']}", headers=h, json={"next_action_on": None}
    )
    await client.post(
        f"{API}/leads/{lead['id']}/move",
        headers=h,
        json={
            "stage_id": stages["lost"]["id"],
            "lost_reason": "Dates did not work.",
        },
    )
    listed = await client.get(f"{API}/leads/attention", headers=h)
    assert lead["id"] not in [row["lead"]["id"] for row in listed.json()]


# --------------------------------------------------------------------------- #
# Source to booking
# --------------------------------------------------------------------------- #


async def test_a_source_is_answerable_for_the_booking_not_the_enquiry(
    client, admin_tokens, sample_catalogue, swept
):
    """The join §5.1 was missing: which channels convert.

    A lead from "wedding_fair" produces a quote, the quote is accepted, and the
    source's win column moves. Counting enquiries alone would flatter whichever
    channel is loudest.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    source = f"fair_{uuid.uuid4().hex[:6]}"
    lead = await _lead(client, h, swept, source=source)

    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Lead Client {uuid.uuid4().hex[:6]}",
            "email": unique_email("leadclient"),
            "residence_category_id": ids["residence_citizen"],
        },
    )
    quote = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record.json()["id"],
            "lead_id": lead["id"],
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
    await client.post(f"{API}/quotes/{quote_id}/options/price", headers=h)
    await client.post(f"{API}/quotes/{quote_id}/issue", headers=h)

    mid = (await client.get(f"{API}/leads/pipeline", headers=h)).json()
    row = next(one for one in mid["sources"] if one["source"] == source)
    assert row["leads"] == 1
    assert row["quoted"] == 1
    # Quoted is not won: the client has not answered yet.
    assert row["won"] == 0
    assert D(row["win_rate"]) == D("0.0000")

    async with AsyncSessionLocal() as db:
        option_id = (
            await db.execute(
                select(Quote.selected_option_id).where(
                    Quote.id == uuid.UUID(quote_id)
                )
            )
        ).scalar_one_or_none()
    accepted = await client.post(
        f"{API}/quotes/{quote_id}/accept",
        headers=h,
        json={"option_id": str(option_id) if option_id else None}
        if option_id
        else {"option_id": quote.json()["options"][0]["id"]},
    )
    assert accepted.status_code == 200, accepted.text

    after = (await client.get(f"{API}/leads/pipeline", headers=h)).json()
    row = next(one for one in after["sources"] if one["source"] == source)
    assert row["won"] == 1
    assert D(row["win_rate"]) == D("1.0000")


async def test_open_budgets_are_reported_per_currency(
    client, admin_tokens, swept
):
    """Never summed across currencies, and only while the lead is open."""
    h = _h(admin_tokens)
    before = (await client.get(f"{API}/leads/pipeline", headers=h)).json()
    await _lead(
        client, h, swept, budget_amount="500000", budget_currency="KES"
    )
    await _lead(client, h, swept, budget_amount="4000", budget_currency="USD")
    after = (await client.get(f"{API}/leads/pipeline", headers=h)).json()

    grew = D(after["open_budget"].get("KES", 0)) - D(
        before["open_budget"].get("KES", 0)
    )
    assert grew == D("500000")
    assert D(after["open_budget"]["USD"]) - D(
        before["open_budget"].get("USD", 0)
    ) == D("4000")


async def test_the_pipeline_lists_every_stage_including_the_empty_ones(
    client, admin_tokens
):
    """An empty stage is the most interesting cell in the table."""
    h = _h(admin_tokens)
    stages = await _stages(client, h)
    report = (await client.get(f"{API}/leads/pipeline", headers=h)).json()
    assert [one["stage"] for one in report["stages"]] == sorted(
        stages, key=lambda key: stages[key]["sort_order"]
    )


async def test_configuring_the_pipeline_is_its_own_permission():
    """An agent moves leads; a manager decides what the pipeline is.

    Reordering the stages changes what every pipeline report means, which is a
    different kind of act from working a lead through them.
    """
    from app.modules.rbac.permissions import PERMISSIONS, ROLE_DEFINITIONS

    assert "lead:manage" in PERMISSIONS
    assert "lead:configure_pipeline" in PERMISSIONS
    agent = ROLE_DEFINITIONS["sales_agent"]["permissions"]
    assert "lead:manage" in agent
    assert "lead:configure_pipeline" not in agent
