"""Recording what happened to a quote, end to end (§5.1).

The rules are covered in ``test_outcomes.py``. This is the wiring — and the
wiring is the point of the stage, because until now there was **no way at all**
to mark a quote won or lost. Two endpoints and a report, over real issued
quotes.
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
from app.modules.quotes.models import Quote, QuoteOption
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal

ARRIVAL, DEPARTURE = "2026-07-01", "2026-07-04"


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest_asyncio.fixture(loop_scope="session")
async def decided_away():
    """Remove the quotes this module decided, so the funnel stays this file's.

    The conversion report is global by design — a business asks "what is our
    win rate", not "what is it among these four quotes" — so a module that
    asserts on totals has to clean up after itself, and one that asserts on
    *its own* quotes has to be able to find them. This file does the second and
    still tidies.
    """
    made: list[str] = []
    yield made
    async with AsyncSessionLocal() as db:
        for quote_id in made:
            row = await db.get(Quote, uuid.UUID(quote_id))
            if row is not None:
                await db.delete(row)
        await db.commit()


async def _issued(client, h, ids, made, *, pax=2, arrival=ARRIVAL, departure=DEPARTURE):
    """A quote with two options, issued, with the second recommended."""
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Outcome Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("outcome"),
            "residence_category_id": ids["residence_citizen"],
        },
    )
    assert record.status_code == 201, record.text
    created = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record.json()["id"],
            "presentation_currency": "KES",
            "residence_category_id": ids["residence_citizen"],
            "arrival_date": arrival,
            "departure_date": departure,
            "pax_count": pax,
            "requested_meal_plan_id": ids["meal_plan_fb"],
            "options": [
                {"accommodation_id": ids["acc_sto_full_board"], "sort_order": 1},
                {
                    "accommodation_id": ids["acc_rack_discounted"],
                    "is_recommended": True,
                    "sort_order": 2,
                },
            ],
        },
    )
    assert created.status_code == 201, created.text
    quote = created.json()
    made.append(quote["id"])
    priced = await client.post(
        f"{API}/quotes/{quote['id']}/options/price", headers=h
    )
    assert priced.status_code == 200, priced.text
    issued = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert issued.status_code == 200, issued.text
    return quote, issued.json()


async def _option_ids(quote_id: str) -> dict[bool, str]:
    """This quote's options, keyed on whether they were recommended."""
    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(QuoteOption).where(
                        QuoteOption.quote_id == uuid.UUID(quote_id)
                    )
                )
            )
            .scalars()
            .all()
        )
    return {bool(row.is_recommended): str(row.id) for row in rows}


async def test_a_quote_can_finally_be_accepted(
    client, admin_tokens, sample_catalogue, decided_away
):
    """The first thing in this system that can set the status.

    ``accepted`` has been a declared status since Stage 2 with nothing able to
    write it, which is why every conversion figure until now would have been
    zero.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(client, h, ids, decided_away)
    options = await _option_ids(quote["id"])

    accepted = await client.post(
        f"{API}/quotes/{quote['id']}/accept",
        headers=h,
        json={"option_id": options[True], "note": "Signed at the meeting."},
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["status"] == "accepted"
    assert body["effective_status"] == "accepted"
    assert body["selected_option_id"] == options[True]
    assert body["decided_at"] is not None
    assert body["decision_note"] == "Signed at the meeting."


async def test_accepting_says_which_option_or_is_refused(
    client, admin_tokens, sample_catalogue, decided_away
):
    """A quote offers several, so "yes" without one leaves the booking undecided.

    Operations cannot book a hotel from an acceptance that does not name it,
    and the revenue figure would be whichever option somebody assumed.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(client, h, ids, decided_away)
    refused = await client.post(
        f"{API}/quotes/{quote['id']}/accept", headers=h, json={}
    )
    assert refused.status_code == 400, refused.text
    assert "which option" in refused.json()["error"]["message"]


async def test_an_option_chosen_earlier_is_enough(
    client, admin_tokens, sample_catalogue, decided_away
):
    """Choosing and accepting stay separate events (§7).

    The gap between "they like the second one" and "they signed" is worth
    measuring, so ``/select`` does not accept and ``/accept`` does not insist
    on being told twice.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(client, h, ids, decided_away)
    options = await _option_ids(quote["id"])
    chosen = await client.post(
        f"{API}/quotes/{quote['id']}/select",
        headers=h,
        json={"option_id": options[False]},
    )
    assert chosen.status_code == 200, chosen.text
    # Still sent: selecting is not accepting.
    assert chosen.json()["status"] == "sent"

    accepted = await client.post(
        f"{API}/quotes/{quote['id']}/accept", headers=h, json={}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["selected_option_id"] == options[False]


async def test_a_quote_can_be_declined_with_a_reason(
    client, admin_tokens, sample_catalogue, decided_away
):
    """The reason is the part the business can act on."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(client, h, ids, decided_away)
    declined = await client.post(
        f"{API}/quotes/{quote['id']}/decline",
        headers=h,
        json={"note": "Went with a competitor on price."},
    )
    assert declined.status_code == 200, declined.text
    body = declined.json()
    assert body["status"] == "declined"
    assert "competitor" in body["decision_note"]


async def test_a_draft_cannot_be_decided(
    client, admin_tokens, sample_catalogue, decided_away
):
    """Nothing has been in front of the client yet."""
    h, ids = _h(admin_tokens), sample_catalogue
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Draft Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("draftoutcome"),
            "residence_category_id": ids["residence_citizen"],
        },
    )
    created = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record.json()["id"],
            "presentation_currency": "KES",
            "residence_category_id": ids["residence_citizen"],
            "arrival_date": ARRIVAL,
            "departure_date": DEPARTURE,
            "pax_count": 2,
            "requested_meal_plan_id": ids["meal_plan_fb"],
            "options": [{"accommodation_id": ids["acc_sto_full_board"]}],
        },
    )
    assert created.status_code == 201, created.text
    decided_away.append(created.json()["id"])
    refused = await client.post(
        f"{API}/quotes/{created.json()['id']}/decline", headers=h, json={}
    )
    assert refused.status_code == 400, refused.text
    assert "still a draft" in refused.json()["error"]["message"]


