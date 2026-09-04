"""Stage 3.5 — the quotation document: imagery, the template, and the boundary.

The most important test in this file is
``test_the_rendered_document_contains_no_internal_figure``. Every other check
here is about the document looking right; that one is about it being safe to
send. It asserts against the actual rendered bytes rather than against a schema,
because the schema is the mechanism and the rendered page is the artefact.
"""

from __future__ import annotations

import io
import re
import uuid
from decimal import Decimal

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from app.core.config import settings
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def png_bytes(colour: tuple[int, int, int] = (32, 64, 96), size=(8, 6)) -> bytes:
    """A tiny real PNG, unique to this call.

    Uniqueness matters because uploads are content-addressed: bytes identical to
    something already stored return the existing row rather than a new one. With
    a fixed palette, a second run against the same throwaway database found the
    *previous* run's images, inherited whatever hero flag they had ended up with,
    and failed — a suite that only passes on a freshly created database, which is
    the kind of flake that gets tests deleted rather than fixed.

    The nonce is metadata, so the pixels a test asserts on are unaffected. A test
    that deliberately needs two identical uploads calls this once and reuses the
    value.
    """
    buffer = io.BytesIO()
    info = PngInfo()
    info.add_text("nonce", uuid.uuid4().hex)
    Image.new("RGB", size, colour).save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


async def _client_record(client, h, residence_category_id):
    resp = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Doc Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("doc"),
            "residence_category_id": residence_category_id,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _issued_quote(client, h, ids, *, options=None, **over):
    """A quote issued with a frozen version, ready to render."""
    record = await _client_record(client, h, ids["residence_citizen"])
    body = {
        "client_id": record["id"],
        "presentation_currency": "KES",
        "residence_category_id": ids["residence_citizen"],
        "arrival_date": "2026-07-01",
        "departure_date": "2026-07-04",
        "pax_count": 25,
        "requested_meal_plan_id": ids["meal_plan_fb"],
        "options": options
        or [
            {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True},
            {"accommodation_id": ids["acc_rack_discounted"], "agent_cover_fee": "25000"},
        ],
    }
    body.update(over)
    created = await client.post(f"{API}/quotes", headers=h, json=body)
    assert created.status_code == 201, created.text
    quote = created.json()
    issued = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert issued.status_code == 200, issued.text
    return quote, issued.json()


async def _render(client, h, quote_id, **params):
    resp = await client.get(
        f"{API}/quotes/{quote_id}/document.html", headers=h, params=params
    )
    assert resp.status_code == 200, resp.text
    return resp.text


# --------------------------------------------------------------------------- #
# Imagery
# --------------------------------------------------------------------------- #


