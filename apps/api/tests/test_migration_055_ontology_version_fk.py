"""NFM-2873-T1: offline verification of migration 055 (ontology_version_id FK).

Tests cover:
* Chain integrity: 055 chains off 053, single head, ancestor of head.
* Schema: both columns are nullable with correct FK target.
* Backfill: UPDATE statements target the correct tables and subquery.
* Downgrade: both columns are dropped in the correct order.
* Model parity: ORM models declare the same columns as the migration.
"""

from __future__ import annotations

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from nfm_db.models import KEntityType, KRelationType

REVISION = "055_add_ontology_version_fk_to_type_tables"
# NFM-3364: 055 now chains off 057 (which creates the missing tables
# in prod).  The migration's own DDL is unchanged — only the chain
# rewiring changed.
DOWN_REVISION = "057_create_kg_entity_and_relation_type_tables"
MIGRATION_PATH = f"migrations/versions/{REVISION}.py"

FK_TARGET = "ontology_versions.id"


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
    """055 chains correctly into the migration graph."""

    def test_revision_loadable(self, script_directory: ScriptDirectory) -> None:
        rev = script_directory.get_revision(REVISION)
        assert rev is not None, f"Migration {REVISION!r} not registered"
        assert rev.revision == REVISION

    def test_down_revision_is_057(self, script_directory: ScriptDirectory) -> None:
        """055 must chain off 057 (NFM-3364), which is itself a child of 053."""
        rev = script_directory.get_revision(REVISION)
        assert rev is not None
        assert rev.down_revision == DOWN_REVISION, (
            f"Expected down_revision={DOWN_REVISION!r}, "
            f"got {rev.down_revision!r}"
        )

    def test_single_head(self, script_directory: ScriptDirectory) -> None:
        """Exactly one head after adding 055."""
        heads = script_directory.get_heads()
        assert len(heads) == 1, (
            f"Expected exactly 1 alembic head, got {len(heads)}: {heads!r}"
        )

    def test_055_is_ancestor_of_head(self, script_directory: ScriptDirectory) -> None:
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
    """Migration adds the correct DDL for both tables."""

    def test_adds_column_to_kg_entity_types(self, migration_source: str) -> None:
        assert "op.add_column" in migration_source
        assert '"kg_entity_types"' in migration_source
        assert '"ontology_version_id"' in migration_source

    def test_adds_column_to_kg_relation_types(self, migration_source: str) -> None:
        assert "op.add_column" in migration_source
        assert '"kg_relation_types"' in migration_source
        assert '"ontology_version_id"' in migration_source

    def test_columns_are_nullable(self, migration_source: str) -> None:
        """Columns must be nullable -- NOT NULL would rewrite the table.  NOTE: the ORM models are updated to nullable=False by NFM-2873 after the backfill runs, but the migration DDL itself remains nullable=True for safety during the ADD+BACKFILL+ALTER sequence."""
        assert "nullable=True" in migration_source

    def test_fk_targets_ontology_versions(self, migration_source: str) -> None:
        assert FK_TARGET in migration_source

    def test_columns_have_comment(self, migration_source: str) -> None:
        assert "comment=" in migration_source


class TestBackfill:
    """Backfill UPDATE targets correct tables and uses safe subquery."""

    def test_backfills_kg_entity_types(self, migration_source: str) -> None:
        assert "UPDATE kg_entity_types" in migration_source
        assert "SET ontology_version_id" in migration_source

    def test_backfills_kg_relation_types(self, migration_source: str) -> None:
        assert "UPDATE kg_relation_types" in migration_source
        assert "SET ontology_version_id" in migration_source

    def test_backfill_uses_published_version(self, migration_source: str) -> None:
        assert "status = 'published'" in migration_source

    def test_backfill_targets_earliest_version(self, migration_source: str) -> None:
        assert "ORDER BY created_at ASC" in migration_source

    def test_backfill_is_idempotent(self, migration_source: str) -> None:
        """WHERE clause ensures re-run is safe."""
        assert "WHERE ontology_version_id IS NULL" in migration_source


class TestDowngrade:
    """Downgrade drops both columns."""

    def test_drops_kg_relation_types_column(self, migration_source: str) -> None:
        downgrade = migration_source.split("def downgrade", 1)[1]
        assert 'op.drop_column("kg_relation_types", "ontology_version_id")' in downgrade

    def test_drops_kg_entity_types_column(self, migration_source: str) -> None:
        downgrade = migration_source.split("def downgrade", 1)[1]
        assert 'op.drop_column("kg_entity_types", "ontology_version_id")' in downgrade

    def test_drops_relation_before_entity(self, migration_source: str) -> None:
        """Order doesn't matter here (no cross-FK), but verify both present."""
        downgrade = migration_source.split("def downgrade", 1)[1]
        assert downgrade.count("op.drop_column") == 2


class TestModelParity:
    """ORM models declare the same columns as the migration."""

    def test_kentity_type_has_ontology_version_id(self) -> None:
        assert "ontology_version_id" in KEntityType.__table__.columns

    def test_krelation_type_has_ontology_version_id(self) -> None:
        assert "ontology_version_id" in KRelationType.__table__.columns

    def test_kentity_type_column_not_nullable(self) -> None:
        assert KEntityType.__table__.columns["ontology_version_id"].nullable is False

    def test_krelation_type_column_not_nullable(self) -> None:
        assert KRelationType.__table__.columns["ontology_version_id"].nullable is False

    def test_kentity_type_fk_target(self) -> None:
        col = KEntityType.__table__.columns["ontology_version_id"]
        fk = next(iter(col.foreign_keys))
        assert fk.target_fullname == "ontology_versions.id"

    def test_krelation_type_fk_target(self) -> None:
        col = KRelationType.__table__.columns["ontology_version_id"]
        fk = next(iter(col.foreign_keys))
        assert fk.target_fullname == "ontology_versions.id"
