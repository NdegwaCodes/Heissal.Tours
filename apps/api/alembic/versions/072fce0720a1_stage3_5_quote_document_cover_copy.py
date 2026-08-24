"""stage3_5 quote document cover copy

Per-quote cover title and subtitle for the quotation document.

Both nullable, with the renderer falling back to a title derived from the
destination, so an older quote is never blank-covered. Per quote rather than per
destination because the copy describes the trip and not the place: the reference
proposal opens on "Corporate Coastal Retreat", which is not something every Diani
quote would share.


Revision ID: 072fce0720a1
Revises: 15c4d3d4af6b
Create Date: 2026-08-25 01:01:31.517751
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '072fce0720a1'
down_revision: Union[str, Sequence[str], None] = '15c4d3d4af6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('quotes', sa.Column('document_title', sa.String(length=160), nullable=True))
    op.add_column('quotes', sa.Column('document_subtitle', sa.String(length=240), nullable=True))


def downgrade() -> None:
    op.drop_column('quotes', 'document_subtitle')
    op.drop_column('quotes', 'document_title')
