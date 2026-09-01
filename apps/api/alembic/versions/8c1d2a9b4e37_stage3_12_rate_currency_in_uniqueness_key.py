"""stage3.12: currency belongs in the accommodation rate uniqueness key

A rate card may publish the *same* room-night in more than one currency and
expect the agent to bill in whichever the client is invoiced in. Kobe Suite
Resort does exactly this: 19,674 KES / 197 USD / 179 EUR for one night of a
Standard Garden View Suite — three rows that are one price, quoted three ways.

The old key treated those as a collision and kept the first, so which currency
survived depended on row order in a spreadsheet. Worse, the survivor might be
the one with no exchange rate on file, making the property unpriceable while a
perfectly usable USD figure sat in the sheet, discarded.

Keeping the currency in the key stores all three and lets the pricing engine
prefer the presentation currency, which removes an FX conversion — and its
rounding — from the quote entirely.

Revision ID: 8c1d2a9b4e37
Revises: 072fce0720a1
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "8c1d2a9b4e37"
down_revision: Union[str, Sequence[str], None] = "072fce0720a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAME = "uq_accommodation_rate_period"
_COLUMNS = [
    "room_type_id",
    "meal_plan_id",
    "residence_category_id",
    "occupancy",
    "effective_from",
]


def upgrade() -> None:
    op.drop_constraint(_NAME, "accommodation_rates", type_="unique")
    op.create_unique_constraint(_NAME, "accommodation_rates", [*_COLUMNS, "currency"])


def downgrade() -> None:
    # Narrowing the key can strand duplicate rows that were legal under the wide
    # one, so this fails loudly rather than deleting a supplier rate to fit.
    op.drop_constraint(_NAME, "accommodation_rates", type_="unique")
    op.create_unique_constraint(_NAME, "accommodation_rates", _COLUMNS)
