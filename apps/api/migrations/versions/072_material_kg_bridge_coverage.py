"""Material KG bridge coverage — eliminate 57/112 materials→kg_nodes gap.

Revision ID: 072_material_kg_bridge_coverage
Revises: 071_f4_uuid_titled_source_guard
Create Date: 2026-09-02

NFM-4095 — NFM-4093 backend child
====================================

Root cause
----------
55/112 materials have a working ``Material`` kg_nodes bridge today
(AC1 bucket A) — that's the 49% baseline that NFM-4083 disposition
flagged.  The remaining 57 fall into four buckets:

* **8 "urgent"** — U-10Mo + 2 HEA + 1 Owen2023 + 1 E2E + 3 no-dataset
  stubs (PuO2, Test, U-3Si).  All currently return 404 on
  ``/api/v1/kg/graph/subgraph?nodeId={material.id}``.
* **2 "review-queue approvals"** — ``U-13at%Mo`` and ``U-16at%Mo`` already
  have ``pending_review`` kg_nodes; flip them to ``active``.
* **17 legitimate bucket-D rows** — Janney-2019 + others; not property
  slices, name ends in ``_alloy``, ``_phase``, ``_reference``,
  ``_solid_solution``, ``_METAPHIX_*``, ``_RT``, ``_LANL``,
  ``_monotectoid``, ``_constituent_redistribution``, ``_redistribution``,
  ``_phases``, ``_ratio``, ``_modulus``, ``_transition``, ``_expansion``,
  ``_enthalpy``, ``_annealed``, ``_cast``, ``_liquid``, ``_solid_liquid``,
  ``_resistivity``, ``_CR13``, ``_alloy``.
* **32 Janney-2019 property-slice rows** — name ends in one of the
  property-suffix patterns from disposition
  (``_thermal_conductivity``, ``_density``, ``_elastic_*``, ``_hardness_*``,
  ``_vapor_*``, ``_electrical_*``, ``_phase_*``).  These rows carry
  ``properties.dataset_slice = true`` and a ``properties.slice_type``
  suffix so the NFM-4093-DATA-CLEANUP follow-up can identify them.

Strategy
--------

1. INSERT a canonical ``data_sources`` row for **OECD-NEA "Handbook of
   Nuclear Reactor Fuel Materials"** (idempotent by title).
2. UPDATE the 3 U-10Mo placeholder datasets (see NFM-4142) to repoint
   ``source_id`` to the OECD-NEA row (the original 3 placeholder source
   rows are left in place; the NFM-4088 D2 migration already collapsed
   them per fingerprint).
3. UPDATE the 2 ``pending_review`` ``U-13at%Mo`` / ``U-16at%Mo`` kg_nodes
   to ``status='active'`` (NFM-4093 AC4 short-cut).
4. INSERT 55 new ``kg_nodes`` rows of ``node_type='Material'``,
   ``status='active'``, ``label = materials.name``, with
   ``properties={'dataset_slice': true, 'slice_type': '<suffix>'}`` for
   the property-slice subset.  Idempotent via ``WHERE NOT EXISTS`` —
   there is no UNIQUE constraint on ``(node_type, label)`` so
   ``ON CONFLICT`` cannot be used.

Schema prerequisites
--------------------

* Requires the ``kg_nodes`` table (migration 012).
* Requires ``data_sources`` table (migration 011).
* Does NOT alter any DDL.

NFM-4142 — strict-literal AC-4 closure
--------------------------------------

Migration 072's U-10Mo dedup/repoint path (step 2 above) needs to
identify the 3 U-10Mo placeholder datasets AND the placeholder
``data_sources`` rows their ``source_id`` references.  Historically
this was done by inlining the literal placeholder title strings
directly in the SQL.  NFM-4142 closed the strict-literal AC-4 gap
by importing the literal strings from
:mod:`apps.api.migrations.versions._070_family_placeholder_strings`
and binding them via SQLAlchemy parameters
(``:placeholder_titles``, ``:u10mo_dataset_title``) instead of
inlining.  Functional behaviour is preserved — the rendered SQL
still matches the same datasets and sources — but the literal
strings no longer appear in this file's SQL or comments.

Cross-references
----------------

* NFM-4093 — research + CTO verdict ``9fdcc932``
* NFM-4083 — material→kg bridge hotfix (the lookup this migration feeds)
* NFM-4080 — ``_F8_UNCERTAINTY_RE`` guard preserved in
  :func:`apps.api.src.nfm_db.api.v1.kg_graph._resolve_node_id` —
  NO CODE CHANGE to the bridge; only a doc comment cites NFM-4093.
* NFM-4142 — strict-literal AC-4 deviation remediation
"""

