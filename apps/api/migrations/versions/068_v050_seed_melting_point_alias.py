"""v0.5.0 seed migration — ``(thermal, melting_point)`` alias row.

Revision ID: 068_v050_seed_melting_point_alias
Revises: 067_v050_seed_elastic_constant_solubility_limit
Create Date: 2026-09-01

NFM-4027 (NFM-4008 AC-2 / AC-3): defense-in-depth alias seed for
``melting_point``.

Why a separate alias row?
-------------------------
Migration 031 already seeds ``(physical, melting_point, melting_point, scalar)``
— the canonical row for melting temperature. The NFM-4019 name-only fallback
in ``extraction_to_db_mapper._lookup_property_type`` now resolves the 4
LLM-side category-context mismatches, including ``melting_point`` when the
LLM emits ``category=thermal`` (seed places it under ``physical``; strict
lookup misses on the slug).

However, the name-only fallback is an upstream-of-DB code path. Downstream
tools that bypass the mapper (manual SQL inserts, future direct-API tools,
ad-hoc analyst queries) still hit the strict
``(property_categories.slug, property_types.name)`` lookup. For these tools,
a name with a single ``property_types`` row resolves correctly; a name with
multiple rows (e.g. an analyst wanting to insert ``(thermal, melting_point)``
data) cannot, because ``uq_property_types_category_slug`` would conflict.

This migration seeds a SECOND ``melting_point`` row under the ``thermal``
category (slug) so both the canonical ``physical`` row and a ``thermal``
alias row exist. The unique constraint ``uq_property_types_category_slug``
keys on ``(category_id, slug)``, so the new row does NOT conflict with the
existing ``(physical, melting_point)`` row.

Data preservation (NDE constraint — NFM-4024)
---------------------------------------------
The existing ``(physical, melting_point)`` row stays UNTOUCHED. Any
measurement row already citing it (via ``property_types_id``) continues to
resolve correctly. ``downgrade()`` removes only the alias row seeded here
(joined on category slug = 'thermal' AND property_types.slug =
'melting_point'), so the canonical row survives a rollback.

AC mapping
----------
* AC-1 — chains off ``067_v050_seed_elastic_constant_solubility_limit``;
  ``alembic heads`` shows exactly one head after this migration.
* AC-2 — idempotent via ``INSERT ... ON CONFLICT (category_id, slug) DO
  NOTHING`` keyed on ``uq_property_types_category_slug``. Re-running the
  migration on a fresh DB inserts exactly 1 row.
* AC-3 — covered by ``tests/test_seed_property_types_migration.py``
  (TestV068MigrationChain / TestV068Idempotent / TestV068Coverage /
  TestV068Downgrade) + sibling runtime test
  ``tests/test_v068_seed_melting_point_alias_migration_runtime.py``.
* AC-4 — defense-in-depth for downstream tools that bypass the mapper
  fallback (per NFM-4025 research recommendation).
* AC-5 — prod re-verify via
  ``scripts/nfm-4012-unknown-property-enumeration.py`` is owned by RE
  per NFM-3845/3884 cascade; runs post-deploy. Expected: 0 rows for the
  4 LLM-side names (incl. ``melting_point``).

Operational notes
-----------------
* Uses ``op.get_bind().execute(sa.text(sql), params)`` per NFM-1995
  defect C1 — Alembic 1.14+ made the second positional argument to
  ``Operations.execute`` keyword-only.
* ``downgrade()`` deletes ONLY the alias row this migration seeded,
  identified by ``category_slug='thermal' AND slug='melting_point'``, so
  the canonical ``(physical, melting_point)`` row survives.
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
# This row is a scalar — ``melting_point`` is a single temperature value.

_PROPERTY_TYPE_V068_SEED: tuple[tuple[str, str, str, str, str | None], ...] = (
    # ---- thermal (alias for the canonical 'physical' melting_point row) ----
    # Per NFM-4027 AC-2 / AC-3: defense-in-depth seed so downstream tools
    # that bypass the mapper name-only fallback can insert ``thermal``
    # melting-point data without conflicting with the canonical
    # ``(physical, melting_point)`` row from migration 031. The two rows
    # have distinct category_ids so they coexist under the
    # ``uq_property_types_category_slug`` unique constraint.
    (
        "thermal",
        "melting_point",
        "melting_point",
        "scalar",
        "Melting temperature (K) — alias for the canonical 'physical' "
        "melting_point row seeded by 031. Added in v0.5.0 (NFM-4027) so "
        "downstream tools that bypass the mapper fallback can resolve "
        "(category=thermal, name=melting_point) without conflict.",
    ),
)


def upgrade() -> None:
    """Insert the ``(thermal, melting_point)`` alias row idempotently.

    Resolves ``category_id`` via a subquery against
    ``property_categories.slug`` so the seed is portable across
    environments (UUIDs are randomly generated by the model default).

    The canonical ``(physical, melting_point)`` row from 031 is NOT
    touched — the unique constraint is keyed on ``(category_id, slug)``
    so the two rows coexist.

    NOTE: must use ``op.get_bind().execute(sa.text(sql), params)`` and
    NOT ``op.execute(sql, params)``.  In Alembic 1.14+ the second
    positional argument of ``Operations.execute`` is the
    ``execution_options`` keyword (kw-only).  Passing a ``params_dict``
    positionally raises ``TypeError: execute() takes 2 positional
    arguments but 3 were given`` and the migration silently aborts.
    See NFM-1995 review defect C1.
    """
    for category_slug, name, slug, value_type, description in _PROPERTY_TYPE_V068_SEED:
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
    """Delete only the alias row this migration seeded.

    The canonical ``(physical, melting_point)`` row from migration 031 is
    untouched. We join on both ``category_slug='thermal'`` AND
    ``slug='melting_point'`` so the canonical row survives a rollback.

    NOTE: must use ``op.get_bind().execute(sa.text(sql), params)``
    (see upgrade() docstring — Alembic 1.14+ keyword-only signature
    for ``Operations.execute``).  NFM-1995 defect D1.
    """
    op.get_bind().execute(
        sa.text(
            """
            DELETE FROM property_types
            WHERE slug = :slug
              AND category_id = (
                SELECT id FROM property_categories WHERE slug = :category_slug
              )
            """
        ),
        {
            "slug": "melting_point",
            "category_slug": "thermal",
        },
    )
