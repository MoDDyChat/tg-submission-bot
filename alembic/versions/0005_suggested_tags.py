"""suggested_tags column

Adds ``submissions.suggested_tags`` (JSON, nullable) — tags the author typed
in the caption, extracted at intake and fuzzy-matched against tag_presets.
NULL means parsing was not performed / nothing found; old rows are not
backfilled.

Revision ID: 0005_suggested_tags
Revises: 0004_group_observability
Create Date: 2026-08-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005_suggested_tags'
down_revision: Union[str, None] = '0004_group_observability'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('submissions', sa.Column('suggested_tags', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('submissions', 'suggested_tags')
