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
``test_migration_057_create_kg_type_tables.py`` patterns: they are
**structural** (file contents + rendered SQL + alembic chain graph),
no live PostgreSQL required.  Functional verification against the
real prod shape is covered by the API-shape tests in
``test_kg_graph_coverage.py::TestNFM4185*`` and by E2E.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

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

    def test_down_revision_is_078(self, script_directory: ScriptDirectory) -> None:
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
        assert "WHERE NOT EXISTS" in sql, (
            "Stub-node INSERT must stay idempotent by (node_type, label)"
        )

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
        sql = migration_module._build_stub_related_to_edges_sql()
        assert "'relatedTo'" in sql
        assert "WHERE NOT EXISTS" in sql


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
