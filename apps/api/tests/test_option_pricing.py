"""Stage 3.3 — OptionPricingService: the lookups behind a multi-option quotation.

``test_option_rules.py`` covers the arithmetic as pure functions. This file
covers the half that reads the database, against the seeded demo catalogue, and
every expected figure below is worked by hand so a change in behaviour shows up
as a number rather than as a passing test.

The demo properties (all invented data, see ``app/db/seed_demo.py``):

| Property          | FB citizen rate            | Exercises                    |
|-------------------|----------------------------|------------------------------|
| Coral Sands       | twin 9,000 / single 6,500  | per-occupancy rates, STO     |
|                   | superior 12,500            | cheapest-within-hotel        |
| Baobab Beach      | twin 24,000, rack -15%     | half-discount pass-through   |
| Kaskazi Guest Hse | BB only, 6,000             | meal-plan fallback + chef    |
| Chui Festive Camp | 14,000; festive 22,000     | minimum stay (4 nights)      |

The 25-pax three-night July scenario, priced by hand:

    Coral Sands twin, room_plan(25, 2) = 12 rooms of 2 + 1 of 1
      12 x 9,000 + 1 x 6,500              = 114,500 per night
      x 3 nights                          = 343,500   <- cheapest in the hotel
    Coral Sands superior (no single rate)
      13 x 12,500 x 3                     = 487,500   <- so not chosen
    + contingency 5%          17,175      -> cost_basis 360,675
    + profit 24%              86,562      -> 447,237
    per person  ceil(447,237 / 25 / 100)  x 100 = 17,900
    group       17,900 x 25               = 447,500
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.config import settings
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")


def D(value: str) -> Decimal:
    return Decimal(value)


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _client_record(client, h, residence_category_id):
    resp = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Option Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("options"),
            "residence_category_id": residence_category_id,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _quote(
    client,
    h,
    ids,
    *,
    accommodations,
    arrival="2026-07-01",
    departure="2026-07-04",
    pax=25,
    residence="residence_citizen",
    currency="KES",
    plan="meal_plan_fb",
    travellers=None,
    **option_overrides,
):
    """Create a quote offering ``accommodations``, one option each."""
    record = await _client_record(client, h, ids[residence])
    body = {
        "client_id": record["id"],
        "presentation_currency": currency,
        "residence_category_id": ids[residence],
        "arrival_date": arrival,
        "departure_date": departure,
        "pax_count": pax,
        "requested_meal_plan_id": ids[plan],
        "travellers": travellers or [],
        "options": [
            {"accommodation_id": ids[key], "sort_order": order, **option_overrides}
            for order, key in enumerate(accommodations, start=1)
        ],
    }
    resp = await client.post(f"{API}/quotes", headers=h, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _price(client, h, quote_id):
    resp = await client.post(f"{API}/quotes/{quote_id}/options/price", headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _option(result, name_fragment):
    """The one priced option whose property name contains ``name_fragment``."""
    matches = [
        o for o in result["options"] if name_fragment in o["accommodation_name"]
    ]
    priced = [o["accommodation_name"] for o in result["options"]]
    assert len(matches) == 1, f"{name_fragment} not uniquely priced; got {priced}"
    return matches[0]


async def _agent_headers(client, h):
    """A sales agent: quote:create + quote:read, but NOT quote:read_cost."""
    email = unique_email("optagent")
    await client.post(
        f"{API}/users",
        headers=h,
        json={"email": email, "password": "AgentPass123", "role_keys": ["sales_agent"]},
    )
    login = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "AgentPass123"}
    )
    assert login.status_code == 200, login.text
    return _h(login.json())


# --------------------------------------------------------------------------- #
# Per-occupancy selection and cheapest-within-hotel (§3.3, §3.7)
# --------------------------------------------------------------------------- #


async def test_prices_the_cheapest_room_type_using_per_occupancy_rates(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote = await _quote(client, h, sample_catalogue, accommodations=["acc_sto_full_board"])
    result = await _price(client, h, quote["id"])

    option = _option(result, "Coral Sands")
    # The twin, not the superior: cheapest within the hotel is decided on the
    # room rate, and the engine never chooses between hotels.
    assert option["room_type_name"] == "Twin"
    assert option["rooms_required"] == 13
    assert option["nights"] == 3
    assert option["meal_plan_code"] == "FB"
    # 12 doubles at 9,000 plus one single at its own quoted 6,500 — the odd room
    # is neither half a double nor a whole one.
    assert D(option["build_up"]["components"]["accommodation"]) == D("343500")
    assert D(option["build_up"]["cost_subtotal"]) == D("343500")
    assert D(option["build_up"]["contingency_value"]) == D("17175")
    assert D(option["build_up"]["cost_basis"]) == D("360675")
    assert D(option["build_up"]["profit_value"]) == D("86562")
    assert D(option["per_person"]) == D("17900")
    assert D(option["group_total"]) == D("447500")


async def test_per_person_times_pax_equals_the_group_total(
    client, admin_tokens, sample_catalogue
):
    """The sample quotation's contradiction, made structurally impossible.

    Its page 6 says 28,800 per person against a total implying 28,400. Rounding
    per-person first and multiplying back out means the two figures on the
    document cannot disagree, whatever the group size.
    """
    h = _h(admin_tokens)
    for pax in (1, 2, 7, 13, 25):
        quote = await _quote(
            client, h, sample_catalogue, accommodations=["acc_sto_full_board"], pax=pax
        )
        option = _option(await _price(client, h, quote["id"]), "Coral Sands")
        assert D(option["per_person"]) * pax == D(option["group_total"]), pax


async def test_a_lone_guest_pays_the_quoted_single_rate(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote = await _quote(
        client, h, sample_catalogue, accommodations=["acc_sto_full_board"], pax=1
    )
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")
    assert option["rooms_required"] == 1
    # 6,500 x 3 nights — the sheet's single rate, not half of the 9,000 double.
    assert D(option["build_up"]["components"]["accommodation"]) == D("19500")


# --------------------------------------------------------------------------- #
# Supplier discounts (§3.5)
# --------------------------------------------------------------------------- #


async def test_rack_discount_passes_half_to_the_client_and_keeps_half(
    client, admin_tokens, sample_catalogue
):
    """Baobab: 24,000 rack, 15% stated. Three numbers, all different.

        sheet    13 rooms x 24,000 x 3 nights = 936,000
        paid     x 0.85                       = 795,600
        costed   x 0.925                      = 865,800
        retained                              =  70,200
    """
    h = _h(admin_tokens)
    quote = await _quote(client, h, sample_catalogue, accommodations=["acc_rack_discounted"])
    option = _option(await _price(client, h, quote["id"]), "Baobab")

    assert D(option["build_up"]["components"]["accommodation"]) == D("865800")
    assert D(option["supplier_paid_total"]) == D("795600")
    assert D(option["retained_discount"]) == D("70200")
    # Realised margin is the profit percentage PLUS the retained half, so the
    # two are tracked apart rather than rolled together (§3.5).
    assert D(option["build_up"]["profit_value"]) == D("218181.6")


async def test_a_stated_single_supplement_is_surfaced_not_added(
    client, admin_tokens, sample_catalogue
):
    """Baobab quotes no single rate, only a single supplement.

    Adding it to a per-room rate would charge one guest 28,000 for the room two
    guests pay 24,000 for. The room is charged in full instead — the other half
    of the same §3.3 rule — and the supplement is raised for review, because its
    presence hints the sheet may be priced per person sharing.
    """
    h = _h(admin_tokens)
    quote = await _quote(client, h, sample_catalogue, accommodations=["acc_rack_discounted"])
    option = _option(await _price(client, h, quote["id"]), "Baobab")

    # 13 full rooms, none of them loaded with the 4,000 supplement.
    assert D(option["build_up"]["components"]["accommodation"]) == D("865800")
    joined = " ".join(option["warnings"])
    assert "single supplement" in joined
    assert "charged in full" in joined
    assert "per person sharing" in joined


async def test_an_sto_rate_is_used_as_is(client, admin_tokens, sample_catalogue):
    h = _h(admin_tokens)
    quote = await _quote(client, h, sample_catalogue, accommodations=["acc_sto_full_board"])
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")
    # An STO sheet is already an operator rate: nothing is retained on it.
    assert D(option["supplier_paid_total"]) == D("343500")
    assert D(option["retained_discount"]) == D("0")


# --------------------------------------------------------------------------- #
# Minimum stay (§3.3a)
# --------------------------------------------------------------------------- #


async def test_a_short_festive_stay_is_refused_but_still_shown(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_min_stay"],
        arrival="2026-12-21",
        departure="2026-12-24",
    )
    result = await _price(client, h, quote["id"])

    assert result["options"] == []
    assert len(result["rejected"]) == 1
    refused = result["rejected"][0]
    assert refused["accommodation_id"] == sample_catalogue["acc_min_stay"]
    assert refused["reason"] == (
        "Requires a minimum stay of 4 nights; this itinerary is 3 nights."
    )


async def test_the_refusal_reason_says_nothing_commercial(
    client, admin_tokens, sample_catalogue
):
    """The reason prints on the client document verbatim (§3.3a)."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_min_stay"],
        arrival="2026-12-21",
        departure="2026-12-24",
    )
    reason = (await _price(client, h, quote["id"]))["rejected"][0]["reason"].lower()
    for forbidden in ("cost", "margin", "profit", "supplier", "rate", "discount"):
        assert forbidden not in reason, forbidden


