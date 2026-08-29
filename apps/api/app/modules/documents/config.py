"""DocumentConfig — the brand copy and boilerplate the quotation prints.

Everything here is text a client reads that is *not* derived from the quote:
the wordmark, the contact details, the "why us" list, the availability notice,
the closing tagline. None of it belongs in a template literal. A hard-coded
phone number on a client-facing document is a support ticket waiting to happen,
and the notices are commercial language that finance and sales will want to
reword without waiting for a deploy.

Stored in ``app_settings`` under the ``document`` key, exactly like the pricing
config. The values below are the copy from the reference proposal, used until an
admin saves their own.

Deliberately *not* here: anything derived from the quote (prices, dates, rooming,
property names) and anything internal (cost, margin, the agent cover fee). This
config is read by the client-facing renderer, so a field added here is a field
printed on a document.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.documents.fonts import font_stack

DOCUMENT_SETTINGS_KEY = "document"

# A CSS font stack and nothing else — see the validator below for why this
# is a charset rule rather than an escaping one.
_FONT_STACK = re.compile(r"[A-Za-z0-9 '\"_.,-]{1,200}")


class DocumentConfig(BaseModel):
    """Typed view over the ``document`` app-setting."""

    model_config = ConfigDict(extra="ignore")

    # --- Brand ------------------------------------------------------------- #
    company_name: str = Field(default="Heissal Tours & Travel")
    contact_email: str = Field(default="info@heissaltours.com")
    contact_phone: str = Field(default="+254 758 896 215")
    social_line: str = Field(default="DM us directly through our social platforms")
    # Printed centred at the end, one line each.
    tagline_lines: list[str] = Field(
        default_factory=lambda: [
            "Curated Journeys.",
            "Authentic Experiences.",
            "Exceptional Service.",
        ]
    )

    # --- Standing copy ----------------------------------------------------- #
    proposal_kind: str = Field(default="Corporate Travel Proposal")
    welcome_eyebrow: str = Field(default="Welcome")
    why_us_heading: str = Field(default="Why Heissal Tours?")
    why_us_points: list[str] = Field(
        default_factory=lambda: [
            "Curated accommodation selection",
            "Private group transportation",
            "Flexible accommodation options",
            "End-to-end travel planning",
            "Dedicated trip coordination",
            "Supplier coordination",
            "Professional pre-trip support",
        ]
    )
    coordination_heading: str = Field(default="Included in our coordination")
    coordination_points: list[str] = Field(
        default_factory=lambda: [
            "Accommodation sourcing",
            "Group transportation coordination",
            "Supplier liaison",
            "Group logistics",
            "Room allocation",
            "Trip planning",
            "Pre-trip communication",
            "On-trip coordination",
        ]
    )
    next_steps_heading: str = Field(default="Let's Create Your Experience")
    next_steps_body: str = Field(
        default=(
            "Once your preferred accommodation option has been selected, our team "
            "will proceed with availability confirmation, room allocation, transport "
            "scheduling and final itinerary coordination."
        )
    )

    # --- Notices ----------------------------------------------------------- #
    availability_notice_heading: str = Field(
        default="Accommodation availability & booking"
    )
    availability_notice: str = Field(
        default=(
            "All accommodation options and quoted rates are subject to availability "
            "at the time of booking. We strongly recommend confirming your preferred "
            "accommodation as early as possible to secure availability and ensure a "
            "smooth experience, especially for group bookings. Rates may be subject "
            "to change where supplier rates or availability change prior to "
            "confirmation."
        )
    )
    closing_disclaimer: str = Field(
        default=(
            "All prices quoted are inclusive of VAT and are based on the current "
            "group requirements and supplier quotations available at the time of "
            "preparation. Accommodation and related services remain subject to "
            "availability and final confirmation at the time of booking."
        )
    )
    # The document states the tax basis rather than adding tax (§3.2), so the
    # rate quoted here is a disclosure and must match what ingestion normalised
    # rates to. It is config, not a literal, because a VAT change is a law change
    # and not a deploy.
    vat_note: str = Field(default="All prices inclusive of 16% VAT")

    # --- Type ------------------------------------------------------------- #
    # The brand faces, confirmed by the client 2026-08-25 and closing §3.11's
    # first open question. Cormorant Garamond carries display — the cover
    # headline, section headings, property names, price figures and the italic
    # taglines; Libre Franklin carries everything read rather than looked at.
    #
    # The whole template reaches type through exactly these two values, which is
    # why swapping the placeholders for the real faces was an edit here and not a
    # hunt through the markup. The files themselves are embedded rather than
    # linked — see ``app.modules.documents.fonts`` for why that matters at print
    # time.
    font_display: str = Field(default=font_stack("Cormorant Garamond"))
    font_body: str = Field(default=font_stack("Libre Franklin"))
    fonts_are_placeholders: bool = Field(default=False)

    @field_validator("font_display", "font_body")
    @classmethod
    def _css_safe_font_stack(cls, value: str) -> str:
        """Restrict a font stack to characters a font stack needs.

        These two values are the only strings the template emits into a
        ``<style>`` block unescaped, and they have to be: HTML-escaping
        ``'Playfair Display'`` yields ``&#39;Playfair Display&#39;``, which is
        not valid CSS and silently drops the face. So the safety comes from the
        charset instead — no braces, semicolons, angle brackets, parentheses,
        slashes or at-signs, which is everything needed to break out of a
        declaration or open a new rule.
        """
        if not _FONT_STACK.fullmatch(value):
            raise ValueError(
                "A font stack may contain only letters, digits, spaces, quotes, "
                "hyphens, underscores, dots and commas."
            )
        return value

    # --- Paper ------------------------------------------------------------- #
    # The reference proposal was laid out on US Letter, but Kenya prints A4 and
    # this document is produced here, so A4 is the default. Stored rather than
    # fixed in the template because it is a property of the printer, not of the
    # design.
    page_size: str = Field(default="A4", pattern="^(A4|Letter)$")
