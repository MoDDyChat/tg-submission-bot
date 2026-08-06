"""Make created_at NOT NULL everywhere

The models have always declared ``created_at`` as NOT NULL, but the original
table definitions left it nullable and no later migration corrected it. Every
row gets its value from the ``now()`` server default, so the column has never
actually held NULL — this only closes the gap between the models and the
schema, and stops autogenerate from proposing the same change forever.

Revision ID: 0002_created_at_not_null
Revises: 0001_initial
Create Date: 2026-08-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_created_at_not_null"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "messages",
    "publications",
    "submission_media",
    "submissions",
    "tag_preset_sections",
    "tag_presets",
    "user_topics",
    "users",
)


def upgrade() -> None:
    for table in _TABLES:
        # Defensive: the column is never NULL in practice, but a row written
        # with an explicit NULL before this ran would abort the ALTER.
        op.execute(f"UPDATE {table} SET created_at = now() WHERE created_at IS NULL")
        op.alter_column(
            table,
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            existing_server_default=sa.text("now()"),
            nullable=False,
        )


def downgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table,
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            existing_server_default=sa.text("now()"),
            nullable=True,
        )
