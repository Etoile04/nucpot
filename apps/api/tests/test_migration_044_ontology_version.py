"""NFM-2579: offline verification of migration 044 (ontology_versions).

These tests lock in the three defects found in code review of the first
044 implementation:

* [AC-1] 044 chains off **043**, not 042.  Both 043 (T1) and 044 (T2) were
  originally forked off 042, which left the graph with two heads and broke
  ``alembic upgrade head`` at deploy time.
* [AC-2] The seed does not resolve ``created_by`` against an unseeded user.
  ``ontology_versions.created_by`` is NOT NULL; the original migration
  selected the system user's id from an empty ``users`` table, yielding NULL
  and aborting the migration on a cold database.  044 now creates the
  internal account first.
* [AC-3] The ``version`` unique constraint is declared identically in the
  migration and the ORM model, so ``alembic revision --autogenerate``
  produces no spurious diff.

Offline by design (mirrors ``test_migration_035_multimodal_flags.py``): the
chain and DDL invariants are checked without a live PostgreSQL.  Applying
the migration against real PG is covered by the deploy smoke gate.
"""

from __future__ import annotations

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from nfm_db.models import OntologyVersion

REVISION = "044_add_ontology_version"
DOWN_REVISION = "043_add_domain_expert_role"
MIGRATION_PATH = f"migrations/versions/{REVISION}.py"

UNIQUE_CONSTRAINT_NAME = "uq_ontology_versions_version"


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
    """AC-1: 044 is registered and linearises the 042/043 fork."""

    def test_revision_loadable(self, script_directory: ScriptDirectory) -> None:
        rev = script_directory.get_revision(REVISION)
        assert rev is not None, f"Migration {REVISION!r} not registered"
        assert rev.revision == REVISION

    def test_down_revision_is_043(self, script_directory: ScriptDirectory) -> None:
        """044 must chain off 043, not 042.

        043 (T1, domain expert role) already chains off 042.  Forking 044 off
        042 as well produces two heads.
        """
        rev = script_directory.get_revision(REVISION)
        assert rev is not None
        assert rev.down_revision == DOWN_REVISION, (
            f"Expected down_revision={DOWN_REVISION!r}, got {rev.down_revision!r} — "
            f"forking 044 off 042 alongside 043 re-creates the two-head split"
        )

    def test_single_head(self, script_directory: ScriptDirectory) -> None:
        """Exactly one head, or `alembic upgrade head` fails at deploy."""
        heads = script_directory.get_heads()
        assert len(heads) == 1, (
            f"Expected exactly 1 alembic head, got {len(heads)}: {heads!r}. "
            f"A forked graph breaks `alembic upgrade head`."
        )

    def test_044_is_ancestor_of_head(self, script_directory: ScriptDirectory) -> None:
        heads = script_directory.get_heads()
        ancestors = {
            rev.revision for rev in script_directory.iterate_revisions(heads[0], "base")
        }
        assert REVISION in ancestors, (
            f"{REVISION!r} is not an ancestor of head {heads[0]!r} — "
            f"the ontology-version chain was detached from the graph"
        )

    def test_no_duplicate_revisions(self, script_directory: ScriptDirectory) -> None:
        revisions = [rev.revision for rev in script_directory.walk_revisions()]
        assert len(revisions) == len(set(revisions)), "Duplicate revision ids found"


