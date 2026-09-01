"""v0.5.0 seed migration — add defense-in-depth ``melting_point`` alias row.

Revision ID: 068_v050_seed_melting_point_alias
Revises: 067_v050_seed_elastic_constant_solubility_limit
Create Date: 2026-09-01

NFM-4026 (NFM-4008 AC-3): add a second ``melting_point`` row under
category ``thermal`` so the name resolves even when downstream tools
bypass the ``extraction_to_db_mapper._lookup_property_type`` strict
two-stage lookup.

Why this migration is needed
----------------------------
The NFM-4019 mapper fix (commit ``e545bca95`` / cherry-pick ``8a91da592``)
adds a name-only fallback in ``_lookup_property_type`` that resolves
``melting_point`` when the LLM emits ``category=thermal`` but the seed
places the row under ``physical``. This resolves the 4 LLM-side
category-context mismatches end-to-end **inside the mapper**.

However, downstream code paths that consume ``property_types`` directly
(not via the mapper) — e.g., validation tools, dashboard aggregations,
graph projections that join on ``(category_id, slug)`` — would still
miss the canonical row when querying under ``thermal``. Inserting a
second row under ``thermal`` gives those callers a defense-in-depth hit
without requiring every reader to re-implement the mapper fallback.

Constraints preserved
---------------------
* The existing ``(physical, melting_point)`` row from migration 031 is
  NOT touched. The canonical row stays under ``physical`` so measurement
  rows already citing that ``(category_id, slug)`` pair remain linked.
* The new row is keyed on the same UNIQUE constraint
  (``uq_property_types_category_slug``) as every other seed row, so the
  INSERT is idempotent via ``ON CONFLICT (category_id, slug) DO NOTHING``.
* The two-stage mapper fallback remains the primary resolution path.
  The new row is a backup for tools that bypass the mapper.

AC mapping
----------
* AC-2 — chains off the v0.5.0 cluster head
  ``067_v050_seed_elastic_constant_solubility_limit`` (NOT off
  ``065_widen_property_measurements_numeric`` — NFM-3918 renumbered the
  v0.5.0 cluster to chain off ``066_seed_material_categories`` so both
  067 and 068 are linear siblings on the same chain).
* AC-3 — covered by
  ``tests/test_v050_seed_property_types_migration_runtime.py`` (extended
  to cover migration 068 with identical C1/D1 pattern guards) plus
  dedicated alias coverage in the same file.
* AC-4 — covered by the regression test that pins the 2-row (physical,
  melting_point) + (thermal, melting_point) coexistence and confirms the
  physical row is preserved across downgrade.
* AC-5 — re-run ``scripts/nfm-4012-unknown-property-enumeration.py`` on
  staging post-merge; the 4 LLM-side names must drop to 0 events and
  the TSV must be empty (or contain only truly-new names added since the
  baseline).

Operational notes
-----------------
* Uses ``op.get_bind().execute(sa.text(sql), params)`` per NFM-1995
  defect C1 — Alembic 1.14+ made the second positional argument to
  ``Operations.execute`` keyword-only.
* ``downgrade()`` deletes ONLY the alias row this migration seeded
  (i.e., the (thermal, melting_point) row), identified by
  ``(category_slug, slug) = ('thermal', 'melting_point')``. The
  pre-existing (physical, melting_point) row is untouched.
* No schema change; ``property_types`` and ``property_categories`` are
  unchanged from migration 009 / 010 / 031.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "068_v050_seed_melting_point_alias"
down_revision: str | Sequence[str] | None = "067_v050_seed_elastic_constant_solubility_limit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Seed payload
# ---------------------------------------------------------------------------
# Format: (category_slug, name, slug, value_type, description)
#
# value_type must be one of ('scalar', 'range', 'expression', 'list',
# 'text') per the check constraint ``ck_property_types_value_type``
# declared in ``src/nfm_db/models/property.py``.
#
# This migration adds ONE alias row: ``melting_point`` under ``thermal``.
# The existing ``melting_point`` row under ``physical`` from migration 031
# is preserved (no UPDATE; alias row is a separate row in the same
# property_types table).

_PROPERTY_TYPE_V050_ALIAS_SEED: tuple[tuple[str, str, str, str, str | None], ...] = (
    # ---- thermal (thermal family per FAMILY_TO_CATEGORY) ----
    # Defense-in-depth alias row. The canonical melting_point row lives
    # under ``physical`` (migration 031, NFM-1995 seed). Adding a second
    # row under ``thermal`` lets downstream tools that bypass the mapper
    # fallback resolve melting_point via the (thermal, melting_point)
    # pair the LLM emits.
    (
        "thermal",
        "melting_point",
        "melting_point",
        "scalar",
        "Defense-in-depth alias of melting_point under category 'thermal'. "
        "The canonical row remains under 'physical' (migration 031). "
        "Inserted by NFM-4026 / 068 so downstream tools that bypass the "
        "extraction_to_db_mapper name-only fallback resolve melting_point "
        "via the (thermal, melting_point) UNIQUE pair.",
    ),
)


def upgrade() -> None:
    """Insert the v0.5.0 alias row idempotently.

    Resolves ``category_id`` via a subquery against
    ``property_categories.slug`` so the seed is portable across
    environments (UUIDs are randomly generated by the model default).

    NOTE: must use ``op.get_bind().execute(sa.text(sql), params)`` and
    NOT ``op.execute(sql, params)``.  In Alembic 1.14+ the second
    positional argument of ``Operations.execute`` is the
    ``execution_options`` keyword (kw-only).  Passing a ``params_dict``
    positionally raises ``TypeError: execute() takes 2 positional
    arguments but 3 were given`` and the migration silently aborts.
    See NFM-1995 review defect C1.
    """
    for category_slug, name, slug, value_type, description in _PROPERTY_TYPE_V050_ALIAS_SEED:
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
    """Delete only the alias row this migration seeded, by (category_slug, slug).

    Identifies the alias row by the composite (category_slug='thermal',
    slug='melting_point') pair, so the pre-existing (physical,
    melting_point) row from migration 031 is preserved.

    NOTE: must use ``op.get_bind().execute(sa.text(sql), params)``
    (see upgrade() docstring — Alembic 1.14+ keyword-only signature
    for ``Operations.execute``).  NFM-1995 defect D1.
    """
    for category_slug, _name, slug, _value_type, _description in _PROPERTY_TYPE_V050_ALIAS_SEED:
        op.get_bind().execute(
            sa.text(
                """
                DELETE FROM property_types
                WHERE slug = CAST(:slug AS VARCHAR)
                  AND category_id = (
                    SELECT id FROM property_categories WHERE slug = CAST(:category_slug AS VARCHAR)
                  )
                """
            ),
            {
                "slug": slug,
                "category_slug": category_slug,
            },
        )
