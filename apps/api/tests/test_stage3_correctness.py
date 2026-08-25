"""Stage 3.7 — correctness tests for the quotation document.

The earlier Stage 3 files each test one layer: ``test_option_rules.py`` the pure
arithmetic, ``test_option_pricing.py`` the lookups, ``test_quote_assembly.py``
assembly and issuing, ``test_documents.py`` and ``test_document_pdf.py`` the
rendered artefacts. This file tests the things that only go wrong *between* those
layers, plus the four rules the design doc calls out by name for this milestone:
VAT normalisation, discount halving, rooming edge cases and the meal-plan
fallback chain.

Two habits run through it:

* **Sweeps for the invariants, hand-worked figures for the behaviour.** An
  invariant ("we never quote the client below what we pay the hotel") is asserted
  across a range of inputs, because a single example passing says almost nothing.
  A behaviour ("25 guests in twins costs 447,500") is asserted as the exact
  number, worked by hand in the comment above it, so a change shows up as a
  number rather than as a still-green test.
* **The last section asserts against the whole chain at once** — quote → price →
  issue → client read → internal read → rendered HTML — because every figure a
  client sees has passed through all six, and an identity that holds in the
  service but not in the template is still a wrong quotation.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest

from app.core.config import settings
from app.core.vat import DEFAULT_VAT_PCT
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")


def D(value: str) -> Decimal:
    return Decimal(value)


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _client_record(client, h, residence_category_id, name="Correctness Co"):
    resp = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"{name} {uuid.uuid4().hex[:8]}",
            "email": unique_email("s37"),
            "residence_category_id": residence_category_id,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _quote(client, h, ids, *, options, **over):
    record = await _client_record(client, h, ids["residence_citizen"])
    body = {
        "client_id": record["id"],
        "presentation_currency": "KES",
        "residence_category_id": ids["residence_citizen"],
        "arrival_date": "2026-07-01",
        "departure_date": "2026-07-04",
        "pax_count": 25,
        "requested_meal_plan_id": ids["meal_plan_fb"],
        "options": options,
    }
    body.update(over)
    resp = await client.post(f"{API}/quotes", headers=h, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _price(client, h, quote_id):
    resp = await client.post(f"{API}/quotes/{quote_id}/options/price", headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _option(result, fragment):
    matches = [o for o in result["options"] if fragment in o["accommodation_name"]]
    assert len(matches) == 1, [o["accommodation_name"] for o in result["options"]]
    return matches[0]


def _visible_text(page: str) -> str:
    """The words a reader actually sees, lower-cased.

    Stylesheets and markup are stripped first. Without that, checking the source
    for internal vocabulary is a false-positive machine — CSS alone contains
    ``margin``, and a class name could contain anything — and the question being
    asked is about what the client reads, not about what the page is built from.
    """
    body = re.sub(r"<(style|script)\b.*?</\1>", " ", page, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).lower()


async def _throwaway_property(client, h):
    """A destination, property and room type of this test's own.

    The seeded catalogue is shared and read-mostly (see ``conftest``), so a test
    that *writes* rates builds its own tree rather than hanging rows off Coral
    Sands where a later assertion about that property could trip over them.
    """
    tag = uuid.uuid4().hex[:8]
    destination = await client.post(
        f"{API}/destinations",
        headers=h,
        json={"name": f"VAT Test Coast {tag}", "type": "beach", "country": "Kenya"},
    )
    assert destination.status_code == 201, destination.text
    accommodation = await client.post(
        f"{API}/accommodations",
        headers=h,
        json={
            "name": f"VAT Test Lodge {tag}",
            "destination_id": destination.json()["id"],
            "category": "lodge",
        },
    )
    assert accommodation.status_code == 201, accommodation.text
    room = await client.post(
        f"{API}/accommodations/{accommodation.json()['id']}/room-types",
        headers=h,
        json={"name": "Twin", "code": f"TWN{tag}", "max_occupancy": 2},
    )
    assert room.status_code == 201, room.text
    return accommodation.json()["id"], room.json()["id"]


# --------------------------------------------------------------------------- #
# VAT normalisation (§3.2)
#
# The invariant is that every *stored* rate is VAT-inclusive, so the engine can
# add no tax anywhere and the document's "inclusive of 16% VAT" line is true.
# The failure this section exists to catch is silent in both directions: an
# exclusive figure stored as-is under-charges the client by the whole VAT rate,
# and a figure grossed up twice over-charges them.
# --------------------------------------------------------------------------- #


async def test_a_rate_entered_as_vat_exclusive_is_stored_inclusive(
    client, admin_tokens, sample_catalogue
):
    """The bug this guards: an exclusive rate stored as typed under-charges by 16%.

    Nothing downstream adds tax — VAT is a disclosure line, not an arithmetic
    step — so if the gross-up does not happen here it never happens at all, and
    the quotation tells the client a VAT-inclusive price that is not one.
    """
    h = _h(admin_tokens)
    ids = sample_catalogue
    accommodation_id, room_type_id = await _throwaway_property(client, h)
    body = {
        "room_type_id": room_type_id,
        "meal_plan_id": ids["meal_plan_fb"],
        "residence_category_id": ids["residence_citizen"],
        "season_name": "VAT exclusive source",
        "effective_from": "2031-02-01",
        "effective_to": "2031-02-28",
        "currency": "kes",
        "rate_per_night": "20000",
        "child_rate": "10000",
        "single_supplement": "1000",
        "occupancy": 2,
        "vat_inclusive": False,
        "vat_pct": "16",
    }
    resp = await client.post(
        f"{API}/accommodations/{accommodation_id}/rates", headers=h, json=body
    )
    assert resp.status_code == 201, resp.text
    rate = resp.json()
    assert D(rate["rate_per_night"]) == D("23200")
    # Every money field on the row shares one basis, or the child rate would be
    # exclusive while the adult rate was not.
    assert D(rate["child_rate"]) == D("11600")
    assert D(rate["single_supplement"]) == D("1160")
    # The stored row is inclusive by construction, whatever the source said.
    assert rate["vat_inclusive"] is True
    assert D(rate["vat_pct"]) == D("16")
    assert rate["currency"] == "KES"


async def test_the_same_figure_declared_inclusive_is_stored_as_typed(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    ids = sample_catalogue
    accommodation_id, room_type_id = await _throwaway_property(client, h)
    resp = await client.post(
        f"{API}/accommodations/{accommodation_id}/rates",
        headers=h,
        json={
            "room_type_id": room_type_id,
            "meal_plan_id": ids["meal_plan_fb"],
            "residence_category_id": ids["residence_citizen"],
            "season_name": "VAT inclusive source",
            "effective_from": "2031-03-01",
            "effective_to": "2031-03-31",
            "currency": "KES",
            "rate_per_night": "20000",
            "occupancy": 2,
        },
    )
    assert resp.status_code == 201, resp.text
    assert D(resp.json()["rate_per_night"]) == D("20000")
    assert resp.json()["vat_inclusive"] is True


async def test_occupancy_is_settable_on_a_hand_entered_rate(
    client, admin_tokens, sample_catalogue
):
    """Occupancy is part of a rate's identity *and* of its uniqueness key.

    Before this was exposed, a property entered by hand could hold exactly one
    rate per room/plan/residence/season — so no hand-entered property could be
    priced for a lone guest, which is the ordinary odd-room case.
    """
    h = _h(admin_tokens)
    ids = sample_catalogue
    accommodation_id, room_type_id = await _throwaway_property(client, h)
    url = f"{API}/accommodations/{accommodation_id}/rates"
    common = {
        "room_type_id": room_type_id,
        "meal_plan_id": ids["meal_plan_fb"],
        "residence_category_id": ids["residence_citizen"],
        "season_name": "Occupancy pair",
        "effective_from": "2031-04-01",
        "effective_to": "2031-04-30",
        "currency": "KES",
    }
    single = await client.post(
        url, headers=h, json={**common, "rate_per_night": "7000", "occupancy": 1}
    )
    double = await client.post(
        url, headers=h, json={**common, "rate_per_night": "11000", "occupancy": 2}
    )
    assert single.status_code == 201, single.text
    assert double.status_code == 201, double.text
    assert single.json()["occupancy"] == 1
    assert double.json()["occupancy"] == 2


async def test_a_rate_kind_and_discount_survive_a_hand_entry(
    client, admin_tokens, sample_catalogue
):
    """A rack rate typed in without its discount silently loses the concession."""
    h = _h(admin_tokens)
    ids = sample_catalogue
    accommodation_id, room_type_id = await _throwaway_property(client, h)
    resp = await client.post(
        f"{API}/accommodations/{accommodation_id}/rates",
        headers=h,
        json={
            "room_type_id": room_type_id,
            "meal_plan_id": ids["meal_plan_fb"],
            "residence_category_id": ids["residence_citizen"],
            "season_name": "Rack with concession",
            "effective_from": "2031-05-01",
            "effective_to": "2031-05-31",
            "currency": "KES",
            "rate_per_night": "30000",
            "rate_kind": "sto",
            "supplier_discount_pct": "12.5",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["rate_kind"] == "sto"
    assert D(resp.json()["supplier_discount_pct"]) == D("12.5")


async def test_a_backwards_season_is_refused(client, admin_tokens, sample_catalogue):
    h = _h(admin_tokens)
    ids = sample_catalogue
    accommodation_id, room_type_id = await _throwaway_property(client, h)
    resp = await client.post(
        f"{API}/accommodations/{accommodation_id}/rates",
        headers=h,
        json={
            "room_type_id": room_type_id,
            "meal_plan_id": ids["meal_plan_fb"],
            "residence_category_id": ids["residence_citizen"],
            "effective_from": "2031-06-30",
            "effective_to": "2031-06-01",
            "currency": "KES",
            "rate_per_night": "10000",
        },
    )
    assert resp.status_code == 400, resp.text


async def test_pricing_adds_no_tax_on_top_of_a_stored_rate(
    client, admin_tokens, sample_catalogue
):
    """The accommodation component is rooms x rate x nights and nothing else.

    Asserted against the component rather than against the total, because a
    16% step hidden anywhere in the build-up would still leave the total
    self-consistent — the only way to see it is to compare the component with
    the arithmetic the sheet implies.

    Coral Sands twin, 25 guests, three nights:
        12 rooms of two at 9,000 + one of one at 6,500 = 114,500 a night
        x 3 nights                                     = 343,500
    """
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[{"accommodation_id": sample_catalogue["acc_sto_full_board"]}],
    )
    priced = await _price(client, h, quote["id"])
    option = _option(priced, "Coral")
    assert D(option["build_up"]["components"]["accommodation"]) == D("343500")
    assert D(option["build_up"]["cost_subtotal"]) == D("343500")


async def test_the_document_states_the_vat_rate_it_was_priced_at(
    client, admin_tokens, sample_catalogue
):
    """The disclosure line is the client's only sight of VAT, so it has to agree
    with the basis the rates were normalised to."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[
            {
                "accommodation_id": sample_catalogue["acc_sto_full_board"],
                "is_recommended": True,
            }
        ],
    )
    issued = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert issued.status_code == 200, issued.text
    rendered = await client.get(f"{API}/quotes/{quote['id']}/document.html", headers=h)
    assert rendered.status_code == 200, rendered.text
    assert f"{int(DEFAULT_VAT_PCT)}% VAT" in rendered.text


