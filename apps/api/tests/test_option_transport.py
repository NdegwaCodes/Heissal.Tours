"""Stage 3.10 — transport reaching an option's price.

``test_transport_rules.py`` covers the rules as pure functions. This file covers
the half that reads the tariff tables, and every figure below is worked by hand
so a change in behaviour shows up as a number rather than as a passing test.

The fixture builds its own destination rather than leaning on the demo
catalogue, because the two things worth proving here need data the seed
deliberately does not have: **two fares for the same class in the same year**
(so a return leg after a revision can be shown to price differently from the
outbound one) and **a fare stored exclusive of VAT** (so the gross-up on the way
out can be checked). All figures invented.

    Rail economy      KES 1,500  to 2 July, then KES 2,000
    Rail business     KES 10,000 exclusive of 16% VAT  -> 11,600 inclusive
    Transfer saloon   KES 4,500 per leg, no named route
    Transfer minibus  KES 12,000 per leg, "Terminus to hotel"

    Four residents, 1-4 July, two twins at 10,000 = 60,000 of beds.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.modules.accommodations.models import (
    Accommodation,
    AccommodationRate,
    MealPlan,
    RoomType,
)
from app.modules.destinations.models import Destination
from app.modules.quotes.models import Quote, QuoteOption
from app.modules.residence.models import ResidenceCategory
from app.modules.transport.models import DestinationTransportMode, TransferRate
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal

ARRIVAL, DEPARTURE = "2026-07-01", "2026-07-04"
WINDOW_FROM, WINDOW_TO = date(2026, 1, 1), date(2026, 12, 31)
#: The economy fare rises mid-trip, which is the whole reason a segment carries
#: a travel date.
REVISION = date(2026, 7, 3)

ECONOMY_OLD, ECONOMY_NEW = D("1500"), D("2000")
BUSINESS_EXCLUSIVE, BUSINESS_INCLUSIVE = D("10000"), D("11600")
SALOON, MINIBUS = D("4500"), D("12000")
PAX = 4


@pytest_asyncio.fixture(loop_scope="session")
async def rail_town():
    """A destination reached by rail, with tariffs nothing else reads."""
    tag = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        fb = (
            await db.execute(select(MealPlan).where(MealPlan.code == "FB"))
        ).scalar_one()
        citizen = (
            await db.execute(
                select(ResidenceCategory).where(ResidenceCategory.key == "citizen")
            )
        ).scalar_one()
        visitor = (
            await db.execute(
                select(ResidenceCategory).where(
                    ResidenceCategory.key == "non_resident"
                )
            )
        ).scalar_one()

        town = Destination(
            name=f"Test Rail Town {tag}", slug=f"test-rail-town-{tag}", type="town"
        )
        db.add(town)
        await db.flush()

        lodge = Accommodation(
            name=f"Rail Test Lodge {tag}",
            slug=f"rail-test-lodge-{tag}",
            destination_id=town.id,
            category="hotel",
        )
        db.add(lodge)
        await db.flush()
        twin = RoomType(
            accommodation_id=lodge.id, name="Twin", code="TWN", max_occupancy=2
        )
        db.add(twin)
        await db.flush()
        # Both residencies priced, so the cohort test below is not thrown by a
        # missing sheet, and singles priced so an odd traveller never falls back
        # to a derived figure.
        for rc, currency, twin_rate in (
            (citizen, "KES", D("10000")),
            (visitor, "USD", D("200")),
        ):
            for occupancy, amount in ((2, twin_rate), (1, twin_rate * D("0.75"))):
                db.add(
                    AccommodationRate(
                        accommodation_id=lodge.id,
                        room_type_id=twin.id,
                        meal_plan_id=fb.id,
                        residence_category_id=rc.id,
                        season_name="standard",
                        occupancy=occupancy,
                        effective_from=WINDOW_FROM,
                        effective_to=WINDOW_TO,
                        currency=currency,
                        rate_per_night=amount,
                        rate_kind="sto",
                    )
                )

        for amount, starts, ends in (
            (ECONOMY_OLD, WINDOW_FROM, date(2026, 7, 2)),
            (ECONOMY_NEW, REVISION, WINDOW_TO),
        ):
            db.add(
                DestinationTransportMode(
                    destination_id=town.id,
                    mode="rail",
                    travel_class="economy",
                    label="Test SGR economy",
                    cost_basis="per_person",
                    price=amount,
                    currency="KES",
                    effective_from=starts,
                    effective_to=ends,
                )
            )
        db.add(
            DestinationTransportMode(
                destination_id=town.id,
                mode="rail",
                travel_class="business",
                label="Test SGR business",
                cost_basis="per_person",
                price=BUSINESS_EXCLUSIVE,
                currency="KES",
                # The one exclusive rate in the corpus, so the gross-up on the
                # way out is exercised rather than assumed.
                vat_inclusive=False,
                vat_pct=D("16"),
                effective_from=WINDOW_FROM,
                effective_to=WINDOW_TO,
            )
        )
        for vehicle_type, price, route in (
            ("saloon", SALOON, ""),
            ("minibus", MINIBUS, "Terminus to hotel"),
        ):
            db.add(
                TransferRate(
                    destination_id=town.id,
                    vehicle_type=vehicle_type,
                    route_label=route,
                    price_per_leg=price,
                    currency="KES",
                    effective_from=WINDOW_FROM,
                    effective_to=WINDOW_TO,
                )
            )
        await db.commit()
        ids = {
            "destination_id": str(town.id),
            "accommodation_id": str(lodge.id),
            "citizen": str(citizen.id),
            "non_resident": str(visitor.id),
            "meal_plan_fb": str(fb.id),
        }

    WHERE.update(ids)
    yield ids

    async with AsyncSessionLocal() as db:
        # Quotes first: pricing writes the resolved room type back onto
        # `quote_options`, so a priced quote outlives the room type it names.
        quote_ids = (
            (
                await db.execute(
                    select(QuoteOption.quote_id).where(
                        QuoteOption.accommodation_id
                        == uuid.UUID(ids["accommodation_id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        for quote_id in set(quote_ids):
            quote = await db.get(Quote, quote_id)
            if quote is not None:
                await db.delete(quote)
        await db.flush()
        town_row = await db.get(Destination, uuid.UUID(ids["destination_id"]))
        lodge_row = await db.get(Accommodation, uuid.UUID(ids["accommodation_id"]))
        if lodge_row is not None:
            await db.delete(lodge_row)
            # Flushed before the destination goes: accommodations.destination_id
            # is ON DELETE RESTRICT and one unit of work does not order these.
            await db.flush()
        if town_row is not None:
            await db.delete(town_row)  # cascades to the tariffs
        await db.commit()


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


#: Filled in by the fixture. Every fare is keyed on a destination, so a segment
#: without one is refused before it can be priced — see the pure rules.
WHERE: dict[str, str] = {}


def _rail(travel_class="economy", **kw):
    kw.setdefault("destination_id", WHERE["destination_id"])
    return {"kind": "line_haul", "mode": "rail", "travel_class": travel_class, **kw}


def _transfer(sequence, vehicle_type="saloon", **kw):
    kw.setdefault("destination_id", WHERE["destination_id"])
    return {
        "sequence": sequence,
        "kind": "transfer",
        "mode": "road",
        "vehicle_type": vehicle_type,
        **kw,
    }


def _four_transfers(start=3, **kw):
    """The transfers a rail return drags with it (§3.8)."""
    return [_transfer(start + n, **kw) for n in range(4)]


async def _quote_body(client, h, ids, *, segments, cohorts=None, options=None):
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Transport Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("transport"),
            "residence_category_id": ids["citizen"],
        },
    )
    assert record.status_code == 201, record.text
    body = {
        "client_id": record.json()["id"],
        "presentation_currency": "KES",
        "residence_category_id": ids["citizen"],
        "arrival_date": ARRIVAL,
        "departure_date": DEPARTURE,
        "requested_meal_plan_id": ids["meal_plan_fb"],
        "options": options
        or [{"accommodation_id": ids["accommodation_id"], "is_recommended": True}],
        "transport_segments": segments,
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
        body["pax_count"] = PAX
    return body


async def _create(client, h, ids, **kw):
    resp = await client.post(
        f"{API}/quotes", headers=h, json=await _quote_body(client, h, ids, **kw)
    )
    return resp


async def _priced(client, h, ids, **kw):
    created = await _create(client, h, ids, **kw)
    assert created.status_code == 201, created.text
    quote_id = created.json()["id"]
    resp = await client.post(f"{API}/quotes/{quote_id}/options/price", headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return quote_id, body


def _transport_of(option):
    return D(option["build_up"]["components"].get("transport", "0"))


# --------------------------------------------------------------------------- #
# The journey reaches the price
# --------------------------------------------------------------------------- #


async def test_the_journey_is_charged_into_the_option(client, admin_tokens, rail_town):
    """Four residents, SGR economy return, four saloon transfers.

        rail       4 x 1,500 x 2 legs   = 12,000
        transfers  4 x 4,500            = 18,000
                                          ------
                                          30,000

    Before 3.10 this component did not exist: the beds were charged and the
    journey that reaches them was not.
    """
    _, body = await _priced(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[_rail(sequence=1), _rail(sequence=2), *_four_transfers()],
    )
    option = body["options"][0]
    assert _transport_of(option) == D("30000")
    # And the beds are still the beds: transport is added, not substituted.
    assert D(option["build_up"]["components"]["accommodation"]) == D("60000")


async def test_a_return_leg_after_a_fare_revision_is_charged_at_the_new_fare(
    client, admin_tokens, rail_town
):
    """Out on 1 July at 1,500, back on 4 July at 2,000.

        4 x 1,500 + 4 x 2,000 + 18,000 = 32,000

    The reason a segment carries a travel date. Priced at one instant instead,
    the return leg would be charged 2,000 short of what the ticket costs.
    """
    _, body = await _priced(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[
            _rail(sequence=1, travel_date=ARRIVAL),
            _rail(sequence=2, travel_date=DEPARTURE),
            *_four_transfers(),
        ],
    )
    assert _transport_of(body["options"][0]) == D("32000")


async def test_a_fare_stored_exclusive_of_vat_is_grossed_up(
    client, admin_tokens, rail_town
):
    """Business class at 10,000 exclusive is 11,600 to the client.

        4 x 11,600 x 2 legs + 18,000 = 110,800

    Every stored accommodation rate is normalised at ingestion, but these two
    tables are entered by hand and carry the flag, so the gross-up happens on
    the way out. Missing it under-charges the leg by the whole VAT rate.
    """
    _, body = await _priced(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[
            _rail(sequence=1, travel_class="business"),
            _rail(sequence=2, travel_class="business"),
            *_four_transfers(),
        ],
    )
    expected = PAX * BUSINESS_INCLUSIVE * 2 + SALOON * 4
    assert _transport_of(body["options"][0]) == expected


async def test_units_multiply_a_per_leg_transfer(client, admin_tokens, rail_town):
    """Two saloons per leg, because four people do not fit in one.

        rail 12,000 + transfers 4 legs x 2 cars x 4,500 = 48,000
    """
    _, body = await _priced(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[_rail(sequence=1), _rail(sequence=2), *_four_transfers(units=2)],
    )
    assert _transport_of(body["options"][0]) == D("48000")


async def test_the_same_journey_is_charged_to_every_option(
    client, admin_tokens, rail_town, sample_catalogue
):
    """Transport is a property of the trip, not of the hotel.

    Charged into each option rather than beside them: outside the options, the
    cheapest bed would look like the cheapest trip, and a client compares trips.
    """
    _, body = await _priced(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[_rail(sequence=1), _rail(sequence=2), *_four_transfers()],
        options=[
            {"accommodation_id": rail_town["accommodation_id"], "is_recommended": True},
            {"accommodation_id": sample_catalogue["acc_sto_full_board"]},
        ],
    )
    assert len(body["options"]) == 2, body
    assert {_transport_of(o) for o in body["options"]} == {D("30000")}


async def test_transport_reaches_every_cohort(client, admin_tokens, rail_town):
    """A seat costs the same whoever is in it, so the cost splits per head.

    Both cohorts' totals rise when the journey is added. Attributing it to the
    quote's own residency instead — what a residency-blind line would do — would
    charge two of the four travellers for all four tickets.
    """
    cohorts = [("citizen", "adult", 2), ("non_resident", "adult", 2)]
    _, without = await _priced(
        client, _h(admin_tokens), rail_town, segments=[], cohorts=cohorts
    )
    _, with_it = await _priced(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[_rail(sequence=1), _rail(sequence=2), *_four_transfers()],
        cohorts=cohorts,
    )
    before = {c["residence"]: D(c["total"]) for c in without["options"][0]["cohorts"]}
    after = {c["residence"]: D(c["total"]) for c in with_it["options"][0]["cohorts"]}
    assert set(before) == set(after) == {"citizen", "non_resident"}
    for residence, total in after.items():
        assert total > before[residence], residence


# --------------------------------------------------------------------------- #
# Flights
# --------------------------------------------------------------------------- #


async def test_a_flight_is_named_and_never_priced(client, admin_tokens, rail_town):
    """Heissal holds no ticketing licence, so the fare is an exclusion.

    Named on the itinerary — a client who is not told to book their own ticket
    is a client who arrives without one — and absent from the money.
    """
    _, body = await _priced(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[
            {
                "sequence": 1,
                "kind": "line_haul",
                "mode": "air",
                "description": "Nairobi to Malindi",
            },
            *_four_transfers(start=2),
        ],
    )
    option = body["options"][0]
    assert any("Nairobi to Malindi" in named for named in option["transport_named"])
    assert _transport_of(option) == SALOON * 4
    assert [c["mode"] for c in option["transport"]] == ["road"] * 4


async def test_the_flight_is_reported_at_readiness(client, admin_tokens, rail_town):
    """As advice, not a refusal: naming a flight is the correct thing to do."""
    quote_id, _ = await _priced(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[
            {"sequence": 1, "kind": "line_haul", "mode": "air"},
            *_four_transfers(start=2),
        ],
    )
    resp = await client.get(
        f"{API}/quotes/{quote_id}/readiness", headers=_h(admin_tokens)
    )
    assert resp.status_code == 200, resp.text
    flight = [
        p
        for p in resp.json()["problems"]
        if p["code"] == "transport_flight_named_not_priced"
    ]
    assert len(flight) == 1, resp.json()["problems"]
    assert flight[0]["severity"] == "advisory"


# --------------------------------------------------------------------------- #
# What is refused
# --------------------------------------------------------------------------- #


async def test_rail_without_its_transfers_is_refused_at_creation(
    client, admin_tokens, rail_town
):
    """A train leaves from a terminus nobody sleeps at (§3.8).

    Refused rather than warned: the missing legs are a cost the client would
    not be charged for, and the quote prices perfectly without them.
    """
    resp = await _create(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[_rail(sequence=1), _transfer(2)],
    )
    assert resp.status_code == 400, resp.text
    assert "transport_rail_without_transfers" in resp.text


async def test_a_movement_with_no_destination_is_refused_at_creation(
    client, admin_tokens, rail_town
):
    """Every fare is keyed on a destination, so there is nothing to price from.

    Refused rather than left to surface as an unpriced movement: the agent is
    one field away from a correct quote, and the earlier that is said the less
    there is to unpick.
    """
    resp = await _create(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[{"sequence": 1, "kind": "transfer", "mode": "road",
                   "vehicle_type": "saloon"}],
    )
    assert resp.status_code == 400, resp.text
    assert "transport_no_destination" in resp.text


async def test_a_mode_we_cannot_sell_is_refused_at_creation(
    client, admin_tokens, rail_town
):
    resp = await _create(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[{"sequence": 1, "kind": "line_haul", "mode": "boat"}],
    )
    assert resp.status_code == 400, resp.text
    assert "transport_unknown_mode" in resp.text


async def test_a_movement_with_no_tariff_blocks_rather_than_pricing_at_zero(
    client, admin_tokens, rail_town
):
    """A coaster transfer nobody has priced.

    Zero is the dangerous answer: on a finished document it is indistinguishable
    from a leg the client is genuinely not being charged for.
    """
    quote_id, body = await _priced(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[_transfer(1, vehicle_type="coaster"), _transfer(2)],
    )
    option = body["options"][0]
    assert len(option["unpriced_transport"]) == 1, option["unpriced_transport"]
    assert _transport_of(option) == SALOON
    resp = await client.get(
        f"{API}/quotes/{quote_id}/readiness", headers=_h(admin_tokens)
    )
    problems = resp.json()["problems"]
    unpriced = [p for p in problems if p["code"] == "unpriced_transport"]
    assert len(unpriced) == 1, problems
    assert unpriced[0]["severity"] == "blocking"
    assert resp.json()["is_ready"] is False


async def test_a_shortfall_of_movements_is_advice(client, admin_tokens, rail_town):
    """One transfer for a trip that takes two is reported, not refused.

    A client arranging their own airport run is a real case; what is not
    acceptable is nobody noticing.
    """
    quote_id, _ = await _priced(
        client, _h(admin_tokens), rail_town, segments=[_transfer(1)]
    )
    resp = await client.get(
        f"{API}/quotes/{quote_id}/readiness", headers=_h(admin_tokens)
    )
    short = [
        p for p in resp.json()["problems"] if p["code"] == "transport_movements_short"
    ]
    assert len(short) == 1, resp.json()["problems"]
    assert short[0]["severity"] == "advisory"


async def test_a_hired_vehicle_is_not_charged_from_a_transfer_tariff(
    client, admin_tokens, rail_town, sample_catalogue
):
    """Our own fleet is costed on km and fuel, so charging a tariff too would
    bill the same movement twice — and it covers every movement at once, so it
    does not read as a shortfall either."""
    quote_id, body = await _priced(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[
            {
                "sequence": 1,
                "kind": "line_haul",
                "mode": "road",
                "vehicle_id": sample_catalogue["vehicle_coaster"],
            }
        ],
    )
    assert _transport_of(body["options"][0]) == D("0")
    resp = await client.get(
        f"{API}/quotes/{quote_id}/readiness", headers=_h(admin_tokens)
    )
    codes = [p["code"] for p in resp.json()["problems"]]
    assert "transport_movements_short" not in codes


async def test_a_transfer_priced_off_another_route_says_so(
    client, admin_tokens, rail_town
):
    """The minibus tariff is for the terminus run, not the airport run.

    Priced off the nearest row rather than left at zero, but said out loud: a
    plausible figure for the wrong drive is the error nobody goes looking for.
    """
    _, body = await _priced(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[
            _transfer(1, vehicle_type="minibus", description="Airport to hotel"),
            _transfer(2, vehicle_type="minibus", description="Terminus to hotel"),
        ],
    )
    option = body["options"][0]
    assert _transport_of(option) == MINIBUS * 2
    mismatched = [w for w in option["warnings"] if "Terminus to hotel" in w]
    assert len(mismatched) == 1, option["warnings"]


# --------------------------------------------------------------------------- #
# Add-ons
# --------------------------------------------------------------------------- #


async def test_a_vvip_add_on_is_priced_apart_from_the_package(
    client, admin_tokens, rail_town
):
    """Quoted separately, so the options stay a comparison of the same journey.

        cost 4,500 + 5% contingency = 4,725, + 24% profit = 5,859
        per person ceil(5,859 / 4 / 100) x 100 = 1,500 -> group 6,000

    Marked up like everything else: an add-on offered at cost is sold at a loss.
    """
    _, body = await _priced(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[
            _transfer(1),
            _transfer(2),
            _transfer(3, is_optional=True, is_vvip=True),
        ],
    )
    option = body["options"][0]
    assert _transport_of(option) == SALOON * 2
    assert D(option["optional_transport_total"]) == SALOON
    assert D(option["optional_transport_price"]) == D("6000")
    assert [c["is_vvip"] for c in option["transport_optional"]] == [True]


async def test_vvip_inside_the_package_is_flagged_at_readiness(
    client, admin_tokens, rail_town
):
    quote_id, _ = await _priced(
        client,
        _h(admin_tokens),
        rail_town,
        segments=[_transfer(1, is_vvip=True), _transfer(2)],
    )
    resp = await client.get(
        f"{API}/quotes/{quote_id}/readiness", headers=_h(admin_tokens)
    )
    flagged = [
        p
        for p in resp.json()["problems"]
        if p["code"] == "transport_vvip_not_optional"
    ]
    assert len(flagged) == 1, resp.json()["problems"]
    assert flagged[0]["severity"] == "advisory"


# --------------------------------------------------------------------------- #
# What the client may see
# --------------------------------------------------------------------------- #


async def test_a_client_facing_role_sees_the_flight_but_no_tariff(
    client, admin_tokens, rail_town
):
    """The fare we pay is ours; the ticket the client must buy is theirs."""
    h = _h(admin_tokens)
    email = unique_email("transportagent")
    await client.post(
        f"{API}/users",
        headers=h,
        json={"email": email, "password": "AgentPass123", "role_keys": ["sales_agent"]},
    )
    login = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "AgentPass123"}
    )
    assert login.status_code == 200, login.text
    agent = _h(login.json())

    created = await _create(
        client,
        h,
        rail_town,
        segments=[
            {"sequence": 1, "kind": "line_haul", "mode": "air", "description": "MBA"},
            *_four_transfers(start=2),
        ],
    )
    assert created.status_code == 201, created.text
    resp = await client.post(
        f"{API}/quotes/{created.json()['id']}/options/price", headers=agent
    )
    assert resp.status_code == 200, resp.text
    option = resp.json()["options"][0]
    assert option["transport_named"], option
    for leaked in ("transport", "transport_optional", "optional_transport_total"):
        assert leaked not in option, leaked
