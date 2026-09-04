"""Booking an accepted quote, end to end (§7.1).

Where a won deal used to lead nowhere. The schedule arithmetic is in
``test_booking_schedule.py``; this is the wiring, and the four things it
defends are all about a figure nobody can argue with later:

* a booking can only be made from an **accepted** quote;
* its total comes from the **version** the client accepted, not from the quote,
  so re-pricing afterwards cannot move it;
* one trip cannot be held twice;
* and a payment in the wrong currency is refused rather than converted.
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
from app.modules.bookings.models import Booking
from app.modules.quotes.models import Quote, QuoteOption
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal
TODAY = date.today()
# Far enough out that the deposit-and-balance schedule is the two-line one
# (past the 30-day balance date), and near enough to stay inside the demo
# catalogue's rate season — which is what a real quote has to be too.
ARRIVAL = TODAY + timedelta(days=60)
DEPARTURE = ARRIVAL + timedelta(days=3)


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest_asyncio.fixture(loop_scope="session")
async def booked_away():
    """Remove the quotes (and their bookings) this module created."""
    made: list[str] = []
    yield made
    async with AsyncSessionLocal() as db:
        for quote_id in made:
            bookings = (
                (
                    await db.execute(
                        select(Booking).where(Booking.quote_id == uuid.UUID(quote_id))
                    )
                )
                .scalars()
                .all()
            )
            for booking in bookings:
                await db.delete(booking)
            await db.flush()
            row = await db.get(Quote, uuid.UUID(quote_id))
            if row is not None:
                await db.delete(row)
        await db.commit()


async def _accepted(
    client, h, ids, made, *, arrival=ARRIVAL, departure=DEPARTURE, accept=True
):
    """A quote, issued, and accepted on its recommended option."""
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Booking Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("booking"),
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
            "arrival_date": arrival.isoformat(),
            "departure_date": departure.isoformat(),
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
    assert created.status_code == 201, created.text
    quote = created.json()
    made.append(quote["id"])
    priced = await client.post(
        f"{API}/quotes/{quote['id']}/options/price", headers=h
    )
    assert priced.status_code == 200, priced.text
    issued = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert issued.status_code == 200, issued.text
    if not accept:
        return quote, issued.json()

    async with AsyncSessionLocal() as db:
        option_id = (
            (
                await db.execute(
                    select(QuoteOption.id).where(
                        QuoteOption.quote_id == uuid.UUID(quote["id"])
                    )
                )
            )
            .scalars()
            .first()
        )
    accepted = await client.post(
        f"{API}/quotes/{quote['id']}/accept",
        headers=h,
        json={"option_id": str(option_id)},
    )
    assert accepted.status_code == 200, accepted.text
    return quote, issued.json()


# --------------------------------------------------------------------------- #
# Creating a booking
# --------------------------------------------------------------------------- #


async def test_an_accepted_quote_becomes_a_booking_with_a_schedule(
    client, admin_tokens, sample_catalogue, booked_away
):
    """The thing that used to lead nowhere.

    Reference, dates, headcount and total all on the booking, plus a deposit
    due now and a balance due before travel.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, version = await _accepted(client, h, ids, booked_away)

    booked = await client.post(
        f"{API}/quotes/{quote['id']}/booking",
        headers=h,
        json={"notes": "Client signed at the meeting."},
    )
    assert booked.status_code == 201, booked.text
    body = booked.json()
    assert body["reference"].startswith(f"HTB-{TODAY.year}-")
    assert body["status"] == "provisional"
    assert body["arrival_date"] == ARRIVAL.isoformat()
    assert body["pax_count"] == 2
    # The figure comes from the version the client accepted.
    assert D(body["total_amount"]) == D(version["selling_price"])
    assert body["currency"] == "KES"

    # 30% deposit by default, and the two lines add up to the total exactly.
    assert [one["label"] for one in body["instalments"]] == ["Deposit", "Balance"]
    assert sum(D(one["amount"]) for one in body["instalments"]) == D(
        body["total_amount"]
    )
    deposit = body["instalments"][0]
    assert deposit["due_on"] == TODAY.isoformat()
    assert D(deposit["amount"]) == (
        D(body["total_amount"]) * D("0.30")
    ).quantize(D("0.01"))
    # The balance is due thirty days before travel.
    assert body["instalments"][1]["due_on"] == (
        ARRIVAL - timedelta(days=30)
    ).isoformat()


