"""A client opening their own trip, end to end (§7.2).

The rules are in ``test_portal_view.py``. This is the wiring, and five things
about it are worth more than the rest:

* a client needs **no account** — one token, one booking, sent by hand;
* the token is stored **hashed**, and the response that creates it is the only
  place it ever appears;
* the trip and the document come from the **frozen version** the client
  accepted, not from a re-render of a quote that has been edited since;
* **no cost or margin appears on any portal response**, and that holds because
  the view is an allow-list rather than a filter;
* and the portal has **no write endpoint at all**, so there is nothing a
  leaked token could be used to change.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_refresh_token
from app.db.session import AsyncSessionLocal
from app.modules.bookings.models import Booking
from app.modules.portal.models import BookingAccessGrant
from app.modules.quotes.models import Quote, QuoteOption
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal
TODAY = date.today()
ARRIVAL = TODAY + timedelta(days=60)
DEPARTURE = ARRIVAL + timedelta(days=3)


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _client_h(token: str):
    """A client's headers. The same bearer transport, a different credential.

    One convention, and no token in a path or a query string — the link the
    client clicked carries it in the fragment, which never reaches a server.
    """
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(loop_scope="session")
async def booked_away():
    """Remove the quotes (with their bookings and grants) this module created."""
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
                # The grants cascade on the booking, but deleting them first
                # keeps the sweep readable rather than relying on it.
                for grant in (
                    (
                        await db.execute(
                            select(BookingAccessGrant).where(
                                BookingAccessGrant.booking_id == booking.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                ):
                    await db.delete(grant)
                await db.flush()
                await db.delete(booking)
            await db.flush()
            row = await db.get(Quote, uuid.UUID(quote_id))
            if row is not None:
                await db.delete(row)
        await db.commit()


async def _booking(client, h, ids, made, *, pax=2):
    """A quote, issued, accepted and booked — the state §7.2 starts from."""
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Portal Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("portal"),
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
            "arrival_date": ARRIVAL.isoformat(),
            "departure_date": DEPARTURE.isoformat(),
            "pax_count": pax,
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
    assert (
        await client.post(f"{API}/quotes/{quote['id']}/options/price", headers=h)
    ).status_code == 200
    issued = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert issued.status_code == 200, issued.text

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
    booked = await client.post(
        f"{API}/quotes/{quote['id']}/booking", headers=h, json={}
    )
    assert booked.status_code == 201, booked.text
    return quote, issued.json(), booked.json()


async def _link(client, h, booking_id, **over):
    body = {"label": "Mrs Achieng"}
    body.update(over)
    issued = await client.post(
        f"{API}/bookings/{booking_id}/portal-links", headers=h, json=body
    )
    assert issued.status_code == 201, issued.text
    return issued.json()


# --------------------------------------------------------------------------- #
# Issuing a link
# --------------------------------------------------------------------------- #


async def test_a_client_gets_a_link_with_no_account_of_any_kind(
    client, admin_tokens, sample_catalogue, booked_away
):
    """No password, no registration, no reset — one token for one booking.

    Somebody books a trip every year or two; a password is a thing they will
    have forgotten by the time they need it, and what it guards is one
    itinerary.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)

    issued = await _link(client, h, booking["id"])
    assert issued["token"]
    assert issued["url"].endswith(f"#{issued['token']}")
    assert issued["label"] == "Mrs Achieng"
    assert issued["view_count"] == 0
    assert issued["last_seen_at"] is None

    # And it opens the trip immediately.
    trip = await client.get(
        f"{API}/portal/trip", headers=_client_h(issued["token"])
    )
    assert trip.status_code == 200, trip.text
    assert trip.json()["reference"] == booking["reference"]


async def test_the_token_is_in_the_links_fragment(
    client, admin_tokens, sample_catalogue, booked_away
):
    """Browsers never send a fragment to a server.

    So the token stays out of access logs, and out of the Referer header when
    the client clicks through from their itinerary to an airline's site.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])

    before, _, after = issued["url"].partition("#")
    assert after == issued["token"]
    assert issued["token"] not in before
    assert "?" not in before


async def test_the_database_holds_a_hash_and_never_the_link(
    client, admin_tokens, sample_catalogue, booked_away
):
    """A table of live links is a table of credentials.

    SHA-256 rather than bcrypt: a work factor exists to make guessing a
    low-entropy secret expensive, and this is 256 bits of randomness.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])

    async with AsyncSessionLocal() as db:
        row = await db.get(BookingAccessGrant, uuid.UUID(issued["id"]))
        assert row is not None
        assert row.token_hash != issued["token"]
        assert row.token_hash == hash_refresh_token(issued["token"])
        assert issued["token"] not in repr(vars(row))