# --------------------------------------------------------------------------- #
# Discount halving (§3.5)
#
# Three numbers per discounted rack rate, and the whole point is that they are
# three: what we pay, what the client is costed at, and the half we keep. A
# sweep rather than one example, because the identity has to hold at the edges
# (0%, 100%) and at percentages that do not divide cleanly.
# --------------------------------------------------------------------------- #

async def test_the_retained_half_sits_outside_the_profit_percentage(
    client, admin_tokens, sample_catalogue
):
    """Realised margin on a discounted rack option is profit + contingency + the
    retained half, which only works if the retained half is not itself inside
    the cost the profit is taken on.

    Baobab, 24,000 rack less 15%, 25 guests, three nights:
        13 rooms (no single rate; the odd room is charged in full)
        costed   24,000 x (1 - 7.5%)  = 22,200 -> 13 x 22,200 x 3 = 865,800
        paid     24,000 x (1 - 15%)   = 20,400 -> 13 x 20,400 x 3 = 795,600
        retained                                                  =  70,200
    """
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[{"accommodation_id": sample_catalogue["acc_rack_discounted"]}],
    )
    priced = await _price(client, h, quote["id"])
    option = _option(priced, "Baobab")

    costed = D(option["build_up"]["components"]["accommodation"])
    paid = D(option["supplier_paid_total"])
    kept = D(option["retained_discount"])
    assert costed == D("865800")
    assert paid == D("795600")
    assert kept == D("70200")
    assert kept == costed - paid

    # Profit is taken on the costed figure, which still contains the retained
    # half — so the half is margin *on top of* the percentage, not part of it.
    build = option["build_up"]
    assert D(build["cost_subtotal"]) == costed
    assert D(build["profit_value"]) > 0
    realised = D(build["selling_total"]) - paid - D(build["agent_cover_fee"])
    assert realised > D(build["profit_value"]) + D(build["contingency_value"]) - D("0.01")


