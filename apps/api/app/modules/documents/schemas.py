from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DocumentConfigRead(BaseModel):
    """The brand copy and boilerplate the quotation prints."""

    model_config = ConfigDict(from_attributes=True)

    company_name: str
    contact_email: str
    contact_phone: str
    social_line: str
    tagline_lines: list[str]
    proposal_kind: str
    welcome_eyebrow: str
    why_us_heading: str
    why_us_points: list[str]
    coordination_heading: str
    coordination_points: list[str]
    next_steps_heading: str
    next_steps_body: str
    availability_notice_heading: str
    availability_notice: str
    closing_disclaimer: str
    vat_note: str
    font_display: str
    font_body: str
    fonts_are_placeholders: bool
    page_size: str


class DocumentConfigUpdate(BaseModel):
    """Partial update — only the provided fields are changed."""

    company_name: str | None = Field(default=None, min_length=1, max_length=120)
    contact_email: str | None = Field(default=None, min_length=1, max_length=200)
    contact_phone: str | None = Field(default=None, min_length=1, max_length=60)
    social_line: str | None = None
    tagline_lines: list[str] | None = None
    proposal_kind: str | None = None
    welcome_eyebrow: str | None = None
    why_us_heading: str | None = None
    why_us_points: list[str] | None = None
    coordination_heading: str | None = None
    coordination_points: list[str] | None = None
    next_steps_heading: str | None = None
    next_steps_body: str | None = None
    availability_notice_heading: str | None = None
    availability_notice: str | None = None
    closing_disclaimer: str | None = None
    vat_note: str | None = None
    # The two placeholders the real brand faces will replace (§3.11). Setting
    # either is the moment to clear `fonts_are_placeholders`.
    font_display: str | None = None
    font_body: str | None = None
    fonts_are_placeholders: bool | None = None
    page_size: str | None = Field(default=None, pattern="^(A4|Letter)$")
