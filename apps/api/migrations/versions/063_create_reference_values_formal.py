"""Create formal ``reference_values`` table — NFM-3872 (Wayfinder pilot C / C-S1).

Per NFM-3830 Q1=C decision (Option A transition formalization), this
revision creates the backend NFMD ``reference_values`` formal table
that the C-S1 ETL populates from ``_ref_gap_fill_staging`` rows that
pass the C-I1 admission gate (NFM-3871 / DOI pre-screen + 30 %
Crossref/OpenAlex statistical validation).

Schema
------

The column shape matches the production ``reference_values`` table on
Supabase that the web matrix route reads (NFM-3780 fix — the actual
column names used in production are ``element``, ``crystal_structure``,
``property_name``, ``value``, ``unit``, ``source``, ``notes``). The
backend ``reference_values`` adds DOI attribution (``source_doi``),
measurement context (``uncertainty``, ``temperature``), and ETL
provenance columns so a future operator can trace each row back to
its admission decision without rerunning C-I1.

Naming
------

* ``element`` (not ``element_system``) and ``crystal_structure`` (not
  ``phase``) mirror the Supabase production schema. The legacy
  staging names are mapped at ETL time inside
  ``promote_staging_etl``.
* ``staging_id`` is UNIQUE — the ETL re-runs idempotently by
  ``INSERT ... ON CONFLICT (staging_id) DO UPDATE``.

Cross-dialect
-------------

Like all recent revisions this migration supports both PostgreSQL
(production) and SQLite (CI / unit tests). The schema is plain
ANSI-compatible: no JSONB, no ``gen_random_uuid()`` server-side
default (the ORM supplies UUIDs at insert time), no PG-specific
types. The FK to ``_ref_gap_fill_staging`` is enforced on PG and
SQLite-with-FK (the conftest toggles ``PRAGMA foreign_keys=ON``).

Revision ID: 063_create_reference_values_formal
Revises: 062_create_rerun_idempotency_keys
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "063_create_reference_values_formal"
down_revision: str | Sequence[str] | None = "062_create_rerun_idempotency_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``reference_values`` formal table."""
    op.create_table(
        "reference_values",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            comment="Synthetic PK. The natural key is (staging_id).",
        ),
        sa.Column(
            "staging_id",
            # PG UUID type on production. SQLite test runs use
            # ``Base.metadata.create_all`` (not this migration), which
            # renders the ORM model's ``Mapped[uuid.UUID]`` as
            # CHAR(32) — matching ``_ref_gap_fill_staging.id`` and so
            # the FK resolves on both dialects.
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            comment=(
                "1:1 link to _ref_gap_fill_staging.id. UNIQUE — makes "
                "the C-S1 ETL re-runnable without duplicate writes."
            ),
        ),
        sa.Column(
            "element",
            sa.String(length=50),
            nullable=False,
            comment=(
                "Element system identifier (e.g. 'U', 'UO2'). Maps from "
                "staging.element_system at ETL time."
            ),
        ),
        sa.Column(
            "crystal_structure",
            sa.String(length=50),
            nullable=True,
            comment=(
                "Crystal / thermodynamic phase (e.g. 'BCC', 'FCC'). Maps "
                "from staging.phase at ETL time."
            ),
        ),
        sa.Column(
            "property_name",
            sa.String(length=100),
            nullable=False,
            comment="Property name (e.g. 'lattice_constant', 'bulk_modulus').",
        ),
        sa.Column(
            "value",
            sa.Float(),
            nullable=False,
            comment="Numeric property value.",
        ),
        sa.Column(
            "unit",
            sa.String(length=50),
            nullable=False,
            comment="Measurement unit (e.g. 'angstrom', 'GPa').",
        ),
        sa.Column(
            "method",
            sa.String(length=100),
            nullable=True,
            comment="Measurement / calculation method (e.g. 'DFT', 'EXP').",
        ),
        sa.Column(
            "source",
            sa.String(length=200),
            nullable=False,
            comment=(
                "Data source identifier (e.g. 'Owen2023'). Same as "
                "staging.source — used as the corpus_id fallback read key."
            ),
        ),
        sa.Column(
            "source_doi",
            sa.String(length=200),
            nullable=True,
            comment=(
                "DOI of the source publication. Only populated when the "
                "C-I1 admission gate admitted the row."
            ),
        ),
        sa.Column(
            "uncertainty",
            sa.Float(),
            nullable=True,
            comment="Measurement uncertainty.",
        ),
        sa.Column(
            "temperature",
            sa.Float(),
            nullable=True,
            comment="Measurement temperature in Kelvin.",
        ),
        sa.Column(
            "notes",
            sa.Text(),
            nullable=True,
            comment=(
                "Free-form notes matching production. Populated by the "
                "ETL with provenance text."
            ),
        ),
        sa.Column(
            "etl_issue",
            sa.String(length=20),
            nullable=True,
            comment=(
                "Paperclip issue ID that ran the promotion (e.g. 'NFM-3872')."
            ),
        ),
        sa.Column(
            "etl_manifest_ref",
            sa.Text(),
            nullable=True,
            comment=(
                "Path or identifier of the C-I1 manifest that admitted "
                "this row. May be a local path or an S3 URL."
            ),
        ),
        sa.Column(
            "etl_ok_reason",
            sa.String(length=64),
            nullable=True,
            comment=(
                "Short token explaining why C-I1 admitted this row. One of: "
                "'prescreen_pass', 'prescreen_pass+sampled_validated', "
                "'prescreen_pass+sampled_dry_run'."
            ),
        ),
        sa.Column(
            "promoted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment=(
                "Server-side INSERT timestamp. Distinct from created_at so "
                "a future re-promotion / refresh can update created_at "
                "without losing the original admission timestamp."
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="Row creation timestamp.",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="Row update timestamp (server-side ON UPDATE).",
        ),
        sa.ForeignKeyConstraint(
            ["staging_id"],
            ["_ref_gap_fill_staging.id"],
            ondelete="CASCADE",
            name="fk_rv_staging_id",
        ),
        sa.UniqueConstraint(
            "staging_id",
            name="uq_rv_staging_id",
        ),
    )

    # --- Indexes ---

    # The fallback read path (derive_ontology_graph) filters by
    # source (= corpus_id). element+property_name supports the
    # materials view record_ref deep links.
    op.create_index(
        "idx_rv_source",
        "reference_values",
        ["source"],
        unique=False,
    )
    op.create_index(
        "idx_rv_element_property",
        "reference_values",
        ["element", "property_name"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the ``reference_values`` formal table."""
    op.drop_index("idx_rv_element_property", table_name="reference_values")
    op.drop_index("idx_rv_source", table_name="reference_values")
    op.drop_table("reference_values")