async def test_the_same_property_prices_normally_outside_the_restricted_window(
    client, admin_tokens, sample_catalogue
):
    """The minimum belongs to the festive rate, not to the property."""
    h = _h(admin_tokens)
    quote = await _quote(client, h, sample_catalogue, accommodations=["acc_min_stay"])
    result = await _price(client, h, quote["id"])

    assert result["rejected"] == []
    option = _option(result, "Chui")
    # Standard season: 13 rooms x 14,000 x 3 nights.
    assert D(option["build_up"]["components"]["accommodation"]) == D("546000")


async def test_a_long_enough_festive_stay_gets_the_festive_rate(
    client, admin_tokens, sample_catalogue
):
    """The overlapping festive row wins per night — later effective_from."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_min_stay"],
        arrival="2026-12-21",
        departure="2026-12-25",
        pax=2,
    )
    option = _option(await _price(client, h, quote["id"]), "Chui")
    # One room, four nights, at the festive 22,000 rather than the standard
    # 14,000 the wide season would otherwise have supplied.
    assert D(option["build_up"]["components"]["accommodation"]) == D("88000")


# --------------------------------------------------------------------------- #
# Supplements (§3.5a)
# --------------------------------------------------------------------------- #


async def test_a_mandatory_supplement_is_charged_only_for_the_nights_it_covers(
    client, admin_tokens, sample_catalogue
):
    """Coral Sands' Christmas dinner: 2,500 per person per night, 24-25 Dec.

    The stay is 23-27 December — four nights, of which only two fall in the
    supplement's window. Charging all four would over-price it and charging none
    would under-price it, which is the whole reason a supplement carries its own
    dates rather than inheriting the season's.

        accommodation  1 room x 9,000 x 4 nights = 36,000
        supplement     2,500 x 2 pax x 2 nights  = 10,000
        subtotal                                 = 46,000
        + contingency 5%   2,300 -> basis 48,300
        + profit 24%      11,592 -> 59,892
        per person  ceil(29,946 / 100) x 100     = 30,000
        group                                    = 60,000
    """
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_sto_full_board"],
        arrival="2026-12-23",
        departure="2026-12-27",
        pax=2,
    )
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")

    assert len(option["supplements"]) == 1
    supplement = option["supplements"][0]
    assert supplement["kind"] == "gala"
    assert supplement["basis"] == "per_person_per_night"
    assert supplement["nights"] == 2
    assert D(supplement["cost"]) == D("10000")

    assert D(option["build_up"]["components"]["accommodation"]) == D("36000")
    assert D(option["build_up"]["components"]["supplements"]) == D("10000")
    assert D(option["build_up"]["cost_subtotal"]) == D("46000")
    assert D(option["per_person"]) == D("30000")
    assert D(option["group_total"]) == D("60000")


async def test_no_supplement_outside_its_window(client, admin_tokens, sample_catalogue):
    h = _h(admin_tokens)
    quote = await _quote(client, h, sample_catalogue, accommodations=["acc_sto_full_board"])
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")
    assert option["supplements"] == []
    assert "supplements" not in option["build_up"]["components"]


# --------------------------------------------------------------------------- #
# Meal plans and the chef (§3.4)
# --------------------------------------------------------------------------- #


async def test_full_board_request_falls_back_to_bed_and_breakfast(
    client, admin_tokens, sample_catalogue
):
    """Kaskazi has no FB or HB row, so the chain lands on BB.

        accommodation  13 rooms x 6,000 x 3 nights = 234,000
        chef           5,000 x (2 meals x 3 nights) =  30,000
        food                                        =  30,000
        subtotal                                    = 294,000
        + contingency 5%  14,700 -> basis 308,700
        + profit 24%      74,088 -> 382,788
        per person  ceil(15,311.52 / 100) x 100     = 15,400
        group                                       = 385,000
    """
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_bb_only"],
        chef_fee_per_meal="5000",
        manual_meal_cost="30000",
    )
    option = _option(await _price(client, h, quote["id"]), "Kaskazi")

    assert option["meal_plan_code"] == "BB"
    assert option["meal_plan_fallback_from"] == "FB"
    # An option on a different board basis is not comparable with the others.
    assert option["is_comparable"] is False
    assert D(option["build_up"]["components"]["chef"]) == D("30000")
    assert D(option["build_up"]["components"]["meals"]) == D("30000")
    assert D(option["build_up"]["cost_subtotal"]) == D("294000")
    assert D(option["per_person"]) == D("15400")
    assert D(option["group_total"]) == D("385000")


async def test_a_bed_and_breakfast_option_without_a_chef_is_flagged(
    client, admin_tokens, sample_catalogue
):
    """Silence is not a zero: an unpriced chef would quietly undercut the option."""
    h = _h(admin_tokens)
    quote = await _quote(client, h, sample_catalogue, accommodations=["acc_bb_only"])
    option = _option(await _price(client, h, quote["id"]), "Kaskazi")
    joined = " ".join(option["warnings"])
    assert "needs a chef" in joined
    assert "6 group meal(s)" in joined


async def test_no_chef_on_a_full_board_option(client, admin_tokens, sample_catalogue):
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_sto_full_board"],
        chef_fee_per_meal="5000",
        manual_meal_cost="30000",
    )
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")
    # The chef fee was entered but full board already feeds the guests, so it is
    # never charged (§3.4).
    assert "chef" not in option["build_up"]["components"]
    assert "meals" not in option["build_up"]["components"]


# --------------------------------------------------------------------------- #
# The build-up (§3.6)
# --------------------------------------------------------------------------- #


async def test_the_agent_cover_fee_reaches_the_client_unmarked_up(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    plain = await _quote(client, h, sample_catalogue, accommodations=["acc_sto_full_board"])
    with_fee = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_sto_full_board"],
        agent_cover_fee="25000",
    )
    base = _option(await _price(client, h, plain["id"]), "Coral Sands")
    loaded = _option(await _price(client, h, with_fee["id"]), "Coral Sands")

    # Profit is identical: the fee sits outside the percentage entirely.
    assert D(loaded["build_up"]["profit_value"]) == D(base["build_up"]["profit_value"])
    assert D(loaded["build_up"]["selling_total"]) - D(
        base["build_up"]["selling_total"]
    ) == D("25000")
    assert D(loaded["build_up"]["agent_cover_fee"]) == D("25000")


async def test_a_mixed_group_is_quoted_as_a_total_only(
    client, admin_tokens, sample_catalogue
):
    """An adult-plus-child group has no single per-person figure (§3.6, §3.4a)."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_sto_full_board"],
        pax=2,
        travellers=[{"traveller_type": "adult"}, {"traveller_type": "child", "age": 8}],
    )
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")
    assert option["per_person"] is None
    assert D(option["group_total"]) > 0


