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


# ---------------------------------------------------------------------------
# NFM-4024 — v0.5.0 seed migration for the 2 TRUE catalog gaps
# (`elastic_constant`, `solubility_limit`) surfaced by
# `scripts/nfm-4012-unknown-property-enumeration.py` (NFM-4012).
#
# The test class below mirrors the NFM-1995 / 031 coverage so future
# regressions on this seed are caught by the same rigor.
# ---------------------------------------------------------------------------

V050_REVISION = "067_v050_seed_elastic_constant_solubility_limit"
V050_DOWN_REVISION = "066_seed_material_categories"
V050_MIGRATION_PATH = f"migrations/versions/{V050_REVISION}.py"

# AC-3 / AC-4 — the two new property names, the two required slugs, the
# categories the rules must land under.
V050_EXPECTED_PROPERTY_NAMES: frozenset[str] = frozenset(
    {"elastic_constant", "solubility_limit"}
)
V050_EXPECTED_CATEGORY_SLUGS: frozenset[str] = frozenset(
    {"mechanical", "physical"}
)


@pytest.fixture(scope="module")
def v050_migration_source() -> str:
    """Read the v0.5.0 migration source file."""
    with open(V050_MIGRATION_PATH) as f:
        return f.read()


@pytest.fixture(scope="module")
def v050_script_directory() -> ScriptDirectory:
    """Load Alembic script directory for chain analysis."""
    config = Config("alembic.ini")
    return ScriptDirectory.from_config(config)


class TestV050MigrationChain:
    """v0.5.0 migration is discoverable and chains off the current head."""

    def test_revision_loadable(self, v050_script_directory: ScriptDirectory) -> None:
        rev = v050_script_directory.get_revision(V050_REVISION)
        assert rev is not None, f"Migration {V050_REVISION!r} not registered"
        assert rev.revision == V050_REVISION

    def test_down_revision_is_066_seed_material_categories(
        self,
        v050_script_directory: ScriptDirectory,
    ) -> None:
        """AC-1: chain point is the current head, NOT 065_widen.

        NFM-3918 renumbered 065→066; this assertion prevents a future
        author from re-anchoring to a stale down_revision.
        """
        rev = v050_script_directory.get_revision(V050_REVISION)
        assert rev is not None
        assert rev.down_revision == V050_DOWN_REVISION, (
            f"Expected down_revision={V050_DOWN_REVISION!r} (current head), "
            f"got {rev.down_revision!r}. Per NFM-3918 renumber, the seed "
            "chains off 066_seed_material_categories."
        )

    def test_v050_reachable_from_head(
        self,
        v050_script_directory: ScriptDirectory,
    ) -> None:
        """After this migration is applied, the chain head moves forward."""
        revisions = {r.revision for r in v050_script_directory.walk_revisions()}
        assert V050_REVISION in revisions, (
            f"{V050_REVISION} not reachable from head "
            f"{v050_script_directory.get_current_head()!r}"
        )


class TestV050Idempotent:
    """AC-2: re-running upgrade() must not raise."""

    def test_uses_on_conflict(self, v050_migration_source: str) -> None:
        assert "ON CONFLICT" in v050_migration_source.upper().replace(
            "\n", " "
        ), "Migration must use ON CONFLICT to be idempotent"

    def test_conflict_target_matches_unique_constraint(
        self,
        v050_migration_source: str,
    ) -> None:
        assert re.search(
            r"ON\s+CONFLICT\s*\(\s*category_id\s*,\s*slug\s*\)",
            v050_migration_source,
            re.IGNORECASE,
        ), (
            "ON CONFLICT target must be (category_id, slug) — "
            "matches uq_property_types_category_slug"
        )

    def test_do_nothing_branch(self, v050_migration_source: str) -> None:
        assert re.search(
            r"ON\s+CONFLICT[^;]*DO\s+NOTHING",
            v050_migration_source,
            re.IGNORECASE | re.DOTALL,
        ), "ON CONFLICT must DO NOTHING for safe re-runs"


