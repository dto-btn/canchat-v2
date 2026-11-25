import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.fixture
def migration_db():
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/b75e2a610f34_copy_gemini_flash_model_info.py"
    )
    spec = importlib.util.spec_from_file_location("gemini_model_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    model = sa.Table(
        "model",
        sa.MetaData(),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("user_id", sa.Text),
        sa.Column("base_model_id", sa.Text),
        sa.Column("name", sa.Text),
        sa.Column("name_fr", sa.Text),
        sa.Column("params", sa.Text),
        sa.Column("meta", sa.Text),
        sa.Column("access_control", sa.JSON),
        sa.Column("is_active", sa.Boolean),
        sa.Column("created_at", sa.BigInteger),
        sa.Column("updated_at", sa.BigInteger),
    )
    model.metadata.create_all(engine)
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        yield migration, connection, model
    engine.dispose()


def old_model():
    return {
        "id": "Gemini Flash 2.5",
        "user_id": "existing-owner",
        "base_model_id": None,
        "name": "Gemini Flash 2.5",
        "name_fr": "Gemini Flash 2.5",
        "params": json.dumps({"system": "Approved system prompt", "temperature": 0.4}),
        "meta": json.dumps(
            {
                "description": "Approved description",
                "description_fr": "Description approuv\u00e9e",
                "tags": [{"name": "Vision"}],
                "tags_fr": [{"name": "Vision"}],
                "capabilities": {"vision": True},
            }
        ),
        "access_control": {"read": {"group_ids": ["approved-group"]}},
        "is_active": True,
        "created_at": 1,
        "updated_at": 2,
    }


@pytest.mark.parametrize(
    "access_control", [None, {}, {"read": {"group_ids": ["group"]}}]
)
@pytest.mark.parametrize("is_active", [True, False])
def test_copies_configuration_without_changing_source(
    migration_db, access_control, is_active
):
    migration, connection, model = migration_db
    source = old_model()
    source.update(access_control=access_control, is_active=is_active)
    connection.execute(model.insert().values(**source))

    migration.upgrade()
    migration.upgrade()
    rows = {
        row["id"]: dict(row) for row in connection.execute(sa.select(model)).mappings()
    }
    assert len(rows) == 2
    assert rows[migration.OLD_MODEL_ID] == source
    copied = rows[migration.NEW_MODEL_ID]
    for field in (
        "user_id",
        "base_model_id",
        "params",
        "meta",
        "access_control",
        "is_active",
    ):
        assert copied[field] == source[field]
    assert copied["name"] == copied["name_fr"] == migration.NEW_MODEL_ID
    assert copied["created_at"] == copied["updated_at"] > 2

    migration.downgrade()
    assert (
        connection.execute(sa.select(sa.func.count()).select_from(model)).scalar() == 2
    )


def test_preserves_existing_target(migration_db):
    migration, connection, model = migration_db
    source = old_model()
    target = {**source, "id": migration.NEW_MODEL_ID, "name": "Admin custom name"}
    connection.execute(model.insert(), [source, target])
    migration.upgrade()
    saved = (
        connection.execute(sa.select(model).where(model.c.id == migration.NEW_MODEL_ID))
        .mappings()
        .one()
    )
    assert dict(saved) == target


def test_missing_source_leaves_database_unchanged(migration_db):
    migration, connection, model = migration_db
    unrelated = {**old_model(), "id": "Other model"}
    connection.execute(model.insert().values(**unrelated))
    migration.upgrade()
    assert [dict(row) for row in connection.execute(sa.select(model)).mappings()] == [
        unrelated
    ]


def test_updates_explicit_old_base_model_reference(migration_db):
    migration, connection, model = migration_db
    source = old_model()
    source.update(base_model_id=migration.OLD_MODEL_ID, name_fr=None)
    connection.execute(model.insert().values(**source))
    migration.upgrade()
    saved = (
        connection.execute(sa.select(model).where(model.c.id == migration.NEW_MODEL_ID))
        .mappings()
        .one()
    )
    assert saved["base_model_id"] == migration.NEW_MODEL_ID
    assert saved["name_fr"] is None