async def test_per_quote_overrides_beat_the_business_defaults(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    record = await _client_record(client, h, sample_catalogue["residence_citizen"])
    resp = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record["id"],
            "presentation_currency": "KES",
            "residence_category_id": sample_catalogue["residence_citizen"],
            "arrival_date": "2026-07-01",
            "departure_date": "2026-07-04",
            "pax_count": 25,
            "requested_meal_plan_id": sample_catalogue["meal_plan_fb"],
            "profit_pct": "10",
            "contingency_pct": "0",
            "options": [
                {"accommodation_id": sample_catalogue["acc_sto_full_board"]}
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    option = _option(await _price(client, h, resp.json()["id"]), "Coral Sands")
    # No contingency, 10% profit: 343,500 -> 343,500 -> 34,350 -> 377,850.
    assert D(option["build_up"]["contingency_value"]) == D("0")
    assert D(option["build_up"]["cost_basis"]) == D("343500")
    assert D(option["build_up"]["profit_value"]) == D("34350")


# --------------------------------------------------------------------------- #
# Residency and currency (§3.5b, §3.6a)
# --------------------------------------------------------------------------- #


async def test_a_usd_rate_converts_at_the_contract_rate(
    client, admin_tokens, sample_catalogue
):
    """The 130 KES/USD contract rate, applied end to end.

    1 room x USD 180 x 3 nights = USD 540 -> KES 70,200.
    """
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_sto_full_board"],
        pax=2,
        residence="residence_non_resident",
        currency="KES",
    )
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")
    assert D(option["build_up"]["components"]["accommodation"]) == D("70200")


async def test_resident_and_non_resident_prices_differ_materially(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    resident = await _quote(
        client, h, sample_catalogue, accommodations=["acc_sto_full_board"], pax=2
    )
    visitor = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_sto_full_board"],
        pax=2,
        residence="residence_non_resident",
        currency="KES",
    )
    local = _option(await _price(client, h, resident["id"]), "Coral Sands")
    foreign = _option(await _price(client, h, visitor["id"]), "Coral Sands")
    # 27,000 against 70,200 — the gap the residence category exists for.
    assert D(local["build_up"]["components"]["accommodation"]) == D("27000")
    assert D(foreign["build_up"]["components"]["accommodation"]) == D("70200")


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


async def test_pricing_writes_the_resolution_back_onto_the_quote(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_sto_full_board", "acc_bb_only"],
    )
    # Fresh options carry no room type: pricing is what resolves them.
    assert all(o["room_type_id"] is None for o in quote["options"])

    await _price(client, h, quote["id"])
    reloaded = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()

    coral = next(
        o
        for o in reloaded["options"]
        if o["accommodation_id"] == sample_catalogue["acc_sto_full_board"]
    )
    assert coral["room_type_id"] == sample_catalogue["room_coral_twin"]
    assert coral["meal_plan_id"] == sample_catalogue["meal_plan_fb"]
    assert coral["rooms_required"] == 13
    assert coral["meal_plan_fallback_from"] is None

    kaskazi = next(
        o
        for o in reloaded["options"]
        if o["accommodation_id"] == sample_catalogue["acc_bb_only"]
    )
    assert kaskazi["meal_plan_id"] == sample_catalogue["meal_plan_bb"]
    assert kaskazi["meal_plan_fallback_from"] == "FB"
    assert kaskazi["is_comparable"] is False


async def test_repricing_replaces_refusals_rather_than_accumulating_them(
    client, admin_tokens, sample_catalogue
):
    """A stale refusal left on the document would contradict the new dates."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_min_stay"],
        arrival="2026-12-21",
        departure="2026-12-24",
    )
    first = await _price(client, h, quote["id"])
    second = await _price(client, h, quote["id"])
    assert len(first["rejected"]) == 1
    assert len(second["rejected"]) == 1

    stored = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()
    assert len(stored["rejected_candidates"]) == 1


async def test_options_are_priced_in_the_agents_order(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_bb_only", "acc_rack_discounted", "acc_sto_full_board"],
    )
    result = await _price(client, h, quote["id"])
    assert [o["accommodation_name"] for o in result["options"]] == [
        "Kaskazi Guest House (demo)",
        "Baobab Beach Lodge (demo)",
        "Coral Sands Resort (demo)",
    ]


# --------------------------------------------------------------------------- #
# The internal/client boundary (§2)
# --------------------------------------------------------------------------- #

COST_KEYS = {
    "cost_subtotal",
    "cost_basis",
    "contingency_value",
    "profit_value",
    "after_profit",
    "agent_cover_fee",
    "supplier_paid_total",
    "retained_discount",
    "build_up",
    "components",
    "supplements",
    "warnings",
    "meal_plan_fallback_from",
}


def _keys_everywhere(payload) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, dict):
        found |= set(payload)
        for value in payload.values():
            found |= _keys_everywhere(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= _keys_everywhere(item)
    return found


async def test_option_pricing_leaks_no_cost_to_a_client_facing_role(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_sto_full_board", "acc_rack_discounted"],
        agent_cover_fee="25000",
    )
    ah = await _agent_headers(client, h)
    resp = await client.post(f"{API}/quotes/{quote['id']}/options/price", headers=ah)
    assert resp.status_code == 200, resp.text

    leaked = _keys_everywhere(resp.json()) & COST_KEYS
    assert not leaked, f"leaked {leaked} to a role without quote:read_cost"
    # The agent still gets a usable price, and the refusals they must explain.
    option = _option(resp.json(), "Coral Sands")
    assert D(option["per_person"]) == D("18900")
    assert D(option["group_total"]) == D("472500")
    assert "rejected" in resp.json()


async def test_a_cost_reader_does_see_the_build_up(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote = await _quote(client, h, sample_catalogue, accommodations=["acc_rack_discounted"])
    option = _option(await _price(client, h, quote["id"]), "Baobab")
    assert D(option["build_up"]["cost_subtotal"]) == D("865800")
    assert D(option["supplier_paid_total"]) == D("795600")


# --------------------------------------------------------------------------- #
# Refusals to guess
# --------------------------------------------------------------------------- #


async def test_pricing_refuses_a_quote_with_no_requested_meal_plan(
    client, admin_tokens, sample_catalogue
):
    """The fallback chain has no starting point without one."""
    h = _h(admin_tokens)
    record = await _client_record(client, h, sample_catalogue["residence_citizen"])
    quote = (
        await client.post(
            f"{API}/quotes",
            headers=h,
            json={
                "client_id": record["id"],
                "presentation_currency": "KES",
                "residence_category_id": sample_catalogue["residence_citizen"],
                "arrival_date": "2026-07-01",
                "departure_date": "2026-07-04",
                "pax_count": 25,
                "options": [
                    {"accommodation_id": sample_catalogue["acc_sto_full_board"]}
                ],
            },
        )
    ).json()
    resp = await client.post(f"{API}/quotes/{quote['id']}/options/price", headers=h)
    assert resp.status_code == 400
    assert "requested_meal_plan_id" in resp.text


async def test_pricing_refuses_a_quote_with_no_group_size(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    record = await _client_record(client, h, sample_catalogue["residence_citizen"])
    quote = (
        await client.post(
            f"{API}/quotes",
            headers=h,
            json={
                "client_id": record["id"],
                "presentation_currency": "KES",
                "residence_category_id": sample_catalogue["residence_citizen"],
                "arrival_date": "2026-07-01",
                "departure_date": "2026-07-04",
                "requested_meal_plan_id": sample_catalogue["meal_plan_fb"],
                "options": [
                    {"accommodation_id": sample_catalogue["acc_sto_full_board"]}
                ],
            },
        )
    ).json()
    resp = await client.post(f"{API}/quotes/{quote['id']}/options/price", headers=h)
    assert resp.status_code == 400
    assert "pax_count" in resp.text


async def test_pricing_refuses_a_quote_with_no_options(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    record = await _client_record(client, h, sample_catalogue["residence_citizen"])
    quote = (
        await client.post(
            f"{API}/quotes",
            headers=h,
            json={
                "client_id": record["id"],
                "presentation_currency": "KES",
                "residence_category_id": sample_catalogue["residence_citizen"],
                "arrival_date": "2026-07-01",
                "departure_date": "2026-07-04",
                "pax_count": 25,
                "requested_meal_plan_id": sample_catalogue["meal_plan_fb"],
            },
        )
    ).json()
    resp = await client.post(f"{API}/quotes/{quote['id']}/options/price", headers=h)
    assert resp.status_code == 400
    assert "no options" in resp.text


async def test_a_property_with_no_rates_is_an_internal_warning_not_a_refusal(
    client, admin_tokens, sample_catalogue
):
    """A gap in our data is not something to tell a client about (§3.3a).

    Pendo has only a half-board rate for citizens, so a non-resident request
    finds nothing. That says nothing about the villa, so it must not appear as a
    considered-and-declined property on the document.
    """
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        accommodations=["acc_villa"],
        pax=4,
        residence="residence_non_resident",
        currency="KES",
    )
    result = await _price(client, h, quote["id"])
    assert result["options"] == []
    assert result["rejected"] == []
    assert any("no rates are loaded" in w for w in result["warnings"])


# --------------------------------------------------------------------------- #
# Stage 3.8 — pricing on the group vector
# --------------------------------------------------------------------------- #


async def _cohort_quote(client, h, ids, *, cohorts, accommodations, **over):
    """A quote whose group is given as cohorts rather than a flat headcount."""
    record = await _client_record(client, h, ids["residence_citizen"])
    body = {
        "client_id": record["id"],
        "presentation_currency": "KES",
        "residence_category_id": ids["residence_citizen"],
        "arrival_date": "2026-07-01",
        "departure_date": "2026-07-04",
        "requested_meal_plan_id": ids["meal_plan_fb"],
        "cohorts": [
            {
                "residence_category_id": ids[f"residence_{residence}"],
                "traveller_type": kind,
                "headcount": n,
            }
            for residence, kind, n in cohorts
        ],
        "options": [
            {"accommodation_id": ids[key], "sort_order": order}
            for order, key in enumerate(accommodations, start=1)
        ],
    }
    body.update(over)
    resp = await client.post(f"{API}/quotes", headers=h, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_a_mixed_residency_group_takes_an_extra_room(
    client, admin_tokens, sample_catalogue
):
    """Three citizens and three non-residents need **four** twins, not three.

    Rooming partitions by residency because the two halves are priced off
    different sheets in different currencies, and no room can hold one of each
    and still have a defined rate. Six people in twins is three rooms by
    ``ceil``; this is the one case where that is the wrong answer, and getting it
    wrong is a room nobody booked discovered at check-in.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _cohort_quote(
        client,
        h,
        ids,
        cohorts=[("citizen", "adult", 3), ("non_resident", "adult", 3)],
        accommodations=["acc_sto_full_board"],
    )
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")

    assert option["rooms_required"] == 4
    # Mixed residency means two currencies and two prices, so a single
    # per-person figure would be a fiction (§3.6).
    assert option["per_person"] is None
    assert D(option["group_total"]) > 0


async def test_each_residency_is_costed_off_its_own_sheet(
    client, admin_tokens, sample_catalogue
):
    """Coral Sands quotes citizens 9,000 KES a twin and non-residents 180 USD.

    A mixed group must cost more than the same headcount of citizens alone —
    both because the non-resident sheet is dearer and because of the extra room.
    Pricing the whole group off the quote's own category, which is what happened
    before the vector, quietly under-charged every non-resident.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    mixed = await _cohort_quote(
        client,
        h,
        ids,
        cohorts=[("citizen", "adult", 3), ("non_resident", "adult", 3)],
        accommodations=["acc_sto_full_board"],
    )
    residents_only = await _cohort_quote(
        client,
        h,
        ids,
        cohorts=[("citizen", "adult", 6)],
        accommodations=["acc_sto_full_board"],
    )

    mixed_option = _option(await _price(client, h, mixed["id"]), "Coral Sands")
    resident_option = _option(
        await _price(client, h, residents_only["id"]), "Coral Sands"
    )

    assert resident_option["rooms_required"] == 3
    assert mixed_option["rooms_required"] == 4
    assert D(mixed_option["group_total"]) > D(resident_option["group_total"])
    # An all-citizen group is uniform, so it still gets a per-person figure.
    assert resident_option["per_person"] is not None


async def test_a_property_that_prices_only_one_residency_is_not_offered(
    client, admin_tokens, sample_catalogue
):
    """Baobab Beach has citizen rates and no non-resident sheet.

    It cannot house a mixed group, and the honest answer is to leave it off
    rather than price the residents and house the rest on a rate nobody quoted.
    The reason stays an internal warning, not a client-facing rejection: "we
    have no non-resident rates loaded" is a statement about our data (§3.3a).
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _cohort_quote(
        client,
        h,
        ids,
        cohorts=[("citizen", "adult", 2), ("non_resident", "adult", 2)],
        accommodations=["acc_rack_discounted"],
    )
    result = await _price(client, h, quote["id"])

    assert result["options"] == []
    assert result["rejected"] == []


async def test_the_headcount_and_the_rooming_come_from_one_place(
    client, admin_tokens, sample_catalogue
):
    """A quote carrying both a headcount and named travellers used to be able to
    room one count and divide by another. The vector is now the single source,
    so the rooming and the per-person divisor always agree.

    Two named travellers beside ``pax_count`` of 2: the rows are the better
    answer, because they carry the adult/child split the headcount cannot.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _quote(
        client,
        h,
        ids,
        accommodations=["acc_sto_full_board"],
        pax=2,
        travellers=[
            {"traveller_type": "adult"},
            {"traveller_type": "child", "age": 8},
        ],
    )
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")
    assert option["rooms_required"] == 1
    # Adult plus child is not uniform, so no per-person figure (§3.4a).
    assert option["per_person"] is None


async def test_a_headcount_beside_a_few_named_guests_still_wins(
    client, admin_tokens, sample_catalogue
):
    """25 people of whom two are named is 25 travelling, not 2. The headcount
    says something the rows do not, so it is the authority."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _quote(
        client,
        h,
        ids,
        accommodations=["acc_sto_full_board"],
        pax=25,
        travellers=[{"traveller_type": "adult"}, {"traveller_type": "adult"}],
    )
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")
    assert option["rooms_required"] == 13


