"""Stage 3.8 — the group vector: cohorts on a quote, and reading them back.

``build_group`` is the only place the question "who is travelling on this quote?"
is answered. Before it, three answers were possible — ``pax_count``, the length
of ``travellers``, and the quote's single residence category — and they could
disagree. A headcount that depends on which caller asked is how a group gets
rooms for twenty-five people and park fees for one.

What matters here is the *precedence* and that the vector can express the
client's confirmed rule: non-residents charged in USD and residents in KES on
the same quote, with a separate per-person figure for each.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import AppError
from app.db.session import AsyncSessionLocal
from app.modules.quotes.group import build_group
from app.modules.quotes.models import Quote
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _client_record(client, h, residence_category_id):
    resp = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Cohort Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("cohort"),
            "residence_category_id": residence_category_id,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create(client, h, ids, **over):
    record = await _client_record(client, h, ids["residence_citizen"])
    body = {
        "client_id": record["id"],
        "presentation_currency": "KES",
        "residence_category_id": ids["residence_citizen"],
        "arrival_date": "2026-07-01",
        "departure_date": "2026-07-04",
        "requested_meal_plan_id": ids["meal_plan_fb"],
    }
    body.update(over)
    return await client.post(f"{API}/quotes", headers=h, json=body)


async def _group(quote_id):
    """The group vector as the pricing engine will see it."""
    async with AsyncSessionLocal() as db:
        quote = (
            await db.execute(select(Quote).where(Quote.id == uuid.UUID(quote_id)))
        ).scalar_one()
        return await build_group(db, quote)


# --------------------------------------------------------------------------- #
# Precedence
# --------------------------------------------------------------------------- #


async def test_cohorts_describe_the_group(client, admin_tokens, sample_catalogue):
    """The shape the client asked for: one quote, two residencies, two
    currencies, children priced apart from adults."""
    h, ids = _h(admin_tokens), sample_catalogue
    resp = await _create(
        client,
        h,
        ids,
        cohorts=[
            {
                "residence_category_id": ids["residence_citizen"],
                "traveller_type": "adult",
                "headcount": 17,
            },
            {
                "residence_category_id": ids["residence_citizen"],
                "traveller_type": "child",
                "headcount": 2,
            },
            {
                "residence_category_id": ids["residence_non_resident"],
                "traveller_type": "adult",
                "headcount": 6,
            },
        ],
    )
    assert resp.status_code == 201, resp.text
    assert len(resp.json()["cohorts"]) == 3

    group = await _group(resp.json()["id"])
    assert group.pax == 25
    assert set(group.residences) == {"citizen", "non_resident"}
    assert group.headcount("citizen") == 19
    assert group.headcount("non_resident") == 6
    # The rule the whole vector exists for.
    assert group.currency_for("citizen") == "KES"
    assert group.currency_for("non_resident") == "USD"
    assert not group.is_uniform


async def test_pax_count_still_describes_a_uniform_group(
    client, admin_tokens, sample_catalogue
):
    """Most groups are uniform in both respects, and a headcount is how an agent
    enters one. It must keep working untouched."""
    h, ids = _h(admin_tokens), sample_catalogue
    resp = await _create(client, h, ids, pax_count=25)
    assert resp.status_code == 201, resp.text
    assert resp.json()["cohorts"] == []

    group = await _group(resp.json()["id"])
    assert group.pax == 25
    assert group.is_uniform
    assert group.residences == ("citizen",)
    assert group.cohorts[0].traveller_type == "adult"


async def test_cohorts_take_precedence_over_pax_count(
    client, admin_tokens, sample_catalogue
):
    """Both can be present on the same quote. The vector is the authority,
    because it is the only one that carries the residency split."""
    h, ids = _h(admin_tokens), sample_catalogue
    resp = await _create(
        client,
        h,
        ids,
        pax_count=8,
        cohorts=[
            {
                "residence_category_id": ids["residence_citizen"],
                "traveller_type": "adult",
                "headcount": 5,
            },
            {
                "residence_category_id": ids["residence_non_resident"],
                "traveller_type": "adult",
                "headcount": 3,
            },
        ],
    )
    assert resp.status_code == 201, resp.text
    group = await _group(resp.json()["id"])
    assert group.pax == 8
    assert len(group.cohorts) == 2


async def test_travellers_are_the_last_resort(client, admin_tokens, sample_catalogue):
    """A small quote with named guests and no headcount. Their recorded types
    become the cohorts; the quote's own residency is the only one available."""
    h, ids = _h(admin_tokens), sample_catalogue
    resp = await _create(
        client,
        h,
        ids,
        travellers=[
            {"traveller_type": "adult"},
            {"traveller_type": "adult"},
            {"traveller_type": "child", "age": 9},
        ],
    )
    assert resp.status_code == 201, resp.text
    group = await _group(resp.json()["id"])
    assert group.pax == 3
    assert {c.traveller_type: c.count for c in group.cohorts} == {
        "adult": 2,
        "child": 1,
    }
    assert group.residences == ("citizen",)


