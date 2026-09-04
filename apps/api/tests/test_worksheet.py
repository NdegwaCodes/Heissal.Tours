"""Stage 3.12 — the internal costing worksheet.

The proposal says what a trip costs; this says why. So the tests that matter
here are the ones about **reconciliation**: that each group of lines adds up to
the subtotal printed beside it, that the worksheet's totals are the same
figures the client's document shows, that every line names the row it came
from, and that none of it reaches a client-facing role.

A worksheet whose lines do not add up to its own subtotal is worse than no
worksheet, because it will be believed.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.modules.documents.service import QuotationDocumentService
from app.modules.documents.worksheet import Worksheet
from tests.conftest import unique_email
from tests.test_document_packages import (
    _h,
    _issued,
    _package,
    _rail_journey,
)

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal


def _amount(formatted: str) -> Decimal:
    """``"KES 45,000"`` -> ``Decimal("45000")``."""
    digits = re.sub(r"[^0-9.\-]", "", formatted or "")
    return D(digits) if digits else D(0)


async def _built(quote_id: str, *, version: int | None = None) -> Worksheet:
    """The worksheet as an object, so reconciliation can be asserted on it."""
    async with AsyncSessionLocal() as db:
        return await QuotationDocumentService(db).worksheet(
            uuid.UUID(quote_id), version_number=version
        )


def _figure(option, label: str) -> Decimal:
    return _amount(next(f.value for f in option.build_up + option.margin
                        if f.label == label))


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #


async def test_every_group_of_lines_adds_up_to_its_subtotal(
    client, admin_tokens, sample_catalogue, upcountry_lodge
):
    """The whole point of the sheet.

    A two-leg package with a rail journey and an optional upgrade: the
    accommodation lines have to sum to the accommodation subtotal, the
    transport lines to the transport subtotal, and the optional upgrade must
    not be inside either — it sits in its own group, because it is not part of
    the price the client was quoted.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[_package(ids, upcountry_lodge)],
        segments=_rail_journey(ids)
        + [
            {
                "sequence": 7,
                "kind": "transfer",
                "mode": "road",
                "vehicle_type": "saloon",
                "destination_id": ids["destination_diani"],
                "description": "VVIP meet and greet",
                "is_optional": True,
                "is_vvip": True,
            }
        ],
    )
    sheet = await _built(quote["id"])
    option = sheet.options[0]
    assert option.groups, "no cost lines on the worksheet"

    for group in option.groups:
        lines = sum((_amount(line.extended) for line in group.lines), D(0))
        assert group.total, f"{group.component} has lines but no subtotal"
        assert lines == _amount(group.total), (
            f"{group.component}: lines sum to {lines}, subtotal says {group.total}"
        )

    # And the components sum to the cost subtotal the build-up starts from —
    # excluding the optional upgrades, which are outside the package.
    package = sum(
        _amount(group.total)
        for group in option.groups
        if group.component != "transport_optional"
    )
    assert package == _figure(option, "Cost subtotal")


async def test_the_worksheet_totals_are_the_client_document_s_totals(
    client, admin_tokens, sample_catalogue
):
    """A mirror that can disagree with the thing it mirrors is not evidence.

    Both read the same frozen version, so the group total on the worksheet is
    the same string the client's page prints.
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
    sheet = await _built(quote["id"])
    total = next(f.value for f in sheet.options[0].build_up if f.label == "Group total")

    page = await client.get(
        f"{API}/quotes/{quote['id']}/document.html", headers=h
    )
    assert page.status_code == 200, page.text
    assert total in page.text, total


async def test_the_arithmetic_between_the_figures_holds(
    client, admin_tokens, sample_catalogue
):
    """Contingency inside the cost basis, the agent fee outside the profit.

    The two orderings §3.6 insists on, checked on the artefact rather than on
    the function that produced it.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {
                "accommodation_id": ids["acc_sto_full_board"],
                "is_recommended": True,
                "agent_cover_fee": "25000",
            }
        ],
    )
    option = (await _built(quote["id"])).options[0]
    subtotal = _figure(option, "Cost subtotal")
    contingency = _figure(option, "Contingency")
    basis = _figure(option, "Cost basis")
    profit = _figure(option, "Profit")
    selling = _figure(option, "Selling total")
    agent = _figure(option, "Agent cover fee")

    assert basis == subtotal + contingency
    assert selling == basis + profit + agent
    assert agent == D(25000)
    # Realised margin is the three numbers §3.5 keeps apart, added back.
    assert _figure(option, "Realised margin") == (
        profit + contingency + _figure(option, "Retained half-discount")
    )


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #


async def test_every_line_names_the_row_it_came_from(
    client, admin_tokens, sample_catalogue
):
    """A cost you cannot trace to a document is a cost you cannot defend."""
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
    option = (await _built(quote["id"])).options[0]
    for group in option.groups:
        for line in group.lines:
            assert line.source, f"{line.label} has no source"
            assert line.basis, f"{line.label} has no basis"
            assert line.quantity >= 1, f"{line.label} has no multiplier"