async def test_a_property_photograph_can_be_uploaded_and_served(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    content = png_bytes((11, 22, 33))
    resp = await client.post(
        f"{API}/accommodations/{sample_catalogue['acc_sto_full_board']}/images",
        headers=h,
        files={"file": ("pool.png", content, "image/png")},
        data={"alt_text": "The pool at dusk", "is_hero": "true"},
    )
    assert resp.status_code == 201, resp.text
    image = resp.json()
    assert image["is_hero"] is True
    assert image["alt_text"] == "The pool at dusk"
    assert image["byte_size"] == len(content)

    served = await client.get(f"{API}/property-images/{image['id']}/file", headers=h)
    assert served.status_code == 200
    assert served.content == content


async def test_the_same_photograph_uploaded_twice_is_one_row(
    client, admin_tokens, sample_catalogue
):
    """A gallery upload where two files repeat should not half-fail."""
    h = _h(admin_tokens)
    content = png_bytes((44, 55, 66))
    url = f"{API}/accommodations/{sample_catalogue['acc_bb_only']}/images"
    first = await client.post(url, headers=h, files={"file": ("a.png", content, "image/png")})
    second = await client.post(url, headers=h, files={"file": ("b.png", content, "image/png")})
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"]


async def test_re_uploading_a_photograph_can_still_make_it_the_hero(
    client, admin_tokens, sample_catalogue
):
    """A repeat upload is deduplicated, but its flags are not discarded.

    Returning the existing row untouched made "I already uploaded this, now make
    it the cover" a silent no-op: 201, the right row, and nothing changed.
    """
    h = _h(admin_tokens)
    url = f"{API}/accommodations/{sample_catalogue['acc_bb_only']}/images"
    content = png_bytes((21, 32, 43))
    first = await client.post(
        url, headers=h, files={"file": ("plain.png", content, "image/png")}
    )
    assert first.status_code == 201, first.text
    assert first.json()["is_hero"] is False

    again = await client.post(
        url,
        headers=h,
        files={"file": ("plain.png", content, "image/png")},
        data={"is_hero": "true", "alt_text": "Now the hero"},
    )
    assert again.status_code == 201, again.text
    assert again.json()["id"] == first.json()["id"], "should still deduplicate"
    assert again.json()["is_hero"] is True
    assert again.json()["alt_text"] == "Now the hero"

    listing = (await client.get(url, headers=h)).json()
    assert sum(1 for image in listing if image["is_hero"]) == 1


async def test_a_plain_repeat_upload_does_not_demote_the_current_hero(
    client, admin_tokens, sample_catalogue
):
    """An upload that says nothing about the hero is not asking to unset one."""
    h = _h(admin_tokens)
    url = f"{API}/accommodations/{sample_catalogue['acc_min_stay']}/images"
    content = png_bytes((90, 91, 92))
    hero = await client.post(
        url,
        headers=h,
        files={"file": ("hero.png", content, "image/png")},
        data={"is_hero": "true"},
    )
    assert hero.status_code == 201, hero.text
    again = await client.post(
        url, headers=h, files={"file": ("hero.png", content, "image/png")}
    )
    assert again.status_code == 201, again.text
    assert again.json()["is_hero"] is True


async def test_only_one_image_is_the_hero(client, admin_tokens, sample_catalogue):
    """Two heroes would render an arbitrary one, differently between runs."""
    h = _h(admin_tokens)
    url = f"{API}/accommodations/{sample_catalogue['acc_villa']}/images"
    for colour in ((1, 2, 3), (4, 5, 6)):
        resp = await client.post(
            url,
            headers=h,
            files={"file": ("v.png", png_bytes(colour), "image/png")},
            data={"is_hero": "true"},
        )
        assert resp.status_code == 201, resp.text

    listing = (await client.get(url, headers=h)).json()
    assert sum(1 for image in listing if image["is_hero"]) == 1
    # The hero sorts first, so the renderer never has to look for it.
    assert listing[0]["is_hero"] is True


async def test_a_non_image_upload_is_refused(client, admin_tokens, sample_catalogue):
    h = _h(admin_tokens)
    resp = await client.post(
        f"{API}/accommodations/{sample_catalogue['acc_villa']}/images",
        headers=h,
        files={"file": ("notes.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert resp.status_code == 400
    assert "not an accepted image format" in resp.text


async def test_a_file_that_is_not_really_an_image_is_refused(
    client, admin_tokens, sample_catalogue
):
    """The declared content type is a claim; decoding it is the check.

    A corrupt file uploads without complaint, embeds without complaint, and then
    renders as alt text across the hero of a client's proposal. Found exactly
    that way, by looking at a printed PDF whose cover was a dark rectangle with a
    caption on it.
    """
    h = _h(admin_tokens)
    # A plausible PNG signature followed by nothing a decoder can use.
    broken = bytes.fromhex("89504e470d0a1a0a") + bytes(40)
    resp = await client.post(
        f"{API}/accommodations/{sample_catalogue['acc_villa']}/images",
        headers=h,
        files={"file": ("broken.png", broken, "image/png")},
    )
    assert resp.status_code == 400
    assert "not a readable image" in resp.text


async def test_an_uploaded_image_records_its_dimensions(
    client, admin_tokens, sample_catalogue
):
    """They fall out of the decode, so the columns stop being permanently NULL."""
    h = _h(admin_tokens)
    resp = await client.post(
        f"{API}/accommodations/{sample_catalogue['acc_villa']}/images",
        headers=h,
        files={"file": ("wide.png", png_bytes((7, 8, 9), (64, 24)), "image/png")},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["width"] == 64
    assert resp.json()["height"] == 24


async def test_svg_is_refused(client, admin_tokens, sample_catalogue):
    """An SVG is a script-bearing document, not a photograph."""
    h = _h(admin_tokens)
    resp = await client.post(
        f"{API}/accommodations/{sample_catalogue['acc_villa']}/images",
        headers=h,
        files={"file": ("x.svg", b"<svg onload='alert(1)'/>", "image/svg+xml")},
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# The document renders
# --------------------------------------------------------------------------- #


async def test_an_unissued_quote_has_no_document(
    client, admin_tokens, sample_catalogue
):
    """Rendering live figures would produce a proposal whose numbers move."""
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
                "options": [
                    {
                        "accommodation_id": sample_catalogue["acc_sto_full_board"],
                        "is_recommended": True,
                    }
                ],
            },
        )
    ).json()
    resp = await client.get(f"{API}/quotes/{quote['id']}/document.html", headers=h)
    assert resp.status_code == 400
    assert "has not been issued" in resp.text


async def test_the_document_renders_the_client_facing_figures(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote, version = await _issued_quote(client, h, sample_catalogue)
    html = await _render(client, h, quote["id"])

    assert html.lstrip().startswith("<!doctype html>")
    assert quote["quote_number"] in html
    # Both properties, both prices.
    assert "Coral Sands Resort (demo)" in html
    assert "Baobab Beach Lodge (demo)" in html
    assert "KES 17,900" in html  # Coral Sands per person
    assert "KES 447,500" in html  # Coral Sands group total
    # The VAT disclosure, not a tax calculation (§3.2).
    assert "All prices inclusive of 16% VAT" in html
    # Standing copy from the document config.
    assert "Curated Journeys." in html
    assert "info@heissaltours.com" in html


async def test_the_recommended_option_is_marked_everywhere_it_appears(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote, _ = await _issued_quote(client, h, sample_catalogue)
    html = await _render(client, h, quote["id"])
    # The index card, the option page chip and the comparison row.
    assert html.count("Recommended") >= 3
    assert 'class="recommended"' in html


async def test_the_comparison_table_is_cheapest_first(
    client, admin_tokens, sample_catalogue
):
    """The pages lead with the recommendation; the table lets a client scan cost."""
    h = _h(admin_tokens)
    quote, _ = await _issued_quote(client, h, sample_catalogue)
    html = await _render(client, h, quote["id"])
    table = html.split("At a Glance")[1]
    coral = table.index("Coral Sands")
    baobab = table.index("Baobab")
    # Coral Sands at 447,500 is cheaper than Baobab at 1,152,500.
    assert coral < baobab


async def test_a_declined_property_appears_with_its_reason(
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
                "arrival_date": "2026-12-21",
                "departure_date": "2026-12-24",
                "pax_count": 25,
                "requested_meal_plan_id": sample_catalogue["meal_plan_fb"],
                "options": [
                    {
                        "accommodation_id": sample_catalogue["acc_sto_full_board"],
                        "is_recommended": True,
                    },
                    {"accommodation_id": sample_catalogue["acc_min_stay"]},
                ],
            },
        )
    ).json()
    await client.post(
        f"{API}/quotes/{quote['id']}/rejected-candidates",
        headers=h,
        json={"name": "Diani Cottages", "reason": "Caps at 16 guests; this group is 25."},
    )
    issued = await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    assert issued.status_code == 200, issued.text

    html = await _render(client, h, quote["id"])
    assert "Additional options considered" in html
    assert "Diani Cottages" in html
    assert "Caps at 16 guests" in html
    # And the engine's own refusal, in client-safe wording.
    assert "Chui Festive Camp (demo)" in html
    assert "minimum stay of 4 nights" in html


async def test_imagery_reaches_the_document(client, admin_tokens, sample_catalogue):
    h = _h(admin_tokens)
    cover = await client.post(
        f"{API}/destinations/{sample_catalogue['destination_diani']}/images",
        headers=h,
        files={"file": ("coast.png", png_bytes((90, 120, 150)), "image/png")},
        data={"is_cover": "true", "alt_text": "The Diani coastline"},
    )
    assert cover.status_code == 201, cover.text
    hero = await client.post(
        f"{API}/accommodations/{sample_catalogue['acc_rack_discounted']}/images",
        headers=h,
        files={"file": ("baobab.png", png_bytes((70, 30, 20)), "image/png")},
        data={"is_hero": "true"},
    )
    assert hero.status_code == 201, hero.text

    quote, _ = await _issued_quote(client, h, sample_catalogue)

    # Embedded by default: the document has to stand alone, because the PDF
    # renderer has no credentials and a browser will not replay a bearer token
    # when fetching an <img>.
    html = await _render(client, h, quote["id"])
    assert "src=\"data:image/png;base64," in html
    assert "/api/v1/property-images/" not in html
    assert "The Diani coastline" in html

    # Linked on request, for a preview whose fetcher can authenticate.
    linked = await _render(client, h, quote["id"], inline_assets="false")
    assert f"/api/v1/destination-images/{cover.json()['id']}/file" in linked
    assert f"/api/v1/property-images/{hero.json()['id']}/file" in linked
    assert "data:image/png;base64," not in linked


async def test_a_version_renders_as_the_client_received_it(
    client, admin_tokens, sample_catalogue
):
    """Re-issuing must not rewrite the document already in someone's inbox."""
    h = _h(admin_tokens)
    quote, _ = await _issued_quote(
        client,
        h,
        sample_catalogue,
        options=[
            {"accommodation_id": sample_catalogue["acc_sto_full_board"], "is_recommended": True}
        ],
    )
    first = await _render(client, h, quote["id"])
    assert "KES 447,500" in first

    await client.patch(
        f"{API}/quotes/{quote['id']}/status", headers=h, json={"status": "draft"}
    )
    reloaded = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()
    await client.patch(
        f"{API}/quotes/{quote['id']}/options/{reloaded['options'][0]['id']}",
        headers=h,
        json={"agent_cover_fee": "25000"},
    )
    assert (
        await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    ).status_code == 200

    latest = await _render(client, h, quote["id"])
    assert "KES 472,500" in latest
    original = await _render(client, h, quote["id"], version=1)
    assert "KES 447,500" in original
    assert "KES 472,500" not in original


# --------------------------------------------------------------------------- #
# The boundary — the test that decides whether this is safe to send
# --------------------------------------------------------------------------- #


async def test_the_rendered_document_contains_no_internal_figure(
    client, admin_tokens, sample_catalogue
):
    """Asserted against the rendered page, not against a schema.

    The two-schema split is the mechanism; this is the artefact. Baobab is on the
    quote precisely because its rack rate carries a discount, so every internal
    figure it produces is a different number from the client's — if any of them
    reach the page, they show up here.

        sheet     13 x 24,000 x 3   = 936,000
        paid      x 0.85            = 795,600   <- must not appear
        costed    x 0.925           = 865,800   <- must not appear
        retained                    =  70,200   <- must not appear
        contingency 5%              =  43,290   <- must not appear
        profit 24%                  = 218,181.60 <- must not appear
        agent cover fee             =  25,000   <- must not appear
        client pays  46,100 x 25    = 1,152,500 <- must appear
    """
    h = _h(admin_tokens)
    quote, version = await _issued_quote(client, h, sample_catalogue)
    html = await _render(client, h, quote["id"])

    forbidden_numbers = [
        "936,000",
        "795,600",
        "865,800",
        "70,200",
        "43,290",
        "218,181",
        "25,000",
        "343,500",  # Coral Sands cost subtotal
        "17,175",  # its contingency
        "86,562",  # its profit
    ]
    leaked = [figure for figure in forbidden_numbers if figure in html]
    assert not leaked, f"internal figures on the client document: {leaked}"

    forbidden_words = [
        "cost_subtotal",
        "contingency",
        "retained_discount",
        "supplier_paid",
        "agent_cover_fee",
        "gross_margin",
        "internal_cost",
        "profit",
    ]
    named = [word for word in forbidden_words if word.lower() in html.lower()]
    assert not named, f"internal field names on the client document: {named}"

    # And the figures the client is meant to see are all there.
    assert "KES 46,100" in html
    assert "KES 1,152,500" in html


async def test_user_entered_text_is_escaped(client, admin_tokens, sample_catalogue):
    """A blurb or a rejection reason is text a person typed."""
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
                "document_title": "Retreat <script>alert(1)</script>",
                "options": [
                    {
                        "accommodation_id": sample_catalogue["acc_sto_full_board"],
                        "is_recommended": True,
                    }
                ],
            },
        )
    ).json()
    await client.post(
        f"{API}/quotes/{quote['id']}/rejected-candidates",
        headers=h,
        json={"name": "Tag & Co <b>", "reason": "Full for these dates."},
    )
    assert (
        await client.post(f"{API}/quotes/{quote['id']}/issue", headers=h)
    ).status_code == 200

    html = await _render(client, h, quote["id"])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "Tag &amp; Co" in html


