"""NFM-3364: offline verification of migration 057 (create kg_*_types tables).

Tests cover:
* Chain integrity: 057 chains off 053; 055 chains off 057; head is 056.
* Schema: both tables are created with the expected columns, types, and
  unique constraints matching the ORM models.
* Idempotency: tables are created with CREATE TABLE IF NOT EXISTS so the
  migration is safe on staging (where the tables already exist via
  ``Base.metadata.create_all()``).
* Downgrade: drops both tables.
"""

from __future__ import annotations

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

REVISION = "057_create_kg_entity_and_relation_type_tables"
DOWN_REVISION = "053_align_extraction_gap_with_adr_nfm_2675"
MIGRATION_PATH = f"migrations/versions/{REVISION}.py"

HEAD_REVISION = "059_add_adr009_reconcile_audit_log"
DOWNSTREAM_REVISION = "055_add_ontology_version_fk_to_type_tables"


@pytest.fixture(scope="module")
def script_directory() -> ScriptDirectory:
    """Load the Alembic script directory for offline chain analysis."""
    return ScriptDirectory.from_config(Config("alembic.ini"))


@pytest.fixture(scope="module")
def migration_source() -> str:
    """Read the migration source file as text."""
    with open(MIGRATION_PATH) as handle:
        return handle.read()


class TestMigrationChain:
    """057 chains correctly into the migration graph."""

    def test_revision_loadable(self, script_directory: ScriptDirectory) -> None:
        rev = script_directory.get_revision(REVISION)
        assert rev is not None, f"Migration {REVISION!r} not registered"
        assert rev.revision == REVISION

    def test_down_revision_is_053(self, script_directory: ScriptDirectory) -> None:
        """057 chains off 053 so prod (currently at 053) picks it up."""
        rev = script_directory.get_revision(REVISION)
        assert rev is not None
        assert rev.down_revision == DOWN_REVISION, (
            f"Expected down_revision={DOWN_REVISION!r}, "
            f"got {rev.down_revision!r}"
        )

    def test_single_head(self, script_directory: ScriptDirectory) -> None:
        """Exactly one head after adding 057 (head remains 056)."""
        heads = script_directory.get_heads()
        assert len(heads) == 1, (
            f"Expected exactly 1 alembic head, got {len(heads)}: {heads!r}"
        )
        assert heads[0] == HEAD_REVISION, (
            f"Expected head {HEAD_REVISION!r}, got {heads[0]!r}"
        )

    def test_055_chains_off_057(self, script_directory: ScriptDirectory) -> None:
        """055 must now chain off 057 (NFM-3364 rewiring)."""
        rev = script_directory.get_revision(DOWNSTREAM_REVISION)
        assert rev is not None
        assert rev.down_revision == REVISION, (
            f"Expected 055.down_revision={REVISION!r}, "
            f"got {rev.down_revision!r}"
        )

    def test_057_is_ancestor_of_head(self, script_directory: ScriptDirectory) -> None:
        heads = script_directory.get_heads()
        ancestors = {
            rev.revision
            for rev in script_directory.iterate_revisions(heads[0], "base")
        }
        assert REVISION in ancestors, (
            f"{REVISION!r} is not an ancestor of head {heads[0]!r}"
        )

    def test_no_duplicate_revisions(self, script_directory: ScriptDirectory) -> None:
        revisions = [rev.revision for rev in script_directory.walk_revisions()]
        assert len(revisions) == len(set(revisions)), "Duplicate revision ids found"


