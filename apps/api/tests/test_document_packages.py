"""Stage 3.11 — the document renders what 3.8–3.10 computed.

The pricing was there and the page was not: an option printed one hotel name
however many legs it had, a mixed-residency group got no per-person figure
anywhere (``per_person`` is deliberately NULL for a group whose travellers do
not all pay the same), and the transport page described the quote's *current*
segments rather than the ones it was issued with.

Everything here renders from the frozen version, which is the point: an issued
document has to say the same thing in a year's time.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.modules.quotes.models import QuoteTransportSegment
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

ARRIVAL, SWITCH, DEPARTURE = "2026-07-01", "2026-07-02", "2026-07-04"


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _package(ids, lodge):
    """Diani for the first night, the highland lodge for the other two."""
    return {
        "accommodation_id": ids["acc_sto_full_board"],
        "is_recommended": True,
        "legs": [
            {
                "sequence": 1,
                "destination_id": ids["destination_diani"],
                "accommodation_id": ids["acc_sto_full_board"],
                "check_in": ARRIVAL,
                "check_out": SWITCH,
            },
            {
                "sequence": 2,
                "destination_id": lodge["destination_id"],
                "accommodation_id": lodge["accommodation_id"],
                "check_in": SWITCH,
                "check_out": DEPARTURE,
            },
        ],
    }


def _rail_journey(ids):
    """The seeded Diani tariffs: SGR economy out and back, four road transfers."""
    line_haul = [
        {
            "sequence": n,
            "kind": "line_haul",
            "mode": "rail",
            "travel_class": "economy",
            "destination_id": ids["destination_diani"],
            "description": "SGR Nairobi to Mombasa",
        }
        for n in (1, 2)
    ]
    transfers = [
        {
            "sequence": 2 + n,
            "kind": "transfer",
            "mode": "road",
            "vehicle_type": "saloon",
            "destination_id": ids["destination_diani"],
            "description": "Terminus to hotel",
        }
        for n in range(1, 5)
    ]
    return line_haul + transfers


async def _issued(client, h, ids, *, options, segments=None, cohorts=None, pax=2):
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Package Doc Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("packagedoc"),
            "residence_category_id": ids["residence_citizen"],
        },
    )
    assert record.status_code == 201, record.text
    body = {
        "client_id": record.json()["id"],
        "presentation_currency": "KES",
        "residence_category_id": ids["residence_citizen"],
        "arrival_date": ARRIVAL,
        "departure_date": DEPARTURE,
        "requested_meal_plan_id": ids["meal_plan_fb"],
        "options": options,
        "transport_segments": segments or [],
    }
    if cohorts:
        body["cohorts"] = [
            {
                "residence_category_id": ids[residence],
                "traveller_type": kind,
                "headcount": n,
            }
            for residence, kind, n in cohorts
        ]
    else:
        body["pax_count"] = pax
    created = await client.post(f"{API}/quotes", headers=h, json=body)
    assert created.status_code == 201, created.text
    quote = created.json()
    issued = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert issued.status_code == 200, issued.text
    return quote, issued.json()


async def _render(client, h, quote_id):
    resp = await client.get(f"{API}/quotes/{quote_id}/document.html", headers=h)
    assert resp.status_code == 200, resp.text
    return resp.text


# --------------------------------------------------------------------------- #
# The package on the page
# --------------------------------------------------------------------------- #


async def test_a_package_prints_its_legs_in_itinerary_order(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    """One night on the coast, two upcountry — and the page says so.

    Before this the option printed a single hotel name and a single room type
    however many legs it had, so a client could not see where the nights went.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client, h, ids, options=[_package(ids, upcountry_lodge)]
    )
    html = await _render(client, h, quote["id"])

    assert "<th>Leg</th>" in html
    first = html.index("Coral Sands")
    second = html.index("Highland Lodge")
    assert first < second, "the legs are out of itinerary order"
    # The destination each leg is in, not only the property.
    assert "Diani" in html
    assert "Package Highlands" in html
    assert "1 night" in html and "2 nights" in html


async def test_a_single_property_option_prints_no_itinerary_table(
    client, admin_tokens, sample_catalogue
):
    """A package of one is what the facts panel already describes.

    Printing a one-row itinerary table beside it would say the same thing
    twice, which on a proposal reads as padding.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
    )
    html = await _render(client, h, quote["id"])
    assert "<th>Leg</th>" not in html


async def test_the_comparison_table_names_the_route(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    """What makes it a package comparison rather than a hotel one.

    Two packages can share their first property and differ two legs later, so
    a table keyed on the property name alone would show a client two rows they
    cannot tell apart.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client, h, ids, options=[_package(ids, upcountry_lodge)]
    )
    html = await _render(client, h, quote["id"])
    table = html.split("At a Glance")[1]
    assert "&#8594;" in table or "→" in table, table[:400]
    assert "Package Highlands" in table


