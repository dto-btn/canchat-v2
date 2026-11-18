"""Copy Gemini Flash 2.5 model configuration to Gemini Flash 3.5.

Revision ID: b75e2a610f34
Revises: 8f3c4a1d2b6e
"""

import logging
import time
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b75e2a610f34"
down_revision: Union[str, None] = "8f3c4a1d2b6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")
OLD_MODEL_ID = "Gemini Flash 2.5"
NEW_MODEL_ID = "Gemini Flash 3.5"


def upgrade() -> None:
    connection = op.get_bind()
    model = sa.Table("model", sa.MetaData(), autoload_with=connection)

    if (
        connection.execute(
            sa.select(model.c.id).where(model.c.id == NEW_MODEL_ID)
        ).first()
        is not None
    ):
        log.info("Preserving existing %s configuration", NEW_MODEL_ID)
        return

    source = (
        connection.execute(sa.select(model).where(model.c.id == OLD_MODEL_ID))
        .mappings()
        .first()
    )
    if source is None:
        log.info("Skipping %s: no %s configuration exists", NEW_MODEL_ID, OLD_MODEL_ID)
        return

    values = dict(source)
    values["id"] = NEW_MODEL_ID
    for field in ("name", "name_fr"):
        if values[field] is not None:
            values[field] = values[field].replace("2.5", "3.5")
    if values["base_model_id"] == OLD_MODEL_ID:
        values["base_model_id"] = NEW_MODEL_ID
    values["created_at"] = values["updated_at"] = int(time.time())
    connection.execute(model.insert().values(**values))
    log.info("Copied %s configuration to %s", OLD_MODEL_ID, NEW_MODEL_ID)


def downgrade() -> None:
    log.info("Preserving %s configuration on downgrade", NEW_MODEL_ID)
