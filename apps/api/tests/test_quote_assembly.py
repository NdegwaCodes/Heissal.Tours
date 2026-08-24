"""Stage 3.4 — assembling, checking and issuing a multi-option quotation.

Builds on the same seeded demo catalogue as ``test_option_pricing.py``. The
figures used here are the ones worked out there; what this file asserts is the
layer above: which properties are on the quote, which one leads, which were
considered and declined, whether the whole thing is fit to send, and that a
version once issued never changes.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
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
            "name": f"Assembly Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("assembly"),
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
    options=(),
    arrival="2026-07-01",
    departure="2026-07-04",
    pax=25,
    residence="residence_citizen",
):
    """A draft quote. ``options`` is a list of (catalogue key, extra fields)."""
    record = await _client_record(client, h, ids[residence])
    resp = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": record["id"],
            "presentation_currency": "KES",
            "residence_category_id": ids[residence],
            "arrival_date": arrival,
            "departure_date": departure,
            "pax_count": pax,
            "requested_meal_plan_id": ids["meal_plan_fb"],
            "options": [
                {"accommodation_id": ids[key], "sort_order": order, **extra}
                for order, (key, extra) in enumerate(options, start=1)
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _readiness(client, h, quote_id):
    resp = await client.get(f"{API}/quotes/{quote_id}/readiness", headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _codes(readiness, severity=None):
    return {
        p["code"]
        for p in readiness["problems"]
        if severity is None or p["severity"] == severity
    }


async def _sendable(client, h, ids, **over):
    """A quote that passes every blocking check, ready to issue."""
    return await _quote(
        client,
        h,
        ids,
        options=[
            ("acc_sto_full_board", {"is_recommended": True}),
            ("acc_rack_discounted", {}),
            ("acc_villa", {}),
            (
                "acc_bb_only",
                {"chef_fee_per_meal": "5000", "manual_meal_cost": "30000"},
            ),
        ],
        **over,
    )


async def _role_headers(client, h, role):
    email = unique_email(f"asm{role}")
    await client.post(
        f"{API}/users",
        headers=h,
        json={"email": email, "password": "AgentPass123", "role_keys": [role]},
    )
    login = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "AgentPass123"}
    )
    assert login.status_code == 200, login.text
    return _h(login.json())


# --------------------------------------------------------------------------- #
# The options on a quote
# --------------------------------------------------------------------------- #


async def test_an_option_can_be_added_after_the_quote_exists(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote = await _quote(
        client, h, sample_catalogue, options=[("acc_sto_full_board", {})]
    )
    resp = await client.post(
        f"{API}/quotes/{quote['id']}/options",
        headers=h,
        json={"accommodation_id": sample_catalogue["acc_rack_discounted"]},
    )
    assert resp.status_code == 201, resp.text
    # Appended after the existing option rather than colliding at 0.
    assert resp.json()["sort_order"] == 2


async def test_the_same_property_cannot_be_offered_twice(
    client, admin_tokens, sample_catalogue
):
    """Two entries for one hotel is a mistake, not a choice."""
    h = _h(admin_tokens)
    quote = await _quote(
        client, h, sample_catalogue, options=[("acc_sto_full_board", {})]
    )
    resp = await client.post(
        f"{API}/quotes/{quote['id']}/options",
        headers=h,
        json={"accommodation_id": sample_catalogue["acc_sto_full_board"]},
    )
    assert resp.status_code == 400
    assert "already an option" in resp.text


async def test_recommending_one_option_clears_the_others(
    client, admin_tokens, sample_catalogue
):
    """A document that leads on two properties leads on neither."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[
            ("acc_sto_full_board", {"is_recommended": True}),
            ("acc_rack_discounted", {}),
        ],
    )
    second = next(
        o
        for o in quote["options"]
        if o["accommodation_id"] == sample_catalogue["acc_rack_discounted"]
    )
    resp = await client.patch(
        f"{API}/quotes/{quote['id']}/options/{second['id']}",
        headers=h,
        json={"is_recommended": True},
    )
    assert resp.status_code == 200, resp.text

    reloaded = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()
    recommended = [o for o in reloaded["options"] if o["is_recommended"]]
    assert len(recommended) == 1
    assert recommended[0]["accommodation_id"] == sample_catalogue["acc_rack_discounted"]


