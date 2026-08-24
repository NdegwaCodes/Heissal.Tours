"""stage3_4 rejected candidate source

Tells an engine-generated refusal from an agent's typed one.

Re-pricing a quote's options rewrites the refusals the engine derived from the
rates (a minimum stay the itinerary does not meet). An agent's typed refusal --
the reference document's "Diani Cottages, caps at 16 guests" -- is not
rediscoverable from any rate, so wiping it on a re-price would silently drop
something the client was shown. A NULL accommodation_id is not a usable
discriminator: a manual refusal may well name a property we hold.

Existing rows are backfilled to 'engine' by the server default, which is correct:
every row written before this migration came from the engine.

Revision ID: 15c4d3d4af6b
Revises: 316e59973b79
Create Date: 2026-08-25 00:22:52.593594
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '15c4d3d4af6b'
down_revision: Union[str, Sequence[str], None] = '316e59973b79'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "quote_rejected_candidates",
        sa.Column(
            "source",
            sa.String(length=10),
            server_default=sa.text("'engine'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    # Loses the distinction, so the next re-price would erase manual refusals
    # again. Nothing to preserve: the column IS the distinction.
    op.drop_column("quote_rejected_candidates", "source")
