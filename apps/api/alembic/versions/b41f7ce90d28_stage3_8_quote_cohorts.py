"""stage3.8: quote cohorts — the group vector

A quote's own ``residence_category_id`` describes the client, and one category
cannot describe the group that is travelling. The client's confirmed rule is
that non-residents are charged in USD and residents in KES **on the same
quote**, with a separate per-person figure for each, which needs a row per
(residency, traveller type) rather than a column.

``pax_count`` stays as the shorthand for a group uniform in both respects, and
these rows take precedence when present, so there is one source of the headcount
instead of three that could disagree.

Revision ID: b41f7ce90d28
Revises: 8c1d2a9b4e37
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b41f7ce90d28"
down_revision: Union[str, Sequence[str], None] = "8c1d2a9b4e37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "quote_cohorts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("quote_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "residence_category_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("traveller_type", sa.String(length=10), nullable=False),
        sa.Column("headcount", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["quote_id"], ["quotes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["residence_category_id"],
            ["residence_categories.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("headcount > 0", name="ck_quote_cohort_headcount_positive"),
        sa.UniqueConstraint(
            "quote_id",
            "residence_category_id",
            "traveller_type",
            name="uq_quote_cohort",
        ),
    )


def downgrade() -> None:
    op.drop_table("quote_cohorts")
