"""stage3.9: quote option legs — a curated multi-destination package

An option stopped being "one hotel" when the client asked for 2 or 3
destinations in a single 7-30 day trip. A package is an ordered set of legs,
each a destination, a property, a per-leg meal plan and a date range.

``quote_options.accommodation_id`` stays as the single-leg shorthand and these
rows take precedence when present — the same precedence the group vector uses
over ``pax_count``, so one place decides what a package is.

Also drops ``uq_quote_option_accommodation``. It meant "do not offer the same
hotel twice", which is no longer expressible as a column pair: two curated
packages can share a property on one leg and differ on another. The intent
survives as a service check over whole leg sequences.

Revision ID: c72ba3d81f45
Revises: b41f7ce90d28
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c72ba3d81f45"
down_revision: Union[str, Sequence[str], None] = "b41f7ce90d28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UQ = "uq_quote_option_accommodation"


def upgrade() -> None:
    op.create_table(
        "quote_option_legs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("quote_option_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("destination_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("accommodation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "requested_meal_plan_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("check_in", sa.Date(), nullable=False),
        sa.Column("check_out", sa.Date(), nullable=False),
        sa.Column("room_type_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("meal_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rooms_required", sa.Integer(), nullable=True),
        sa.Column("meal_plan_fallback_from", sa.String(length=30), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["quote_option_id"], ["quote_options.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"], ["destinations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["accommodation_id"], ["accommodations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_meal_plan_id"], ["meal_plans.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["room_type_id"], ["room_types.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["meal_plan_id"], ["meal_plans.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("check_out > check_in", name="ck_quote_option_leg_dates"),
        sa.CheckConstraint("sequence > 0", name="ck_quote_option_leg_sequence"),
        sa.UniqueConstraint(
            "quote_option_id", "sequence", name="uq_quote_option_leg_seq"
        ),
    )
    op.create_index(
        "ix_quote_option_legs_quote_option_id",
        "quote_option_legs",
        ["quote_option_id"],
    )
    op.drop_constraint(_UQ, "quote_options", type_="unique")


def downgrade() -> None:
    # Restoring the constraint can fail on data that is legal under packages —
    # two options sharing a property — and that is the correct outcome: it says
    # the data has outgrown the old rule rather than deleting an option to fit.
    op.create_unique_constraint(_UQ, "quote_options", ["quote_id", "accommodation_id"])
    op.drop_index("ix_quote_option_legs_quote_option_id", table_name="quote_option_legs")
    op.drop_table("quote_option_legs")