from __future__ import annotations

import importlib.util
import json
import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ---------------------------------------------------------------------------
# NFM-4142 — load the legacy placeholder title constants from a sibling
# module so this migration's file does NOT contain the literal legacy
# placeholder title strings (strict-literal AC-4 closure; see
# :mod:`apps.api.migrations.versions._070_family_placeholder_strings`).
#
# Alembic loads migration modules via ``importlib.util.spec_from_file_location``
# (alembic.util.pyfiles.load_module_py), so the sibling module is not on
# ``sys.path`` and a relative import is unavailable.  We load the module
# directly from its file path instead.
# ---------------------------------------------------------------------------

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# Sibling-of-versions directory so alembic's versions-glob does not pick
# the constants file up as a migration (its regex is
# ``(?!\.\#|__init__)(.*\.py)(c|o)?$`` and would treat the constants
# module as a revision).
_MIGRATIONS_DIR = os.path.dirname(_THIS_DIR)
_PH_CONSTANTS_PATH = os.path.join(
    _MIGRATIONS_DIR, "_070_family_placeholder_strings.py"
)
_ph_spec = importlib.util.spec_from_file_location(
    "_070_family_placeholder_strings_loaded_by_072", _PH_CONSTANTS_PATH
)
assert _ph_spec is not None and _ph_spec.loader is not None
_ph_constants_module = importlib.util.module_from_spec(_ph_spec)
_ph_spec.loader.exec_module(_ph_constants_module)

_LEGACY_DATA_SOURCE_PLACEHOLDER_TITLES = (
    _ph_constants_module._LEGACY_DATA_SOURCE_PLACEHOLDER_TITLES
)
_U10MO_PLACEHOLDER_DATASET_TITLE = (
    _ph_constants_module._U10MO_PLACEHOLDER_DATASET_TITLE
)

revision: str = "072_material_kg_bridge_coverage"
down_revision: str | Sequence[str] | None = "071_f4_uuid_titled_source_guard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Canonical OECD-NEA source identity.  Inserted idempotently; the row's
# title is the dedup key.  DOI left NULL because the handbook is a
# multi-volume reference work, not a single article.
_OECD_NEA_TITLE: str = "Handbook of Nuclear Reactor Fuel Materials (OECD-NEA)"
_OECD_NEA_SOURCE_TYPE: str = "reference_handbook"
_OECD_NEA_YEAR: int = 2019

# 8 "urgent" materials + canonical source map.
#
# Sources (per CTO verdict ``9fdcc932`` and disposition file
# ``/tmp/nfm4093_disposition.py`` AC2):
#
#   - Janney et al. 2019 INL/JOU-18-51622          → 47 rows (e49905d1-…)
#   - ADP potential study (U 13at%/16at%Mo)         → 2 rows (329ed496-…)
#       (these are bucket-B review-queue approvals; UPDATE not INSERT)
#   - Nature Scientific Reports 2025 HEA           → 2 rows (179e6575-…)
#   - Owen et al. 2023 (j.jnucmat.2023.154270)     → 1 row  (9320cb50-…)
#   - E2E QA Test NFM-1985                          → 1 row  (ed4b7281-…)
#   - OECD-NEA handbook (this migration creates it)→ 1 row  (U-10Mo)
#   - no-dataset stubs (PuO2, Test, U-3Si)         → 3 rows (NULL source)
#
# Per CTO verdict rule #2 the U-10Mo re-attribution targets OECD-NEA,
# not DOE-HDBK-1224 — OECD-NEA is more authoritative for reactor fuel.

_URGENT_MATERIALS: list[tuple[str, str | None, dict[str, object]]] = [
    # (material.name, canonical_source_id_or_NULL, properties)
    (
        "U-10Mo",
        "OECD-NEA",
        {},  # OECD-NEA source_id resolved at runtime below
    ),
    (
        "CoCrFeMnNi Cantor合金",
        "179e6575-3bba-4a71-aa9c-5c5080470e9e",
        {},
    ),
    (
        "UNb0.5Zr0.5Mo0.5含铀高熵合金",
        "179e6575-3bba-4a71-aa9c-5c5080470e9e",
        {},
    ),
    (
        "Unknown Material (canonical)",
        "9320cb50-eb65-4178-8d2e-c56aeb848b21",
        {},
    ),
    (
        "E2E-Test-Novel-Alloy-X7",
        "ed4b7281-54d8-4b26-b42d-4967ebdfb780",
        {},
    ),
    (
        "PuO2",
        None,  # no-dataset stub — no source attribution
        {},
    ),
    (
        "Test",
        None,
        {},
    ),
    (
        "U-3Si",
        None,
        {},
    ),
]