# --------------------------------------------------------------------------- #
# Stage 3.8 — per-cohort prices, each in its own billing currency
# --------------------------------------------------------------------------- #


def _by_cohort(option):
    return {
        (c["residence"], c["traveller_type"]): c for c in option["cohorts"]
    }


async def test_residents_and_non_residents_get_their_own_per_person_figure(
    client, admin_tokens, sample_catalogue
):
    """The client's requirement, in one response.

    A mixed group has no single per-person price — ``per_person`` is NULL, and
    correctly so, because one number cannot span two currencies. What replaces
    it is a figure per cohort **in that cohort's own billing currency**:
    residents in KES off the shilling sheet, non-residents in USD off the dollar
    one.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _cohort_quote(
        client,
        h,
        ids,
        cohorts=[("citizen", "adult", 3), ("non_resident", "adult", 3)],
        accommodations=["acc_sto_full_board"],
    )
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")

    assert option["per_person"] is None
    cohorts = _by_cohort(option)
    assert set(cohorts) == {("citizen", "adult"), ("non_resident", "adult")}

    resident = cohorts[("citizen", "adult")]
    visitor = cohorts[("non_resident", "adult")]
    assert resident["currency"] == "KES"
    assert visitor["currency"] == "USD"
    assert resident["headcount"] == 3
    assert visitor["headcount"] == 3
    assert D(resident["per_person"]) > 0
    assert D(visitor["per_person"]) > 0


async def test_every_cohort_total_is_its_per_person_times_its_headcount(
    client, admin_tokens, sample_catalogue
):
    """The reconciliation rule the whole design exists for (§3.6).

    Per person is rounded up **first** and multiplied back out, so a client can
    check the arithmetic on the page. Rounding a total and dividing instead is
    what makes the reference proposal contradict itself — page 6 quotes 28,800
    per person against a 720,000 total that implies 28,400.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _cohort_quote(
        client,
        h,
        ids,
        cohorts=[
            ("citizen", "adult", 4),
            ("citizen", "child", 2),
            ("non_resident", "adult", 2),
        ],
        accommodations=["acc_sto_full_board"],
    )
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")

    assert len(option["cohorts"]) == 3
    for cohort in option["cohorts"]:
        assert D(cohort["total"]) == D(cohort["per_person"]) * cohort["headcount"], (
            cohort
        )


