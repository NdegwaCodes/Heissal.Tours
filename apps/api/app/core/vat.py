"""VAT normalisation for stored rates (design doc §3.2).

**Every stored rate is VAT-inclusive.** That is an invariant, not a preference.
The engine adds no tax anywhere — VAT is a disclosure line on the quotation
("All prices inclusive of 16% VAT"), never an arithmetic step — so a rate that
reaches the database still exclusive of tax under-charges the client by the whole
VAT rate *and* makes the document's own disclosure line false.

A supplier sheet that quotes exclusive rates is therefore grossed up **once, at
ingestion**, and the row records the basis it was normalised from so the figure
can still be reconciled against the PDF it came from.

Normalising here rather than at pricing time is deliberate. Pricing reads rates
from five places (nightly rates, supplements, park fees, activities, transport);
a gross-up applied at read time is a rule five call sites have to remember, and
the failure mode when one forgets is a silent 16% under-charge. Applied at write
time it is a property of the data instead, and the ``vat_inclusive`` column
becomes a record of provenance rather than a flag anyone has to act on.
"""

from __future__ import annotations

from decimal import Decimal

# Kenya's standard rate, and the default basis of every sheet in the corpus
# (24 of the 32 machine-readable documents state it explicitly; all inclusive).
# A default, not a constant: the per-rate ``vat_pct`` column is what actually
# applies, so a rate change or a zero-rated supplier needs no code change.
DEFAULT_VAT_PCT = Decimal("16")


def to_vat_inclusive(
    amount: Decimal, *, vat_inclusive: bool, vat_pct: Decimal
) -> Decimal:
    """The VAT-inclusive equivalent of ``amount``.

    Already-inclusive amounts are returned untouched — that is what stops a rate
    being grossed up twice by a second confirmation of the same sheet.
    """
    if vat_inclusive:
        return amount
    if vat_pct < 0:
        raise ValueError("VAT percentage cannot be negative")
    return amount * (Decimal(1) + vat_pct / Decimal(100))
