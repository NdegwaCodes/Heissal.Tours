"""stage3.10: a travel date on each transport segment

Transport tariffs are effective-dated — fares move, which is why they are rows
and not constants — but a segment carried no date, so every movement on a quote
had to be priced at one instant. On a trip that straddles a fare revision the
return rail leg is a different price from the outbound one, and pricing both at
the arrival-date tariff under-charges the quote without anything showing.

Nullable, falling back to the quote's arrival date, so the common same-week
journey needs nothing typed and existing rows keep pricing exactly as they did.

Revision ID: d5a3e81c60b9
Revises: c72ba3d81f45
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5a3e81c60b9"
down_revision: Union[str, Sequence[str], None] = "c72ba3d81f45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "quote_transport_segments",
        sa.Column("travel_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("quote_transport_segments", "travel_date")