# --------------------------------------------------------------------------- #
# What is refused
# --------------------------------------------------------------------------- #


async def test_a_pax_count_that_contradicts_the_cohorts_is_refused(
    client, admin_tokens, sample_catalogue
):
    """The cohorts would win, so this could be resolved silently. It is not:
    two headcounts that disagree is a data-entry error, and letting it through
    means someone later reads the wrong one with no way to tell."""
    h, ids = _h(admin_tokens), sample_catalogue
    resp = await _create(
        client,
        h,
        ids,
        pax_count=25,
        cohorts=[
            {
                "residence_category_id": ids["residence_citizen"],
                "traveller_type": "adult",
                "headcount": 12,
            }
        ],
    )
    assert resp.status_code == 400, resp.text
    body = resp.text
    assert "25" in body and "12" in body


async def test_a_cohort_naming_an_unknown_category_is_refused(
    client, admin_tokens, sample_catalogue
):
    h, ids = _h(admin_tokens), sample_catalogue
    resp = await _create(
        client,
        h,
        ids,
        cohorts=[
            {
                "residence_category_id": str(uuid.uuid4()),
                "traveller_type": "adult",
                "headcount": 4,
            }
        ],
    )
    assert resp.status_code == 404, resp.text


async def test_the_same_residency_and_type_cannot_appear_twice(
    client, admin_tokens, sample_catalogue
):
    """Two rows for resident adults is not a group of two kinds of resident
    adult; it is one cohort entered twice, and the total would double-count."""
    h, ids = _h(admin_tokens), sample_catalogue
    row = {
        "residence_category_id": ids["residence_citizen"],
        "traveller_type": "adult",
        "headcount": 4,
    }
    resp = await _create(client, h, ids, cohorts=[row, dict(row, headcount=6)])
    assert resp.status_code == 400, resp.text


@pytest.mark.parametrize("headcount", [0, -3])
async def test_a_cohort_with_nobody_in_it_is_refused(
    client, admin_tokens, sample_catalogue, headcount
):
    h, ids = _h(admin_tokens), sample_catalogue
    resp = await _create(
        client,
        h,
        ids,
        cohorts=[
            {
                "residence_category_id": ids["residence_citizen"],
                "traveller_type": "adult",
                "headcount": headcount,
            }
        ],
    )
    assert resp.status_code == 422, resp.text


async def test_an_unknown_traveller_type_is_refused(
    client, admin_tokens, sample_catalogue
):
    h, ids = _h(admin_tokens), sample_catalogue
    resp = await _create(
        client,
        h,
        ids,
        cohorts=[
            {
                "residence_category_id": ids["residence_citizen"],
                "traveller_type": "teenager",
                "headcount": 4,
            }
        ],
    )
    assert resp.status_code == 422, resp.text