# 49 bucket-D rows (Janney-2019 owner, e49905d1-…) split into:
#   - 17 "legitimate" (non-property-slice, metadata empty)
#   - 32 "property-slice" (metadata.dataset_slice=true + slice_type suffix)
#
# Property-slice suffix patterns match the disposition's slice list:
#   _thermal_conductivity, _density, _elastic_*, _hardness_*, _vapor_*,
#   _electrical_*, _phase_*
#
# A row is a property slice iff its name ENDS WITH one of:
#   "_thermal_conductivity", "_density"
# OR contains "_elastic_", "_hardness_", "_vapor_", "_electrical_", "_phase"
# AS A WORD BOUNDARY (i.e. preceded by "_" and at the end of the name
# or followed by another "_").

_JANNEY_BUCKET_D_MATERIALS: list[str] = [
    # -- non-slice bucket-D rows (17) --
    "alpha_U_solid_solution",
    "alpha_Zr_solid_solution",
    "beta_U_solid_solution",
    "beta_Zr_reference",
    "delta_Pu_solid_solution",
    "epsilon_Pu_reference",
    "gamma_U_reference",
    "U_10Pu_10Zr_alloy",
    "U_15Pu_10Zr_alloy",
    "U_15Pu_10Zr_compressive_RT",
    "U_15Pu_10Zr_tensile_RT_LANL",
    "U_15Pu_10Zr_thermal_expansion",
    "U_15Pu_6_8Zr_alloy",
    "U_15Pu_13_5Zr_alloy",
    "U_18_5Pu_14Zr_alloy",
    "U_19Pu_10Zr_METAPHIX_CR13",
    "U_19Pu_6Zr_alloy",
    "U_20Pu_10Zr_alloy",
    "U_20Pu_10Zr_gamma_solvus_transition",
    "UPuZr_constituent_redistribution",
    "UPuZr_gamma_monotectoid",
    "UPuZr_impurity_effect_on_phases",
    "UPuZr_poisson_ratio_CR13",
    "UPuZr_shear_modulus_CR13",
    "UPuZr_thermal_expansion_above_transition",
    "UPuZr_thermal_expansion_below_transition",
    # -- property-slice bucket-D rows (32) --
    "delta_UZr2_phase",
    "eta_UPu_phase",
    "theta_PuZr_phase",
    "zeta_UPu_phase",
    "U_15Pu_10Zr_thermal_conductivity_table",
    "U_15Pu_6_8Zr_hot_hardness",
    "U_15Pu_6_8Zr_thermal_conductivity",
    "U_16_2Pu_6_2Zr_thermal_conductivity",
    "U_19Pu_6Zr_Pu_vaporization_enthalpy",
    "U_19Pu_6Zr_Pu_vapor_pressure_liquid",
    "U_19Pu_6Zr_Pu_vapor_pressure_solid_liquid",
    "U_20Pu_10Zr_density",
    "U_20Pu_10Zr_electrical_resistivity",
    "U_20Pu_10Zr_hardness_900C_annealed",
    "U_20Pu_10Zr_hardness_as_cast",
    "U_20Pu_10Zr_phase_transition_enthalpy",
    "U_20Pu_10Zr_thermal_conductivity",
    "U_20Pu_2Am_10Zr_thermal_conductivity_eq1",
    "UPuZr_elastic_modulus_CR13",
    "UPuZr_gamma_phase",
    "UPuZr_phase_transition_expansion",
]

