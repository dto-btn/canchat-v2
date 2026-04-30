"""remove memory

Revision ID: 5a92423d379e
Revises: 9503a87090c4
Create Date: 2026-02-26 12:41:17.844406

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a92423d379e"
down_revision: Union[str, None] = "9503a87090c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("memory")


def downgrade() -> None:
    pass