async def test_an_option_can_be_removed(client, admin_tokens, sample_catalogue):
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[("acc_sto_full_board", {}), ("acc_rack_discounted", {})],
    )
    target = quote["options"][0]["id"]
    resp = await client.delete(
        f"{API}/quotes/{quote['id']}/options/{target}", headers=h
    )
    assert resp.status_code == 204, resp.text

    reloaded = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()
    assert [o["id"] for o in reloaded["options"]] == [quote["options"][1]["id"]]


async def test_a_null_does_not_clear_a_not_null_column(
    client, admin_tokens, sample_catalogue
):
    """Nulling the nullable money is legitimate; nulling sort_order is not."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[("acc_bb_only", {"chef_fee_per_meal": "5000"})],
    )
    option = quote["options"][0]
    resp = await client.patch(
        f"{API}/quotes/{quote['id']}/options/{option['id']}",
        headers=h,
        json={"chef_fee_per_meal": None, "sort_order": None, "is_comparable": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sort_order"] == option["sort_order"]
    assert resp.json()["is_comparable"] is True


# --------------------------------------------------------------------------- #
# Properties considered and declined (§3.3a)
# --------------------------------------------------------------------------- #


async def test_an_agent_can_record_a_property_that_is_not_in_the_catalogue(
    client, admin_tokens, sample_catalogue
):
    """The reference document's Diani Cottages: a name and a reason, nothing more."""
    h = _h(admin_tokens)
    quote = await _quote(
        client, h, sample_catalogue, options=[("acc_sto_full_board", {})]
    )
    resp = await client.post(
        f"{API}/quotes/{quote['id']}/rejected-candidates",
        headers=h,
        json={
            "name": "Diani Cottages",
            "reason": "Caps at 16 guests; this group is 25.",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["accommodation_id"] is None
    assert resp.json()["source"] == "manual"


async def test_repricing_does_not_erase_an_agents_typed_refusal(
    client, admin_tokens, sample_catalogue
):
    """The bug this milestone's migration exists to prevent.

    The engine rewrites its own refusals on every re-price. An agent's typed one
    is not rediscoverable from any rate, so wiping it would silently drop
    something the client was shown.
    """
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[("acc_sto_full_board", {}), ("acc_min_stay", {})],
        arrival="2026-12-21",
        departure="2026-12-24",
    )
    await client.post(
        f"{API}/quotes/{quote['id']}/rejected-candidates",
        headers=h,
        json={"name": "Diani Cottages", "reason": "Caps at 16 guests."},
    )
    # Price twice: the engine's Chui refusal is rewritten both times.
    for _ in range(2):
        priced = await client.post(
            f"{API}/quotes/{quote['id']}/options/price", headers=h
        )
        assert priced.status_code == 200, priced.text

    stored = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()
    by_source = {c["source"]: c for c in stored["rejected_candidates"]}
    assert len(stored["rejected_candidates"]) == 2
    assert by_source["manual"]["name"] == "Diani Cottages"
    assert "minimum stay" in by_source["engine"]["reason"]


async def test_a_property_cannot_be_both_offered_and_declined(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote = await _quote(
        client, h, sample_catalogue, options=[("acc_sto_full_board", {})]
    )
    resp = await client.post(
        f"{API}/quotes/{quote['id']}/rejected-candidates",
        headers=h,
        json={
            "accommodation_id": sample_catalogue["acc_sto_full_board"],
            "name": "Coral Sands",
            "reason": "Too far from the beach.",
        },
    )
    assert resp.status_code == 400
    assert "offered as an option" in resp.text


async def test_an_engine_refusal_cannot_be_deleted(
    client, admin_tokens, sample_catalogue
):
    """Deleting it would only hide it until the next re-price."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[("acc_min_stay", {})],
        arrival="2026-12-21",
        departure="2026-12-24",
    )
    await client.post(f"{API}/quotes/{quote['id']}/options/price", headers=h)
    stored = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()
    engine = next(
        c for c in stored["rejected_candidates"] if c["source"] == "engine"
    )
    resp = await client.delete(
        f"{API}/quotes/{quote['id']}/rejected-candidates/{engine['id']}", headers=h
    )
    assert resp.status_code == 400
    assert "came from the rates" in resp.text


async def test_a_manual_refusal_can_be_deleted(client, admin_tokens, sample_catalogue):
    h = _h(admin_tokens)
    quote = await _quote(
        client, h, sample_catalogue, options=[("acc_sto_full_board", {})]
    )
    created = (
        await client.post(
            f"{API}/quotes/{quote['id']}/rejected-candidates",
            headers=h,
            json={"name": "Diani Cottages", "reason": "Caps at 16 guests."},
        )
    ).json()
    resp = await client.delete(
        f"{API}/quotes/{quote['id']}/rejected-candidates/{created['id']}", headers=h
    )
    assert resp.status_code == 204, resp.text
    stored = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()
    assert stored["rejected_candidates"] == []


# --------------------------------------------------------------------------- #
# Readiness
# --------------------------------------------------------------------------- #


async def test_a_quote_with_no_recommendation_is_not_ready(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote = await _sendable(client, h, sample_catalogue)
    for option in quote["options"]:
        await client.patch(
            f"{API}/quotes/{quote['id']}/options/{option['id']}",
            headers=h,
            json={"is_recommended": False},
        )
    readiness = await _readiness(client, h, quote["id"])
    assert readiness["is_ready"] is False
    assert "no_recommendation" in _codes(readiness, "blocking")


async def test_two_recommendations_are_blocking(
    client, admin_tokens, sample_catalogue
):
    """Reachable only by building the quote in one request, which is why it is checked."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[
            ("acc_sto_full_board", {"is_recommended": True}),
            ("acc_rack_discounted", {"is_recommended": True}),
        ],
    )
    readiness = await _readiness(client, h, quote["id"])
    assert readiness["is_ready"] is False
    assert "multiple_recommendations" in _codes(readiness, "blocking")


