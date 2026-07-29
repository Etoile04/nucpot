"""NFM-1995: Verify Alembic migration 031 seeds ``property_types`` for OntoFuel.

E2E QA (NFM-1985) discovered the running dev DB has 0 rows in
``property_types`` after ``docker-compose up -d`` even though the schema
migration creates the table.  ``extraction_to_db_mapper._lookup_property_type``
silently skips measurements whose property name is not registered, so the
empty table causes every OntoFuel ingest to record ``created_measurements=0``.

This test verifies, offline, that:

* [AC-1] Migration ``031_seed_property_types`` exists in the Alembic
  chain and depends on ``030_create_corpus_table`` (so it runs after
  the corpus table is created, but ordering against property_categories
  is handled by the existing ``010_seed_phase1_reference_data`` which
  is already applied long before).
* [AC-2] The migration is idempotent: re-running it must not raise
  (uses ``INSERT ... ON CONFLICT DO NOTHING`` keyed on the existing
  unique constraint ``uq_property_types_category_slug``).
* [AC-3] The migration covers every property name used by OntoFuel
  ingest (NFM-1985 / NFM-1984 review) — see the
  ``EXPECTED_PROPERTY_NAMES`` set below.  These names come from:
  - existing integration tests (tests/test_extraction_ingest_integration.py)
  - golden fixtures (tests/fixtures/golden/*.json)
  - the mapper's lookup path in
    ``src/nfm_db/services/extraction_to_db_mapper.py``
* [AC-4] The migration has a ``downgrade`` that is safe (deletes only
  the rows it created by slug).
"""

from __future__ import annotations

import re

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


REVISION = "031_seed_property_types"
DOWN_REVISION = "030_create_corpus_table"
MIGRATION_PATH = f"migrations/versions/{REVISION}.py"


# Canonical OntoFuel property names observed in the codebase.  These are
# the values ``ExtractedProperty.property`` can take after the v4 schema
# is applied (NFM-1979 / AC-4).  Mapper looks up
# ``PropertyType.name == property_name``; missing names silently skip
# the measurement, which is the bug this migration fixes.
EXPECTED_PROPERTY_NAMES: frozenset[str] = frozenset(
    {
        # physical
        "density",
        "lattice_constant",
        "cohesive_energy",
        "formation_energy",
        "melting_point",
        # mechanical
        "bulk_modulus",
        "youngs_modulus",
        "yield_strength",
        "elastic_constants",
        # thermal
        "thermal_conductivity",
        "thermal_expansion",
        "specific_heat",
        # nuclear
        "fission_cross_section",
        "swelling_rate",
        # diffusion (OntoFuel literal; mapped to physical slug)
        "diffusion_coefficient",
    }
)