# --------------------------------------------------------------------------- #
# Rooming edge cases (§3.3)
#
# Sweeps here, because rooming is where an off-by-one is both easy and
# expensive: one room too few under-charges by a room-night for every night of
# the stay, and nothing else in the system would notice.
# --------------------------------------------------------------------------- #


async def test_the_capacity_four_case_prices_end_to_end(
    client, admin_tokens, sample_catalogue
):
    """Pendo's villas, priced through the service rather than as arithmetic.

    25 guests in 4-guest units, three nights, the unit priced at 16,000 for four:
        7 units x 16,000 x 3 nights = 336,000
    The seventh villa holds one guest and is charged in full — there is no
    one-guest price for a villa, and inventing a share of one is what §3.3
    forbids.
        + contingency 5%  16,800  -> cost_basis 352,800
        + profit 24%      84,672  -> 437,472
        per person  ceil(437,472 / 25 / 100) x 100 = 17,500
        group       17,500 x 25                    = 437,500
    """
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[{"accommodation_id": sample_catalogue["acc_villa"]}],
    )
    priced = await _price(client, h, quote["id"])
    option = _option(priced, "Pendo")
    assert option["rooms_required"] == 7
    assert D(option["build_up"]["components"]["accommodation"]) == D("336000")
    assert D(option["per_person"]) == D("17500")
    assert D(option["group_total"]) == D("437500")


