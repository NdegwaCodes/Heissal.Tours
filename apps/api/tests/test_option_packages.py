"""Stage 3.9 — pricing a curated multi-destination package end to end.

``test_packages.py`` covers the leg rules as pure functions. This file covers
the half that reads the database: that a package is priced leg by leg, that each
leg gets its own property, meal plan and nights, that the legs are **summed**
rather than compared, and that an incoherent package is refused rather than
priced into something plausible-looking.

The demo catalogue is all in Diani, so a genuine two-destination trip needs a
second place — the fixture builds a lodge of its own in a park, which also lets
a package pick up park fees on one leg and not the other.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.modules.quotes.models import Quote
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal
# Nairobi 1 night, then the upcountry lodge 2 — three nights, 1 to 4 July.
ARRIVAL, DEPARTURE = "2026-07-01", "2026-07-04"
SWITCH = "2026-07-02"


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _record(client, h, residence_category_id):
    resp = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Package Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("package"),
            "residence_category_id": residence_category_id,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _quote(client, h, ids, *, options, arrival=ARRIVAL, departure=DEPARTURE):
    record = await _record(client, h, ids["residence_citizen"])
    return await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record["id"],
            "presentation_currency": "KES",
            "residence_category_id": ids["residence_citizen"],
            "arrival_date": arrival,
            "departure_date": departure,
            "pax_count": 2,
            "requested_meal_plan_id": ids["meal_plan_fb"],
            "options": options,
        },
    )


def _two_legs(ids, lodge, *, switch=SWITCH, departure=DEPARTURE, plan=None):
    """Diani for the first night, the highland lodge for the rest."""
    first = {
        "sequence": 1,
        "destination_id": ids["destination_diani"],
        "accommodation_id": ids["acc_sto_full_board"],
        "check_in": ARRIVAL,
        "check_out": switch,
    }
    second = {
        "sequence": 2,
        "destination_id": lodge["destination_id"],
        "accommodation_id": lodge["accommodation_id"],
        "check_in": switch,
        "check_out": departure,
    }
    if plan:
        second["requested_meal_plan_id"] = plan
    return {
        "accommodation_id": ids["acc_sto_full_board"],
        "sort_order": 1,
        "legs": [first, second],
    }


async def _price(client, h, quote_id):
    resp = await client.post(f"{API}/quotes/{quote_id}/options/price", headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------- #
# A package prices leg by leg, and the legs sum
# --------------------------------------------------------------------------- #


async def test_a_two_destination_package_prices_both_legs(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    """The client's own shape: two destinations in one trip, one offer.

    Coral Sands twin FB is 9,000 a night and the highland lodge 18,000. One
    night then two:
        9,000 + 2 x 18,000  = 45,000 accommodation
        + contingency 5%    = 47,250
        + profit 24%        = 58,590
        per person  ceil(58,590 / 2 / 100) x 100 = 29,300
        group       29,300 x 2 = 58,600
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _quote(
        client, h, ids, options=[_two_legs(ids, upcountry_lodge)]
    )
    assert quote.status_code == 201, quote.text
    result = await _price(client, h, quote.json()["id"])

    assert len(result["options"]) == 1
    option = result["options"][0]
    assert D(option["build_up"]["components"]["accommodation"]) == D("45000")
    assert D(option["per_person"]) == D("29300")
    assert D(option["group_total"]) == D("58600")
    # Three nights across two legs, not three nights twice.
    assert option["nights"] == 3


