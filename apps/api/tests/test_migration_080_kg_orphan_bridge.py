"""NFM-4185 — offline verification of migration 080 (KG orphan bridge).

Structural tests pinning the migration that fixes the two defects the
CTO post-landing verification of NFM-4093 found on prod:

* **D1** — the U-10Mo ``Material`` kg_node was an orphan (n=1/e=0 at
  every depth).  Root cause chain: migration 070's placeholder dedup
  CASCADE-deleted the 3 U-10Mo datasets, so migration 072's
  repoint-then-bridge UPDATE matched 0 rows; migration 075 later
  restored the datasets from backup **with their original placeholder
  source_ids**, and 072 never created any dataset-representing
  kg_nodes / kg_edges in the first place.
* **D2** — ``U-3Si`` / ``PuO2`` returned 404.  Root cause: migration
  072's stub-node INSERT carried an ``AND EXISTS (… data_sources.id =
  CAST(:src_id AS uuid))`` guard; for the three NULL-source stubs
  (PuO2, Test, U-3Si) ``ds.id = NULL`` evaluates to NULL, so the
  EXISTS guard silently skipped every insert.

Tests mirror the ``test_migration_072_no_placeholders.py`` /
``test_migration_057_create_kg_type_tables.py`` patterns: the classes
below are **structural** (file contents + rendered SQL + alembic chain
graph), no live PostgreSQL required.  Functional verification against
the real prod shape is covered by the API-shape tests in
``test_kg_graph_coverage.py::TestNFM4185*`` and by E2E.

``TestMigration080Execution`` at the bottom adds execution-based
coverage (gate review 2026-09-02 finding 2): it runs the real
``upgrade()``/``downgrade()`` against a disposable Postgres whose
schema comes from the ORM metadata.  It skips unless
``NFM_TEST_DATABASE_URL`` is set (the NFM-2032 opt-in pattern); the
CI-side fresh-database crash net is the schema-drift-guard job, which
runs ``alembic upgrade head`` against a clean Postgres service.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

REVISION = "080_kg_orphan_bridge_u10mo_u3si_puo2"
DOWN_REVISION = "079_restore_070_measurement_casualties"
MIGRATION_PATH = Path("migrations/versions") / f"{REVISION}.py"


@pytest.fixture(scope="module")
def script_directory() -> ScriptDirectory:
    """Load the Alembic script directory for offline chain analysis."""
    return ScriptDirectory.from_config(Config("alembic.ini"))


@pytest.fixture(scope="module")
def migration_source() -> str:
    """Source text of the 080 migration file."""
    assert MIGRATION_PATH.is_file(), f"NFM-4185: migration file {MIGRATION_PATH} must exist"
    return MIGRATION_PATH.read_text()


@pytest.fixture(scope="module")
def migration_module():
    """Import the 080 migration module the same way alembic does.

    alembic loads revision modules via ``importlib.util.spec_from_file_location``
    (``alembic.util.pyfiles.load_module_py``), so the migration must be
    importable as a top-level non-package module.  This fixture mirrors
    that loading pattern (same as the 072 structural tests).
    """
    spec = importlib.util.spec_from_file_location("m080_under_test", str(MIGRATION_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Chain integrity — 080 branches from the post-#1117 head (CPO dispatch
# note 1: prevents a repeat of the NFM-4178 alembic collision).
# ---------------------------------------------------------------------------


class TestMigration080Chain:
    """080 registers in the alembic graph as the single new head."""

    def test_revision_loadable(self, script_directory: ScriptDirectory) -> None:
        rev = script_directory.get_revision(REVISION)
        assert rev is not None, f"Migration {REVISION!r} not registered"
        assert rev.revision == REVISION

    def test_down_revision_is_079(self, script_directory: ScriptDirectory) -> None:
        """080 chains off 079_restore_070_measurement_casualties — do NOT renumber."""
        rev = script_directory.get_revision(REVISION)
        assert rev is not None
        assert rev.down_revision == DOWN_REVISION, (
            f"080 must set down_revision={DOWN_REVISION!r} "
            f"(NFM-4178 collision guard); got {rev.down_revision!r}"
        )

    def test_080_is_single_head(self, script_directory: ScriptDirectory) -> None:
        """Exactly one head exists and it is 080."""
        heads = script_directory.get_heads()
        assert heads == [REVISION], f"Expected single head {REVISION!r}; got {heads}"


# ---------------------------------------------------------------------------
# D1 — U-10Mo dataset bridge: Measurement nodes + containsData edges.
# ---------------------------------------------------------------------------


class TestMigration080U10MoDatasetBridge:
    """The rendered SQL creates the Material→dataset bridge idempotently."""

    def test_dataset_node_sql_inserts_measurement_type(self, migration_module) -> None:
        sql = migration_module._build_u10mo_dataset_nodes_sql()
        assert "'Measurement'" in sql, (
            "Dataset-representing nodes must use node_type='Measurement' "
            "(ck_kg_nodes_node_type allows no 'Dataset' type)"
        )
        assert "dataset_id" in sql, (
            "The dataset node must carry the durable properties.dataset_id "
            "link back to the datasets row"
        )

    def test_dataset_node_sql_is_idempotent(self, migration_module) -> None:
        sql = migration_module._build_u10mo_dataset_nodes_sql()
        assert "NOT EXISTS" in sql, (
            "Dataset-node INSERT must be idempotent via NOT EXISTS "
            "(NFM-4095 migration-071 family pattern)"
        )
        assert "properties->>'dataset_id'" in sql, (
            "Idempotency key must be properties->>'dataset_id' — labels "
            "are not unique across dataset nodes"
        )

    def test_contains_data_edge_sql_uses_canonical_relation(self, migration_module) -> None:
        sql = migration_module._build_u10mo_contains_data_edges_sql()
        assert "'containsData'" in sql, (
            "Material→dataset relation must be 'containsData' "
            "(RelationType.CONTAINS_DATA in nucmat_ontology)"
        )
        assert "NOT EXISTS" in sql

    def test_contains_data_edge_sql_targets_dataset_nodes(self, migration_module) -> None:
        """Edges must join the Measurement nodes by dataset_id, not label."""
        sql = migration_module._build_u10mo_contains_data_edges_sql()
        assert "properties->>'dataset_id'" in sql, (
            "The containsData edge join must resolve the Measurement node "
            "via properties->>'dataset_id' so identical labels cannot "
            "cross-link"
        )


# ---------------------------------------------------------------------------
# D2 — U-3Si / PuO2 stub nodes: the NULL-source EXISTS-guard fix.
# ---------------------------------------------------------------------------


class TestMigration080StubNodes:
    """The stub-node INSERT must not repeat 072's NULL-source bug."""

    def test_stub_node_sql_has_no_source_exists_guard(self, migration_module) -> None:
        """Regression guard for the D2 root cause.

        Migration 072's stub INSERT carried
        ``AND EXISTS (SELECT 1 FROM data_sources ds WHERE ds.id =
        CAST(:src_id AS uuid))`` — with ``src_id = NULL`` that EXISTS
        is never true, so PuO2 / Test / U-3Si were silently skipped.
        080's stub INSERT must contain NO ``data_sources`` existence
        guard at all (kg_nodes.source_id is nullable by schema).
        """
        sql = migration_module._build_stub_material_nodes_sql()
        assert "data_sources" not in sql, (
            "072's NULL-source EXISTS guard is the D2 root cause — the "
            "stub-node INSERT must not reference data_sources"
        )
        assert "NOT EXISTS" in sql, "Stub-node INSERT must stay idempotent by (node_type, label)"

    def test_stub_node_sql_guards_on_materials_existence(self, migration_module) -> None:
        """Stub nodes must only exist for real ``materials`` rows.

        Gate review 2026-09-02 (fresh-database safety): without this
        guard the stub INSERT runs on any database, creating Material
        nodes that reference no material — and arming the relatedTo
        insert's NULL-target crash path on databases where 072's
        anchors never landed.  Tied to ``materials.name`` so an empty
        database inserts nothing.
        """
        sql = migration_module._build_stub_material_nodes_sql()
        assert "FROM materials m" in sql, (
            "Stub-node INSERT must be guarded by a materials-existence "
            "check so empty databases stay node-free"
        )
        assert "m.name = CAST(:label AS text)" in sql

    def test_stub_labels_are_u3si_and_puo2(self, migration_module) -> None:
        labels = migration_module._STUB_MATERIAL_LABELS
        assert set(labels) == {"U-3Si", "PuO2"}, (
            "Only U-3Si and PuO2 are in scope — 'Test' is out of scope (CPO dispatch note 4)"
        )

    def test_related_to_edge_sql_anchors(self, migration_module) -> None:
        """U-3Si/PuO2 get relatedTo edges to orphan bucket-D anchors.

        Anchors must NOT be edge-bearing materials (AC3 pins the 60
        edge-bearing subgraphs at n/e ±0) and must not be the
        out-of-scope *_phase/*_reference pseudo-materials.
        """
        anchors = dict(migration_module._STUB_RELATED_TO_ANCHORS)
        assert anchors["U-3Si"] == "alpha_U_solid_solution"
        assert anchors["PuO2"] == "delta_Pu_solid_solution"
        for anchor in anchors.values():
            assert not anchor.endswith(("_phase", "_reference")), (
                f"Anchor {anchor!r} is an out-of-scope pseudo-material"
            )

    def test_related_to_edge_sql_exists_guarded(self, migration_module) -> None:
        """Gate review 2026-09-02 finding 1 — NULL-endpoint guards.

        The stub relatedTo INSERT resolves both endpoints via scalar
        subqueries; when either node is absent (fresh database: 072's
        bucket-D anchors are gated on a prod-only data_sources row) the
        subquery yields NULL.  Without explicit ``IS NOT NULL`` guards
        the NOT EXISTS clause passes (``e.target_node_id = NULL`` is
        never true) and the INSERT writes a NULL ``target_node_id`` —
        a NOT NULL violation that kills ``alembic upgrade head`` on
        every fresh database (CI schema-drift-guard included).
        """
        sql = migration_module._build_stub_related_to_edges_sql()
        assert "'relatedTo'" in sql
        assert "NOT EXISTS" in sql
        assert sql.count("IS NOT NULL") == 2, (
            "Both scalar-subquery endpoints (source stub + anchor) must "
            "carry an IS NOT NULL guard so absent nodes are a clean no-op"
        )