async def test_a_discounted_rack_rate_shows_all_three_of_its_numbers(
    client, admin_tokens, sample_catalogue
):
    """Baobab's sheet says 24,000 with 15% off (§3.5).

        sheet   24,000   what the PDF says
        paid    20,400   what the property invoices
        costed  22,200   what enters the client's price — half the concession

    Three numbers, tracked apart, which is the only way realised margin can be
    reported honestly.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_rack_discounted"], "is_recommended": True}
        ],
    )
    option = (await _built(quote["id"])).options[0]
    beds = next(g for g in option.groups if g.component == "accommodation")
    line = beds.lines[0]
    assert _amount(line.sheet_amount or "") == D(24000)
    assert _amount(line.paid_amount or "") == D(20400)
    assert _amount(line.unit_amount) == D(22200)
    assert "RACK" in line.source
    assert "sheet discount 15" in line.source


async def test_a_hand_entered_cost_says_so(client, admin_tokens, sample_catalogue):
    """There is no supplier document behind a chef fee somebody typed.

    Saying that in the source column is the point: it is the line an operator
    cannot check against anything, so it is the line that needs re-checking.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {
                "accommodation_id": ids["acc_bb_only"],
                "is_recommended": True,
                "chef_fee_per_meal": "3000",
                "manual_meal_cost": "20000",
            }
        ],
    )
    option = (await _built(quote["id"])).options[0]
    chef = next(g for g in option.groups if g.component == "chef")
    assert "entered by hand" in chef.lines[0].source
    assert chef.lines[0].quantity == 6, "two meals a day for three nights"


async def test_the_journey_is_listed_once_not_once_per_option(
    client, admin_tokens, sample_catalogue
):
    """It is charged into every option because it is the same journey (§3.10).

    Printed once at the top of the sheet, so it does not read as though it had
    been paid for twice.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True},
            {"accommodation_id": ids["acc_rack_discounted"], "sort_order": 2},
        ],
        segments=_rail_journey(ids),
    )
    sheet = await _built(quote["id"])
    assert len(sheet.options) == 2
    assert len(sheet.journey) == 6, [line.label for line in sheet.journey]


# --------------------------------------------------------------------------- #
# Who may read it
# --------------------------------------------------------------------------- #


async def test_a_client_facing_role_cannot_open_the_worksheet(
    client, admin_tokens, sample_catalogue
):
    """The same permission that gates the internal pricing read.

    A sales agent renders proposals all day; this is the half of the same
    information the client must never see, and the boundary is a permission
    rather than a convention.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    email = unique_email("wsagent")
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

    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
    )
    proposal = await client.get(
        f"{API}/quotes/{quote['id']}/document.html", headers=agent
    )
    assert proposal.status_code == 200, "an agent must still be able to quote"
    sheet = await client.get(
        f"{API}/quotes/{quote['id']}/worksheet.html", headers=agent
    )
    assert sheet.status_code == 403, sheet.text


async def test_the_rendered_worksheet_carries_the_figures_and_the_sources(
    client, admin_tokens, sample_catalogue
):
    """Asserted against the rendered page, like the client document's leak test.

    The opposite direction: here the internal figures are the *required*
    content, and their absence is the failure.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, _ = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_rack_discounted"], "is_recommended": True}
        ],
    )
    resp = await client.get(
        f"{API}/quotes/{quote['id']}/worksheet.html", headers=h
    )
    assert resp.status_code == 200, resp.text
    html = resp.text
    assert "Internal costing worksheet" in html
    assert "not for the client" in html
    for required in (
        "Cost subtotal",
        "Contingency",
        "Realised margin",
        "Retained half-discount",
        "accommodation_rates ",
        "KES 20,400",  # what the property invoices
        "KES 22,200",  # what enters the client's price
    ):
        assert required in html, required


# --------------------------------------------------------------------------- #
# Frozen with its version
# --------------------------------------------------------------------------- #


async def test_an_earlier_version_s_worksheet_still_says_what_it_said(
    client, admin_tokens, sample_catalogue
):
    """Re-issuing appends a version; it does not rewrite the last one.

    The worksheet is the record of what was costed, so version 1 has to keep
    reporting version 1's figures after an agent has loaded a cover fee and
    re-issued.
    """
    h, ids = _h(admin_tokens), sample_catalogue
    quote, first = await _issued(
        client,
        h,
        ids,
        options=[
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
        ],
    )
    before = _figure((await _built(quote["id"], version=1)).options[0], "Selling total")

    stored = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()
    await client.patch(
        f"{API}/quotes/{quote['id']}/status", headers=h, json={"status": "draft"}
    )
    patched = await client.patch(
        f"{API}/quotes/{quote['id']}/options/{stored['options'][0]['id']}",
        headers=h,
        json={"agent_cover_fee": "40000"},
    )
    assert patched.status_code == 200, patched.text
    second = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert second.status_code == 200, second.text
    assert second.json()["version_number"] == first["version_number"] + 1

    assert _figure(
        (await _built(quote["id"], version=1)).options[0], "Selling total"
    ) == before
    assert _figure(
        (await _built(quote["id"], version=2)).options[0], "Selling total"
    ) == before + D(40000)


async def test_the_worksheet_shows_both_totals_where_they_differ(
    client, admin_tokens, sample_catalogue
):
    """The build-up's figure and what the client is billed.

    They differ by the rounding whenever a group is priced per cohort — each
    cohort rounds up in its own currency and is multiplied back out — and the
    gap is the kind of thing somebody reconciling an invoice needs to see
    rather than rediscover. The client is billed the cohort sum; the proposal
    shows only that one.
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
    option = (await _built(quote["id"])).options[0]
    assert _figure(option, "Group total") == D("126600")
    assert _figure(option, "Billed to the client") == D("126720")
    # Which is exactly the cohort rows summed at the disclosed rate.
    assert [(one.per_person, one.total) for one in option.cohorts] == [
        ("KES 17,600", "KES 35,200"),
        ("USD 352", "USD 704"),
    ]
