"""KG orphan bridge — U-10Mo dataset edges + U-3Si/PuO2 stub nodes.

Revision ID: 080_kg_orphan_bridge_u10mo_u3si_puo2
Revises: 079_restore_070_measurement_casualties
Create Date: 2026-09-02

NFM-4185 — CPO-dispatched fix for the CTO post-landing verification of
NFM-4093 (executed 2026-09-02 ~13:45Z against the prod API)
====================================================================

Two defects, both diagnosed read-only against the prod DB before this
migration was written.

D1 — the U-10Mo ``Material`` kg_node is an orphan (n=1 / e=0 at every
---------------------------------------------------------------------
depth)
``nodeId=8537da2d-edac-47be-9b0d-ce61283b143f`` resolves to the kg_node
``c9291967-01c8-4ca4-81d8-eb8003ac0a67`` but no ``kg_edges`` touch it,
so the 3 U-10Mo datasets are invisible in the knowledge graph.

Root-cause chain (verified on prod 2026-09-02):

1. Migration ``070_d2_dedup_bad_data_sources`` collapsed the
   placeholder ``data_sources`` rows and CASCADE-deleted the
   ``datasets`` rows referencing them — including the 3 U-10Mo
   placeholder-titled datasets.
2. Migration ``072_material_kg_bridge_coverage`` then ran its
   U-10Mo dedup/repoint UPDATE against a state where those datasets
   no longer existed — the UPDATE matched **0 rows** (a silent
   no-op).  The U-10Mo ``Material`` kg_node insert itself succeeded
   (its ``source_id`` resolves via the OECD-NEA subquery, which does
   not depend on the datasets).
3. Migration ``075_restore_placeholder_sources_datasets`` (NFM-4139 /
   NFM-4135 verdict Option B) restored the 10 deleted datasets from
   ``datasets_backup_070`` with their **original** placeholder
   ``source_id`` values — honestly re-instating the rows, and with
   them the placeholder attribution that 072 had intended to replace.
4. No migration in the 070+ family ever created dataset-representing
   ``kg_nodes`` or Material->dataset ``kg_edges`` — 072 only inserted
   the ``Material`` nodes.

Fix shipped here: one ``Measurement`` kg_node per U-10Mo dataset
(``properties.dataset_id`` is the durable link back to the
``datasets`` row), plus one ``containsData`` edge from the U-10Mo
``Material`` node to each.  ``containsData`` is the canonical
``RelationType.CONTAINS_DATA`` and ``node_type='Measurement'`` is the
closest canonical entity type for a dataset (the ``ck_kg_nodes_node_type``
CHECK has no 'Dataset' value; the extraction map routes data-point /
observation entities to 'Measurement').

Deliberately NOT re-pointing ``datasets.source_id`` to the OECD-NEA
row: all 3 datasets share one ``material_id``, so pointing them at a
single source would violate ``uq_datasets_source_material``, and
de-duplicating down to one row would contradict the NFM-4135
restore.  The dataset nodes carry the datasets' *current* (honest
placeholder) ``source_id``; the U-10Mo ``Material`` node keeps the
OECD-NEA attribution 072 already applied.

D2 — U-3Si / PuO2 have no ``Material`` kg_node at all (404)
-----------------------------------------------------------
``materials`` rows exist (``06df3e99-…`` U-3Si, ``619008d5-…`` PuO2)
but no ``kg_nodes`` row matches, so the NFM-4083 material bridge 404s.

Root cause: migration 072's stub-node INSERT carried an
``AND EXISTS (SELECT 1 FROM data_sources ds WHERE ds.id =
CAST(:src_id AS uuid))`` guard.  For the three no-dataset stubs
(PuO2, Test, U-3Si) ``src_id`` was ``NULL``, and ``ds.id = NULL``
evaluates to NULL — so the ``EXISTS`` guard was never true and the
INSERT silently skipped every stub row.  ``kg_nodes.source_id`` is
nullable by schema (FK ``ON DELETE SET NULL``); the guard was simply
wrong.

Fix shipped here: insert the two ``Material`` nodes with
``source_id = NULL`` and **no** ``data_sources`` existence guard
(pinned by ``test_migration_080_kg_orphan_bridge.py::
TestMigration080StubNodes::test_stub_node_sql_has_no_source_exists_guard``).

Each stub then gets one ``relatedTo`` edge to an **orphan** bucket-D
anchor so the subgraphs return 200 with >=1 edge (NFM-4185 AC2):

* ``U-3Si  -relatedTo-> alpha_U_solid_solution``  (both uranium-system
  entries; U-3Si is a uranium intermetallic, alpha-U the elemental
  uranium reference phase)
* ``PuO2   -relatedTo-> delta_Pu_solid_solution`` (both plutonium-system
  entries; PuO2 is the plutonium oxide, delta-Pu the elemental plutonium
  reference phase)

Anchors were chosen under the AC3 no-regression invariant: the 60
currently edge-bearing materials must keep n/e within +/-0, so new
edges may only attach to nodes that are **not** reachable from any
edge-bearing focal — i.e. current orphan singletons.  Both anchors
are orphan bucket-D "legitimate" rows (not property slices, not
pseudo-materials) created by migration 072.  Chemically honest
alternatives (PuO2->UO2 oxide kinship, U-3Si->U) are edge-bearing and
therefore AC3-forbidden.

Scope guards
------------
* The 'Test' material row stays node-less (CPO dispatch note 4).
* The ~46 property-slice / pseudo-material orphans are untouched —
  that cohort belongs to the NFM-4093-DATA-CLEANUP follow-up.
* All inserts are idempotent (``WHERE NOT EXISTS``, the NFM-4095
  migration-071 family pattern) and every inserted row carries
  ``properties.provenance = 'NFM-4185:migration 080'`` so the
  downgrade deletes exactly this migration's rows and never organic
  ones.
* Empty-database safe: every INSERT…SELECT simply matches 0 rows.

Cross-references
----------------
* NFM-4185 — this fix (parent NFM-4093, ancestor NFM-4083)
* NFM-4093 / NFM-4095 — the 57-material bridge (072) whose residual
  gaps this closes
* NFM-4088 / NFM-4135 / NFM-4139 — the 070 dedup + 075 restore
  sequence that silently no-op'd 072's U-10Mo repoint
* NFM-4083 — the material->focal bridge this feeds
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "080_kg_orphan_bridge_u10mo_u3si_puo2"
down_revision: str | Sequence[str] | None = "079_restore_070_measurement_casualties"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Provenance tag stamped on every row this migration inserts.  The
#: downgrade scopes its DELETEs to this tag so organic rows (and any
#: future re-extraction) are never clobbered.
_PROVENANCE: str = "NFM-4185:migration 080"

#: The headline material whose datasets get the containsData bridge.
_U10MO_MATERIAL_NAME: str = "U-10Mo"

#: The two no-dataset stub materials that 072's NULL-source guard
#: silently skipped (D2).  'Test' is deliberately absent — out of
#: scope per the CPO dispatch note 4.
_STUB_MATERIAL_LABELS: tuple[str, ...] = ("U-3Si", "PuO2")

#: relatedTo anchor per stub — orphan bucket-D "legitimate" rows only
#: (AC3 forbids touching any of the 60 edge-bearing materials, and the
#: pseudo-material cohort is out of scope).
_STUB_RELATED_TO_ANCHORS: dict[str, str] = {
    "U-3Si": "alpha_U_solid_solution",
    "PuO2": "delta_Pu_solid_solution",
}

#: Rationale recorded on each stub relatedTo edge so reviewers can see
#: why the anchor was chosen without grepping the migration.
_STUB_ANCHOR_RATIONALES: dict[str, str] = {
    "U-3Si": "uranium-system kinship: U-3Si intermetallic <-> alpha-U reference phase",
    "PuO2": "plutonium-system kinship: PuO2 oxide <-> delta-Pu reference phase",
}


# ---------------------------------------------------------------------------
# SQL builders — plain DML only (NFM-4099: no DO $$ blocks; asyncpg
# cannot bind parameters into plpgsql blocks).
# ---------------------------------------------------------------------------


def _build_u10mo_dataset_nodes_sql() -> str:
    """Render the INSERT of one Measurement node per U-10Mo dataset.

    Idempotency key is ``properties->>'dataset_id'`` — the datasets all
    share the same ``title``, so label-based keys cannot distinguish
    them (``kg_nodes`` has no UNIQUE on ``(node_type, label)``).
    """
    return f"""
        INSERT INTO kg_nodes (
            node_type, label, properties, confidence, source_id, status,
            extraction_method
        )
        SELECT
            'Measurement',
            d.title,
            jsonb_build_object(
                'dataset_id', d.id::text,
                'provenance', '{_PROVENANCE}'
            ),
            1.0,
            d.source_id,
            'active',
            'manual'
        FROM datasets d
        JOIN materials m ON m.id = d.material_id
        WHERE m.name = '{_U10MO_MATERIAL_NAME}'
          AND NOT EXISTS (
              SELECT 1 FROM kg_nodes kn
              WHERE kn.node_type = 'Measurement'
                AND kn.properties->>'dataset_id' = d.id::text
          )
        """


def _build_u10mo_contains_data_edges_sql() -> str:
    """Render the INSERT of Material-containsData->Measurement edges.

    The target Measurement node is resolved via
    ``properties->>'dataset_id'`` so identical dataset titles can never
    cross-link.  The source Material node is resolved by label against
    the same material row (the node migration 072 created).
    """
    return f"""
        INSERT INTO kg_edges (
            source_node_id, target_node_id, relation_type,
            properties, confidence, extraction_method
        )
        SELECT
            mat.id,
            dsnode.id,
            'containsData',
            jsonb_build_object(
                'dataset_id', d.id::text,
                'provenance', '{_PROVENANCE}'
            ),
            1.0,
            'manual'
        FROM datasets d
        JOIN materials m ON m.id = d.material_id
        JOIN kg_nodes mat
          ON mat.node_type = 'Material'
         AND mat.label = m.name
         AND mat.status = 'active'
        JOIN kg_nodes dsnode
          ON dsnode.node_type = 'Measurement'
         AND dsnode.properties->>'dataset_id' = d.id::text
        WHERE m.name = '{_U10MO_MATERIAL_NAME}'
          AND NOT EXISTS (
              SELECT 1 FROM kg_edges e
              WHERE e.source_node_id = mat.id
                AND e.target_node_id = dsnode.id
                AND e.relation_type = 'containsData'
          )
        """


def _build_stub_material_nodes_sql() -> str:
    """Render the INSERT of the U-3Si / PuO2 Material stub nodes.

    D2 root-cause fix: NO ``data_sources`` existence guard.  072's
    ``AND EXISTS (… ds.id = CAST(:src_id AS uuid))`` was never true for
    the NULL-source stubs, silently skipping every insert;
    ``kg_nodes.source_id`` is nullable by schema.
    """
    return """
        INSERT INTO kg_nodes (
            node_type, label, properties, confidence, source_id, status,
            extraction_method
        )
        SELECT 'Material', CAST(:label AS text), CAST(:props AS jsonb),
               1.0, NULL, 'active', 'manual'
        WHERE NOT EXISTS (
            SELECT 1 FROM kg_nodes kn
            WHERE kn.node_type = 'Material'
              AND kn.label = CAST(:label AS text)
        )
        """


def _build_stub_related_to_edges_sql() -> str:
    """Render the INSERT of the stub Material-relatedTo->anchor edges.

    Source and target node ids resolve via scalar subqueries (NULL when
    the node is absent, e.g. on a fresh database where 072's bucket-D
    inserts did not apply) — the trailing ``IS NOT NULL`` guards then
    make the INSERT a clean no-op instead of a NOT NULL violation.
    """
    return f"""
        INSERT INTO kg_edges (
            source_node_id, target_node_id, relation_type,
            properties, confidence, extraction_method
        )
        SELECT
            (SELECT id FROM kg_nodes
              WHERE node_type = 'Material' AND label = CAST(:src_label AS text)
                AND status = 'active'
              ORDER BY created_at LIMIT 1),
            (SELECT id FROM kg_nodes
              WHERE node_type = 'Material' AND label = CAST(:anchor_label AS text)
                AND status = 'active'
              ORDER BY created_at LIMIT 1),
            'relatedTo',
            jsonb_build_object(
                'provenance', '{_PROVENANCE}',
                'rationale', CAST(:rationale AS text)
            ),
            1.0,
            'manual'
        WHERE NOT EXISTS (
            SELECT 1 FROM kg_edges e
            WHERE e.source_node_id = (
                    SELECT id FROM kg_nodes
                     WHERE node_type = 'Material'
                       AND label = CAST(:src_label AS text)
                       AND status = 'active'
                     ORDER BY created_at LIMIT 1
                )
              AND e.target_node_id = (
                    SELECT id FROM kg_nodes
                     WHERE node_type = 'Material'
                       AND label = CAST(:anchor_label AS text)
                       AND status = 'active'
                     ORDER BY created_at LIMIT 1
                )
              AND e.relation_type = 'relatedTo'
        )
        """


def _build_downgrade_cleanup_sql() -> list[str]:
    """Render the downgrade DELETEs, scoped to the provenance tag.

    Every row this migration inserts carries
    ``properties.provenance = 'NFM-4185:migration 080'``; the downgrade
    removes exactly those rows.  Edges are deleted before nodes
    (the FK would CASCADE anyway, but explicit ordering keeps the
    intent readable).  Organic nodes/edges — including any future
    re-extraction of the same datasets — are never touched.

    Returns a list of single statements — asyncpg's prepared-statement
    interface executes one statement per call (the NFM-4169
    migration-073 lesson), so each DELETE gets its own
    ``bind.execute``.
    """
    return [
        f"DELETE FROM kg_edges WHERE properties->>'provenance' = '{_PROVENANCE}'",
        f"DELETE FROM kg_nodes WHERE properties->>'provenance' = '{_PROVENANCE}'",
    ]


# ---------------------------------------------------------------------------
# Forward (upgrade)
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Idempotent forward migration — close the NFM-4185 KG orphan gaps.

    * D1: one Measurement node per U-10Mo dataset + a containsData
      edge from the U-10Mo Material node to each (AC1: depth-1
      subgraph becomes self + 3 dataset nodes, 3 edges).
    * D2: Material nodes for U-3Si / PuO2 + one relatedTo edge each
      to an orphan bucket-D anchor (AC2: 200 with >=1 edge).

    Neither step touches any node reachable from the 60 edge-bearing
    materials, so AC3's n/e +/-0 invariant holds by construction.
    """
    bind = op.get_bind()

    # --------------------------------------------------------------
    # D1 — U-10Mo dataset bridge.
    # --------------------------------------------------------------
    bind.execute(sa.text(_build_u10mo_dataset_nodes_sql()))
    bind.execute(sa.text(_build_u10mo_contains_data_edges_sql()))

    # --------------------------------------------------------------
    # D2 — stub Material nodes (NULL source, no source-exists guard).
    # --------------------------------------------------------------
    for label in _STUB_MATERIAL_LABELS:
        bind.execute(
            sa.text(_build_stub_material_nodes_sql()),
            {
                "label": label,
                "props": json.dumps({"provenance": _PROVENANCE}),
            },
        )

    # --------------------------------------------------------------
    # D2 — one relatedTo edge per stub to its orphan anchor.
    # --------------------------------------------------------------
    for label in _STUB_MATERIAL_LABELS:
        bind.execute(
            sa.text(_build_stub_related_to_edges_sql()),
            {
                "src_label": label,
                "anchor_label": _STUB_RELATED_TO_ANCHORS[label],
                "rationale": _STUB_ANCHOR_RATIONALES[label],
            },
        )


# ---------------------------------------------------------------------------
# Backward (downgrade)
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Remove exactly the rows this migration inserted.

    Scoped by the provenance tag — see
    :func:`_build_downgrade_cleanup_sql`.  Data-only migration, so
    there is no DDL to reverse.
    """
    bind = op.get_bind()
    for statement in _build_downgrade_cleanup_sql():
        bind.execute(sa.text(statement))
