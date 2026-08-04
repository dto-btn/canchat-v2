"""Add Terms Table

Revision ID: 9bf11256511d
Revises: 27c53be7a4aa
Create Date: 2026-06-15 14:11:55.407893

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "9bf11256511d"
down_revision: Union[str, None] = "27c53be7a4aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "terms",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True, unique=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("accepted_at", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("terms")
