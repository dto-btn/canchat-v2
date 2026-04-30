"""add_chat_cleanup_indexes

Revision ID: 9503a87090c4
Revises: f47e8b9c1d23
Create Date: 2026-02-09 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9503a87090c4"
down_revision: Union[str, None] = "f47e8b9c1d23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add performance index for chat cleanup queries
    # This index optimizes the age-based cleanup query that filters by updated_at
    # Used by automated chat lifecycle management to efficiently find expired chats

    op.create_index("idx_chat_updated_at", "chat", ["updated_at"], unique=False)


def downgrade() -> None:
    # Remove the chat cleanup performance index
    op.drop_index("idx_chat_updated_at", table_name="chat")
