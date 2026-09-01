"""069 — v0.5.0 F8 Property classes: add 6 new property_types rows.

Revision ID: 069_add_v050_f8_property_types
Revises: 068_v050_seed_melting_point_alias
Create Date: 2026-09-01

NFM-4000 — v0.5.0 ``kg_nodes.label`` ontology additions.

Adds 6 canonical ``property_types`` rows that the heuristic extractor
already emits (NFM-3517, NFM-3835) but which were silently dropped at
``extraction_to_db_mapper._lookup_property_type`` because the rows
were not seeded by ``031_seed_property_types.py``. Without these
rows the kg_nodes strict surface on source ``9320cb50-eb65-4178-8d2e-c56aeb848b21``
(Owen 2023) remained 2/8 instead of the expected 8/8 (NFM-3424
re-extraction, 2026-09-01 01:47Z).

Also adds the ``activation_energy`` row that F8 #1 (undoped Ea 0.30 eV)
requires — the regression-guard baseline in NFM-4000 AC-2 ("Do NOT
regress existing 2/8 strict checkpoints (undoped Ea, undoped D0)")
cannot be evaluated against the canonical seed today. The
``ON CONFLICT (category_id, slug) DO NOTHING`` clause makes the
migration idempotent if another branch already seeded this row.

Idempotency
-----------

* ``INSERT ... ON CONFLICT (category_id, slug) DO NOTHING`` keyed on
  the existing unique constraint ``uq_property_types_category_slug``.
  Re-running the migration is safe and silent.

Schema prerequisites
--------------------

* Requires the ``property_categories`` rows created by
  ``010_seed_phase1_reference_data``. The migration is in the linear
  chain so the slug lookup is guaranteed to succeed at upgrade time.
* Does NOT alter the ``property_types`` schema; only inserts canonical
  rows for the 6 new v0.5.0 F8 classes plus the regression-guard
  ``activation_energy`` row.

Mapper coordination
-------------------

* Category slugs here match ``ONTOFUEL_CATEGORY_TO_SLUG`` in
  ``src/nfm_db/services/extraction_to_db_mapper.py`` —
  ``ONTOFUEL_CATEGORY_TO_SLUG["diffusion"]`` resolves to ``"physical"``
  and these new rows are all under the ``physical`` slug.
* ``heuristic_extractor.FAMILY_TO_CATEGORY`` already maps the families
  (``energy`` → ``diffusion``, ``diffusivity`` → ``diffusion``,
  ``density`` → ``physical``, ``length`` → ``physical``); no changes
  needed there.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "069_add_v050_f8_property_types"
down_revision: str | Sequence[str] | None = "068_v050_seed_melting_point_alias"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Seed payload — NFM-4000 v0.5.0 F8 Property class additions
# ---------------------------------------------------------------------------
# Format: (category_slug, name, slug, value_type, description)
#
# ``name`` and ``slug`` are intentionally identical so the lookup at
# ``extraction_to_db_mapper._lookup_property_type`` (which queries by
# ``PropertyType.name == property_name``) matches the heuristic
# extractor output 1:1.
#
# value_type = "scalar" for all 6 (point-value F8 scorecard checkpoints).
#
# The 7th entry ``activation_energy`` is the regression-guard row for
# the existing undoped Ea F8 #1 checkpoint.

_V050_PROPERTY_TYPE_SEED: tuple[tuple[str, str, str, str, str | None], ...] = (
    # ---- physical ----
    # F8 #1 (regression guard): undoped Ea — already in
    # property_mapping.json (line 13) and heuristic_extractor (line 190)
    # but not previously seeded, so the strict-2/8 baseline is
    # fragile. Idempotent insert.
    (
        "physical",
        "activation_energy",
        "activation_energy",
        "scalar",
        "Diffusion activation energy (eV)",
    ),
    # ---- F8 #3 — Cr-doped Ea ----
    (
        "physical",
        "cr_doped_activation_energy",
        "cr_doped_activation_energy",
        "scalar",
        "Cr-doped UO2 diffusion activation energy (eV)",
    ),
    # ---- F8 #4 — Cr-doped D0 ----
    (
        "physical",
        "cr_doped_diffusion_coefficient",
        "cr_doped_diffusion_coefficient",
        "scalar",
        "Cr-doped UO2 pre-exponential factor D0 (cm^2/s)",
    ),
    # ---- F8 #5 — amorphous density ----
    (
        "physical",
        "density_amorphous",
        "density_amorphous",
        "scalar",
        "Amorphous-phase density (g/cm^3)",
    ),
    # ---- F8 #6 — doped density ----
    (
        "physical",
        "density_doped",
        "density_doped",
        "scalar",
        "Density of doped variants (g/cm^3)",
    ),
    # ---- F8 #7 — RDF peak distance (renamed from heuristic's rdf_peak) ----
    (
        "physical",
        "rdf_peak_distance",
        "rdf_peak_distance",
        "scalar",
        "Pair-distribution function peak position (angstrom)",
    ),
    # ---- F8 #8 — bond length ----
    (
        "physical",
        "bond_length",
        "bond_length",
        "scalar",
        "Bond length between specific atom pairs (angstrom)",
    ),
)


def upgrade() -> None:
    """Insert canonical v0.5.0 F8 property_types rows idempotently.

    Resolves ``category_id`` via a subquery against
    ``property_categories.slug`` so the seed is portable across
    environments (UUIDs are randomly generated by the model default).
    Mirrors the implementation pattern from migration 031 — same
    idempotency strategy, same Alembic 1.14+ kw-only ``execute``
    workaround (NFM-1995 defect C1).
    """
    for (
        category_slug,
        name,
        slug,
        value_type,
        description,
    ) in _V050_PROPERTY_TYPE_SEED:
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO property_types
                    (category_id, name, slug, value_type, description, created_at, updated_at)
                SELECT
                    pc.id,
                    ins.name,
                    ins.slug,
                    ins.value_type,
                    ins.description,
                    NOW(),
                    NOW()
                FROM (SELECT CAST(:name AS VARCHAR) AS name,
                             CAST(:slug AS VARCHAR) AS slug,
                             CAST(:value_type AS VARCHAR) AS value_type,
                             CAST(:description AS TEXT) AS description) AS ins
                CROSS JOIN (
                    SELECT id FROM property_categories WHERE slug = :category_slug
                ) AS pc
                ON CONFLICT (category_id, slug) DO NOTHING
                """
            ),
            {
                "name": name,
                "slug": slug,
                "value_type": value_type,
                "description": description,
                "category_slug": category_slug,
            },
        )


def downgrade() -> None:
    """Delete only the rows this migration seeded, by slug.

    Other ``property_types`` rows created at runtime are untouched.
    Reverses the v0.5.0 ontology additions without affecting the
    pre-existing 031_seed_property_types entries.
    """
    seeded_slugs = tuple(row[2] for row in _V050_PROPERTY_TYPE_SEED)
    if not seeded_slugs:
        return

    op.get_bind().execute(
        sa.text(
            """
            DELETE FROM property_types
            WHERE slug = ANY(CAST(:slugs AS VARCHAR[]))
            """
        ),
        {"slugs": list(seeded_slugs)},
    )