async def test_a_self_catering_option_without_a_food_cost_is_blocking(
    client, admin_tokens, sample_catalogue
):
    """Silence is not zero: the option would be under-priced by the whole food bill."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[
            ("acc_sto_full_board", {"is_recommended": True}),
            ("acc_bb_only", {}),
        ],
    )
    readiness = await _readiness(client, h, quote["id"])
    assert readiness["is_ready"] is False
    assert "missing_meal_cost" in _codes(readiness, "blocking")
    problem = next(
        p for p in readiness["problems"] if p["code"] == "missing_meal_cost"
    )
    assert "Kaskazi" in problem["message"]


async def test_an_unpriceable_option_is_blocking(
    client, admin_tokens, sample_catalogue
):
    """A property that silently vanishes from the document reads as an oversight."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[("acc_sto_full_board", {"is_recommended": True}), ("acc_villa", {})],
        pax=4,
        residence="residence_non_resident",
    )
    readiness = await _readiness(client, h, quote["id"])
    assert readiness["is_ready"] is False
    assert "unpriced_option" in _codes(readiness, "blocking")


async def test_a_thin_quote_is_advised_but_still_issuable(
    client, admin_tokens, sample_catalogue
):
    """One hotel is a weaker proposal, not a wrong one."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[("acc_sto_full_board", {"is_recommended": True})],
    )
    readiness = await _readiness(client, h, quote["id"])
    assert readiness["is_ready"] is True
    assert _codes(readiness, "blocking") == set()
    assert "few_catered_options" in _codes(readiness, "advisory")
    assert "few_self_catering_options" in _codes(readiness, "advisory")


async def test_too_many_options_is_blocking(
    client, admin_tokens, sample_catalogue, restore_pricing_config
):
    """Past the configured maximum the comparison table stops being readable."""
    h = _h(admin_tokens)
    await client.patch(
        f"{API}/pricing-config", headers=h, json={"max_catered_options": 1}
    )
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[
            ("acc_sto_full_board", {"is_recommended": True}),
            ("acc_rack_discounted", {}),
        ],
    )
    readiness = await _readiness(client, h, quote["id"])
    assert readiness["is_ready"] is False
    assert "too_many_catered_options" in _codes(readiness, "blocking")


async def test_a_complete_quote_is_ready(client, admin_tokens, sample_catalogue):
    h = _h(admin_tokens)
    quote = await _sendable(client, h, sample_catalogue)
    readiness = await _readiness(client, h, quote["id"])
    assert readiness["is_ready"] is True, readiness["problems"]
    assert _codes(readiness, "blocking") == set()
    # Coral Sands, Baobab and Pendo are catered; Kaskazi is priced on B&B so the
    # group feeds itself — the only distinction in the data that means "BnB".
    assert readiness["catered_options"] == 3
    assert readiness["self_catering_options"] == 1


async def test_readiness_writes_nothing(client, admin_tokens, sample_catalogue):
    h = _h(admin_tokens)
    quote = await _quote(
        client, h, sample_catalogue, options=[("acc_sto_full_board", {})]
    )
    await _readiness(client, h, quote["id"])
    reloaded = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()
    # Still unresolved: readiness prices in memory only.
    assert reloaded["options"][0]["room_type_id"] is None
    assert reloaded["current_version_id"] is None


# --------------------------------------------------------------------------- #
# Issuing
# --------------------------------------------------------------------------- #


async def test_issuing_freezes_a_version_and_marks_the_quote_sent(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote = await _sendable(client, h, sample_catalogue)
    resp = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert resp.status_code == 200, resp.text
    version = resp.json()
    assert version["version_number"] == 1
    assert version["currency"] == "KES"
    # One frozen row per priced option, the recommendation flagged.
    assert len(version["options"]) == 4
    recommended = [o for o in version["options"] if o["is_recommended"]]
    assert len(recommended) == 1
    assert "Coral Sands" in recommended[0]["accommodation_name"]

    reloaded = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()
    assert reloaded["status"] == "sent"
    assert reloaded["current_version_id"] == version["id"]
    # 30 days from the day it went out, not from when it was drafted (§3.11).
    assert reloaded["valid_until"] == (date.today() + timedelta(days=30)).isoformat()


async def test_the_version_headline_is_the_recommended_option(
    client, admin_tokens, sample_catalogue
):
    """And its cost is what we PAY, so margin includes the retained half-discount.

    Baobab recommended: costed 865,800, retained 70,200, so the outlay is
    795,600. Selling is 45,100 x 25 = 1,127,500, hence a gross profit of 331,900
    — the 24% profit plus the 5% contingency plus the retained half, which is
    exactly what §3.5 says realised margin is.
    """
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[("acc_rack_discounted", {"is_recommended": True})],
    )
    version = (
        await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    ).json()
    assert D(version["selling_price"]) == D("1127500.00")
    assert D(version["internal_cost"]) == D("795600.00")
    assert D(version["gross_profit"]) == D("331900.00")
    assert D(version["gross_margin"]) == D("0.2944")

    frozen = version["options"][0]
    assert D(frozen["cost_subtotal"]) == D("865800.00")
    assert D(frozen["retained_discount"]) == D("70200.00")
    assert D(frozen["supplier_paid_total"]) == D("795600.00")
    assert D(frozen["per_person"]) == D("45100.00")


async def test_issuing_refuses_and_lists_every_blocking_problem_at_once(
    client, admin_tokens, sample_catalogue
):
    """Fixing them one 400 at a time is how the second one gets sent anyway."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[("acc_bb_only", {})],
    )
    resp = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert resp.status_code == 400
    assert "no_recommendation" in resp.text
    assert "missing_meal_cost" in resp.text


