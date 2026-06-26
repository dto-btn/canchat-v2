"""update models table
Revision ID: 27c53be7a4aa
Revises: 0020fee30b61
Create Date: 2026-06-26 13:36:46.001927
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import open_webui.internal.db

# revision identifiers, used by Alembic.
revision: str = "27c53be7a4aa"
down_revision: Union[str, None] = "0020fee30b61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add the 'name_fr' column to the 'models' table
    op.add_column("models", sa.Column("name_fr", sa.Text(), nullable=False))


def downgrade() -> None:
    # Remove the 'name_fr' column from the 'models' table in case of rollback
    op.drop_column("models", "name_fr")