# --------------------------------------------------------------------------- #
# Type discipline (§3.11)
# --------------------------------------------------------------------------- #


async def test_every_font_comes_from_one_of_the_two_variables(
    client, admin_tokens, sample_catalogue
):
    """The discipline that made the brand-font swap a two-line change.

    It paid off on 2026-08-25: replacing the placeholders with Cormorant Garamond
    and Libre Franklin touched DocumentConfig and nothing else, because every
    rule in the template reaches type through one of two custom properties. A
    stray ``font-family: Georgia`` deep in the markup is what would have turned
    that into a hunt.

    The ``@font-face`` block is excluded: it necessarily names families, being
    where the embedded faces are declared rather than where type is applied.
    """
    h = _h(admin_tokens)
    quote, _ = await _issued_quote(client, h, sample_catalogue)
    html = await _render(client, h, quote["id"])
    html = re.sub(r"@font-face\{[^}]*\}", "", html)

    declarations = re.findall(r"font-family:\s*([^;}\n]+)", html)
    indirect = [d for d in declarations if d.strip().startswith("var(--font-")]
    direct = [d for d in declarations if not d.strip().startswith("var(--font-")]
    assert indirect, "the template stopped using the font variables"
    assert not direct, f"fonts named outside the two variables: {direct}"
    # And the variables themselves are defined once each.
    assert html.count("--font-display:") == 1
    assert html.count("--font-body:") == 1