async def test_the_token_is_returned_once_and_never_again(
    client, admin_tokens, sample_catalogue, booked_away
):
    """An agent who needs to resend issues a new grant — one click.

    Which is better than a retrievable one anyway: the new link is separately
    revocable, and that is what you want once the first has been forwarded
    into a family group chat.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])

    listed = await client.get(
        f"{API}/bookings/{booking['id']}/portal-links", headers=h
    )
    assert listed.status_code == 200, listed.text
    assert issued["token"] not in listed.text
    assert [one["id"] for one in listed.json()] == [issued["id"]]


async def test_a_link_outlives_the_trip_by_default(
    client, admin_tokens, sample_catalogue, booked_away
):
    """The statement and the receipts are wanted after they travel."""
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])
    assert date.fromisoformat(issued["expires_on"]) > DEPARTURE


async def test_a_cancelled_booking_is_not_given_a_link(
    client, admin_tokens, sample_catalogue, booked_away
):
    """Sending somebody a link that opens onto "this was cancelled" is worse
    than a telephone call, so the refusal says so."""
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    cancelled = await client.post(
        f"{API}/bookings/{booking['id']}/cancel",
        headers=h,
        json={"reason": "Client's visa was refused."},
    )
    assert cancelled.status_code == 200, cancelled.text

    refused = await client.post(
        f"{API}/bookings/{booking['id']}/portal-links", headers=h, json={}
    )
    assert refused.status_code == 400, refused.text
    assert "phone call, not a link" in refused.text


# --------------------------------------------------------------------------- #
# The boundary: no cost reaches a client
# --------------------------------------------------------------------------- #


async def test_no_cost_or_margin_appears_on_any_portal_response(
    client, admin_tokens, sample_catalogue, booked_away
):
    """§2's internal/client split, carried into §7.

    The version snapshot the trip is built from holds the whole costing. This
    asserts over the raw response text rather than field by field, because the
    field that leaks is always the one added after the test was written — and
    the allow-list in ``portal.view`` is what makes that structural.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])
    ch = _client_h(issued["token"])

    for path in ("/portal/trip", "/portal/statement"):
        response = await client.get(f"{API}{path}", headers=ch)
        assert response.status_code == 200, response.text
        body = response.text
        for word in (
            "cost",
            "margin",
            "profit",
            "markup",
            "supplier_paid",
            "contingency",
            "internal",
        ):
            assert word not in body.lower(), f"{word} in {path}"
        # And the figures, not only their names.
        assert str(version["internal_cost"]).split(".")[0] not in body
        assert str(version["gross_profit"]).split(".")[0] not in body


async def test_the_client_sees_only_the_option_they_booked(
    client, admin_tokens, sample_catalogue, booked_away
):
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])
    trip = await client.get(
        f"{API}/portal/trip", headers=_client_h(issued["token"])
    )
    body = trip.json()
    assert body["property_name"]
    assert "options" not in body


# --------------------------------------------------------------------------- #
# What the client sees
# --------------------------------------------------------------------------- #


async def test_the_trip_carries_the_itinerary_that_was_quoted(
    client, admin_tokens, sample_catalogue, booked_away
):
    """From the frozen version (§3.4/§4.1), so it says what they agreed to."""
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])
    body = (
        await client.get(f"{API}/portal/trip", headers=_client_h(issued["token"]))
    ).json()

    assert body["reference"] == booking["reference"]
    assert body["arrival_date"] == ARRIVAL.isoformat()
    assert body["departure_date"] == DEPARTURE.isoformat()
    assert body["pax_count"] == 2
    assert D(body["total"]) == D(booking["total_amount"])
    # Four dates for a three-night trip: arrival through departure day.
    assert [day["number"] for day in body["days"]] == [1, 2, 3, 4]
    assert body["days"][0]["is_arrival"] is True
    assert body["days"][-1]["is_departure"] is True
    assert body["days"][0]["date"] == ARRIVAL.isoformat()
    assert body["stays"][0]["nights"] == 3