# ---------------------------------------------------------------------------
# Family regression guards (NFM-4099 / NFM-4142 patterns).
# ---------------------------------------------------------------------------


class TestMigration080FamilyGuards:
    """Guards carried over from the 070+ migration family."""

    @pytest.mark.parametrize(
        "builder_name",
        [
            "_build_u10mo_dataset_nodes_sql",
            "_build_u10mo_contains_data_edges_sql",
            "_build_stub_material_nodes_sql",
            "_build_stub_related_to_edges_sql",
        ],
    )
    def test_sql_builders_emit_no_do_block(self, migration_module, builder_name: str) -> None:
        """NFM-4099 — asyncpg cannot bind parameters into DO $$ blocks."""
        sql = getattr(migration_module, builder_name)()
        assert "DO $$" not in sql
        assert "DO " not in sql

    def test_no_literal_placeholder_titles(self, migration_source: str) -> None:
        """NFM-4142 AC-4 family rule — no placeholder title literals.

        The migration identifies the U-10Mo datasets by
        ``materials.name`` join, never by the placeholder dataset /
        source title literals.
        """
        assert "Unknown Source" not in migration_source
        assert "Unattributed source" not in migration_source

    def test_downgrade_reverses_inserts(self, migration_module) -> None:
        """Downgrade deletes exactly what upgrade inserted, by provenance.

        The downgrade builder must scope its DELETEs to the migration's
        provenance tag so future organic rows are never clobbered, and
        must emit one statement per list entry (asyncpg prepared-statement
        rule — the NFM-4169 migration-073 lesson).
        """
        statements = migration_module._build_downgrade_cleanup_sql()
        assert isinstance(statements, list)
        assert len(statements) >= 2
        for sql in statements:
            assert "NFM-4185" in sql, (
                "Every downgrade DELETE must be scoped to the migration's "
                "provenance tag so future organic rows survive"
            )
            assert sql.count(";") == 0, (
                "Each downgrade statement must be single — asyncpg's "
                "prepared-statement splitter rejects multi-statement text"
            )
        joined = "\n".join(statements)
        assert "kg_edges" in joined and "kg_nodes" in joined

    def test_scope_guard_no_pseudo_material_patterns(self, migration_source: str) -> None:
        """CPO dispatch note 4 — property-slice pseudo-materials stay out.

        The migration must not touch any ``UPuZr_*`` / ``U_*Pu_*Zr_*``
        material (the ~46-row DATA-CLEANUP residual cohort).
        """
        for token in ("UPuZr_", "U_10Pu_", "U_15Pu_", "U_19Pu_", "U_20Pu_"):
            assert token not in migration_source, (
                f"080 must not reference out-of-scope pseudo-material pattern {token!r}"
            )