async def test_the_real_fonts_can_be_swapped_in_through_config(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    before = (await client.get(f"{API}/document-config", headers=h)).json()
    # Since 3.11 the shipped defaults ARE the brand faces, so nothing here is a
    # placeholder any more. This assertion used to read `is True` and kept
    # passing only because the shared test database still held a config row
    # written before that change; it failed the moment the database was rebuilt
    # from migrations. What the test is for — that type is swappable in one
    # place — is unaffected either way.
    assert before["fonts_are_placeholders"] is False
    assert "Cormorant Garamond" in before["font_display"]
    try:
        patched = await client.patch(
            f"{API}/document-config",
            headers=h,
            json={
                "font_display": "'Heissal Display', serif",
                "font_body": "'Heissal Sans', sans-serif",
                "fonts_are_placeholders": False,
                "company_name": "Heissal Tours & Travel Ltd",
            },
        )
        assert patched.status_code == 200, patched.text

        quote, _ = await _issued_quote(client, h, sample_catalogue)
        html = await _render(client, h, quote["id"])
        assert "--font-display: 'Heissal Display', serif" in html
        assert "--font-body: 'Heissal Sans', sans-serif" in html
        assert "Heissal Tours &amp; Travel Ltd" in html
        # Still exactly two font declarations naming a face.
        assert html.count("Heissal Display") == 1
    finally:
        await client.patch(
            f"{API}/document-config",
            headers=h,
            json={
                "font_display": before["font_display"],
                "font_body": before["font_body"],
                # Restore what was actually there. A literal here is what leaked
                # `fonts_are_placeholders: True` into the shared database and
                # kept the stale assertion above alive across runs.
                "fonts_are_placeholders": before["fonts_are_placeholders"],
                "company_name": before["company_name"],
            },
        )


async def test_the_page_size_defaults_to_a4(client, admin_tokens, sample_catalogue):
    """The sample was laid out on Letter; this document is printed in Kenya."""
    h = _h(admin_tokens)
    quote, _ = await _issued_quote(client, h, sample_catalogue)
    html = await _render(client, h, quote["id"])
    assert "@page { size: A4; margin: 0; }" in html
    assert "--page-w: 210mm" in html


# --------------------------------------------------------------------------- #
# Sections appear only when their data does
# --------------------------------------------------------------------------- #


async def test_no_transport_page_without_transport_segments(
    client, admin_tokens, sample_catalogue
):
    """A proposal describing transfers the client is not getting is worse than
    one that says nothing about them."""
    h = _h(admin_tokens)
    quote, _ = await _issued_quote(client, h, sample_catalogue)
    html = await _render(client, h, quote["id"])
    assert "Seamless Group Transport" not in html
    # And the option pages do not claim transfers either.
    assert "Complete group transfers" not in html


async def test_the_document_is_one_section_per_printed_page(
    client, admin_tokens, sample_catalogue
):
    """Cover, welcome, options index, two option pages, comparison, next steps."""
    h = _h(admin_tokens)
    quote, _ = await _issued_quote(client, h, sample_catalogue)
    html = await _render(client, h, quote["id"])
    assert html.count('<section class="page">') == 7


async def test_a_non_comparable_option_says_so(client, admin_tokens, sample_catalogue):
    """The reference proposal does exactly this for its villas."""
    h = _h(admin_tokens)
    quote, _ = await _issued_quote(
        client,
        h,
        sample_catalogue,
        options=[
            {"accommodation_id": sample_catalogue["acc_sto_full_board"], "is_recommended": True},
            {
                "accommodation_id": sample_catalogue["acc_bb_only"],
                "chef_fee_per_meal": "5000",
                "manual_meal_cost": "30000",
            },
        ],
    )
    html = await _render(client, h, quote["id"])
    assert "not presented as directly equivalent" in html
    # A self-catering option says the group arranges meals rather than implying
    # board the hotel does not provide.
    assert "Group meal arrangement" in html


async def test_money_is_grouped_and_loses_the_pointless_cents(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote, _ = await _issued_quote(client, h, sample_catalogue)
    html = await _render(client, h, quote["id"])
    assert "KES 447,500" in html
    assert "447500" not in html
    assert "KES 447,500.00" not in html


async def test_the_document_needs_only_quote_read(
    client, admin_tokens, sample_catalogue
):
    """It is the client-facing artefact by definition, so no cost permission."""
    h = _h(admin_tokens)
    quote, _ = await _issued_quote(client, h, sample_catalogue)
    email = unique_email("docagent")
    await client.post(
        f"{API}/users",
        headers=h,
        json={"email": email, "password": "AgentPass123", "role_keys": ["viewer"]},
    )
    login = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "AgentPass123"}
    )
    resp = await client.get(
        f"{API}/quotes/{quote['id']}/document.html", headers=_h(login.json())
    )
    assert resp.status_code == 200, resp.text
    assert "KES 447,500" in resp.text


def test_money_formatting_is_pure():
    from app.modules.documents.viewmodel import money

    assert money(Decimal("1065000"), "KES") == "KES 1,065,000"
    assert money(Decimal("16800.00"), "KES") == "KES 16,800"
    # A fractional amount keeps its decimals rather than being truncated.
    assert money(Decimal("120.50"), "USD") == "USD 120.50"
    assert money(None, "KES") is None
