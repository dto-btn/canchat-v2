"""merge refresh session head

Revision ID: a4d9aa1d5d7e
Revises: 0020fee30b61, 8f3c4a1d2b6e
Create Date: 2026-05-28 12:05:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "a4d9aa1d5d7e"
down_revision: Union[str, Sequence[str], None] = (
    "0020fee30b61",
    "8f3c4a1d2b6e",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