# ---------------------------------------------------------------------------
# Execution-based verification — gate review 2026-09-02 finding 2.
#
# The structural classes above pin shape; these tests run the real
# ``upgrade()``/``downgrade()`` against a disposable Postgres whose schema
# comes from the ORM metadata (the conftest ``_safe_create_all`` path).
# Opt-in via ``NFM_TEST_DATABASE_URL`` (NFM-2032 pattern): skipped unless
# the env var points at a THROWAWAY database — the schema is created and
# dropped around the run, so never point it at prod or a prod-clone.
# ---------------------------------------------------------------------------

_PG_URL = os.environ.get("NFM_TEST_DATABASE_URL", "").strip()

# Deterministic seed ids — the two real prod material ids (read back from
# the prod API during diagnosis) plus synthetic UUIDs for the anchors,
# the U-10Mo focal kg_node, and the three datasets.
_U10MO_MATERIAL = "8537da2d-edac-47be-9b0d-ce61283b143f"
_U3SI_MATERIAL = "06df3e99-0e85-4044-8166-2d3afc630e8f"
_PUO2_MATERIAL = "619008d5-8864-4151-a980-bc56dc656d13"
_U10MO_NODE = "c9291967-01c8-4ca4-81d8-eb8003ac0a67"
_ANCHOR_U = "aaaa0000-0000-4000-8000-0000000000aa"
_ANCHOR_PU = "bbbb0000-0000-4000-8000-0000000000bb"
_DS_IDS = (
    "dddd0000-0000-4000-8000-000000000001",
    "dddd0000-0000-4000-8000-000000000002",
    "dddd0000-0000-4000-8000-000000000003",
)