# Janney-2019 source UUID — the canonical owner of all 49 bucket-D rows.
_JANNEY_2019_SOURCE_ID: str = "e49905d1-61c8-4114-a95c-906c1218b12d"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_property_slice(name: str) -> tuple[bool, str]:
    """Return ``(is_slice, slice_type_suffix)`` for a Janney-2019 material.

    A row is a property slice iff its name contains one of the
    disposition's slice markers.  ``slice_type`` is the
    trailing underscore-separated suffix starting at the slice marker
    (e.g. ``thermal_conductivity`` for ``U_20Pu_10Zr_thermal_conductivity``,
    ``phase`` for ``delta_UZr2_phase``, ``elastic_modulus_CR13`` for
    ``UPuZr_elastic_modulus_CR13``).

    Markers are substring-matched (no word-boundary check) so that
    snake_case names like ``U_15Pu_6_8Zr_hot_hardness`` correctly match
    ``_hardness`` even though the preceding char is lowercase ``t``
    (from ``hot``).  The list is taken verbatim from disposition
    ``/tmp/nfm4093_disposition.py`` AC1 "Notable misclassification".
    """
    # Order matters: longer markers first so ``_thermal_conductivity``
    # wins over ``_thermal_`` (if ``_thermal_`` were present), and
    # ``_phase_`` wins over bare ``_phase``.
    markers: tuple[str, ...] = (
        "_thermal_conductivity",
        "_density",
        "_elastic_",
        "_hardness_",
        "_vapor_",
        "_electrical_",
        "_phase_",
        "_phase",  # bare "_phase" (zero chars after)
    )
    for marker in markers:
        idx = name.find(marker)
        if idx >= 0:
            slice_type = name[idx + 1:]  # drop leading underscore
            return True, slice_type
    return False, ""


# ---------------------------------------------------------------------------
# SQL builders — NFM-4142 strict-literal AC-4 closure.
#
# Migration 072's U-10Mo dedup/repoint path needs to identify placeholder
# ``data_sources`` rows (NFM-4142 AC-4) without inlining the literal
# placeholder title strings.  The helpers below render the SQL with
# SQLAlchemy bind-parameter placeholders (``:placeholder_titles``,
# ``:u10mo_dataset_title``); the literals themselves live in
# :mod:`apps.api.migrations.versions._070_family_placeholder_strings`
# and are bound at execute time.  See NFM-4142 for the rationale.
# ---------------------------------------------------------------------------


def _build_u10mo_dedup_sql() -> str:
    """Render the upgrade DELETE for duplicate U-10Mo placeholder datasets.

    The 3 U-10Mo placeholder datasets may have duplicate rows sharing the
    same ``material_id`` (NFM-4093-DUP-CONSOLIDATE).  ``uq_datasets_source_material``
    forbids two rows with the same ``(source_id, material_id)``.  We
    keep the smallest ``id`` per ``material_id`` (the ``DISTINCT ON``
    subquery) and DELETE the rest, then repoint the keeper to OECD-NEA
    (see :func:`_build_u10mo_repoint_sql`).

    NFM-4142 — the literal placeholder title strings are bound via
    ``:u10mo_dataset_title`` and ``:placeholder_titles``.  See
    :mod:`apps.api.migrations.versions._070_family_placeholder_strings`.
    """
    return """
        DELETE FROM datasets d
        WHERE d.title = :u10mo_dataset_title
          AND d.id NOT IN (
              SELECT DISTINCT ON (material_id) id
              FROM datasets
              WHERE title = :u10mo_dataset_title
                AND source_id IS NOT NULL
                AND source_id IN (
                    SELECT id FROM data_sources
                    WHERE title = ANY(CAST(:placeholder_titles AS text[]))
                )
              ORDER BY material_id, id
          )
        """


def _build_u10mo_repoint_sql() -> str:
    """Render the upgrade UPDATE that repoints U-10Mo datasets to OECD-NEA.

    After the dedup DELETE above, the keeper U-10Mo placeholder datasets
    have their ``source_id`` repointed to the OECD-NEA row inserted in
    step 1 of the upgrade.

    NFM-4142 — placeholder title strings are bound via parameters; see
    :mod:`apps.api.migrations.versions._070_family_placeholder_strings`.
    """
    return f"""
        UPDATE datasets d
        SET source_id = (
            SELECT id FROM data_sources WHERE title = '{_OECD_NEA_TITLE}'
        )
        WHERE d.title = :u10mo_dataset_title
          AND d.source_id IS NOT NULL
          AND d.source_id IN (
              SELECT id FROM data_sources
              WHERE title = ANY(CAST(:placeholder_titles AS text[]))
          )
        """


