"""group observability: author notes, dead publications, direct messages

Adds ``users.moderator_note`` (moderator's note about the author),
``publications.dead_at`` (set when a scheduled publication is recognised as
dead — overdue more than 24h with no APScheduler job; NULL means alive),
makes ``messages.submission_id`` nullable and adds ``messages.target_user_id``
identifying the direct moderator ↔ viewer conversation thread without a
submission (both directions).

Revision ID: 0004_group_observability
Revises: 0003_moderator_invites
Create Date: 2026-08-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004_group_observability'
down_revision: Union[str, None] = '0003_moderator_invites'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('moderator_note', sa.Text(), nullable=True))
    op.add_column('publications', sa.Column('dead_at', sa.DateTime(timezone=True), nullable=True))
    op.alter_column('messages', 'submission_id', existing_type=sa.BigInteger(), existing_nullable=False, nullable=True)
    op.add_column('messages', sa.Column('target_user_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key('fk_messages_target_user_id_users', 'messages', 'users', ['target_user_id'], ['id'], ondelete='CASCADE')
    op.create_index('idx_messages_target_user_id', 'messages', ['target_user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_messages_target_user_id', table_name='messages')
    op.drop_constraint('fk_messages_target_user_id_users', 'messages', type_='foreignkey')
    op.drop_column('messages', 'target_user_id')
    # Downgrade data loss: messages without a submission are deleted.
    op.execute("DELETE FROM messages WHERE submission_id IS NULL")
    op.alter_column('messages', 'submission_id', existing_type=sa.BigInteger(), existing_nullable=True, nullable=False)
    op.drop_column('publications', 'dead_at')
    op.drop_column('users', 'moderator_note')