class TestSeedAuthorIsCreated:
    """AC-2: the seed cannot resolve created_by against an empty users table."""

    def test_creates_system_user_before_seeding(self, migration_source: str) -> None:
        """The migration inserts the internal author itself.

        Without this, `created_by` resolves to NULL on a cold database and the
        NOT NULL constraint aborts the whole migration.
        """
        assert "INSERT INTO users" in migration_source, (
            "Migration must create the internal system user before seeding "
            "ontology_versions — created_by is NOT NULL and no upstream "
            "migration seeds a user"
        )

    def test_system_user_insert_is_idempotent(self, migration_source: str) -> None:
        """Re-running against a DB that already has the user must not fail."""
        users_stmt = migration_source.split("INSERT INTO users", 1)[1]
        users_stmt = users_stmt.split('"""', 1)[0]
        assert "ON CONFLICT DO NOTHING" in users_stmt, (
            "users seed must be ON CONFLICT DO NOTHING — username and email "
            "both carry unique indexes"
        )

    def test_system_user_cannot_authenticate(self, migration_source: str) -> None:
        """The seeded account is inert: unusable hash and inactive."""
        users_stmt = migration_source.split("INSERT INTO users", 1)[1]
        users_stmt = users_stmt.split('"""', 1)[0]
        assert "'!'" in users_stmt, (
            "system user must use an unusable password hash ('!'), which no "
            "bcrypt verify can match"
        )
        assert "false" in users_stmt, "system user must be created with is_active=false"

    def test_seed_selects_author_from_users(self, migration_source: str) -> None:
        """The ontology seed sources created_by from the users row it created."""
        seed = migration_source.split("INSERT INTO ontology_versions", 1)[1]
        assert "FROM users" in seed, (
            "seed must resolve created_by from the users table so the FK is valid"
        )

    def test_seed_is_idempotent(self, migration_source: str) -> None:
        seed = migration_source.split("INSERT INTO ontology_versions", 1)[1]
        assert "ON CONFLICT (version) DO NOTHING" in seed, (
            "ontology_versions seed must tolerate re-runs"
        )

    def test_seed_values_match_acceptance_criteria(self, migration_source: str) -> None:
        """Seed row is version=0.1.0, status=published, ontology_data={}."""
        seed = migration_source.split("INSERT INTO ontology_versions", 1)[1]
        assert "'0.1.0'" in seed
        assert "'published'" in seed
        assert "'{}'::jsonb" in seed

    def test_no_hardcoded_email_in_sql(self, migration_source: str) -> None:
        """Email is bound as a parameter, not interpolated into the SQL text."""
        assert ":system_user_email" in migration_source, (
            "system user email must be passed via bindparams"
        )


class TestUniqueConstraintParity:
    """AC-3: migration DDL and ORM model declare the same unique constraint."""

    def test_migration_declares_named_unique_constraint(
        self, migration_source: str
    ) -> None:
        assert f'name="{UNIQUE_CONSTRAINT_NAME}"' in migration_source, (
            f"migration must declare the unique constraint as "
            f"{UNIQUE_CONSTRAINT_NAME!r} so autogenerate can match it by name"
        )

    def test_model_declares_matching_unique_constraint(self) -> None:
        """The ORM model carries the same named constraint.

        The original model omitted uniqueness entirely, so autogenerate saw a
        constraint in the DB with no model counterpart and proposed dropping it.
        """
        names = {
            constraint.name for constraint in OntologyVersion.__table__.constraints
        }
        assert UNIQUE_CONSTRAINT_NAME in names, (
            f"OntologyVersion must declare {UNIQUE_CONSTRAINT_NAME!r}; "
            f"found constraints: {sorted(n for n in names if n)}"
        )

    def test_no_redundant_secondary_index(self, migration_source: str) -> None:
        """The unique constraint already backs `version` with an index."""
        assert "ix_ontology_versions_version" not in migration_source, (
            "a separate ix_ index on `version` duplicates the index implied by "
            f"{UNIQUE_CONSTRAINT_NAME}"
        )

    def test_version_is_not_nullable(self) -> None:
        assert OntologyVersion.__table__.columns["version"].nullable is False

    def test_created_by_is_not_nullable(self) -> None:
        """created_by NOT NULL is what makes the seed ordering load-bearing."""
        assert OntologyVersion.__table__.columns["created_by"].nullable is False


class TestDowngrade:
    """Downgrade reverses both the table and the seeded account."""

    def test_drops_table(self, migration_source: str) -> None:
        downgrade = migration_source.split("def downgrade", 1)[1]
        assert 'op.drop_table("ontology_versions")' in downgrade

    def test_removes_seeded_system_user(self, migration_source: str) -> None:
        downgrade = migration_source.split("def downgrade", 1)[1]
        assert "DELETE FROM users" in downgrade, (
            "downgrade must remove the system user it created, or re-upgrading "
            "leaves an orphaned account"
        )

    def test_drops_table_before_deleting_user(self, migration_source: str) -> None:
        """FK ordering: the referencing table must go first."""
        downgrade = migration_source.split("def downgrade", 1)[1]
        assert downgrade.index('op.drop_table("ontology_versions")') < downgrade.index(
            "DELETE FROM users"
        ), "ontology_versions references users.id and must be dropped first"