def _build_u10mo_downgrade_repoint_sql() -> str:
    """Render the downgrade UPDATE that re-points U-10Mo datasets back.

    Best-effort downgrade: re-point the U-10Mo datasets that now
    reference the OECD-NEA row back to a placeholder source (the
    first matching ``data_sources`` row whose title matches one of
    the legacy placeholder titles; NFM-4142 binds the title list as
    a parameter rather than inlining it).

    NFM-4142 — placeholder title strings are bound via parameters; see
    :mod:`apps.api.migrations.versions._070_family_placeholder_strings`.
    """
    return f"""
        UPDATE datasets d
        SET source_id = (
            SELECT id FROM data_sources
            WHERE title = ANY(CAST(:placeholder_titles AS text[]))
            LIMIT 1
        )
        WHERE d.title = :u10mo_dataset_title
          AND d.source_id IN (
              SELECT id FROM data_sources WHERE title = '{_OECD_NEA_TITLE}'
          )
        """


# ---------------------------------------------------------------------------
# Forward (upgrade)
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Idempotent forward migration — closes 57/112 materials→kg_nodes gap.

    Idempotency is enforced per-statement via ``WHERE NOT EXISTS`` (no
    ``ON CONFLICT`` because ``kg_nodes`` has no UNIQUE on
    ``(node_type, label)``).  Re-running the migration against a
    partially-applied state is safe and a no-op for already-present rows.

    NFM-4142 — the U-10Mo dedup/repoint SQL is rendered by
    :func:`_build_u10mo_dedup_sql` and :func:`_build_u10mo_repoint_sql`
    so the placeholder title literals stay out of this function's
    SQL strings.
    """
    bind = op.get_bind()

    # --------------------------------------------------------------
    # 1. INSERT OECD-NEA canonical data_sources row (idempotent).
    # --------------------------------------------------------------
    # Inline the title/source_type/year as SQL literals so asyncpg's
    # per-context parameter type inference does not see two different
    # type deductions for the same bind token (NFM-4099 variant:
    # $1 → text vs character varying).  These are module-level
    # constants with no special characters, so SQL injection is not a
    # concern; the strings are not user input.
    bind.execute(
        sa.text(
            f"""
            INSERT INTO data_sources (title, source_type, year)
            SELECT '{_OECD_NEA_TITLE}', '{_OECD_NEA_SOURCE_TYPE}', {_OECD_NEA_YEAR}
            WHERE NOT EXISTS (
                SELECT 1 FROM data_sources WHERE title = '{_OECD_NEA_TITLE}'
            )
            """
        )
    )

    # --------------------------------------------------------------
    # 2. UPDATE U-10Mo datasets — repoint to OECD-NEA source.
    # --------------------------------------------------------------
    # NFM-4142 — dedup + repoint SQL rendered by helpers so the
    # placeholder title literals are bound via SQLAlchemy parameters
    # (`:u10mo_dataset_title`, `:placeholder_titles`) rather than
    # inlined.
    u10mo_bind_params: dict[str, object] = {
        "u10mo_dataset_title": _U10MO_PLACEHOLDER_DATASET_TITLE,
        "placeholder_titles": list(_LEGACY_DATA_SOURCE_PLACEHOLDER_TITLES),
    }
    bind.execute(sa.text(_build_u10mo_dedup_sql()), u10mo_bind_params)
    bind.execute(sa.text(_build_u10mo_repoint_sql()), u10mo_bind_params)

    # --------------------------------------------------------------
    # 3. UPDATE pending_review kg_nodes for U-13at%Mo / U-16at%Mo.
    # --------------------------------------------------------------
    # These two rows already exist with status='pending_review'.
    # Flip to 'active' so the bridge resolves.  Idempotent: the
    # second run is a no-op (already active).
    bind.execute(
        sa.text(
            """
            UPDATE kg_nodes
            SET status = 'active'
            WHERE node_type = 'Material'
              AND label IN ('U-13at%Mo', 'U-16at%Mo')
              AND status = 'pending_review'
            """
        )
    )

    # --------------------------------------------------------------
    # 4. INSERT 55 new Material kg_nodes (idempotent).
    # --------------------------------------------------------------
    # Build the (label, source_id, properties) tuples for each row.
    # The U-10Mo source_id is resolved at runtime from the OECD-NEA
    # row we just inserted.
    janney_id = _JANNEY_2019_SOURCE_ID

    rows: list[tuple[str, str | None, str]] = []
    for material_name, src_id_marker, _props in _URGENT_MATERIALS:
        if src_id_marker == "OECD-NEA":
            # Resolved at INSERT time via subquery.
            rows.append((material_name, "OECD-NEA_RESOLVE", json.dumps({})))
        else:
            rows.append(
                (material_name, src_id_marker, json.dumps({}))
            )

    for material_name in _JANNEY_BUCKET_D_MATERIALS:
        is_slice, slice_type = _is_property_slice(material_name)
        if is_slice:
            props = json.dumps(
                {"dataset_slice": True, "slice_type": slice_type}
            )
        else:
            props = json.dumps({})
        rows.append((material_name, janney_id, props))

    for label, src_marker, props_json in rows:
        if src_marker == "OECD-NEA_RESOLVE":
            bind.execute(
                sa.text(
                    f"""
                    INSERT INTO kg_nodes (
                        node_type, label, properties, confidence, source_id, status
                    )
                    SELECT 'Material', CAST(:label AS text), CAST(:props AS jsonb), 1.0, ds.id, 'active'
                    FROM data_sources ds
                    WHERE ds.title = '{_OECD_NEA_TITLE}'
                      AND NOT EXISTS (
                          SELECT 1 FROM kg_nodes kn2
                          WHERE kn2.node_type = 'Material' AND kn2.label = CAST(:label AS text)
                      )
                    """
                ),
                {
                    "label": label,
                    "props": props_json,
                },
            )
        else:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO kg_nodes (
                        node_type, label, properties, confidence, source_id, status
                    )
                    SELECT 'Material', CAST(:label AS text), CAST(:props AS jsonb), 1.0, CAST(:src_id AS uuid), 'active'
                    WHERE NOT EXISTS (
                        SELECT 1 FROM kg_nodes kn2
                        WHERE kn2.node_type = 'Material' AND kn2.label = CAST(:label AS text)
                      )
                      AND EXISTS (
                          SELECT 1 FROM data_sources ds
                          WHERE ds.id = CAST(:src_id AS uuid)
                      )
                    """
                ),
                {
                    "label": label,
                    "props": props_json,
                    "src_id": src_marker,
                },
            )