# --------------------------------------------------------------------------- #
# The journey on the page
# --------------------------------------------------------------------------- #


async def test_the_journey_is_described_and_never_priced_per_leg(
    client, admin_tokens, sample_catalogue
):
    """The movements' fares are what we pay; their total is in the price.

    Two rail legs at 1,500 a head and four transfers at 4,500 are 30,000 of
    cost inside the option's figure. None of those numbers is the client's
    business — what the page owes them is what is being arranged.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
        segments=_rail_journey(ids),
    )
    html = await _render(client, h, quote["id"])

    assert "Seamless Group Transport" in html
    assert "SGR Nairobi to Mombasa" in html
    assert "Terminus to hotel" in html
    assert "Included" in html
    for fare in ("1,500", "4,500", "3,000", "18,000"):
        assert fare not in html, f"a transport tariff reached the document: {fare}"


async def test_a_flight_is_named_as_the_client_s_own_to_book(
    client, admin_tokens, sample_catalogue
):
    """Heissal holds no ticketing licence, so the fare is theirs (§3.10).

    Leaving the flight off the page entirely is how a client turns up without a
    ticket, so it is named — and named as an exclusion, not as a charge.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    segments = [
        {
            "sequence": 1,
            "kind": "line_haul",
            "mode": "air",
            "description": "Nairobi to Ukunda",
        },
        {
            "sequence": 2,
            "kind": "transfer",
            "mode": "road",
            "vehicle_type": "saloon",
            "destination_id": ids["destination_diani"],
            "description": "Airstrip to hotel",
        },
        {
            "sequence": 3,
            "kind": "transfer",
            "mode": "road",
            "vehicle_type": "saloon",
            "destination_id": ids["destination_diani"],
            "description": "Hotel to airstrip",
        },
    ]
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
        segments=segments,
    )
    html = await _render(client, h, quote["id"])
    assert "Flights are not included" in html
    assert "Nairobi to Ukunda" in html
    assert "booked directly by you" in html


