"""RateExtractionProvider seam — how a supplier rate sheet becomes candidate rows.

The ingestion service depends only on this protocol, never on a particular PDF
library or vision model. That matters here more than for most seams, because the
corpus needs two very different implementations:

* 32 of 35 documents carry a usable text layer and are parsed deterministically
  (:class:`app.modules.supplier_docs.extraction.GridRateExtractor`) — no model
  call, no per-document cost, and the same input always gives the same output.
* 3 are image-only scans and will need a vision/OCR provider. It plugs in here
  without the service changing.

Nothing this module produces is ever written straight to ``accommodation_rates``.
Every row is a *candidate* that a human confirms (design doc §5); a wrong money
value that reaches a client is a commercial incident, and the whole point of the
seam is that both a parser and a model are treated with the same suspicion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class ExtractionHint:
    """What the uploader already knows, which the document often does not say.

    Residence category and rate kind are properties of the *document* (the
    corpus ships resident and non-resident as separate files), and the discount
    percentage is frequently only in the filename — "15% Commission to us" —
    so these are supplied rather than parsed. Parsing them would be guessing.
    """

    residence_category: str | None = None
    rate_kind: str | None = None
    default_currency: str | None = None
    default_meal_plan: str | None = None
    supplier_discount_pct: Decimal | None = None
    vat_inclusive: bool = True
    vat_pct: Decimal = Decimal("16")


@dataclass(frozen=True)
class ExtractedRateRow:
    """One candidate rate. Every field is optional because a sheet may omit it.

    ``confidence`` is advisory only — it orders the reviewer's attention, it does
    not authorise anything. ``source_note`` carries the raw cell text so a
    reviewer can see what the parser was looking at without opening the PDF.
    """

    room_type: str | None = None
    meal_plan: str | None = None
    occupancy: int | None = None
    season_name: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    currency: str | None = None
    rate_per_night: Decimal | None = None
    confidence: float = 0.0
    source_note: str = ""
    page: int | None = None
    warnings: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        """Whether this row has everything a stored rate needs.

        An incomplete row is still shown to the reviewer — a missing occupancy on
        an otherwise good price is a one-click fix, and discarding it would hide
        real data.
        """
        return all(
            (
                self.room_type,
                self.meal_plan,
                self.occupancy,
                self.effective_from,
                self.effective_to,
                self.currency,
                self.rate_per_night is not None,
            )
        )


@dataclass
class ExtractionResult:
    """Everything one pass over one document produced."""

    rows: list[ExtractedRateRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    page_count: int = 0
    provider: str = ""

    @property
    def needs_other_provider(self) -> bool:
        """True when this document has no text to parse.

        The service reports this rather than treating an empty result as "the
        sheet contains no rates", which would silently lose a document.
        """
        return not self.rows and any("no text layer" in w for w in self.warnings)


class RateExtractionProvider(Protocol):
    """Turns a stored document into candidate rate rows.

    Implementations MUST:
    - never raise for an unparseable document; return warnings instead, so one
      bad file cannot take down an upload endpoint;
    - never invent a value to fill a gap — leave the field ``None``;
    - be deterministic where the underlying technology allows it.
    """

    name: str

    def supports(self, content_type: str, filename: str) -> bool: ...

    def extract(self, path: str, hint: ExtractionHint) -> ExtractionResult: ...