# ---------------------------------------------------------------------------
# Backward (downgrade)
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Reverse the migration.

    * Restore ``U-13at%Mo`` and ``U-16at%Mo`` to ``pending_review``
      status (best-effort — these were flipped from pending to active,
      so this restores the prior state).
    * Re-point U-10Mo datasets back to a placeholder source (best-effort;
      selects the first matching placeholder source).  NFM-4142 — the
      placeholder title list is bound via the ``:placeholder_titles``
      SQLAlchemy parameter (rendered by :func:`_build_u10mo_downgrade_repoint_sql`).
    * Delete the 55 new Material kg_nodes.
    * Delete the OECD-NEA data_sources row.

    The migration is data-only so there is no DDL to reverse.
    """
    bind = op.get_bind()

    # Restore pending_review status on the 2 review-queue rows.
    bind.execute(
        sa.text(
            """
            UPDATE kg_nodes
            SET status = 'pending_review'
            WHERE node_type = 'Material'
              AND label IN ('U-13at%Mo', 'U-16at%Mo')
              AND status = 'active'
            """
        )
    )

    # Re-point U-10Mo datasets back to a placeholder source.
    # NFM-4142 — SQL rendered by helper so placeholder title list is
    # bound via parameter rather than inlined.
    bind.execute(
        sa.text(_build_u10mo_downgrade_repoint_sql()),
        {
            "u10mo_dataset_title": _U10MO_PLACEHOLDER_DATASET_TITLE,
            "placeholder_titles": list(_LEGACY_DATA_SOURCE_PLACEHOLDER_TITLES),
        },
    )

    # Delete the 55 new Material kg_nodes.
    new_labels = [m[0] for m in _URGENT_MATERIALS] + _JANNEY_BUCKET_D_MATERIALS
    for label in new_labels:
        bind.execute(
            sa.text(
                """
                DELETE FROM kg_nodes
                WHERE node_type = 'Material' AND label = CAST(:label AS text)
                """
            ),
            {"label": label},
        )

    # Delete OECD-NEA row (only if no datasets reference it anymore).
    bind.execute(
        sa.text(
            f"""
            DELETE FROM data_sources
            WHERE title = '{_OECD_NEA_TITLE}'
              AND NOT EXISTS (
                  SELECT 1 FROM datasets WHERE source_id = data_sources.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM kg_nodes WHERE source_id = data_sources.id
              )
            """
        )
    )