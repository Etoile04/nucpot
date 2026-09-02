"""NFM-3916: Verify Alembic migration 065 seeds ``material_categories``.

Tier 1C prerequisite for the ``/materials`` UX research findings
(NFM-3903, NFM-3913).  Without this seed the dropdown Tier 1D plans
to ship is an empty list — exactly the bug the UX research
diagnosed.

This test verifies, offline, that:

* [AC-1] Migration ``066_seed_material_categories`` exists and
  chains from ``065_widen_property_measurements_numeric`` (the
  current Alembic head before this ticket).
* [AC-2] The migration is idempotent — re-running it must not
  raise (uses ``INSERT ... ON CONFLICT (slug) DO NOTHING`` keyed
  on the existing unique constraint
  ``uq_material_categories_slug``).
* [AC-3] The migration seeds the eight canonical nuclear-fuel /
  structural-material taxonomy rows specified in the NFM-3913
  brief (and approved by CPO via the NFM-3916 ticket scope).
* [AC-4] The migration exposes a ``downgrade()`` that is safe
  (deletes only the rows it created, by slug).
"""

from __future__ import annotations

import re

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

REVISION = "066_seed_material_categories"
DOWN_REVISION = "065_widen_property_measurements_numeric"
MIGRATION_PATH = f"migrations/versions/{REVISION}.py"


# Canonical slugs from the NFM-3913 / NFM-3916 ticket scope.  These
# names are stable identifiers used by the backfill script
# (``scripts/backfill_material_category.py``) and the upcoming
# Tier 1D frontend dropdown, so the test enforces the exact set
# rather than a fuzzy "contains" check.
EXPECTED_SLUGS: frozenset[str] = frozenset(
    {
        "oxide_fuel",
        "metallic_fuel",
        "carbide_nitride_fuel",
        "cladding_alloy",
        "structural_steel",
        "refractory_metal",
        "amorphous_glassy",
        "other",
    }
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def script_directory() -> ScriptDirectory:
    """Load Alembic script directory for offline chain analysis."""
    config = Config("alembic.ini")
    return ScriptDirectory.from_config(config)


@pytest.fixture(scope="module")
def migration_source() -> str:
    """Read the migration source file."""
    with open(MIGRATION_PATH) as f:
        return f.read()


# ---------------------------------------------------------------------------
# AC-1: Migration exists and is on the linear chain
# ---------------------------------------------------------------------------


class TestMigrationChain:
    """066_seed_material_categories is discoverable and depends on 065_widen."""

    def test_revision_loadable(self, script_directory: ScriptDirectory) -> None:
        rev = script_directory.get_revision(REVISION)
        assert rev is not None, f"Migration {REVISION!r} not registered"
        assert rev.revision == REVISION

    def test_down_revision_is_065_widen(self, script_directory: ScriptDirectory) -> None:
        rev = script_directory.get_revision(REVISION)
        assert rev is not None
        assert rev.down_revision == DOWN_REVISION, (
            f"Expected down_revision={DOWN_REVISION!r}, "
            f"got {rev.down_revision!r}"
        )

    def test_head_is_at_or_past_066(self, script_directory: ScriptDirectory) -> None:
        """After this migration is applied, the chain head moves forward."""
        revisions = {r.revision for r in script_directory.walk_revisions()}
        assert REVISION in revisions, (
            f"{REVISION} not reachable from head {script_directory.get_current_head()!r}"
        )

    def test_no_duplicate_revisions(self, script_directory: ScriptDirectory) -> None:
        revisions = [r.revision for r in script_directory.walk_revisions()]
        assert len(revisions) == len(set(revisions)), "Duplicate revisions found"


# ---------------------------------------------------------------------------
# AC-2: Migration is idempotent
# ---------------------------------------------------------------------------


class TestIdempotent:
    """The migration must be safe to re-run on an already-seeded DB."""

    def test_uses_on_conflict(self, migration_source: str) -> None:
        """Source contains ``ON CONFLICT`` so re-runs are no-ops."""
        assert "ON CONFLICT" in migration_source.upper().replace("\n", " "), (
            "Migration must use ON CONFLICT to be idempotent"
        )

    def test_conflict_target_is_slug(self, migration_source: str) -> None:
        """Conflict target is ``(slug)`` — the existing uq."""
        assert re.search(
            r"ON\s+CONFLICT\s*\(\s*slug\s*\)",
            migration_source,
            re.IGNORECASE,
        ), (
            "ON CONFLICT target must be (slug) — "
            "matches uq_material_categories_slug"
        )

    def test_do_nothing_branch(self, migration_source: str) -> None:
        """ON CONFLICT path is DO NOTHING (no destructive overwrite)."""
        assert re.search(
            r"ON\s+CONFLICT[^;]*DO\s+NOTHING",
            migration_source,
            re.IGNORECASE | re.DOTALL,
        ), "ON CONFLICT must DO NOTHING for safe re-runs"


# ---------------------------------------------------------------------------
# AC-3: Coverage of NFMD taxonomy slugs
# ---------------------------------------------------------------------------


class TestCoverage:
    """Every canonical slug from the NFM-3913 brief must be seeded."""

    @pytest.mark.parametrize("slug", sorted(EXPECTED_SLUGS))
    def test_slug_seeded(self, migration_source: str, slug: str) -> None:
        """Each canonical slug appears in the seed tuple."""
        # Match either single-quoted or double-quoted slug literal.
        pattern = rf"['\"]\b{re.escape(slug)}\b['\"]"
        assert re.search(pattern, migration_source), (
            f"material_categories seed missing canonical slug: {slug!r}"
        )

    def test_no_schema_change(self, migration_source: str) -> None:
        """The migration must NOT alter the schema — only data is added.

        Per the NFM-3916 ticket scope, ``material_categories`` already
        exists from ``009_create_phase1_core_tables``; this migration
        is a *data* seed, not a *schema* change.
        """
        forbidden = ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "CREATE INDEX")
        for stmt in forbidden:
            assert stmt not in migration_source.upper().replace("\n", " "), (
                f"Seed migration must not issue {stmt!r} — schema changes "
                f"belong in a separate revision."
            )