class TestSchemaChanges:
    """057 creates the expected DDL for both tables."""

    def test_creates_kg_entity_types(self, migration_source: str) -> None:
        assert "CREATE TABLE IF NOT EXISTS kg_entity_types" in migration_source

    def test_creates_kg_relation_types(self, migration_source: str) -> None:
        assert "CREATE TABLE IF NOT EXISTS kg_relation_types" in migration_source

    def test_entity_types_has_id_pk(self, migration_source: str) -> None:
        # Isolate the entity_types DDL block by splitting on the unique
        # ``CREATE TABLE IF NOT EXISTS`` marker -- the docstring mentions
        # both table names so a naive split picks up prose instead.
        block = migration_source.split(
            "CREATE TABLE IF NOT EXISTS kg_entity_types", 1
        )[1].split("CREATE TABLE IF NOT EXISTS kg_relation_types", 1)[0]
        assert "id UUID PRIMARY KEY" in block

    def test_entity_types_has_name_unique(self, migration_source: str) -> None:
        assert "CONSTRAINT uq_kg_entity_types_name UNIQUE (name)" in (
            migration_source
        )

    def test_relation_types_has_name_unique(self, migration_source: str) -> None:
        assert "CONSTRAINT uq_kg_relation_types_name UNIQUE (name)" in (
            migration_source
        )

    def test_entity_types_has_required_properties_array(self, migration_source: str) -> None:
        """required_properties is the JSONArray — TEXT[] on PostgreSQL."""
        block = migration_source.split(
            "CREATE TABLE IF NOT EXISTS kg_entity_types", 1
        )[1].split("CREATE TABLE IF NOT EXISTS kg_relation_types", 1)[0]
        assert "required_properties TEXT[]" in block

    def test_relation_types_has_jsonb_schema(self, migration_source: str) -> None:
        """properties_schema is the CompatJSONB — JSONB on PostgreSQL."""
        block = migration_source.split(
            "CREATE TABLE IF NOT EXISTS kg_relation_types", 1
        )[1]
        assert "properties_schema JSONB" in block

    def test_entity_types_has_timestamps(self, migration_source: str) -> None:
        block = migration_source.split(
            "CREATE TABLE IF NOT EXISTS kg_entity_types", 1
        )[1].split("CREATE TABLE IF NOT EXISTS kg_relation_types", 1)[0]
        assert "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()" in block
        assert "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()" in block

    def test_relation_types_has_timestamps(self, migration_source: str) -> None:
        block = migration_source.split(
            "CREATE TABLE IF NOT EXISTS kg_relation_types", 1
        )[1]
        assert "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()" in block
        assert "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()" in block

    def test_no_ontology_version_id_column(self, migration_source: str) -> None:
        """057 must NOT add ontology_version_id — that's 055's job.

        The migration docstring mentions ``ontology_version_id`` in prose,
        so we restrict the assertion to the upgrade() function body where
        055's column would actually appear.
        """
        upgrade_body = migration_source.split("def upgrade()", 1)[1].split(
            "def downgrade()", 1
        )[0]
        assert "ontology_version_id" not in upgrade_body


class TestIdempotency:
    """Migration is safe on staging where tables may already exist."""

    def test_uses_create_table_if_not_exists(self, migration_source: str) -> None:
        assert "CREATE TABLE IF NOT EXISTS" in migration_source

    def test_uses_create_unique_index_if_not_exists(self, migration_source: str) -> None:
        assert "CREATE UNIQUE INDEX IF NOT EXISTS" in migration_source


class TestDowngrade:
    """Downgrade drops both tables."""

    def test_drops_kg_entity_types(self, migration_source: str) -> None:
        downgrade = migration_source.split("def downgrade", 1)[1]
        assert "DROP TABLE IF EXISTS kg_entity_types" in downgrade

    def test_drops_kg_relation_types(self, migration_source: str) -> None:
        downgrade = migration_source.split("def downgrade", 1)[1]
        assert "DROP TABLE IF EXISTS kg_relation_types" in downgrade

    def test_uses_cascade_drop(self, migration_source: str) -> None:
        """CASCADE ensures dependents (none today, but future-proof) drop too."""
        downgrade = migration_source.split("def downgrade", 1)[1]
        assert "DROP TABLE IF EXISTS kg_entity_types CASCADE" in downgrade
        assert "DROP TABLE IF EXISTS kg_relation_types CASCADE" in downgrade
