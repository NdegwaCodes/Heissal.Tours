"""Reading a quote's group vector out of the database (§3.8).

One function, and the only place the question "who is travelling on this quote?"
is answered. Three answers were previously possible — ``pax_count``, the length
of ``travellers``, and the quote's single ``residence_category_id`` — and they
could disagree. A headcount that depends on which caller asked is how a group
gets rooms for twenty-five people and park fees for one.

The precedence is deliberate and one-directional:

1. ``quote_cohorts`` if any exist. This is the full vector and the only thing
   that can express the client's confirmed rule — non-residents charged in USD
   and residents in KES on the same quote.
2. ``pax_count`` on the quote's own residence category, all adults. The
   shorthand for a group uniform in both respects, which most groups are.
3. The ``travellers`` rows, grouped by their recorded type. What a small quote
   with named guests has.

Currency is never asked of the caller: it is the residence category's own
``default_currency_code``, because who bills in what is a property of the
category (§3.8) rather than a per-quote choice.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.quotes.cohorts import Group, group_from_counts
from app.modules.quotes.models import Quote
from app.modules.residence.models import ResidenceCategory


async def build_group(db: AsyncSession, quote: Quote) -> Group:
    """The group travelling on ``quote``, however it was recorded."""
    categories = await _categories(db)

    if quote.cohorts:
        counts = [
            (
                _key(categories, row.residence_category_id),
                row.traveller_type.strip().lower(),
                row.headcount,
            )
            for row in quote.cohorts
        ]
        return _build(counts, categories)

    own = _key(categories, quote.residence_category_id)

    if quote.pax_count:
        return _build([(own, "adult", quote.pax_count)], categories)

    if quote.travellers:
        # Everyone on a quote with no cohorts shares the quote's residency —
        # there is nowhere else for a per-traveller one to have been recorded —
        # so only the traveller type varies.
        by_type: dict[str, int] = {}
        for traveller in quote.travellers:
            kind = (traveller.traveller_type or "adult").strip().lower()
            by_type[kind] = by_type.get(kind, 0) + 1
        return _build(
            [(own, kind, count) for kind, count in sorted(by_type.items())], categories
        )

    raise AppError(
        "This quote has nobody travelling on it. Set pax_count, add cohorts, or "
        "add travellers before pricing it."
    )


def _build(
    counts: list[tuple[str, str, int]],
    categories: dict[uuid.UUID, ResidenceCategory],
) -> Group:
    """Attach each residency's billing currency, then hand off to the pure layer.

    Only the residencies actually travelling are checked for a currency.
    ``default_currency_code`` is nullable, and a cohort without one is not
    priceable — every figure it produced would be a bare number — but a category
    nobody on this quote belongs to is not this quote's problem.
    """
    currencies = _currencies(categories)
    missing = sorted(
        {residence for residence, _type, _n in counts if not currencies.get(residence)}
    )
    if missing:
        raise AppError(
            "These residence categories have no default currency, so travellers "
            f"in them cannot be priced: {', '.join(missing)}."
        )
    return group_from_counts(counts, currencies)


async def _categories(db: AsyncSession) -> dict[uuid.UUID, ResidenceCategory]:
    rows = (await db.execute(select(ResidenceCategory))).scalars().all()
    return {row.id: row for row in rows}


def _key(categories: dict[uuid.UUID, ResidenceCategory], id_: uuid.UUID) -> str:
    category = categories.get(id_)
    if category is None:
        raise AppError(
            "This quote references a residence category that no longer exists."
        )
    return category.key


def _currencies(categories: dict[uuid.UUID, ResidenceCategory]) -> dict[str, str]:
    """Billing currency per residence key, refusing a category that has none.

    ``default_currency_code`` is nullable, and a cohort with no currency is not
    priceable — every figure it produces would be a bare number. Saying which
    category is missing one beats a ``ValueError`` about a three-letter code
    raised three frames down in the pure layer.
    """
    return {
        row.key: (row.default_currency_code or "").upper() for row in categories.values()
    }