async def test_re_pricing_the_quote_does_not_change_what_the_client_sees(
    client, admin_tokens, sample_catalogue, booked_away
):
    """The §7.1 guarantee, followed all the way to the client's screen.

    A booking points at a version; re-pricing appends a new one. A portal that
    read the quote's current version would show a client a figure they never
    agreed to — and they would be looking at it on their phone.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])
    ch = _client_h(issued["token"])
    before = (await client.get(f"{API}/portal/trip", headers=ch)).json()

    assert (
        await client.post(f"{API}/quotes/{quote['id']}/options/price", headers=h)
    ).status_code == 200
    assert (
        await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    ).status_code == 200

    after = (await client.get(f"{API}/portal/trip", headers=ch)).json()
    assert D(after["total"]) == D(before["total"])
    assert after == before


async def test_the_statement_is_the_same_arithmetic_the_operator_reads(
    client, admin_tokens, sample_catalogue, booked_away
):
    """Two would eventually disagree, and the client's copy is the one in an inbox."""
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])
    ch = _client_h(issued["token"])

    deposit = D(booking["instalments"][0]["amount"])
    paid = await client.post(
        f"{API}/bookings/{booking['id']}/payments",
        headers=h,
        json={
            "amount": str(deposit),
            "currency": "KES",
            "paid_on": TODAY.isoformat(),
            "method": "mpesa",
            "reference": "QGH7XY2ZZ1",
        },
    )
    assert paid.status_code == 200, paid.text

    theirs = (await client.get(f"{API}/portal/statement", headers=ch)).json()
    ours = (
        await client.get(f"{API}/bookings/{booking['id']}/owed", headers=h)
    ).json()
    assert D(theirs["paid"]) == D(ours["paid"]) == deposit
    assert D(theirs["balance"]) == D(ours["balance"])
    assert theirs["reference"] == booking["reference"]
    # Their own payment, with the reference they will recognise.
    assert theirs["payments"][0]["reference"] == "QGH7XY2ZZ1"
    assert [one["label"] for one in theirs["schedule"]] == ["Deposit", "Balance"]