@pytest.fixture(scope="module")
def exec_engine():
    """Module-scoped disposable Postgres with the ORM schema applied."""
    if not _PG_URL:
        pytest.skip(
            "NFM_TEST_DATABASE_URL not set; migration-080 execution tests need a disposable Postgres"
        )
    pytest.importorskip("psycopg2", reason="psycopg2 required to execute migrations synchronously")

    from sqlalchemy import create_engine

    from nfm_db.models import Base
    from tests.conftest import _safe_create_all

    engine = create_engine(_PG_URL.replace("+asyncpg", ""), future=True)
    with engine.begin() as conn:
        _safe_create_all(conn, Base.metadata)
        # The ORM declares several NOT NULL columns with python-side
        # defaults only, so ``create_all`` emits no DB default — but the
        # real (migration-built) schema carries server defaults and
        # migration 080's INSERTs rely on them by omitting those
        # columns.  Mirror the real DDL so the execution surface
        # matches production:
        #   id DEFAULT gen_random_uuid()          (012:57,84)
        #   synced_to_graph DEFAULT FALSE         (015:53,74)
        #   review_status DEFAULT 'pending'       (014:39 / 022:201)
        for table in ("kg_nodes", "kg_edges"):
            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT gen_random_uuid()"))
            conn.execute(
                text(f"ALTER TABLE {table} ALTER COLUMN synced_to_graph SET DEFAULT FALSE")
            )
            conn.execute(
                text(f"ALTER TABLE {table} ALTER COLUMN review_status SET DEFAULT 'pending'")
            )
    yield engine
    with engine.begin() as conn:
        Base.metadata.drop_all(conn)
    engine.dispose()


@pytest.fixture
def exec_conn(exec_engine):
    """Transactional connection — every test's DML is rolled back."""
    conn = exec_engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()


def _run_migration(conn, migration_module, func_name: str) -> None:
    """Run ``upgrade()``/``downgrade()`` bound to a SQLAlchemy connection."""
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(migration_module, func_name)()


def _seed_materials(conn) -> None:
    conn.execute(
        text(
            "INSERT INTO materials (id, name, is_active) VALUES "
            f"('{_U10MO_MATERIAL}', 'U-10Mo', TRUE), "
            f"('{_U3SI_MATERIAL}', 'U-3Si', TRUE), "
            f"('{_PUO2_MATERIAL}', 'PuO2', TRUE)"
        )
    )


