"""add mcp_tool to message_metrics

Revision ID: 0020fee30b61
Revises: 5a92423d379e
Create Date: 2026-03-03 14:19:17.048218

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0020fee30b61"
down_revision: Union[str, None] = "5a92423d379e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add mcp_tool column to message_metrics table (nullable for backwards compat)
    # Stores the MCP toggle/process name (e.g. "time_server", "news_server",
    # "mpo_sharepoint_server", "pmo_sharepoint_server") when the metric row
    # originates from a CrewAI MCP call.  NULL for standard (non-MCP) calls.
    op.add_column(
        "message_metrics",
        sa.Column("mcp_tool", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("message_metrics", "mcp_tool")