async def test_an_optional_upgrade_is_priced_on_the_transport_page(
    client, admin_tokens, sample_catalogue
):
    """Quoted apart from the package, but at a selling price.

        one saloon leg 4,500 + 5% = 4,725, + 24% = 5,859
        per person ceil(5,859 / 2 / 100) x 100 = 3,000 -> group 6,000

    An add-on shown at cost would be an add-on sold at a loss.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    segments = [
        {
            "sequence": n,
            "kind": "transfer",
            "mode": "road",
            "vehicle_type": "saloon",
            "destination_id": ids["destination_diani"],
            "description": "Airport to hotel",
            **extra,
        }
        for n, extra in (
            (1, {}),
            (2, {}),
            (3, {"is_optional": True, "is_vvip": True, "description": "VVIP meet and greet"}),
        )
    ]
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
        segments=segments,
    )
    html = await _render(client, h, quote["id"])
    assert "Optional upgrade" in html
    assert "VVIP meet and greet" in html
    assert "KES 6,000" in html


async def test_the_document_keeps_the_journey_it_was_issued_with(
    client, admin_tokens, sample_catalogue
):
    """Deleting a segment afterwards must not change an issued proposal.

    The transport page used to read the quote's live segments, so a document
    already sent to a client would quietly start describing a different
    journey. It renders from the frozen version now.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
        segments=_rail_journey(ids),
    )
    before = await _render(client, h, quote["id"])
    assert "SGR Nairobi to Mombasa" in before

    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(QuoteTransportSegment).where(
                        QuoteTransportSegment.quote_id == uuid.UUID(quote["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows
        for row in rows:
            await db.delete(row)
        await db.commit()

    after = await _render(client, h, quote["id"])
    assert "SGR Nairobi to Mombasa" in after
    assert after == before


# --------------------------------------------------------------------------- #
# A mixed group's prices
# --------------------------------------------------------------------------- #


async def test_a_mixed_group_gets_a_figure_per_cohort(
    client, admin_tokens, sample_catalogue
):
    """Two residents in shillings, two non-residents in dollars.

    The client's own requirement, and the only meaningful per-person figures
    for such a group: the whole-group ``per_person`` is NULL by design, because
    these four travellers are not paying the same thing.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
        cohorts=[("residence_citizen", "adult", 2), ("residence_non_resident", "adult", 2)],
    )
    html = await _render(client, h, quote["id"])

    assert "Per traveller" in html
    # The categories' own names, never our storage keys.
    assert "non_resident" not in html
    assert "USD" in html and "KES" in html
    assert "2 travellers" in html


async def test_a_cohort_only_quote_still_states_the_group_size(
    client, admin_tokens, sample_catalogue
):
    """Four travellers, and the document says four.

    A quote given cohorts has no ``pax_count`` column at all — the vector is
    the headcount (§3.8) — so everything downstream that reached for that
    column read zero, and the client's proposal said "0 participants" beside a
    price for four people. The version freezes the headcount pricing used.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
        cohorts=[
            ("residence_citizen", "adult", 2),
            ("residence_non_resident", "adult", 2),
        ],
    )
    html = await _render(client, h, quote["id"])
    assert "4 participants" in html
    assert "0 participants" not in html


async def test_a_uniform_group_gets_no_per_cohort_panel(
    client, admin_tokens, sample_catalogue
):
    """One cohort would repeat the per-person figure printed above it."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
        cohorts=[("residence_citizen", "adult", 2)],
    )
    html = await _render(client, h, quote["id"])
    assert "Per traveller" not in html
    assert "per person" in html


async def test_repeated_movements_are_counted_rather_than_listed(
    client, admin_tokens, sample_catalogue
):
    """A rail return with its four mandatory transfers is two routes, not six.

    The page printed "Terminus to hotel — Included" four times in a row and
    repeated the whole list again in the route line above it, which reads as a
    bug rather than as thoroughness.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
        segments=_rail_journey(ids),
    )
    html = await _render(client, h, quote["id"])
    assert "Terminus to hotel × 4" in html
    assert "SGR Nairobi to Mombasa × 2" in html
    # Once in the route line, once in its own cell — not six times.
    assert html.count("Terminus to hotel") == 2, html.count("Terminus to hotel")


# --------------------------------------------------------------------------- #
# What the price excludes (§3.12)
# --------------------------------------------------------------------------- #


async def test_the_document_says_what_the_price_excludes(
    client, admin_tokens, sample_catalogue
):
    """One total reads as covering everything a holiday needs.

    Not saying otherwise is the commonest cause of a dispute at invoice time,
    so the standing list is on the page a client reads before they choose.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
    )
    html = await _render(client, h, quote["id"])
    assert "What the quoted price excludes" in html
    assert "Travel insurance" in html
    assert "Tips and gratuities" in html


async def test_a_named_flight_is_repeated_in_the_exclusions(
    client, admin_tokens, sample_catalogue
):
    """This is the list a client checks before they sign.

    The transport page names the flight as theirs to book; the exclusions say
    the fare is not in the price. Both, because a client who reads only one of
    them still has to end up with a ticket.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
        segments=[
            {
                "sequence": 1,
                "kind": "line_haul",
                "mode": "air",
                "description": "Nairobi to Ukunda",
            },
            {
                "sequence": 2,
                "kind": "transfer",
                "mode": "road",
                "vehicle_type": "saloon",
                "destination_id": ids["destination_diani"],
                "description": "Airstrip to hotel",
            },
        ],
    )
    html = await _render(client, h, quote["id"])
    excludes = html.split("What the quoted price excludes")[1]
    assert "Nairobi to Ukunda" in excludes
    assert "does not ticket air travel" in excludes
    # And our own composed label never reaches the page.
    assert "Line haul" not in html


async def test_an_optional_upgrade_is_named_as_outside_the_price(
    client, admin_tokens, sample_catalogue
):
    """With its price, so "not included" cannot be read as "not available"."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
        segments=[
            {
                "sequence": 1,
                "kind": "transfer",
                "mode": "road",
                "vehicle_type": "saloon",
                "destination_id": ids["destination_diani"],
                "description": "Airport to hotel",
            },
            {
                "sequence": 2,
                "kind": "transfer",
                "mode": "road",
                "vehicle_type": "saloon",
                "destination_id": ids["destination_diani"],
                "description": "Hotel to airport",
            },
            {
                "sequence": 3,
                "kind": "transfer",
                "mode": "road",
                "vehicle_type": "saloon",
                "destination_id": ids["destination_diani"],
                "description": "VVIP meet and greet",
                "is_optional": True,
                "is_vvip": True,
            },
        ],
    )
    html = await _render(client, h, quote["id"])
    excludes = html.split("What the quoted price excludes")[1]
    assert "Optional transport upgrades" in excludes
    assert "KES 6,000" in excludes
