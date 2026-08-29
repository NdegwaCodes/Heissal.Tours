"""The brand typefaces reach the document, and cannot silently fail to.

Type is the one part of a rendered document whose failure is invisible: a missing
image leaves a hole, a missing font leaves a page that looks finished and is set
in the wrong face at the wrong metrics. So these tests assert the mechanism
rather than the appearance — that the faces are embedded, that nothing in the
template names a font outside the two custom properties, and that a missing file
is reported rather than swallowed.
"""

from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.modules.documents.config import DocumentConfig
from app.modules.documents.fonts import (
    FACES,
    FONT_DIR,
    face_css,
    font_stack,
    missing_faces,
)
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _issued(client, h, ids):
    created = await client.post(
        f"{API}/clients",
        headers=h,
        json={
            "name": f"Font Co {uuid.uuid4().hex[:8]}",
            "email": unique_email("font"),
            "residence_category_id": ids["residence_citizen"],
        },
    )
    assert created.status_code == 201, created.text
    quote = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": created.json()["id"],
            "presentation_currency": "KES",
            "residence_category_id": ids["residence_citizen"],
            "arrival_date": "2026-07-01",
            "departure_date": "2026-07-04",
            "pax_count": 25,
            "requested_meal_plan_id": ids["meal_plan_fb"],
            "options": [
                {"accommodation_id": ids["acc_sto_full_board"], "is_recommended": True}
            ],
        },
    )
    assert quote.status_code == 201, quote.text
    issued = await client.post(f"{API}/quotes/{quote.json()['id']}/issue", headers=h)
    assert issued.status_code == 200, issued.text
    return quote.json()["id"]


# --------------------------------------------------------------------------- #
# The files
# --------------------------------------------------------------------------- #


def test_every_declared_face_is_on_disk():
    """The deployment ships the fonts it claims to."""
    assert missing_faces() == []


def test_three_files_cover_every_weight_because_both_are_variable():
    """Downloading the five listed Cormorant weights produced five identical
    files. One per style is the whole range, at a third of the bytes."""
    files = sorted(p.name for p in FONT_DIR.glob("*.woff2"))
    assert files == [
        "cormorant-garamond-italic.woff2",
        "cormorant-garamond-normal.woff2",
        "libre-franklin-normal.woff2",
    ]
    total = sum(p.stat().st_size for p in FONT_DIR.glob("*.woff2"))
    assert total < 150_000, f"{total} bytes of fonts embedded in every document"


def test_the_files_really_are_woff2():
    """A wrong-format file would load as nothing and fall back in silence."""
    for path in FONT_DIR.glob("*.woff2"):
        assert path.read_bytes()[:4] == b"wOF2", path.name