async def test_each_leg_reports_its_own_property_room_and_nights(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _quote(client, h, ids, options=[_two_legs(ids, upcountry_lodge)])
    option = (await _price(client, h, quote.json()["id"]))["options"][0]

    legs = option["legs"]
    assert [one["sequence"] for one in legs] == [1, 2]
    assert [one["nights"] for one in legs] == [1, 2]
    assert "Coral Sands" in legs[0]["accommodation_name"]
    assert "Highland Lodge" in legs[1]["accommodation_name"]
    assert legs[0]["room_type_name"] != legs[1]["room_type_name"]
    # Two guests in twins is one room on each leg — sequential, not simultaneous.
    assert option["rooms_required"] == 1


async def test_the_option_name_shows_the_whole_itinerary(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    """A package is one offer, so its label has to say where it goes. Naming it
    after the first property alone would put two different trips on a document
    under the same heading."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _quote(client, h, ids, options=[_two_legs(ids, upcountry_lodge)])
    option = (await _price(client, h, quote.json()["id"]))["options"][0]
    assert "→" in option["accommodation_name"]
    assert "Coral Sands" in option["accommodation_name"]
    assert "Highland Lodge" in option["accommodation_name"]


async def test_a_meal_plan_is_chosen_per_leg(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    """A day out of the hotel makes half board the right plan for that leg,
    rather than a fallback from the quote's full board. The two have to be
    distinguishable, so a per-leg choice is an exact match and NOT a fallback.

    The lodge on bed and breakfast is 14,000 rather than 18,000:
        9,000 + 2 x 14,000 = 37,000
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _quote(
        client,
        h,
        ids,
        options=[
            _two_legs(ids, upcountry_lodge, plan=upcountry_lodge["meal_plan_bb"])
        ],
    )
    option = (await _price(client, h, quote.json()["id"]))["options"][0]

    assert D(option["build_up"]["components"]["accommodation"]) == D("37000")
    legs = option["legs"]
    assert legs[0]["meal_plan_code"] == "FB"
    assert legs[1]["meal_plan_code"] == "BB"
    # An explicit per-leg choice is not a fallback from the quote's plan.
    assert legs[1]["meal_plan_fallback_from"] is None


async def test_the_resolution_is_written_back_per_leg(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    """A package's document prints per leg, so the room and plan the engine
    picked have to be recorded per leg — the option-level fields describe the
    first one only."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _quote(client, h, ids, options=[_two_legs(ids, upcountry_lodge)])
    quote_id = quote.json()["id"]
    await _price(client, h, quote_id)

    fresh = await client.get(f"{API}/quotes/{quote_id}", headers=h)
    legs = fresh.json()["options"][0]["legs"]
    assert len(legs) == 2
    for leg in legs:
        assert leg["room_type_id"] is not None
        assert leg["meal_plan_id"] is not None
        assert leg["rooms_required"] == 1
    assert legs[0]["room_type_id"] != legs[1]["room_type_id"]


async def test_a_single_property_option_is_a_package_of_one(
    client, admin_tokens, sample_catalogue
):
    """Every existing quote takes this path. If it stopped working the whole
    Stage 3 document would go with it, so it is asserted rather than assumed."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _quote(
        client,
        h,
        ids,
        options=[{"accommodation_id": ids["acc_sto_full_board"]}],
    )
    option = (await _price(client, h, quote.json()["id"]))["options"][0]
    assert len(option["legs"]) == 1
    assert option["legs"][0]["nights"] == 3
    assert option["accommodation_name"].startswith("Coral Sands Resort")
    assert "→" not in option["accommodation_name"]


# --------------------------------------------------------------------------- #
# What is refused
# --------------------------------------------------------------------------- #


async def test_a_package_with_a_missing_night_is_refused_at_creation(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    """A gap is a night the client has no bed, and it prices perfectly happily —
    the per-person figure is as plausible as ever. So it is refused when stored,
    not flagged for somebody to notice."""
    h, ids = _h(admin_tokens), sample_catalogue
    option = _two_legs(ids, upcountry_lodge)
    option["legs"][1]["check_in"] = "2026-07-03"  # leg 1 ends on the 2nd
    resp = await _quote(client, h, ids, options=[option])
    assert resp.status_code == 400, resp.text
    assert "package_leg_gap" in resp.text


async def test_a_package_that_does_not_reach_departure_is_refused(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    h, ids = _h(admin_tokens), sample_catalogue
    option = _two_legs(ids, upcountry_lodge, departure="2026-07-03")
    resp = await _quote(client, h, ids, options=[option])
    assert resp.status_code == 400, resp.text
    assert "package_ends_before_departure" in resp.text


async def test_the_same_package_cannot_be_offered_twice(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    """Two packages may share a property — Nairobi then Mara against Nairobi
    then Amboseli — which is why the old (quote, accommodation) uniqueness had
    to go. Two identical leg sequences are still one offer listed twice."""
    h, ids = _h(admin_tokens), sample_catalogue
    first = _two_legs(ids, upcountry_lodge)
    second = dict(_two_legs(ids, upcountry_lodge), sort_order=2)
    resp = await _quote(client, h, ids, options=[first, second])
    assert resp.status_code == 400, resp.text
    assert "repeats a package" in resp.text


async def test_two_packages_may_share_a_property(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    """The behaviour the dropped constraint used to forbid: the same first hotel,
    a different second leg."""
    h, ids = _h(admin_tokens), sample_catalogue
    shared = _two_legs(ids, upcountry_lodge)
    other = _two_legs(ids, upcountry_lodge, switch="2026-07-03")
    other["sort_order"] = 2
    resp = await _quote(client, h, ids, options=[shared, other])
    assert resp.status_code == 201, resp.text
    assert len(resp.json()["options"]) == 2


async def test_a_leg_that_cannot_be_priced_drops_the_whole_package(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    """Half a trip is not a cheaper trip. If any leg has no rate the package
    goes, with an internal warning naming the leg."""
    h, ids = _h(admin_tokens), sample_catalogue
    option = _two_legs(ids, upcountry_lodge)
    option["legs"][1]["accommodation_id"] = upcountry_lodge["rateless_id"]
    resp = await _quote(client, h, ids, options=[option])
    assert resp.status_code == 201, resp.text
    result = await _price(client, h, resp.json()["id"])
    assert result["options"] == []
    joined = " ".join(result["warnings"])
    assert "leg 2" in joined, joined


async def test_readiness_catches_a_package_broken_by_a_date_change(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    """Creation refuses an incoherent package, but a quote's dates can move
    afterwards. Readiness is the check that survives editing — and it has to
    block, because nothing about the priced figures reveals it."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _quote(client, h, ids, options=[_two_legs(ids, upcountry_lodge)])
    quote_id = quote.json()["id"]

    ready = await client.get(f"{API}/quotes/{quote_id}/readiness", headers=h)
    assert not any(
        p["code"].startswith("package_") for p in ready.json()["problems"]
    ), ready.json()["problems"]

    # Push departure out by a day behind the packages' backs.
    async with AsyncSessionLocal() as db:
        row = await db.get(Quote, uuid.UUID(quote_id))
        row.departure_date = date(2026, 7, 5)
        await db.commit()

    ready = await client.get(f"{API}/quotes/{quote_id}/readiness", headers=h)
    body = ready.json()
    codes = {p["code"] for p in body["problems"]}
    assert "package_ends_before_departure" in codes, body["problems"]
    assert body["is_ready"] is False


# --------------------------------------------------------------------------- #
# Two packages that share a hotel are still two packages (§3.9, §3.11)
# --------------------------------------------------------------------------- #


async def test_issuing_takes_the_headline_from_the_recommended_package(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    """Both packages start at Coral Sands and differ on where the nights go.

        recommended  Diani 1 + lodge 2 = 45,000 -> group 58,600
        the other    Diani 2 + lodge 1 = 36,000 -> group 47,000

    The version's money, margin and per-option rows were matched to the quote's
    options **by property** until 3.11. With two packages sharing a lead hotel
    that is not a key: one of them silently took the other's figures, and the
    dict that mapped them collapsed to a single entry. It is the option id now.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    recommended = _two_legs(ids, upcountry_lodge)
    recommended["is_recommended"] = True
    later_switch = _two_legs(ids, upcountry_lodge, switch="2026-07-03")
    later_switch["sort_order"] = 2
    quote = await _quote(client, h, ids, options=[recommended, later_switch])
    assert quote.status_code == 201, quote.text
    quote_id = quote.json()["id"]

    issued = await client.post(f"{API}/quotes/{quote_id}/issue", headers=h)
    assert issued.status_code == 200, issued.text
    version = issued.json()
    assert D(version["selling_price"]) == D("58600.00")

    rows = version["options"]
    assert len(rows) == 2, rows
    # Distinct option rows, each with its own total: nothing collapsed.
    assert len({row["option_id"] for row in rows}) == 2, rows
    by_recommendation = {row["is_recommended"]: D(row["selling_total"]) for row in rows}
    assert by_recommendation == {True: D("58600.00"), False: D("47000.00")}
    stored = (await client.get(f"{API}/quotes/{quote_id}", headers=h)).json()
    assert {row["option_id"] for row in rows} == {o["id"] for o in stored["options"]}