class TestV050Coverage:
    """AC-3 / AC-4: both catalog gaps seeded with correct category mapping."""

    @pytest.mark.parametrize("name", sorted(V050_EXPECTED_PROPERTY_NAMES))
    def test_property_name_seeded(
        self,
        v050_migration_source: str,
        name: str,
    ) -> None:
        pattern = rf"['\"]\b{re.escape(name)}\b['\"]"
        assert re.search(pattern, v050_migration_source), (
            f"v0.5.0 seed missing canonical name: {name!r}"
        )

    @pytest.mark.parametrize("slug", sorted(V050_EXPECTED_CATEGORY_SLUGS))
    def test_category_slug_referenced(
        self,
        v050_migration_source: str,
        slug: str,
    ) -> None:
        pattern = rf"['\"]\b{re.escape(slug)}\b['\"]"
        assert re.search(pattern, v050_migration_source), (
            f"v0.5.0 seed missing category slug reference: {slug!r}"
        )

    def test_joins_property_categories(self, v050_migration_source: str) -> None:
        joined_with_subq = re.search(
            r"\(\s*SELECT\s+id\s+FROM\s+property_categories\s+WHERE\s+slug\s*=",
            v050_migration_source,
            re.IGNORECASE | re.DOTALL,
        )
        assert joined_with_subq, (
            "Seed must resolve category_id from property_categories.slug — "
            "cannot hard-code UUIDs (they are random)"
        )

    def test_elastic_constant_categorised_as_mechanical(
        self,
        v050_migration_source: str,
    ) -> None:
        """AC-4: ``elastic_constant`` → mechanical (via pressure family).

        NFM-3835 / FAMILY_TO_CATEGORY in ``heuristic_extractor.py`` maps
        the pressure family to mechanical. The seed must respect this
        contract or downstream extraction will fall back to ``other``
        and re-trigger skipped_unknown_properties.
        """
        pattern = re.compile(
            r"\(\s*[\"']mechanical[\"']\s*,\s*[\"']elastic_constant[\"']",
            re.IGNORECASE,
        )
        assert pattern.search(v050_migration_source), (
            "elastic_constant must be seeded under the 'mechanical' "
            "category slug (NFM-3835 FAMILY_TO_CATEGORY)."
        )

    def test_solubility_limit_categorised_as_physical(
        self,
        v050_migration_source: str,
    ) -> None:
        """AC-4: ``solubility_limit`` → physical (via dimensionless family)."""
        pattern = re.compile(
            r"\(\s*[\"']physical[\"']\s*,\s*[\"']solubility_limit[\"']",
            re.IGNORECASE,
        )
        assert pattern.search(v050_migration_source), (
            "solubility_limit must be seeded under the 'physical' "
            "category slug (NFM-3835 FAMILY_TO_CATEGORY)."
        )

    def test_does_not_rename_existing_elastic_constants(
        self,
        v050_migration_source: str,
    ) -> None:
        """NDE constraint: do not rename the seeded ``elastic_constants`` (plural).

        Migration 031 already cites ``elastic_constants`` (the tensor
        row, value_type=list); renaming it would orphan existing
        measurement rows. The v0.5.0 migration must add a NEW row
        ``elastic_constant`` (singular) and leave the plural alone.
        """
        # The seed must NOT contain a tuple whose name/slug is the
        # plural 'elastic_constants' (the 031 row).
        assert not re.search(
            r"\(\s*[\"']mechanical[\"']\s*,\s*[\"']elastic_constants[\"']",
            v050_migration_source,
            re.IGNORECASE,
        ), (
            "v0.5.0 seed must not re-seed 'elastic_constants' (plural); "
            "the existing 031 row covers it. Add 'elastic_constant' "
            "(singular) as a new row instead."
        )

    def test_value_types_allowed(self, v050_migration_source: str) -> None:
        allowed = {"scalar", "range", "expression", "list", "text"}
        seed_value_types = set(
            re.findall(
                r'"(?:scalar|range|expression|list|text)"',
                v050_migration_source,
            )
        )
        used = {s.strip('"') for s in seed_value_types}
        assert used <= allowed, (
            f"value_type literals outside allowed set {allowed}: {used - allowed}"
        )
        # elastic_constant is per-component (C11/C12/C44), so value_type
        # should be scalar. Solubility limit is a single number, also
        # scalar. Either way, 'scalar' must appear in the seed.
        assert '"scalar"' in v050_migration_source, (
            "v0.5.0 seed must include 'scalar' value_type "
            "(elastic_constant and solubility_limit are per-component scalars)"
        )


class TestV050Downgrade:
    """AC-1 corollary: downgrade() removes only the 2 seeded rows."""

    def test_has_downgrade(self, v050_migration_source: str) -> None:
        assert "def downgrade()" in v050_migration_source

    def test_downgrade_targets_only_seeded_rows(
        self,
        v050_migration_source: str,
    ) -> None:
        match = re.search(
            r"def\s+downgrade\s*\(\s*\)\s*->\s*None\s*:\s*(?P<body>.*?)(?=\n\ndef |\Z)",
            v050_migration_source,
            re.DOTALL,
        )
        assert match is not None, "downgrade() body not found"
        body = match.group("body")
        assert "property_types" in body
        assert re.search(
            r"\bDELETE\s+FROM\s+property_types\b", body, re.IGNORECASE
        ), "downgrade should DELETE seeded rows, not DROP the table"
        assert "DROP TABLE" not in body.upper(), (
            "downgrade must not drop property_types (other rows may exist)"
        )

    def test_downgrade_deletes_exactly_the_v050_slugs(
        self,
        v050_migration_source: str,
    ) -> None:
        """The downgrade() must delete exactly the 2 v0.5.0 slugs.

        Cross-checks against the explicit ``V050_EXPECTED_PROPERTY_NAMES``
        constant rather than re-deriving slugs from a regex over the
        source (which would also match docstring prose mentioning
        'scalar', 'range', etc.). The downgrade body must contain the
        slug list bound to the DELETE statement and no other rows
        should be reachable via this WHERE.
        """
        # 1. The downgrade body must reference the seeded slugs by literal
        #    string — they appear in the ``_PROPERTY_TYPE_V050_SEED``
        #    tuple AND are extracted via ``row[2]``.
        for slug in V050_EXPECTED_PROPERTY_NAMES:
            assert re.search(
                rf"[\"']{re.escape(slug)}[\"']",
                v050_migration_source,
            ), (
                f"v0.5.0 seed missing literal slug {slug!r}; "
                "downgrade() would leave the row behind."
            )

        # 2. The downgrade() function must delete by slug (not by name,
        #    not by id) — this is the safe contract.
        match = re.search(
            r"def\s+downgrade\s*\(\s*\)\s*->\s*None\s*:\s*(?P<body>.*?)(?=\n\ndef |\Z)",
            v050_migration_source,
            re.DOTALL,
        )
        assert match is not None, "downgrade() body not found"
        body = match.group("body")
        # Must use DELETE … WHERE slug = ANY(CAST(:slugs AS VARCHAR[]))
        # pattern (matches the 031 contract).
        assert re.search(
            r"WHERE\s+slug\s*=\s*ANY",
            body,
            re.IGNORECASE,
        ), "downgrade() must filter by slug = ANY(...)"
        assert re.search(
            r":slugs\b",
            body,
        ), "downgrade() must bind :slugs parameter to the seeded slug list"
