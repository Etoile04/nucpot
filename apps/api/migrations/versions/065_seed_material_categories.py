"""Seed material_categories with the canonical NFMD taxonomy.

Revision ID: 065_seed_material_categories
Revises: 064_widen_property_measurements_numeric
Create Date: 2026-08-31

NFM-3916 (Tier 1C) — ``materials.category_id`` is currently NULL on
131/131 rows and ``material_categories`` is empty.  The downstream
``/materials`` UX (Tier 1D) is blocked-by this issue; shipping the
dropdown without seed data would surface an empty list to end users.

Scope
-----
* Inserts eight nuclear-fuel / structural-material taxonomy rows.
* ``upgrade()`` is **idempotent** — keyed on the unique constraint
  ``uq_material_categories_slug``.  Running the migration twice does
  not raise and does not duplicate rows (matches the
  NFM-1995 ``031_seed_property_types`` pattern — same dialect
  guarantees).
* ``downgrade()`` deletes only the rows this migration seeded,
  identified by slug, so any taxonomy rows added by future operators
  are preserved.
* **No schema change** — the ``material_categories`` table was created
  by ``009_create_phase1_core_tables`` and only data is added here.

Naming
------
Names follow the convention proposed by NFM-3913 (CPO-led UX
research).  Slugs are stable identifiers used by the backfill script
(``scripts/backfill_material_category.py``) and the upcoming Tier 1D
frontend dropdown.  Two slugs contain an ampersand; Postgres VARCHAR
and slug lookup tolerate this without escaping.

Coverage intent
---------------
These eight categories cover the formula classes observed in the
real 131-row production dataset (per the NFM-3916 ticket):

* ``metallic_fuel``         — binary/ternary intermetallics (Au, Cu,
                              Nb-V, CuAu, Pt-W, Cr-Mo-V, Ag-Pt, ...)
* ``refractory_metal``      — pure elements / alloys built on W, Mo,
                              Nb, Ta, Cr, V (overlaps with metallic
                              fuel on Cr-Mo-V; rule-priority resolves
                              to refractory when refractory symbol
                              dominates)
* ``oxide_fuel``            — UO2, PuO2, ThO2, MOX, fluorite-type
                              ceramics (matches crystal_structure =
                              'Fluorite')
* ``carbide_nitride_fuel``  — UC, UN, (U,Pu)C, (U,Pu)N
* ``cladding_alloy``        — Zircaloy, M5, E110, HT9 wrapper steels
* ``structural_steel``      — generic reactor structural alloys
                              (SS304, SS316, F82H, Eurofer)
* ``amorphous_glassy``      — metallic glasses, glassy phases
* ``other``                 — fallback for rows that match no rule
                              (the backfill script leaves these
                              ``category_id = NULL`` rather than
                              force-fitting, per the ticket)

The ``other`` row is seeded for symmetry but the backfill script
does NOT auto-assign it (per ticket: "匹配不上的行保持
``category_id=NULL``（不硬塞进 ``other``，避免制造假分类）").
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "065_seed_material_categories"
down_revision: str | Sequence[str] | None = "064_widen_property_measurements_numeric"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Seed payload
# ---------------------------------------------------------------------------
# Format: (slug, name, description, sort_order)
#
# sort_order controls the display sequence in the upcoming Tier 1D
# dropdown — most commonly used categories first.

_MATERIAL_CATEGORY_SEED: tuple[tuple[str, str, str, int], ...] = (
    (
        "oxide_fuel",
        "Oxide Fuel",
        "UO2, PuO2, ThO2, mixed-oxide (MOX), fluorite-type ceramic fuels.",
        10,
    ),
    (
        "metallic_fuel",
        "Metallic Fuel",
        "Binary / ternary intermetallic fuels and alloys (e.g. U-Zr, "
        "U-Pu-Zr, U-Mo).",
        20,
    ),
    (
        "carbide_nitride_fuel",
        "Carbide & Nitride Fuel",
        "UC, UN, (U,Pu)C, (U,Pu)N and related ceramic fuel forms.",
        30,
    ),
    (
        "cladding_alloy",
        "Cladding Alloy",
        "Zirconium-based cladding alloys (Zircaloy-4, M5, E110) and "
        "advanced FeCrAl / SiC wrapper materials.",
        40,
    ),
    (
        "structural_steel",
        "Structural Steel",
        "Reactor pressure-vessel and structural steels "
        "(SS304, SS316, F82H, Eurofer97, HT9).",
        50,
    ),
    (
        "refractory_metal",
        "Refractory Metal",
        "W, Mo, Nb, Ta, Cr, V and alloys built on refractory base "
        "elements.",
        60,
    ),
    (
        "amorphous_glassy",
        "Amorphous / Glassy",
        "Metallic glasses and glassy / amorphous phases.",
        70,
    ),
    (
        "other",
        "Other",
        "Fallback bucket for materials that do not match any of the "
        "rule patterns. The backfill script deliberately leaves "
        "unmatched rows ``category_id = NULL`` rather than assigning "
        "this category, to avoid manufacturing fake classifications.",
        90,
    ),
)


def upgrade() -> None:
    """Insert canonical material_categories rows idempotently.

    Uses ``INSERT ... ON CONFLICT (slug) DO NOTHING`` keyed on
    ``uq_material_categories_slug`` so the migration is replay-safe
    across forward+down+forward cycles.

    NOTE: must use ``op.get_bind().execute(sa.text(sql), params)``
    and NOT ``op.execute(sql, params)``.  In Alembic 1.14+ the second
    positional argument of ``Operations.execute`` is the
    ``execution_options`` keyword (kw-only).  Passing a ``params_dict``
    positionally raises ``TypeError: execute() takes 2 positional
    arguments but 3 were given`` and the migration silently aborts.
    See NFM-1995 review defect C1 and the equivalent note in
    ``031_seed_property_types.py``.
    """
    for slug, name, description, sort_order in _MATERIAL_CATEGORY_SEED:
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO material_categories
                    (name, slug, description, sort_order, created_at, updated_at)
                SELECT
                    ins.name,
                    ins.slug,
                    ins.description,
                    ins.sort_order,
                    NOW(),
                    NOW()
                FROM (SELECT CAST(:name AS VARCHAR) AS name,
                             CAST(:slug AS VARCHAR) AS slug,
                             CAST(:description AS TEXT) AS description,
                             CAST(:sort_order AS INTEGER) AS sort_order) AS ins
                ON CONFLICT (slug) DO NOTHING
                """
            ),
            {
                "name": name,
                "slug": slug,
                "description": description,
                "sort_order": sort_order,
            },
        )


def downgrade() -> None:
    """Delete only the rows this migration seeded, by slug.

    The DELETE is restricted to the seeded slug set so any
    taxonomy rows added by future operators (or by the backfill
    script's follow-on taxonomy work) survive a downgrade.

    NOTE: must use ``op.get_bind().execute(sa.text(sql), params)``
    — see the upgrade() docstring for the Alembic 1.14+ kw-only
    ``Operations.execute`` signature.

    Rollback safety
    ---------------
    The NFM-3916 ticket notes the rollback path as ``alembic
    downgrade -1`` followed by
    ``update materials set category_id=NULL where category_id in
    (select id from material_categories)``.  The latter is the
    inverse of the backfill script; because every backfilled row
    had ``category_id = NULL`` before the backfill ran (confirmed
    during ticket triage), that UPDATE is lossless and can be run
    independently after this migration's downgrade if a full reset
    is desired.
    """
    seeded_slugs = tuple(row[0] for row in _MATERIAL_CATEGORY_SEED)
    if not seeded_slugs:
        return

    op.get_bind().execute(
        sa.text(
            """
            DELETE FROM material_categories
            WHERE slug = ANY(CAST(:slugs AS VARCHAR[]))
            """
        ),
        {"slugs": list(seeded_slugs)},
    )