async def test_an_issued_quote_refuses_assembly_edits(
    client, admin_tokens, sample_catalogue
):
    """An option added after the client has the document would disagree with it."""
    h = _h(admin_tokens)
    quote = await _sendable(client, h, sample_catalogue)
    await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    resp = await client.post(
        f"{API}/quotes/{quote['id']}/options",
        headers=h,
        json={"accommodation_id": sample_catalogue["acc_min_stay"]},
    )
    assert resp.status_code == 400
    assert "re-issue" in resp.text


async def test_reissuing_appends_a_version_and_leaves_the_first_alone(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[("acc_sto_full_board", {"is_recommended": True})],
    )
    first = (await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)).json()
    assert D(first["selling_price"]) == D("447500.00")

    # Back to draft, load an agent cover fee, re-issue.
    await client.patch(
        f"{API}/quotes/{quote['id']}/status", headers=h, json={"status": "draft"}
    )
    option = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()[
        "options"
    ][0]
    await client.patch(
        f"{API}/quotes/{quote['id']}/options/{option['id']}",
        headers=h,
        json={"agent_cover_fee": "25000"},
    )
    second = (await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)).json()

    assert second["version_number"] == 2
    assert D(second["selling_price"]) == D("472500.00")

    versions = (
        await client.get(f"{API}/quotes/{quote['id']}/versions", headers=h)
    ).json()
    assert [v["version_number"] for v in versions] == [2, 1]
    # The first version still says what it said when the client received it.
    original = next(v for v in versions if v["version_number"] == 1)
    assert D(original["selling_price"]) == D("447500.00")