async def test_a_child_cohort_is_priced_apart_from_the_adults(
    client, admin_tokens, sample_catalogue
):
    """Same residency, same rooms, two cohorts — so the split is visible even
    where no child *rate* applies. This is what makes an adult-plus-child group
    quotable per person at all; before the vector it was a total and nothing
    else."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _cohort_quote(
        client,
        h,
        ids,
        cohorts=[("citizen", "adult", 4), ("citizen", "child", 2)],
        accommodations=["acc_sto_full_board"],
    )
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")

    cohorts = _by_cohort(option)
    assert set(cohorts) == {("citizen", "adult"), ("citizen", "child")}
    assert all(c["currency"] == "KES" for c in option["cohorts"])
    # Six travellers, all accounted for.
    assert sum(c["headcount"] for c in option["cohorts"]) == 6


async def test_a_uniform_group_gets_one_cohort_matching_the_headline_figure(
    client, admin_tokens, sample_catalogue
):
    """25 citizens is one cohort, and its per-person figure must be the same
    number the option already quotes. Two ways of computing the same price that
    disagree is worse than either one alone."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _cohort_quote(
        client,
        h,
        ids,
        cohorts=[("citizen", "adult", 25)],
        accommodations=["acc_sto_full_board"],
    )
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")

    assert len(option["cohorts"]) == 1
    only = option["cohorts"][0]
    assert D(only["per_person"]) == D(option["per_person"])
    assert D(only["total"]) == D(option["group_total"])
    assert only["currency"] == option["currency"] == "KES"


async def test_the_rate_behind_a_converted_total_is_disclosed(
    client, admin_tokens, sample_catalogue
):
    """A group total spanning currencies is a conversion, and a converted total
    with an unstated rate is a dispute waiting to happen. The rate used is
    returned alongside it."""
    h, ids = _h(admin_tokens), sample_catalogue
    quote = await _cohort_quote(
        client,
        h,
        ids,
        cohorts=[("citizen", "adult", 2), ("non_resident", "adult", 2)],
        accommodations=["acc_sto_full_board"],
    )
    option = _option(await _price(client, h, quote["id"]), "Coral Sands")

    assert option["conversions"].get("USD/KES") == "130.00000000"
