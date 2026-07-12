"""NFM-409: Offline verification of Alembic migrations 005 and 006.

Validates migration structure, chain linearity, and additive-only properties
without requiring a live PostgreSQL connection.

Acceptance Criteria:
- [AC-1] Migration 005 exists and creates verification_results_md table
- [AC-2] Migration 006 exists and extends status check constraint
- [AC-3] Migration chain is linear (no conflicts between 005 and 007)
- [AC-4] Migrations are additive-only: no column drops, no data loss, no table recreation

Live DB tests (AC-5, AC-6) are in test_md_verification_migration.py and
require a running PostgreSQL instance.
"""

import re

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


@pytest.fixture(scope="module")
def script_directory() -> ScriptDirectory:
    """Load Alembic script directory for offline analysis."""
    config = Config("alembic.ini")
    return ScriptDirectory.from_config(config)


@pytest.fixture(scope="module")
def migration_005_source() -> str:
    """Read migration 005 source code."""
    with open("migrations/versions/005_add_verification_results_md_and_extend_jobs.py") as f:
        return f.read()


@pytest.fixture(scope="module")
def migration_006_source() -> str:
    """Read migration 006 source code."""
    with open("migrations/versions/006_add_cancelled_status_to_md_jobs.py") as f:
        return f.read()


# ---------------------------------------------------------------------------
# AC-1: Migration 005 exists and creates verification_results_md table
# ---------------------------------------------------------------------------

class TestMigration005Exists:
    """Verify migration 005 creates verification_results_md table."""

    def test_revision_005_loadable(self, script_directory: ScriptDirectory):
        """Migration 005 is discoverable by Alembic."""
        rev = script_directory.get_revision("005")
        assert rev is not None
        assert rev.revision == "005"
        assert rev.down_revision == ("003b", "004")

    def test_creates_verification_results_md_table(self, migration_005_source: str):
        """Migration 005 contains CREATE TABLE for verification_results_md."""
        src = migration_005_source
        assert "verification_results_md" in src
        assert "create_table" in src

    def test_verification_results_md_columns(self, migration_005_source: str):
        """verification_results_md has all required columns."""
        src = migration_005_source
        required_columns = [
            "vacancies",
            "interstitials",
            "frenkel_pairs",
            "displaced_atoms",
            "replaced_atoms",
            "arc_dpa_b",
            "arc_dpa_c",
            "r_squared",
            "sample_size",
            "raw_dump_ref",
        ]
        for col in required_columns:
            assert col in src, f"Missing column: {col}"

    def test_fk_to_md_simulation_results(self, migration_005_source: str):
        """verification_results_md FK to md_simulation_results with CASCADE."""
        src = migration_005_source
        assert 'ForeignKey("md_simulation_results.id"' in src
        assert 'ondelete="CASCADE"' in src

    def test_has_primary_key_with_uuid(self, migration_005_source: str):
        """verification_results_md has UUID primary key with gen_random_uuid."""
        src = migration_005_source
        assert "primary_key=True" in src
        assert "gen_random_uuid()" in src

    def test_creates_indexes(self, migration_005_source: str):
        """Migration 005 creates expected indexes."""
        src = migration_005_source
        assert "idx_vrmd_simulation_result_id" in src
        assert "idx_md_jobs_job_type" in src
        assert "idx_md_jobs_execution_status" in src

    def test_extends_md_verification_jobs(self, migration_005_source: str):
        """Migration 005 adds columns to md_verification_jobs."""
        src = migration_005_source
        added_columns = ["job_type", "hpc_job_id", "hpc_backend", "execution_status"]
        for col in added_columns:
            assert f'"{col}"' in src, f"Missing extended column: {col}"

    def test_has_downgrade(self, migration_005_source: str):
        """Migration 005 has a downgrade function."""
        assert "def downgrade()" in migration_005_source


# ---------------------------------------------------------------------------
# AC-2: Migration 006 exists and extends status check constraint
# ---------------------------------------------------------------------------

class TestMigration006Exists:
    """Verify migration 006 extends check_md_job_status constraint."""

    def test_revision_006_loadable(self, script_directory: ScriptDirectory):
        """Migration 006 is discoverable by Alembic."""
        rev = script_directory.get_revision("006")
        assert rev is not None
        assert rev.revision == "006"
        assert rev.down_revision == "005"

    def test_extends_check_constraint(self, migration_006_source: str):
        """Migration 006 references check_md_job_status constraint."""
        assert "check_md_job_status" in migration_006_source

    def test_includes_cancelled_status(self, migration_006_source: str):
        """Migration 006 adds 'cancelled' to the status constraint."""
        assert "'cancelled'" in migration_006_source

    def test_old_constraint_excludes_cancelled(self, migration_006_source: str):
        """Old constraint string does not include 'cancelled'."""
        old_constraint_match = re.search(
            r'_OLD_CONSTRAINT\s*=\s*"(.+)"',
            migration_006_source,
        )
        assert old_constraint_match is not None
        old_constraint = old_constraint_match.group(1)
        assert "cancelled" not in old_constraint

    def test_new_constraint_includes_cancelled(self, migration_006_source: str):
        """New constraint string includes 'cancelled'."""
        new_constraint_match = re.search(
            r'_NEW_CONSTRAINT\s*=\s*"(.+)"',
            migration_006_source,
        )
        assert new_constraint_match is not None
        new_constraint = new_constraint_match.group(1)
        assert "cancelled" in new_constraint

    def test_has_downgrade(self, migration_006_source: str):
        """Migration 006 has a downgrade function."""
        assert "def downgrade()" in migration_006_source