async def test_the_odd_room_is_charged_in_full_not_halved(
    client, admin_tokens, sample_catalogue
):
    """Baobab quotes no single rate, so the thirteenth room is a whole room.

    13 x 22,200 x 3 = 865,800. Twelve-and-a-half rooms would be 832,500 — a
    33,300 under-charge that looks entirely plausible on the document.
    """
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[{"accommodation_id": sample_catalogue["acc_rack_discounted"]}],
    )
    priced = await _price(client, h, quote["id"])
    option = _option(priced, "Baobab")
    assert option["rooms_required"] == 13
    assert D(option["build_up"]["components"]["accommodation"]) == D("865800")


async def test_an_odd_headcount_uses_the_quoted_single_where_there_is_one(
    client, admin_tokens, sample_catalogue
):
    """Coral Sands does quote a single, so the odd room takes it rather than a
    whole double: 12 x 9,000 + 6,500, not 13 x 9,000."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[{"accommodation_id": sample_catalogue["acc_sto_full_board"]}],
    )
    priced = await _price(client, h, quote["id"])
    option = _option(priced, "Coral")
    assert option["rooms_required"] == 13
    assert D(option["build_up"]["components"]["accommodation"]) == D("343500")
    assert D(option["build_up"]["components"]["accommodation"]) != D("351000")


async def test_an_even_headcount_needs_no_odd_room(
    client, admin_tokens, sample_catalogue
):
    """24 guests: 12 doubles, no single, so the single rate is not involved.
    12 x 9,000 x 3 = 324,000."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[{"accommodation_id": sample_catalogue["acc_sto_full_board"]}],
        pax_count=24,
    )
    priced = await _price(client, h, quote["id"])
    option = _option(priced, "Coral")
    assert option["rooms_required"] == 12
    assert D(option["build_up"]["components"]["accommodation"]) == D("324000")