def _seed_prod_shape(conn) -> None:
    """Seed the prod-shaped pre-080 state the migration was written for.

    Mirrors what exists on prod immediately before 080: the three
    materials, 072's U-10Mo ``Material`` kg_node, 072's two orphan
    bucket-D anchor nodes, and the three 075-restored U-10Mo datasets
    (``source_id`` NULL is the honest placeholder attribution).
    """
    _seed_materials(conn)
    conn.execute(
        text(
            "INSERT INTO kg_nodes "
            "(id, node_type, label, properties, confidence, status, "
            " synced_to_graph, review_status) VALUES "
            f"('{_U10MO_NODE}', 'Material', 'U-10Mo', '{{}}', 1.0, 'active', FALSE, 'approved'), "
            f"('{_ANCHOR_U}', 'Material', 'alpha_U_solid_solution', '{{}}', 1.0, 'active', FALSE, 'approved'), "
            f"('{_ANCHOR_PU}', 'Material', 'delta_Pu_solid_solution', '{{}}', 1.0, 'active', FALSE, 'approved')"
        )
    )
    conn.execute(
        text(
            "INSERT INTO datasets (id, material_id, source_id, title, is_verified) VALUES "
            + ", ".join(
                f"('{ds}', '{_U10MO_MATERIAL}', NULL, 'U-10Mo dataset {i + 1}', FALSE)"
                for i, ds in enumerate(_DS_IDS)
            )
        )
    )


def _bridge_counts(conn) -> dict[str, int]:
    """Post-upgrade counts the AC assertions read."""
    provenance = "NFM-4185:migration 080"
    return {
        "measurement_nodes": conn.execute(
            text(
                "SELECT count(*) FROM kg_nodes "
                "WHERE node_type = 'Measurement' "
                f"  AND properties->>'provenance' = '{provenance}'"
            )
        ).scalar(),
        "contains_data_edges": conn.execute(
            text(
                "SELECT count(*) FROM kg_edges e "
                "JOIN kg_nodes s ON s.id = e.source_node_id "
                "WHERE s.label = 'U-10Mo' AND e.relation_type = 'containsData' "
                f"  AND e.properties->>'provenance' = '{provenance}'"
            )
        ).scalar(),
        "stub_nodes": conn.execute(
            text(
                "SELECT count(*) FROM kg_nodes "
                "WHERE node_type = 'Material' AND label IN ('U-3Si', 'PuO2') "
                "  AND source_id IS NULL "
                f"  AND properties->>'provenance' = '{provenance}'"
            )
        ).scalar(),
        "related_to_edges": conn.execute(
            text(
                "SELECT count(*) FROM kg_edges "
                "WHERE relation_type = 'relatedTo' "
                f"  AND properties->>'provenance' = '{provenance}'"
            )
        ).scalar(),
        "null_target_edges": conn.execute(
            text("SELECT count(*) FROM kg_edges WHERE target_node_id IS NULL")
        ).scalar(),
        "provenance_nodes_total": conn.execute(
            text(f"SELECT count(*) FROM kg_nodes WHERE properties->>'provenance' = '{provenance}'")
        ).scalar(),
        "organic_nodes": conn.execute(
            text(
                "SELECT count(*) FROM kg_nodes "
                "WHERE properties->>'provenance' IS DISTINCT FROM "
                f"'{provenance}'"
            )
        ).scalar(),
    }