def test_the_licence_travels_with_the_fonts():
    """Both families are OFL 1.1, which permits bundling and requires the notice."""
    licence = (FONT_DIR / "LICENSE-OFL.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE" in licence.upper()


# --------------------------------------------------------------------------- #
# The CSS
# --------------------------------------------------------------------------- #


def test_the_faces_are_embedded_not_linked():
    css = face_css()
    assert "data:font/woff2;base64," in css
    assert "fonts.googleapis.com" not in css
    assert "fonts.gstatic.com" not in css
    assert "http" not in css


def test_every_declared_face_produces_a_rule():
    css = face_css()
    assert css.count("@font-face") == len(FACES)
    for family, style, weight, _filename in FACES:
        assert f"font-family:'{family}'" in css
        assert f"font-style:{style}" in css
        assert f"font-weight:{weight}" in css


def test_the_embedded_payload_is_the_file_on_disk():
    """Guards the encoding step itself — a truncated payload still parses as CSS."""
    css = face_css()
    payload = re.search(r"base64,([A-Za-z0-9+/=]+)\)", css).group(1)
    assert base64.b64decode(payload)[:4] == b"wOF2"


def test_the_weight_ranges_are_the_brand_ranges():
    """Variable axes, declared over the ranges the client specified."""
    ranges = {(family, style): weight for family, style, weight, _ in FACES}
    assert ranges[("Cormorant Garamond", "normal")] == "400 700"
    assert ranges[("Libre Franklin", "normal")] == "300 700"


def test_a_stack_names_the_brand_face_first_then_a_real_fallback():
    """The fallback is a considered stack, not the generic keyword: if the brand
    face cannot load, the document should still set in something of its character."""
    display = font_stack("Cormorant Garamond")
    assert display.startswith("'Cormorant Garamond'")
    assert "Garamond" in display.split(",", 1)[1]
    body = font_stack("Libre Franklin")
    assert body.startswith("'Libre Franklin'")
    assert "sans-serif" in body


def test_a_missing_file_is_skipped_rather_than_fatal(monkeypatch, tmp_path):
    """A document set in a fallback beats no document at all — but it must be
    reportable, which is what ``missing_faces`` is for."""
    monkeypatch.setattr("app.modules.documents.fonts.FONT_DIR", Path(tmp_path))
    face_css.cache_clear()
    try:
        assert face_css() == ""
        assert len(missing_faces()) == len(FACES)
    finally:
        face_css.cache_clear()


# --------------------------------------------------------------------------- #
# The configured defaults
# --------------------------------------------------------------------------- #


def test_the_defaults_are_the_brand_faces_and_no_longer_placeholders():
    config = DocumentConfig()
    assert "Cormorant Garamond" in config.font_display
    assert "Libre Franklin" in config.font_body
    assert config.fonts_are_placeholders is False
    # The placeholders that stood in until 2026-08-25 are gone.
    assert "Playfair" not in config.font_display
    assert "Lato" not in config.font_body


def test_the_brand_stacks_still_pass_the_css_charset_guard():
    """The guard exists because autoescaping a font stack silently drops the
    face; the real stacks have to survive it as the placeholders did."""
    config = DocumentConfig(
        font_display=font_stack("Cormorant Garamond"),
        font_body=font_stack("Libre Franklin"),
    )
    assert "&#39;" not in config.font_display
    assert "&#39;" not in config.font_body


# --------------------------------------------------------------------------- #
# The rendered document
# --------------------------------------------------------------------------- #


async def test_the_rendered_document_carries_its_own_typography(
    client, admin_tokens, sample_catalogue
):
    h = _h(admin_tokens)
    quote_id = await _issued(client, h, sample_catalogue)
    page = (
        await client.get(f"{API}/quotes/{quote_id}/document.html", headers=h)
    ).text

    assert page.count("@font-face") == len(FACES)
    assert "data:font/woff2;base64," in page
    assert "Cormorant Garamond" in page
    assert "Libre Franklin" in page


async def test_the_document_makes_no_network_request_for_type(
    client, admin_tokens, sample_catalogue
):
    """The reason the files are in the repo. A linked face that fails to resolve
    at print time re-sets the whole proposal with nothing raised."""
    h = _h(admin_tokens)
    quote_id = await _issued(client, h, sample_catalogue)
    page = (
        await client.get(f"{API}/quotes/{quote_id}/document.html", headers=h)
    ).text
    for host in ("fonts.googleapis.com", "fonts.gstatic.com", "use.typekit"):
        assert host not in page


async def test_fonts_are_embedded_even_when_assets_are_linked(
    client, admin_tokens, sample_catalogue
):
    """There is no preview mode in which linking type is the better trade: an
    image that fails leaves a visible hole, a face that fails does not."""
    h = _h(admin_tokens)
    quote_id = await _issued(client, h, sample_catalogue)
    page = (
        await client.get(
            f"{API}/quotes/{quote_id}/document.html",
            headers=h,
            params={"inline_assets": "false"},
        )
    ).text
    assert page.count("@font-face") == len(FACES)
    assert "data:font/woff2;base64," in page


async def test_no_rule_outside_the_two_properties_names_a_font(
    client, admin_tokens, sample_catalogue
):
    """The rule that made swapping the placeholders a two-line edit. Every
    font-family in the document is either a @font-face declaration or one of the
    two custom properties."""
    h = _h(admin_tokens)
    quote_id = await _issued(client, h, sample_catalogue)
    page = (
        await client.get(f"{API}/quotes/{quote_id}/document.html", headers=h)
    ).text
    css = page[page.index("<style>") : page.index("</style>")]
    # Drop the @font-face block, which necessarily names families.
    body_css = re.sub(r"@font-face\{[^}]*\}", "", css)

    declarations = re.findall(r"font-family:\s*([^;}]+)", body_css)
    assert declarations, "expected the template to set type at all"
    for value in declarations:
        assert "var(--font-display)" in value or "var(--font-body)" in value, value