async def test_a_quote_nobody_accepted_cannot_be_booked(
    client, admin_tokens, sample_catalogue, booked_away
):
    """§5.1 records the sale; this is the record that follows it.

    Booking an unaccepted quote would put a trip in the operations list that no
    client has agreed to.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _accepted(client, h, ids, booked_away, accept=False)
    refused = await client.post(
        f"{API}/quotes/{quote['id']}/booking", headers=h, json={}
    )
    assert refused.status_code == 400, refused.text
    message = refused.json()["error"]["message"]
    assert "not accepted" in message
    assert "there is no sale yet" in message


async def test_one_trip_cannot_be_held_twice(
    client, admin_tokens, sample_catalogue, booked_away
):
    """And the refusal names the booking that already holds it."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _accepted(client, h, ids, booked_away)
    first = await client.post(
        f"{API}/quotes/{quote['id']}/booking", headers=h, json={}
    )
    assert first.status_code == 201, first.text
    again = await client.post(
        f"{API}/quotes/{quote['id']}/booking", headers=h, json={}
    )
    assert again.status_code == 400, again.text
    assert first.json()["reference"] in again.json()["error"]["message"]


async def test_a_cancelled_booking_frees_the_quote_to_be_rebooked(
    client, admin_tokens, sample_catalogue, booked_away
):
    """Clients come back, and the cancelled row stays as the record."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _accepted(client, h, ids, booked_away)
    first = (
        await client.post(f"{API}/quotes/{quote['id']}/booking", headers=h, json={})
    ).json()
    cancelled = await client.post(
        f"{API}/bookings/{first['id']}/cancel",
        headers=h,
        json={"reason": "Client postponed to next season."},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert "postponed" in cancelled.json()["cancellation_reason"]

    second = await client.post(
        f"{API}/quotes/{quote['id']}/booking", headers=h, json={}
    )
    assert second.status_code == 201, second.text
    assert second.json()["reference"] != first["reference"]


async def test_the_total_does_not_move_when_the_quote_is_repriced(
    client, admin_tokens, sample_catalogue, booked_away
):
    """A booking whose figure could change is not a booking.

    It points at the immutable version the client accepted (§3.4), so
    re-pricing the quote afterwards — which an agent may do for all sorts of
    reasons — cannot restate what is owed.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, version = await _accepted(client, h, ids, booked_away)
    booked = (
        await client.post(f"{API}/quotes/{quote['id']}/booking", headers=h, json={})
    ).json()

    repriced = await client.post(
        f"{API}/quotes/{quote['id']}/options/price", headers=h
    )
    assert repriced.status_code == 200, repriced.text

    after = (await client.get(f"{API}/bookings/{booked['id']}", headers=h)).json()
    assert D(after["total_amount"]) == D(version["selling_price"])
    assert after["quote_version_id"] == version["id"]


# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #


async def test_the_deposit_confirms_the_booking(
    client, admin_tokens, sample_catalogue, booked_away
):
    """Confirming is telling the suppliers it is happening.

    So it happens on the deposit, not on the balance — otherwise nothing is
    ever confirmed until a fortnight before travel.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _accepted(client, h, ids, booked_away)
    booked = (
        await client.post(f"{API}/quotes/{quote['id']}/booking", headers=h, json={})
    ).json()
    deposit = booked["instalments"][0]

    paid = await client.post(
        f"{API}/bookings/{booked['id']}/payments",
        headers=h,
        json={
            "amount": deposit["amount"],
            "paid_on": TODAY.isoformat(),
            "method": "M-Pesa",
            "reference": "QGH7XYZ123",
            "instalment_id": deposit["id"],
        },
    )
    assert paid.status_code == 200, paid.text
    body = paid.json()
    assert body["status"] == "confirmed"
    assert body["confirmed_at"] is not None
    # The method is normalised, so a statement groups by it.
    assert body["payments"][0]["method"] == "m_pesa"
    assert body["payments"][0]["reference"] == "QGH7XYZ123"

    position = (
        await client.get(f"{API}/bookings/{booked['id']}/owed", headers=h)
    ).json()
    assert D(position["paid"]) == D(deposit["amount"])
    assert D(position["balance"]) == D(booked["total_amount"]) - D(
        deposit["amount"]
    )
    assert position["next_due"]["label"] == "Balance"
    assert position["is_settled"] is False


async def test_a_part_payment_does_not_confirm(
    client, admin_tokens, sample_catalogue, booked_away
):
    """Half a deposit does not hold a lodge."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _accepted(client, h, ids, booked_away)
    booked = (
        await client.post(f"{API}/quotes/{quote['id']}/booking", headers=h, json={})
    ).json()
    half = (D(booked["instalments"][0]["amount"]) / 2).quantize(D("0.01"))
    paid = await client.post(
        f"{API}/bookings/{booked['id']}/payments",
        headers=h,
        json={"amount": str(half), "paid_on": TODAY.isoformat(), "method": "cash"},
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "provisional"


async def test_paying_in_full_settles_it(
    client, admin_tokens, sample_catalogue, booked_away
):
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _accepted(client, h, ids, booked_away)
    booked = (
        await client.post(f"{API}/quotes/{quote['id']}/booking", headers=h, json={})
    ).json()
    await client.post(
        f"{API}/bookings/{booked['id']}/payments",
        headers=h,
        json={
            "amount": booked["total_amount"],
            "paid_on": TODAY.isoformat(),
            "method": "bank_transfer",
        },
    )
    position = (
        await client.get(f"{API}/bookings/{booked['id']}/owed", headers=h)
    ).json()
    assert position["is_settled"] is True
    assert D(position["balance"]) == D("0.00")
    assert position["next_due"] is None


async def test_an_overpayment_is_a_credit_not_a_negative_bill(
    client, admin_tokens, sample_catalogue, booked_away
):
    """Clients round up. "You owe minus four thousand" is not a statement."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _accepted(client, h, ids, booked_away)
    booked = (
        await client.post(f"{API}/quotes/{quote['id']}/booking", headers=h, json={})
    ).json()
    over = D(booked["total_amount"]) + D("4000")
    await client.post(
        f"{API}/bookings/{booked['id']}/payments",
        headers=h,
        json={"amount": str(over), "paid_on": TODAY.isoformat(), "method": "cash"},
    )
    position = (
        await client.get(f"{API}/bookings/{booked['id']}/owed", headers=h)
    ).json()
    assert D(position["balance"]) == D("0.00")
    assert D(position["overpaid"]) == D("4000.00")


async def test_a_payment_in_another_currency_is_refused_not_converted(
    client, admin_tokens, sample_catalogue, booked_away
):
    """What cleared is a fact; the exchange rate is a decision.

    And the decision belongs to whoever reconciles the statement, so the
    refusal says exactly that rather than converting quietly at today's rate.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _accepted(client, h, ids, booked_away)
    booked = (
        await client.post(f"{API}/quotes/{quote['id']}/booking", headers=h, json={})
    ).json()
    refused = await client.post(
        f"{API}/bookings/{booked['id']}/payments",
        headers=h,
        json={
            "amount": "1000",
            "currency": "USD",
            "paid_on": TODAY.isoformat(),
            "method": "bank_transfer",
        },
    )
    assert refused.status_code == 400, refused.text
    message = refused.json()["error"]["message"]
    assert "invoiced in KES" in message
    assert "exchange rate is a decision" in message


async def test_a_payment_against_another_bookings_instalment_is_refused(
    client, admin_tokens, sample_catalogue, booked_away
):
    h, ids = _h(admin_tokens), sample_catalogue
    first_quote, _ = await _accepted(client, h, ids, booked_away)
    second_quote, _ = await _accepted(client, h, ids, booked_away)
    first = (
        await client.post(
            f"{API}/quotes/{first_quote['id']}/booking", headers=h, json={}
        )
    ).json()
    second = (
        await client.post(
            f"{API}/quotes/{second_quote['id']}/booking", headers=h, json={}
        )
    ).json()
    refused = await client.post(
        f"{API}/bookings/{first['id']}/payments",
        headers=h,
        json={
            "amount": "1000",
            "paid_on": TODAY.isoformat(),
            "method": "cash",
            "instalment_id": second["instalments"][0]["id"],
        },
    )
    assert refused.status_code == 404, refused.text


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


async def test_cancelling_needs_a_reason_and_computes_no_charge(
    client, admin_tokens, sample_catalogue, booked_away
):
    """The cancellation ladder is policy nobody has given us.

    A plausible invented figure on a refund looks as though it came from a
    contract, so what was owed and what was paid stay exactly as they are —
    which is what the refund conversation actually needs.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _accepted(client, h, ids, booked_away)
    booked = (
        await client.post(f"{API}/quotes/{quote['id']}/booking", headers=h, json={})
    ).json()
    deposit = booked["instalments"][0]
    await client.post(
        f"{API}/bookings/{booked['id']}/payments",
        headers=h,
        json={
            "amount": deposit["amount"],
            "paid_on": TODAY.isoformat(),
            "method": "mpesa",
        },
    )

    refused = await client.post(
        f"{API}/bookings/{booked['id']}/cancel", headers=h, json={"reason": "  "}
    )
    # Whitespace is not a reason, and the service says so rather than the
    # schema: a min_length that counted spaces would accept it.
    assert refused.status_code == 400, refused.text
    assert "Say why" in refused.json()["error"]["message"]

    cancelled = await client.post(
        f"{API}/bookings/{booked['id']}/cancel",
        headers=h,
        json={"reason": "Client's visa was refused."},
    )
    assert cancelled.status_code == 200, cancelled.text
    position = (
        await client.get(f"{API}/bookings/{booked['id']}/owed", headers=h)
    ).json()
    # Untouched: no charge, no write-off, no invented retention.
    assert D(position["paid"]) == D(deposit["amount"])
    assert D(position["total"]) == D(booked["total_amount"])


async def test_a_cancelled_booking_takes_no_more_payments(
    client, admin_tokens, sample_catalogue, booked_away
):
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _accepted(client, h, ids, booked_away)
    booked = (
        await client.post(f"{API}/quotes/{quote['id']}/booking", headers=h, json={})
    ).json()
    await client.post(
        f"{API}/bookings/{booked['id']}/cancel",
        headers=h,
        json={"reason": "Not travelling."},
    )
    refused = await client.post(
        f"{API}/bookings/{booked['id']}/payments",
        headers=h,
        json={"amount": "1000", "paid_on": TODAY.isoformat(), "method": "cash"},
    )
    assert refused.status_code == 400, refused.text
    assert "cancelled" in refused.json()["error"]["message"]


async def test_a_trip_cannot_be_completed_before_it_has_happened(
    client, admin_tokens, sample_catalogue, booked_away
):
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _accepted(client, h, ids, booked_away)
    booked = (
        await client.post(f"{API}/quotes/{quote['id']}/booking", headers=h, json={})
    ).json()
    refused = await client.post(
        f"{API}/bookings/{booked['id']}/complete", headers=h
    )
    assert refused.status_code == 400, refused.text
    assert "cannot be completed before" in refused.json()["error"]["message"]


async def test_the_due_list_finds_an_overdue_balance(
    client, admin_tokens, sample_catalogue, booked_away
):
    """The operations equivalent of the leads' morning list (§5.2).

    A booking that reaches the airport unpaid is a loss, so an unpaid balance
    inside the window is a phone call.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    # Travel soon, so the whole thing is due now and immediately overdue-ish.
    quote, _ = await _accepted(
        client,
        h,
        ids,
        booked_away,
        arrival=TODAY + timedelta(days=10),
        departure=TODAY + timedelta(days=13),
    )
    booked = (
        await client.post(f"{API}/quotes/{quote['id']}/booking", headers=h, json={})
    ).json()
    # A late booking is one payment due today (§7.1).
    assert [one["label"] for one in booked["instalments"]] == ["Full payment"]

    listed = await client.get(f"{API}/bookings/due", headers=h, params={"within_days": 7})
    assert listed.status_code == 200, listed.text
    mine = next(
        row for row in listed.json() if row["booking"]["id"] == booked["id"]
    )
    assert D(mine["owed"]["balance"]) == D(booked["total_amount"])
    assert mine["owed"]["next_due"]["label"] == "Full payment"


async def test_a_settled_booking_is_off_the_due_list(
    client, admin_tokens, sample_catalogue, booked_away
):
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _accepted(
        client,
        h,
        ids,
        booked_away,
        arrival=TODAY + timedelta(days=10),
        departure=TODAY + timedelta(days=13),
    )
    booked = (
        await client.post(f"{API}/quotes/{quote['id']}/booking", headers=h, json={})
    ).json()
    await client.post(
        f"{API}/bookings/{booked['id']}/payments",
        headers=h,
        json={
            "amount": booked["total_amount"],
            "paid_on": TODAY.isoformat(),
            "method": "mpesa",
        },
    )
    listed = await client.get(f"{API}/bookings/due", headers=h)
    assert booked["id"] not in [row["booking"]["id"] for row in listed.json()]


async def test_the_deposit_terms_are_configuration(
    client, admin_tokens, sample_catalogue, booked_away, restore_pricing_config
):
    """"50% deposit, balance 45 days out" is commercial policy, not code.

    And the schedule is resolved to dated rows at the moment of booking, which
    is what freezes it: changing the policy next month must not restate an
    invoice already sent.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    config = (await client.get(f"{API}/pricing-config", headers=h)).json()
    assert D(config["deposit_pct"]) == D("30")
    assert config["balance_due_days_before_travel"] == 30

    changed = await client.patch(
        f"{API}/pricing-config",
        headers=h,
        json={"deposit_pct": "50", "balance_due_days_before_travel": 45},
    )
    assert changed.status_code == 200, changed.text

    quote, _ = await _accepted(client, h, ids, booked_away)
    booked = (
        await client.post(f"{API}/quotes/{quote['id']}/booking", headers=h, json={})
    ).json()
    total = D(booked["total_amount"])
    assert D(booked["instalments"][0]["amount"]) == (total / 2).quantize(D("0.01"))
    assert booked["instalments"][1]["due_on"] == (
        ARRIVAL - timedelta(days=45)
    ).isoformat()


async def test_recording_money_is_its_own_permission():
    """The person who books a trip is not always the one who reconciles a bank."""
    from app.modules.rbac.permissions import PERMISSIONS, ROLE_DEFINITIONS

    assert "booking:manage" in PERMISSIONS
    assert "booking:record_payment" in PERMISSIONS
    assert "booking:record_payment" in ROLE_DEFINITIONS["admin"]["permissions"]
    # An agent sells; recording payments is not part of selling.
    assert (
        "booking:record_payment"
        not in ROLE_DEFINITIONS["sales_agent"]["permissions"]
    )