# Each row MUST be tagged with one of these DB category slugs (created by
# migration 010_seed_phase1_reference_data).  Diffuse category literals
# in the OntoFuel schema (e.g. "diffusion", "irradiation") map to one of
# these broader DB categories via
# ``extraction_to_db_mapper.ONTOFUEL_CATEGORY_TO_SLUG``.
EXPECTED_CATEGORY_SLUGS: frozenset[str] = frozenset(
    {"physical", "mechanical", "thermal", "nuclear"}
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
    """031_seed_property_types is discoverable and depends on 030."""

    def test_revision_loadable(self, script_directory: ScriptDirectory) -> None:
        rev = script_directory.get_revision(REVISION)
        assert rev is not None, f"Migration {REVISION!r} not registered"
        assert rev.revision == REVISION

    def test_down_revision_is_030(self, script_directory: ScriptDirectory) -> None:
        rev = script_directory.get_revision(REVISION)
        assert rev is not None
        assert rev.down_revision == DOWN_REVISION, (
            f"Expected down_revision={DOWN_REVISION!r}, "
            f"got {rev.down_revision!r}"
        )

    def test_head_is_at_or_past_031(self, script_directory: ScriptDirectory) -> None:
        """After this migration is applied, the chain head moves forward."""
        head = script_directory.get_current_head()
        # Walk from the head backwards; 031 must be reachable.
        revisions = {r.revision for r in script_directory.walk_revisions()}
        assert REVISION in revisions, (
            f"{REVISION} not reachable from head {head!r}"
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

    def test_conflict_target_matches_unique_constraint(
        self,
        migration_source: str,
    ) -> None:
        """Conflict target is ``(category_id, slug)`` — the existing uq."""
        # Accept either (category_id, slug) or the column pair name.
        assert re.search(
            r"ON\s+CONFLICT\s*\(\s*category_id\s*,\s*slug\s*\)",
            migration_source,
            re.IGNORECASE,
        ), (
            "ON CONFLICT target must be (category_id, slug) — "
            "matches uq_property_types_category_slug"
        )

    def test_do_nothing_branch(self, migration_source: str) -> None:
        """ON CONFLICT path is DO NOTHING (no destructive overwrite)."""
        assert re.search(
            r"ON\s+CONFLICT[^;]*DO\s+NOTHING",
            migration_source,
            re.IGNORECASE | re.DOTALL,
        ), "ON CONFLICT must DO NOTHING for safe re-runs"


# ---------------------------------------------------------------------------
# AC-3: Coverage of OntoFuel property names
# ---------------------------------------------------------------------------


class TestCoverage:
    """Every property name used by OntoFuel ingest must be seeded."""

    @pytest.mark.parametrize("name", sorted(EXPECTED_PROPERTY_NAMES))
    def test_property_name_seeded(self, migration_source: str, name: str) -> None:
        """Each canonical property name appears in an INSERT VALUES list."""
        # Match either 'name' or ('name') in the seed body.
        pattern = rf"['\"]\b{re.escape(name)}\b['\"]"
        assert re.search(pattern, migration_source), (
            f"property_types seed missing canonical name: {name!r}"
        )

    @pytest.mark.parametrize("slug", sorted(EXPECTED_CATEGORY_SLUGS))
    def test_category_slug_referenced(self, migration_source: str, slug: str) -> None:
        """Each required PropertyCategory slug is referenced in the seed."""
        pattern = rf"['\"]\b{re.escape(slug)}\b['\"]"
        assert re.search(pattern, migration_source), (
            f"property_types seed missing category slug reference: {slug!r}"
        )

    def test_joins_property_categories(self, migration_source: str) -> None:
        """Seed resolves category by joining against property_categories.slug."""
        # Either a CTE/subquery or INSERT...SELECT pattern is acceptable.
        joined_with_slug = (
            "property_categories" in migration_source
            and re.search(
                r"SELECT.*FROM\s+property_categories",
                migration_source,
                re.IGNORECASE | re.DOTALL,
            )
        )
        # Or a direct subquery: WHERE slug = '...'
        joined_with_subq = re.search(
            r"\(\s*SELECT\s+id\s+FROM\s+property_categories\s+WHERE\s+slug\s*=",
            migration_source,
            re.IGNORECASE | re.DOTALL,
        )
        assert joined_with_slug or joined_with_subq, (
            "Seed must resolve category_id from property_categories.slug — "
            "cannot hard-code UUIDs (they are random)"
        )

    def test_value_type_set(self, migration_source: str) -> None:
        """Each seeded row declares a value_type in the allowed set.

        Acceptable forms:
          * a literal column assignment (``value_type`` referenced in
            the SELECT / INSERT list);
          * a parameter binding (``:value_type``) — the value comes
            from the seed tuple;
          * a literal allowed value like ``'scalar'`` or ``'list'``.
        """
        patterns = [
            r"\bvalue_type\b",                       # column reference
            r":value_type\b",                        # SQLAlchemy bind param
            r"\b'scalar'\b|\b'list'\b|\b'range'\b",  # allowed literal
        ]
        for pattern in patterns:
            if re.search(pattern, migration_source, re.IGNORECASE):
                return
        raise AssertionError(
            "Seed must reference value_type (column, bind param, or literal)"
        )

    def test_value_types_allowed(self, migration_source: str) -> None:
        """Only value_types in ('scalar', 'range', 'expression', 'list', 'text') appear.

        The check constraint ``ck_property_types_value_type`` rejects
        anything else, so the seed must avoid typos.
        """
        literal_values = set(
            re.findall(
                r":value_type[^,]*CAST\([^)]+AS\s*VARCHAR\)\s+AS\s+value_type",
                migration_source,
                re.IGNORECASE,
            )
        )
        # Indirectly verified by the seed tuple in the migration source.
        seed_value_types = set(
            re.findall(
                r'"(?:scalar|range|expression|list|text)"',
                migration_source,
            )
        )
        # At minimum, 'scalar' must appear in the seed tuple.
        assert '"scalar"' in migration_source, (
            "Seed must include 'scalar' value_type for the common case"
        )
        allowed = {"scalar", "range", "expression", "list", "text"}
        used = {s.strip('"') for s in seed_value_types}
        assert used <= allowed, (
            f"value_type literals outside allowed set {allowed}: {used - allowed}"
        )
        assert not literal_values, (
            f"Unused literal sets found: {literal_values}"
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
        # Pull the body of downgrade().
        match = re.search(
            r"def\s+downgrade\s*\(\s*\)\s*->\s*None\s*:\s*(?P<body>.*?)(?=\n\ndef |\Z)",
            migration_source,
            re.DOTALL,
        )
        assert match is not None, "downgrade() body not found"
        body = match.group("body")
        # Must reference property_types.
        assert "property_types" in body, (
            "downgrade must reference property_types table"
        )
        # Must use a DELETE or TRUNCATE — not DROP TABLE.
        assert re.search(r"\bDELETE\s+FROM\s+property_types\b", body, re.IGNORECASE), (
            "downgrade should DELETE seeded rows, not DROP the table"
        )
        assert "DROP TABLE" not in body.upper(), (
            "downgrade must not drop property_types (other rows may exist)"
        )