async def test_the_document_is_the_one_they_accepted(
    client, admin_tokens, sample_catalogue, booked_away
):
    """Pinned to the booking's own version, not to the quote's current one."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote, version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])
    ch = _client_h(issued["token"])

    theirs = await client.get(f"{API}/portal/document.html", headers=ch)
    assert theirs.status_code == 200, theirs.text
    assert theirs.headers["content-type"].startswith("text/html")
    assert quote["quote_number"] in theirs.text

    # Re-issue, then check the client still gets version 1's document.
    await client.post(f"{API}/quotes/{quote['id']}/options/price", headers=h)
    reissued = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert reissued.json()["version_number"] == version["version_number"] + 1
    again = await client.get(f"{API}/portal/document.html", headers=ch)
    assert again.text == theirs.text


# --------------------------------------------------------------------------- #
# Links that do not work
# --------------------------------------------------------------------------- #


async def test_no_link_at_all_says_where_to_find_one(client):
    refused = await client.get(f"{API}/portal/trip")
    assert refused.status_code == 403, refused.text
    assert "link your consultant sent you" in refused.text


async def test_an_unknown_token_is_refused_without_confirming_its_shape(client):
    """The same wording as an expired one, deliberately.

    Anything else tells a stranger holding a guessed string whether they got
    close.
    """
    refused = await client.get(
        f"{API}/portal/trip", headers=_client_h("not-a-real-token")
    )
    assert refused.status_code == 403, refused.text
    assert "does not work" in refused.text


async def test_a_withdrawn_link_stops_working_and_reassures(
    client, admin_tokens, sample_catalogue, booked_away
):
    """Because a link gets forwarded into a family group chat."""
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])
    ch = _client_h(issued["token"])
    assert (await client.get(f"{API}/portal/trip", headers=ch)).status_code == 200

    revoked = await client.post(
        f"{API}/portal-links/{issued['id']}/revoke",
        headers=h,
        json={"reason": "Forwarded to a group chat."},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoke_reason"] == "Forwarded to a group chat."

    refused = await client.get(f"{API}/portal/trip", headers=ch)
    assert refused.status_code == 403, refused.text
    assert "withdrawn" in refused.text
    assert "your booking is unaffected" in refused.text


async def test_withdrawing_needs_a_reason(
    client, admin_tokens, sample_catalogue, booked_away
):
    """The next agent has to be able to tell a leak from a mistake."""
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])
    refused = await client.post(
        f"{API}/portal-links/{issued['id']}/revoke",
        headers=h,
        json={"reason": "   "},
    )
    assert refused.status_code == 400, refused.text


async def test_revoking_one_link_leaves_the_others_working(
    client, admin_tokens, sample_catalogue, booked_away
):
    """One for the person paying, one for the person travelling."""
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    payer = await _link(client, h, booking["id"], label="Mr Otieno (paying)")
    traveller = await _link(client, h, booking["id"], label="Ms Otieno")

    await client.post(
        f"{API}/portal-links/{payer['id']}/revoke",
        headers=h,
        json={"reason": "He asked us to stop sending it."},
    )
    assert (
        await client.get(
            f"{API}/portal/trip", headers=_client_h(payer["token"])
        )
    ).status_code == 403
    assert (
        await client.get(
            f"{API}/portal/trip", headers=_client_h(traveller["token"])
        )
    ).status_code == 200


async def test_an_expired_link_says_when_and_that_the_booking_is_fine(
    client, admin_tokens, sample_catalogue, booked_away
):
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])

    async with AsyncSessionLocal() as db:
        row = await db.get(BookingAccessGrant, uuid.UUID(issued["id"]))
        assert row is not None
        row.expires_on = TODAY - timedelta(days=1)
        await db.commit()

    refused = await client.get(
        f"{API}/portal/trip", headers=_client_h(issued["token"])
    )
    assert refused.status_code == 403, refused.text
    assert "stopped working on" in refused.text
    assert "nothing about your booking has changed" in refused.text


async def test_a_cancelled_bookings_link_says_cancelled_not_expired(
    client, admin_tokens, sample_catalogue, booked_away
):
    """A client told their link expired will assume we have lost their booking."""
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])
    await client.post(
        f"{API}/bookings/{booking['id']}/cancel",
        headers=h,
        json={"reason": "Client's visa was refused."},
    )
    refused = await client.get(
        f"{API}/portal/trip", headers=_client_h(issued["token"])
    )
    assert refused.status_code == 403, refused.text
    assert "has been cancelled" in refused.text
    assert "the record of it is not gone" in refused.text


# --------------------------------------------------------------------------- #
# What a visit does, and what it cannot do
# --------------------------------------------------------------------------- #


async def test_opening_a_trip_records_that_the_client_looked(
    client, admin_tokens, sample_catalogue, booked_away
):
    """"Did they even see the itinerary?" is a real sales question."""
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])
    ch = _client_h(issued["token"])

    await client.get(f"{API}/portal/trip", headers=ch)
    await client.get(f"{API}/portal/statement", headers=ch)

    listed = await client.get(
        f"{API}/bookings/{booking['id']}/portal-links", headers=h
    )
    row = listed.json()[0]
    assert row["view_count"] == 2
    assert row["last_seen_at"] is not None


async def test_a_grant_cannot_reach_anything_but_the_portal(
    client, admin_tokens, sample_catalogue, booked_away
):
    """It is not a login: it carries no permissions and no user.

    The portal has no write endpoint at all, so the read-only guarantee is the
    absence of code — but a grant must also not be usable as a staff token
    anywhere else, and that is what this asserts.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, booking["id"])
    ch = _client_h(issued["token"])

    for path in (
        f"/bookings/{booking['id']}",
        "/leads",
        "/quotes",
        "/users/me",
        f"/bookings/{booking['id']}/portal-links",
    ):
        response = await client.get(f"{API}{path}", headers=ch)
        assert response.status_code in (401, 403), f"{path} -> {response.status_code}"


async def test_a_grant_cannot_open_somebody_elses_trip(
    client, admin_tokens, sample_catalogue, booked_away
):
    """Scoped to one booking rather than to a client.

    So a leaked link exposes one trip, not a relationship — which is the whole
    argument for a grant per booking.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    _q1, _v1, first = await _booking(client, h, ids, booked_away)
    _q2, _v2, second = await _booking(client, h, ids, booked_away)
    issued = await _link(client, h, first["id"])

    trip = await client.get(
        f"{API}/portal/trip", headers=_client_h(issued["token"])
    )
    assert trip.json()["reference"] == first["reference"]
    assert trip.json()["reference"] != second["reference"]


async def test_issuing_a_link_is_its_own_permission(
    client, admin_tokens, sample_catalogue, booked_away
):
    """Handing somebody a credential is not the same act as editing a booking.

    A viewer can read a booking and cannot issue a link to one.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    _quote, _version, booking = await _booking(client, h, ids, booked_away)

    email = unique_email("viewer")
    made = await client.post(
        f"{API}/users",
        headers=h,
        json={
            "email": email,
            "password": "ViewerPass123",
            "role_keys": ["viewer"],
        },
    )
    assert made.status_code in (200, 201), made.text
    logged_in = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "ViewerPass123"}
    )
    viewer_h = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}

    refused = await client.post(
        f"{API}/bookings/{booking['id']}/portal-links", headers=viewer_h, json={}
    )
    assert refused.status_code == 403, refused.text
