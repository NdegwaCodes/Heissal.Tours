"""A per-person rounding step per currency (client-confirmed 2026-09-04).

One global step of 100 was right for shillings and badly wrong for dollars. Two
figures from the real corpus, priced against the client's own rates:

    Pride Inn Diani   USD 135 per person  ->  USD 200   +48.1%
    Palm Garden       USD 144 per person  ->  USD 200   +38.9%

That is not a rounding convention to a client, it is a different quote — and it
loses a booking without anyone learning why. The step is now per currency: KES
100 as before, **USD 1** as the client chose, and the other foreign currencies
default to 1 rather than waiting to be discovered the same way.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.config import settings
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal

# Three nights at Coral Sands, whose non-resident twin is USD 180 (demo data).
ARRIVAL, DEPARTURE = "2026-07-01", "2026-07-04"
#   180 x 3 nights                = 540.00
#   + contingency 5%              = 567.00
#   + profit 24%                  = 703.08
#   per person 703.08 / 2         = 351.54  -> 352 at a USD 1 step
#   group      352 x 2            = 704
USD_PER_PERSON, USD_GROUP = D("352"), D("704")
#   ...and 400 / 800 under a step of 100, which is the defect.
USD_KES = D("130")


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _quote(client, h, ids, *, currency, residence, cohorts=None, pax=2):
    record = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Rounding Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("rounding"),
            "residence_category_id": ids[residence],
        },
    )
    assert record.status_code == 201, record.text
    body = {
        "client_id": record.json()["id"],
        "presentation_currency": currency,
        "residence_category_id": ids[residence],
        "arrival_date": ARRIVAL,
        "departure_date": DEPARTURE,
        "requested_meal_plan_id": ids["meal_plan_fb"],
        "options": [
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
    }
    if cohorts:
        body["cohorts"] = [
            {
                "residence_category_id": ids[residence_key],
                "traveller_type": kind,
                "headcount": n,
            }
            for residence_key, kind, n in cohorts
        ]
    else:
        body["pax_count"] = pax
    created = await client.post(f"{API}/quotes", headers=h, json=body)
    assert created.status_code == 201, created.text
    quote = created.json()
    priced = await client.post(
        f"{API}/quotes/{quote['id']}/options/price", headers=h
    )
    assert priced.status_code == 200, priced.text
    return quote, priced.json()["options"][0]


async def test_a_dollar_quote_rounds_to_the_next_dollar(
    client, admin_tokens, sample_catalogue
):
    """USD 351.54 per person becomes 352, not 400."""
    h, ids = _h(admin_tokens), sample_catalogue
    _, option = await _quote(
        client, h, ids, currency="USD", residence="residence_non_resident"
    )
    assert D(option["per_person"]) == USD_PER_PERSON
    assert D(option["group_total"]) == USD_GROUP
    # The figure the old global step produced, named so this test says what it
    # is protecting against rather than only what it expects.
    assert D(option["per_person"]) != D("400")


async def test_a_shilling_quote_still_rounds_to_the_next_hundred(
    client, admin_tokens, sample_catalogue
):
    """The change is per currency, not a change of policy for KES.

    Coral Sands twin at 9,000, two residents, three nights:
        27,000 + 5% = 28,350, + 24% = 35,154
        per person 17,577 -> 17,600
    """
    h, ids = _h(admin_tokens), sample_catalogue
    _, option = await _quote(
        client, h, ids, currency="KES", residence="residence_citizen"
    )
    assert D(option["per_person"]) == D("17600")
    assert D(option["group_total"]) == D("35200")


async def test_each_cohort_rounds_in_the_currency_it_is_billed_in(
    client, admin_tokens, sample_catalogue
):
    """The case one global step could never serve (§3.8).

    Residents in shillings and non-residents in dollars on the same quote, so
    the two cohorts need two different steps *within one price*:

        residents      17,577 -> KES 17,600 each, 35,200 total
        non-residents  351.54 -> USD 352 each,       704 total
    """
    h, ids = _h(admin_tokens), sample_catalogue
    _, option = await _quote(
        client,
        h,
        ids,
        currency="KES",
        residence="residence_citizen",
        cohorts=[
            ("residence_citizen", "adult", 2),
            ("residence_non_resident", "adult", 2),
        ],
    )
    by_residence = {row["residence"]: row for row in option["cohorts"]}
    assert set(by_residence) == {"citizen", "non_resident"}

    citizen = by_residence["citizen"]
    assert citizen["currency"] == "KES"
    assert D(citizen["per_person"]) == D("17600")

    visitor = by_residence["non_resident"]
    assert visitor["currency"] == "USD"
    assert D(visitor["per_person"]) == USD_PER_PERSON
    assert D(visitor["total"]) == USD_GROUP

    # Each cohort's own figures multiply out exactly, which is the invariant
    # rounding in two currencies has to preserve.
    assert D(citizen["total"]) == D(citizen["per_person"]) * D(2)
    assert D(visitor["total"]) == D(visitor["per_person"]) * D(2)


async def test_the_step_is_configurable_and_falls_back(
    client, admin_tokens, sample_catalogue, restore_pricing_config
):
    """A currency the map does not name uses ``per_person_rounding``.

    Which is also the proof that the map is what is doing the work: empty it,
    and the dollar quote goes back to rounding up to 400.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    config = (await client.get(f"{API}/pricing-config", headers=h)).json()
    assert config["per_person_rounding_by_currency"] == {
        "USD": "1",
        "EUR": "1",
        "GBP": "1",
    }, config["per_person_rounding_by_currency"]

    emptied = await client.patch(
        f"{API}/pricing-config",
        headers=h,
        json={"per_person_rounding_by_currency": {}},
    )
    assert emptied.status_code == 200, emptied.text
    _, option = await _quote(
        client, h, ids, currency="USD", residence="residence_non_resident"
    )
    assert D(option["per_person"]) == D("400"), "the fallback is not being used"

    restored = await client.patch(
        f"{API}/pricing-config",
        headers=h,
        json={"per_person_rounding_by_currency": {"USD": "5"}},
    )
    assert restored.status_code == 200, restored.text
    _, option = await _quote(
        client, h, ids, currency="USD", residence="residence_non_resident"
    )
    # 351.54 at a five-dollar step.
    assert D(option["per_person"]) == D("355")