async def test_a_decision_cannot_be_overwritten(
    client, admin_tokens, sample_catalogue, decided_away
):
    """A second outcome would lose what the client actually decided."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(client, h, ids, decided_away)
    await client.post(f"{API}/quotes/{quote['id']}/decline", headers=h, json={})
    options = await _option_ids(quote["id"])
    refused = await client.post(
        f"{API}/quotes/{quote['id']}/accept",
        headers=h,
        json={"option_id": options[True]},
    )
    assert refused.status_code == 400, refused.text
    assert "already declined" in refused.json()["error"]["message"]


async def test_an_expired_quote_reads_as_expired_and_cannot_be_accepted(
    client, admin_tokens, sample_catalogue, decided_away
):
    """Rates have moved, so the honest answer is a re-issue.

    Expiry is derived: the row still says ``sent`` and every read says
    ``expired``, with no scheduler in between.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(client, h, ids, decided_away)
    async with AsyncSessionLocal() as db:
        row = await db.get(Quote, uuid.UUID(quote["id"]))
        row.valid_until = date.today() - timedelta(days=1)
        await db.commit()

    read = await client.get(f"{API}/quotes/{quote['id']}", headers=h)
    assert read.json()["status"] == "sent"
    assert read.json()["effective_status"] == "expired"

    options = await _option_ids(quote["id"])
    refused = await client.post(
        f"{API}/quotes/{quote['id']}/accept",
        headers=h,
        json={"option_id": options[True]},
    )
    assert refused.status_code == 400, refused.text
    message = refused.json()["error"]["message"]
    assert "expired on" in message
    assert "re-issue" in message

    # Declining it is still allowed and still worth recording.
    declined = await client.post(
        f"{API}/quotes/{quote['id']}/decline",
        headers=h,
        json={"note": "Lapsed; client never came back."},
    )
    assert declined.status_code == 200, declined.text


async def test_the_conversion_report_counts_this_files_quotes(
    client, admin_tokens, sample_catalogue, decided_away
):
    """One accepted, one declined, one still out — and the money per currency.

    Filtered to today so the report is about the quotes this test issued
    rather than everything in the database.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    today = date.today().isoformat()

    before = await client.get(
        f"{API}/quotes/analytics/conversion",
        headers=h,
        params={"since": today, "until": today},
    )
    assert before.status_code == 200, before.text
    start = before.json()

    won, won_version = await _issued(client, h, ids, decided_away)
    lost, lost_version = await _issued(client, h, ids, decided_away)
    await _issued(client, h, ids, decided_away)

    options = await _option_ids(won["id"])
    await client.post(
        f"{API}/quotes/{won['id']}/accept",
        headers=h,
        json={"option_id": options[True]},
    )
    await client.post(
        f"{API}/quotes/{lost['id']}/decline",
        headers=h,
        json={"note": "Dates moved."},
    )

    after = (
        await client.get(
            f"{API}/quotes/analytics/conversion",
            headers=h,
            params={"since": today, "until": today},
        )
    ).json()

    assert after["counts"]["accepted"] == start["counts"].get("accepted", 0) + 1
    assert after["counts"]["declined"] == start["counts"].get("declined", 0) + 1
    assert after["counts"]["sent"] == start["counts"].get("sent", 0) + 1

    # The money moved by exactly the versions' own selling prices.
    grew = D(after["won"].get("KES", 0)) - D(start["won"].get("KES", 0))
    assert grew == D(won_version["selling_price"])
    fell = D(after["lost"].get("KES", 0)) - D(start["lost"].get("KES", 0))
    assert fell == D(lost_version["selling_price"])

    # Decided the same day it was issued, so the median is zero days.
    assert after["median_days_to_decide"] == 0
    # And the client took the recommendation, which is the figure §3.7 exists
    # to be checked against.
    assert (
        after["recommendation_taken"] == start["recommendation_taken"] + 1
    )


async def test_the_report_needs_no_cost_permission(
    client, admin_tokens, sample_catalogue, decided_away
):
    """Selling values only — these are the figures clients were shown.

    Margin lives on the internal version read, behind ``quote:read_cost``, so
    a sales manager can see the funnel without seeing what things cost us.
    """
    h = _h(admin_tokens)
    report = await client.get(f"{API}/quotes/analytics/conversion", headers=h)
    assert report.status_code == 200, report.text
    body = report.json()
    assert set(body) == {
        "counts",
        "won",
        "lost",
        "outstanding",
        "win_rate",
        "median_days_to_decide",
        "recommendation_taken",
        "recommendation_declined",
        "recommendation_rate",
    }
    # Nothing in it names cost, margin or a supplier payment.
    assert not [key for key in body if "cost" in key or "margin" in key]


async def test_recording_an_outcome_is_its_own_permission():
    """Deciding what the business believes about itself is a separate trust.

    The win rate, the pipeline value and every report built on them come from
    these two endpoints, and a quote marked accepted by mistake is a booking
    somebody expects to happen.
    """
    from app.modules.rbac.permissions import PERMISSIONS, ROLE_DEFINITIONS

    assert "quote:record_outcome" in PERMISSIONS
    assert "quote:record_outcome" in ROLE_DEFINITIONS["admin"]["permissions"]