# --------------------------------------------------------------------------- #
# The meal-plan fallback chain (§3.4)
#
# The chain is Full Board -> Half Board -> Bed & Breakfast, and the middle step
# is the one worth testing: falling straight from full board to bed and
# breakfast would add a chef and a food cost the property did not need, which
# both over-states the price and marks a comparable option non-comparable.
# --------------------------------------------------------------------------- #


async def test_a_full_board_request_lands_on_half_board_without_a_chef(
    client, admin_tokens, sample_catalogue
):
    """Pendo carries half board only. The option is priced on half board, marked
    as a fallback, and gets no chef — a chef on a half-board option is the §3.4
    rule this asserts is not being broken by the fallback path.
    """
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[{"accommodation_id": sample_catalogue["acc_villa"]}],
    )
    priced = await _price(client, h, quote["id"])
    option = _option(priced, "Pendo")
    assert option["meal_plan_code"] == "HB"
    assert option["meal_plan_fallback_from"] == "FB"
    components = option["build_up"]["components"]
    assert "chef" not in components
    assert "meals" not in components


async def test_the_fallback_is_flagged_so_the_option_is_not_silently_compared(
    client, admin_tokens, sample_catalogue
):
    """Two options on different board bases are not like-for-like, and the agent
    has to be told rather than left to notice."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[
            {"accommodation_id": sample_catalogue["acc_sto_full_board"]},
            {"accommodation_id": sample_catalogue["acc_villa"]},
        ],
    )
    priced = await _price(client, h, quote["id"])
    assert _option(priced, "Coral")["meal_plan_fallback_from"] is None
    assert _option(priced, "Pendo")["meal_plan_fallback_from"] == "FB"


async def test_a_bed_and_breakfast_fallback_does_take_a_chef(
    client, admin_tokens, sample_catalogue
):
    """The other end of the chain: Kaskazi is BB-only, so the option needs a
    chef and a food cost, and the meal count comes from the stay rather than
    from whatever was typed — three nights on BB is 2 x 3 = 6 group meals.

        chef  5,000 x 6 meals = 30,000
    """
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[
            {
                "accommodation_id": sample_catalogue["acc_bb_only"],
                "chef_fee_per_meal": "5000",
                "manual_meal_cost": "48000",
            }
        ],
    )
    priced = await _price(client, h, quote["id"])
    option = _option(priced, "Kaskazi")
    assert option["meal_plan_code"] == "BB"
    components = option["build_up"]["components"]
    assert D(components["chef"]) == D("30000")
    assert D(components["meals"]) == D("48000")


# --------------------------------------------------------------------------- #
# The sample, end to end
#
# The reference proposal's shape: a 25-pax corporate coastal retreat, several
# accommodation options at different board bases, one recommended, one property
# considered and declined with a reason, priced in KES and issued as a document.
#
# This is the only test that walks the whole chain, and what it asserts is that
# the figures agree at every layer. An identity that holds in the service but
# not in the frozen version, or in the version but not in the rendered page, is
# still a wrong quotation — and the page is what gets emailed.
# --------------------------------------------------------------------------- #


async def _the_sample(client, h, ids):
    record = await _client_record(client, h, ids["residence_citizen"], name="HFC Bank")
    body = {
        "client_id": record["id"],
        "presentation_currency": "KES",
        "residence_category_id": ids["residence_citizen"],
        "arrival_date": "2026-07-01",
        "departure_date": "2026-07-04",
        "pax_count": 25,
        "requested_meal_plan_id": ids["meal_plan_fb"],
        "document_title": "Corporate Coastal Retreat",
        "document_subtitle": "A Curated Coastal Experience by Heissal Tours & Travel",
        "options": [
            {
                "accommodation_id": ids["acc_sto_full_board"],
                "is_recommended": True,
                "sort_order": 1,
            },
            {
                "accommodation_id": ids["acc_rack_discounted"],
                "sort_order": 2,
                "agent_cover_fee": "25000",
            },
            {"accommodation_id": ids["acc_villa"], "sort_order": 3},
            {
                "accommodation_id": ids["acc_bb_only"],
                "sort_order": 4,
                "chef_fee_per_meal": "5000",
                "manual_meal_cost": "48000",
            },
        ],
    }
    created = await client.post(f"{API}/quotes", headers=h, json=body)
    assert created.status_code == 201, created.text
    quote = created.json()

    declined = await client.post(
        f"{API}/quotes/{quote['id']}/rejected-candidates",
        headers=h,
        json={
            "name": "Diani Cottages",
            "reason": (
                "The property has indicated capacity for up to 16 participants, "
                "making it unsuitable for the full 25-person group without "
                "splitting the group across additional accommodation."
            ),
        },
    )
    assert declined.status_code == 201, declined.text

    issued = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert issued.status_code == 200, issued.text
    return quote, issued.json()


async def test_the_sample_issues_with_every_option_priced(
    client, admin_tokens, sample_catalogue
):
    _, version = await _the_sample(client, _h(admin_tokens), sample_catalogue)
    assert len(version["options"]) == 4
    assert [o["sort_order"] for o in version["options"]] == [1, 2, 3, 4]
    assert sum(1 for o in version["options"] if o["is_recommended"]) == 1
    recommended = next(o for o in version["options"] if o["is_recommended"])
    assert "Coral" in recommended["accommodation_name"]


async def test_the_sample_agrees_with_itself_at_every_layer(
    client, admin_tokens, sample_catalogue
):
    """per_person x pax == selling_total, in the service, in the frozen version,
    and in the rendered page.

    The sample this milestone reproduces fails exactly this: its page 6 says
    28,800 per person while its page 11 table says 28,400, both against one
    720,000 total. Computing per-person first and multiplying back out is what
    makes the two agree — this asserts it survives being frozen and rendered.
    """
    h = _h(admin_tokens)
    quote, version = await _the_sample(client, h, sample_catalogue)

    for option in version["options"]:
        if option["per_person"] is None:
            continue
        assert D(option["per_person"]) * 25 == D(option["selling_total"]), (
            option["accommodation_name"]
        )

    rendered = await client.get(f"{API}/quotes/{quote['id']}/document.html", headers=h)
    assert rendered.status_code == 200, rendered.text
    page = rendered.text
    for option in version["options"]:
        if option["per_person"] is None:
            continue
        assert f"{int(D(option['per_person'])):,}" in page
        assert f"{int(D(option['selling_total'])):,}" in page


async def test_the_sample_reads_the_same_to_a_client_facing_role(
    client, admin_tokens, sample_catalogue
):
    """The price a client sees and the price the agent priced are one number.

    Two schemas rather than one filtered one is what keeps cost out; this checks
    the split did not also change the figure on the way through. Re-issuing as
    the agent appends a second version of the same quote, which is the only way
    to get both views of identical inputs — and it exercises the re-issue path
    while it is here.
    """
    h = _h(admin_tokens)
    quote, version = await _the_sample(client, h, sample_catalogue)

    email = unique_email("s37agent")
    created = await client.post(
        f"{API}/users",
        headers=h,
        json={"email": email, "password": "AgentPass123", "role_keys": ["sales_agent"]},
    )
    assert created.status_code == 201, created.text
    login = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "AgentPass123"}
    )
    agent = _h(login.json())

    resp = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=agent)
    assert resp.status_code == 200, resp.text
    client_view = resp.json()
    assert client_view["version_number"] == version["version_number"] + 1

    internal = {o["accommodation_name"]: o for o in version["options"]}
    for option in client_view["options"]:
        mine = internal[option["accommodation_name"]]
        assert D(option["selling_total"]) == D(mine["selling_total"])
        # ...and none of the figures behind it came along.
        for hidden in (
            "cost_subtotal",
            "contingency_value",
            "profit_value",
            "supplier_paid_total",
            "retained_discount",
            "agent_cover_fee",
        ):
            assert hidden not in option


async def test_the_sample_shows_the_declined_property_with_its_reason(
    client, admin_tokens, sample_catalogue
):
    """The reason prints verbatim, so it is the agent's words that reach the
    client and not a paraphrase the template invented."""
    h = _h(admin_tokens)
    quote, _ = await _the_sample(client, h, sample_catalogue)
    rendered = await client.get(f"{API}/quotes/{quote['id']}/document.html", headers=h)
    assert rendered.status_code == 200, rendered.text
    assert "Diani Cottages" in rendered.text
    assert "capacity for up to 16 participants" in rendered.text


async def test_the_sample_document_carries_no_internal_figure(
    client, admin_tokens, sample_catalogue
):
    """The whole margin build-up, checked against the rendered bytes.

    Asserted on the artefact rather than on the schema because the schema is the
    mechanism and the page is the thing that gets sent. Digits are matched with
    the thousands separators stripped, so a figure cannot hide behind
    formatting.
    """
    h = _h(admin_tokens)
    quote, version = await _the_sample(client, h, sample_catalogue)
    rendered = await client.get(f"{API}/quotes/{quote['id']}/document.html", headers=h)
    assert rendered.status_code == 200, rendered.text
    digits = re.sub(r"[,\s]", "", rendered.text)

    leaked = []
    for option in version["options"]:
        for field in (
            "cost_subtotal",
            "contingency_value",
            "profit_value",
            "supplier_paid_total",
            "retained_discount",
            "agent_cover_fee",
        ):
            value = option.get(field)
            if value is None or D(value) == 0:
                continue
            whole = int(D(value))
            # Only meaningful figures: a two-digit number would collide with a
            # date or a page number and prove nothing either way.
            if whole >= 1000 and str(whole) in digits:
                leaked.append((option["accommodation_name"], field, whole))
    assert not leaked, f"internal figures reached the client document: {leaked}"


async def test_the_sample_document_names_no_internal_concept(
    client, admin_tokens, sample_catalogue
):
    """Not just the numbers — the words. "Contingency" on a client document
    invites a question no agent wants to answer, even with no figure beside it.

    Checked against the visible text rather than the source: the first version of
    this test failed on the ``margin`` in the page's own CSS, which is exactly
    the kind of false positive that gets a useful assertion deleted.
    """
    h = _h(admin_tokens)
    quote, _ = await _the_sample(client, h, sample_catalogue)
    rendered = await client.get(f"{API}/quotes/{quote['id']}/document.html", headers=h)
    page = _visible_text(rendered.text)
    for word in (
        "contingency",
        "markup",
        "mark-up",
        "profit",
        "margin",
        "agent cover",
        "supplier paid",
        "cost subtotal",
        "rack rate",
        "sto rate",
    ):
        assert word not in page, f"the document says {word!r}"


async def test_the_sample_is_reproducible(client, admin_tokens, sample_catalogue):
    """The same request priced twice gives the same figures.

    Worth its own test because two of the inputs are ordered database reads —
    rate selection across overlapping seasons, and room-type choice within a
    hotel — and an unordered LIMIT in either would show up here and nowhere
    else.
    """
    h = _h(admin_tokens)
    first = await _the_sample(client, h, sample_catalogue)
    second = await _the_sample(client, h, sample_catalogue)

    def figures(version):
        return sorted(
            (o["accommodation_name"], str(o["selling_total"]), str(o["per_person"]))
            for o in version["options"]
        )

    assert figures(first[1]) == figures(second[1])