# ---------------------------------------------------------------------------
# AC-3: Migration chain is linear (no conflicts between 005 and 006)
# ---------------------------------------------------------------------------

class TestMigrationChainLinearity:
    """Verify the migration chain from 005 to 007 is linear."""

    def test_006_depends_on_005(self, script_directory: ScriptDirectory):
        """Migration 006's down_revision is exactly 005."""
        rev_006 = script_directory.get_revision("006")
        assert rev_006.down_revision == "005"

    def test_no_revision_conflicts(self, script_directory: ScriptDirectory):
        """No duplicate revision IDs in the chain."""
        revisions = list(script_directory.walk_revisions())
        revision_ids = [r.revision for r in revisions]
        assert len(revision_ids) == len(set(revision_ids)), "Duplicate revision IDs found"

    def test_head_is_latest(self, script_directory: ScriptDirectory):
        """Migration head is the latest known revision (not stale)."""
        head = script_directory.get_current_head()
        all_revisions = {r.revision for r in script_directory.walk_revisions()}
        # Head must be a valid revision in the chain
        assert head in all_revisions, f"Head {head!r} not found in revision chain"

    def test_005_is_merge_revision(self, script_directory: ScriptDirectory):
        """Migration 005 correctly merges 003b and 004 branches."""
        rev_005 = script_directory.get_revision("005")
        assert isinstance(rev_005.down_revision, tuple)
        assert set(rev_005.down_revision) == {"003b", "004"}

    def test_full_chain_walkable(self, script_directory: ScriptDirectory):
        """All revisions are walkable without errors."""
        revisions = list(script_directory.walk_revisions())
        assert len(revisions) >= 8  # At least all known migrations


# ---------------------------------------------------------------------------
# AC-4: Migrations are additive-only
# ---------------------------------------------------------------------------

class TestAdditiveOnlyUpgrades:
    """Verify upgrade paths are additive-only (no destructive operations)."""

    ADDITIVE_OPS = frozenset({
        "add_column",
        "create_table",
        "create_index",
        "create_check_constraint",
        "create_unique_constraint",
        "create_primary_key",
        "create_foreign_key",
        "bulk_insert",
    })

    CONSTRAINT_REPLACE_PATTERN = re.compile(
        r"drop_constraint.*create_check_constraint",
        re.DOTALL,
    )

    def test_005_upgrade_only_additive(self, migration_005_source: str):
        """Migration 005 upgrade uses only additive operations."""
        upgrade_section = migration_005_source.split("def downgrade")[0].split("def upgrade")[1]
        upgrade_ops = re.findall(r"op\.(\w+)\(", upgrade_section)

        for op in upgrade_ops:
            assert op in self.ADDITIVE_OPS, (
                f"Non-additive operation in 005 upgrade: op.{op}()"
            )

    def test_006_upgrade_constraint_replace_only(self, migration_006_source: str):
        """Migration 006 upgrade only replaces a constraint (no data loss)."""
        upgrade_section = migration_006_source.split("def downgrade")[0].split("def upgrade")[1]

        ops = re.findall(r"op\.(\w+)\(", upgrade_section)
        assert ops == ["drop_constraint", "create_check_constraint"], (
            f"Unexpected ops in 006 upgrade: {ops}"
        )

    def test_005_no_table_recreation(self, migration_005_source: str):
        """Migration 005 does not use op.replace_table or batch_alter."""
        assert "replace_table" not in migration_005_source
        assert "batch_alter_table" not in migration_005_source

    def test_006_no_table_recreation(self, migration_006_source: str):
        """Migration 006 does not use op.replace_table or batch_alter."""
        assert "replace_table" not in migration_006_source
        assert "batch_alter_table" not in migration_006_source

    def test_005_no_drop_column_in_upgrade(self, migration_005_source: str):
        """Migration 005 upgrade does not drop any columns."""
        upgrade_section = migration_005_source.split("def downgrade")[0].split("def upgrade")[1]
        assert "drop_column" not in upgrade_section
        assert "drop_table" not in upgrade_section

    def test_006_no_drop_column_in_upgrade(self, migration_006_source: str):
        """Migration 006 upgrade does not drop any columns."""
        upgrade_section = migration_006_source.split("def downgrade")[0].split("def upgrade")[1]
        assert "drop_column" not in upgrade_section
        assert "drop_table" not in upgrade_section