async def test_a_quote_with_nobody_on_it_cannot_be_grouped(
    client, admin_tokens, sample_catalogue
):
    """No cohorts, no pax_count, no travellers. Rather than defaulting to one
    traveller — which would price a real quote for the wrong group — this says
    what is missing."""
    h, ids = _h(admin_tokens), sample_catalogue
    resp = await _create(client, h, ids)
    assert resp.status_code == 201, resp.text
    with pytest.raises(AppError, match="nobody travelling"):
        await _group(resp.json()["id"])


# --------------------------------------------------------------------------- #
# Rooming, which is what the vector changes first
# --------------------------------------------------------------------------- #


async def test_rooming_partitions_by_residency_not_by_headcount(
    client, admin_tokens, sample_catalogue
):
    """Three residents and three non-residents need **four** twins, not three.

    No room can hold one of each and still have a defined rate — the two
    residencies are priced off different sheets in different currencies — so the
    partition costs an extra room. That is the price of a mixed group being
    quotable at all, and it is why rooming cannot be ``ceil(pax / capacity)``.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    resp = await _create(
        client,
        h,
        ids,
        cohorts=[
            {
                "residence_category_id": ids["residence_citizen"],
                "traveller_type": "adult",
                "headcount": 3,
            },
            {
                "residence_category_id": ids["residence_non_resident"],
                "traveller_type": "adult",
                "headcount": 3,
            },
        ],
    )
    assert resp.status_code == 201, resp.text
    group = await _group(resp.json()["id"])

    assert group.pax == 6
    assert group.total_rooms(2) == 4
    assert group.rooming(2) == {"citizen": [2, 1], "non_resident": [2, 1]}
    # The naive figure, for contrast: six people in twins is three rooms.
    assert -(-group.pax // 2) == 3


async def test_the_cohorts_come_back_in_one_deterministic_order(
    client, admin_tokens, sample_catalogue
):
    """Whatever order they were typed in, the vector reads the same way.

    Not decoration: this order is the order of the per-traveller rows on a
    client proposal (§3.8), and it is frozen into the version — so an unstable
    one means the same quote lists residents first today and visitors first
    tomorrow. It *was* unstable, and ordering by the primary key does not fix
    it: a UUIDv7 carries a millisecond and ten random bytes, so two cohorts
    inserted in the same millisecond sort arbitrarily. A §7.1 test run found
    it.

    The order is the residency's own sort_order, then adult, child, infant.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    # Typed in deliberately awkward order: visitors first, children before
    # adults within each residency.
    typed = [
        ("residence_non_resident", "child", 1),
        ("residence_citizen", "child", 2),
        ("residence_non_resident", "adult", 2),
        ("residence_citizen", "adult", 3),
    ]
    first = await _priced_cohorts(client, h, ids, typed)
    # And again, in the reverse order, on a second quote.
    second = await _priced_cohorts(client, h, ids, list(reversed(typed)))

    reads = [(row["residence"], row["traveller_type"]) for row in first]
    assert reads == [
        ("citizen", "adult"),
        ("citizen", "child"),
        ("non_resident", "adult"),
        ("non_resident", "child"),
    ], reads
    assert [
        (row["residence"], row["traveller_type"]) for row in second
    ] == reads


async def _priced_cohorts(client, h, ids, cohorts):
    """Price a quote with these cohorts and return its cohort rows."""
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Order Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("cohortorder"),
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
            "arrival_date": "2026-07-01",
            "departure_date": "2026-07-04",
            "requested_meal_plan_id": ids["meal_plan_fb"],
            "cohorts": [
                {
                    "residence_category_id": ids[residence],
                    "traveller_type": kind,
                    "headcount": n,
                }
                for residence, kind, n in cohorts
            ],
            "options": [
                {
                    "accommodation_id": ids["acc_sto_full_board"],
                    "is_recommended": True,
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    priced = await client.post(
        f"{API}/quotes/{created.json()['id']}/options/price", headers=h
    )
    assert priced.status_code == 200, priced.text
    return priced.json()["options"][0]["cohorts"]