# ---------------------------------------------------------------------------
# AC-4: Safe downgrade
# ---------------------------------------------------------------------------


class TestDowngrade:
    """The migration exposes a working downgrade()."""

    def test_has_downgrade(self, migration_source: str) -> None:
        assert "def downgrade()" in migration_source

    def test_downgrade_targets_only_seeded_rows(self, migration_source: str) -> None:
        """Downgrade deletes by slug (in seeded set), not all rows."""
        match = re.search(
            r"def\s+downgrade\s*\(\s*\)\s*->\s*None\s*:\s*(?P<body>.*?)(?=\n\ndef |\Z)",
            migration_source,
            re.DOTALL,
        )
        assert match is not None, "downgrade() body not found"
        body = match.group("body")
        # Must reference material_categories.
        assert "material_categories" in body, (
            "downgrade must reference material_categories table"
        )
        # Must use a DELETE — not DROP TABLE.
        assert re.search(
            r"\bDELETE\s+FROM\s+material_categories\b", body, re.IGNORECASE,
        ), "downgrade should DELETE seeded rows, not DROP the table"
        assert "DROP TABLE" not in body.upper(), (
            "downgrade must not drop material_categories (other rows may exist)"
        )

    def test_downgrade_uses_slug_array(self, migration_source: str) -> None:
        """Downgrade uses a parameterised slug-array filter so future
        taxonomy additions survive a downgrade."""
        assert re.search(
            r"slug\s*=\s*ANY\s*\(",
            migration_source,
            re.IGNORECASE,
        ), "downgrade must filter by slug via ANY(:slugs) for safety"
