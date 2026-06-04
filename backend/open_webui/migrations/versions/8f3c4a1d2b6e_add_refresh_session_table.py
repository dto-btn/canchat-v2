"""Add refresh session table

Revision ID: 8f3c4a1d2b6e
Revises: 5233b3d5b959
Create Date: 2026-05-08 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "8f3c4a1d2b6e"
down_revision = "5233b3d5b959"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "refresh_session",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True, unique=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("revoked_at", sa.BigInteger(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
    )

    op.create_index(
        "ix_refresh_session_user_id", "refresh_session", ["user_id"], unique=False
    )
    op.create_index(
        "ix_refresh_session_expires_at",
        "refresh_session",
        ["expires_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_refresh_session_expires_at", table_name="refresh_session")
    op.drop_index("ix_refresh_session_user_id", table_name="refresh_session")
    op.drop_table("refresh_session")