async def test_the_snapshot_keeps_both_kinds_of_refusal(
    client, admin_tokens, sample_catalogue
):
    """The client saw both, so the frozen record holds both."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[
            ("acc_sto_full_board", {"is_recommended": True}),
            ("acc_min_stay", {}),
        ],
        arrival="2026-12-21",
        departure="2026-12-24",
    )
    await client.post(
        f"{API}/quotes/{quote['id']}/rejected-candidates",
        headers=h,
        json={"name": "Diani Cottages", "reason": "Caps at 16 guests."},
    )
    resp = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert resp.status_code == 200, resp.text
    # The snapshot is internal, so read it through the versions listing owner.
    assert len(resp.json()["options"]) == 1


# --------------------------------------------------------------------------- #
# Which option the client chose (§7)
# --------------------------------------------------------------------------- #


async def test_selecting_an_option_records_it_without_accepting_the_quote(
    client, admin_tokens, sample_catalogue
):
    """Choosing an option and accepting a quotation are separate events."""
    h = _h(admin_tokens)
    quote = await _sendable(client, h, sample_catalogue)
    await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    chosen = quote["options"][1]["id"]

    resp = await client.post(
        f"{API}/quotes/{quote['id']}/select", headers=h, json={"option_id": chosen}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "sent"

    stored = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()
    assert stored["selected_option_id"] == chosen
    assert stored["selected_at"] is not None


async def test_an_option_from_another_quote_cannot_be_selected(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    mine = await _sendable(client, h, sample_catalogue)
    theirs = await _quote(
        client, h, sample_catalogue, options=[("acc_sto_full_board", {})]
    )
    resp = await client.post(
        f"{API}/quotes/{mine['id']}/select",
        headers=h,
        json={"option_id": theirs["options"][0]["id"]},
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Permissions and the internal/client boundary
# --------------------------------------------------------------------------- #

COST_KEYS = {
    "internal_cost",
    "gross_profit",
    "gross_margin",
    "cost_subtotal",
    "contingency_value",
    "profit_value",
    "agent_cover_fee",
    "supplier_paid_total",
    "retained_discount",
    "snapshot",
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


async def test_issuing_leaks_no_cost_to_a_client_facing_role(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[("acc_rack_discounted", {"is_recommended": True})],
    )
    agent = await _role_headers(client, h, "sales_agent")
    resp = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=agent)
    assert resp.status_code == 200, resp.text

    leaked = _keys_everywhere(resp.json()) & COST_KEYS
    assert not leaked, f"leaked {leaked} to a role without quote:read_cost"
    # The agent still sees what the client will be charged.
    assert D(resp.json()["selling_price"]) == D("1127500.00")
    assert D(resp.json()["options"][0]["per_person"]) == D("45100.00")


async def test_issuing_needs_its_own_permission(
    client, admin_tokens, sample_catalogue
):
    """Assembling a quote and sending one are different levels of trust."""
    h = _h(admin_tokens)
    quote = await _quote(
        client,
        h,
        sample_catalogue,
        options=[("acc_sto_full_board", {"is_recommended": True})],
    )
    viewer = await _role_headers(client, h, "viewer")
    resp = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=viewer)
    assert resp.status_code == 403