class TestMigration080Execution:
    """Execute the real migration against a disposable Postgres."""

    def test_upgrade_on_empty_schema_is_clean_noop(self, exec_conn, migration_module) -> None:
        """Gate review finding 1 regression pin — the fresh-DB crash.

        Pre-fix: the unconditional stub-node INSERT created U-3Si/PuO2
        Material nodes on any database, then the relatedTo INSERT
        resolved the (absent) anchor to NULL and wrote a NULL
        ``target_node_id`` — a NOT NULL violation that failed
        ``alembic upgrade head`` on every fresh database.  Post-fix the
        whole upgrade is a clean no-op: no nodes, no edges, no crash.
        """
        _run_migration(exec_conn, migration_module, "upgrade")

        counts = _bridge_counts(exec_conn)
        assert counts["provenance_nodes_total"] == 0, (
            "empty database must gain zero provenance-tagged nodes"
        )
        assert counts["related_to_edges"] == 0
        assert counts["null_target_edges"] == 0

    def test_absent_anchors_insert_stubs_but_no_edges(self, exec_conn, migration_module) -> None:
        """The sharpest finding-1 pin: materials exist, anchors absent.

        Shape: a database where the data migrations ran but 072's
        bucket-D anchor inserts were skipped (their guard requires a
        prod-only data_sources row).  The stub Material nodes ARE
        wanted here (their materials exist), but the relatedTo edges
        must be a clean no-op — pre-fix this exact state crashed with
        a NOT NULL violation on ``kg_edges.target_node_id``.
        """
        _seed_materials(exec_conn)

        _run_migration(exec_conn, migration_module, "upgrade")

        counts = _bridge_counts(exec_conn)
        assert counts["stub_nodes"] == 2, (
            "U-3Si and PuO2 stub nodes must be created when their materials rows exist"
        )
        assert counts["related_to_edges"] == 0, (
            "absent anchors must yield zero relatedTo edges, not a crash"
        )
        assert counts["null_target_edges"] == 0

    def test_upgrade_bridges_u10mo_and_stubs_on_prod_shape(
        self, exec_conn, migration_module
    ) -> None:
        """Happy path: the full NFM-4185 AC shape lands on the prod shape."""
        _seed_prod_shape(exec_conn)

        _run_migration(exec_conn, migration_module, "upgrade")

        counts = _bridge_counts(exec_conn)
        assert counts["measurement_nodes"] == 3, "one Measurement node per U-10Mo dataset (AC1)"
        assert counts["contains_data_edges"] == 3, (
            "one containsData edge per dataset (AC1: >=3 edges)"
        )
        assert counts["stub_nodes"] == 2, "U-3Si + PuO2 Material nodes with NULL source (AC2)"
        assert counts["related_to_edges"] == 2, "one relatedTo edge per stub (AC2: >=1 edge each)"
        assert counts["null_target_edges"] == 0

        # AC2 targets: each stub's relatedTo lands on its designated anchor.
        anchor_pairs = exec_conn.execute(
            text(
                "SELECT s.label, t.label FROM kg_edges e "
                "JOIN kg_nodes s ON s.id = e.source_node_id "
                "JOIN kg_nodes t ON t.id = e.target_node_id "
                "WHERE e.relation_type = 'relatedTo' "
                "  AND e.properties->>'provenance' = 'NFM-4185:migration 080' "
                "ORDER BY s.label"
            )
        ).all()
        assert [(s, t) for s, t in anchor_pairs] == [
            ("PuO2", "delta_Pu_solid_solution"),
            ("U-3Si", "alpha_U_solid_solution"),
        ]

        # AC1 dataset linkage: Measurement nodes carry the durable dataset_id key.
        linked = (
            exec_conn.execute(
                text(
                    "SELECT kn.properties->>'dataset_id' FROM kg_nodes kn "
                    "WHERE kn.node_type = 'Measurement' "
                    "  AND kn.properties->>'provenance' = 'NFM-4185:migration 080' "
                    "ORDER BY 1"
                )
            )
            .scalars()
            .all()
        )
        assert sorted(linked) == sorted(_DS_IDS)

    def test_upgrade_is_idempotent(self, exec_conn, migration_module) -> None:
        """A second upgrade() on the same state changes nothing."""
        _seed_prod_shape(exec_conn)

        _run_migration(exec_conn, migration_module, "upgrade")
        first = _bridge_counts(exec_conn)
        assert first["provenance_nodes_total"] == 5, "3 Measurement + 2 stub nodes"

        _run_migration(exec_conn, migration_module, "upgrade")
        second = _bridge_counts(exec_conn)

        assert second == first, f"idempotent upgrade changed counts: {first} -> {second}"

    def test_downgrade_removes_only_provenance_rows(self, exec_conn, migration_module) -> None:
        """downgrade() deletes exactly this migration's rows, nothing organic."""
        _seed_prod_shape(exec_conn)

        _run_migration(exec_conn, migration_module, "upgrade")
        _run_migration(exec_conn, migration_module, "downgrade")

        counts = _bridge_counts(exec_conn)
        assert counts["provenance_nodes_total"] == 0, (
            "downgrade must remove every provenance-tagged node"
        )
        assert counts["related_to_edges"] == 0
        assert counts["contains_data_edges"] == 0
        assert counts["measurement_nodes"] == 0
        assert counts["organic_nodes"] == 3, (
            "the seeded organic rows (U-10Mo focal node + two anchors) survive"
        )
        datasets = exec_conn.execute(text("SELECT count(*) FROM datasets")).scalar()
        assert datasets == 3, "downgrade never touches datasets"
