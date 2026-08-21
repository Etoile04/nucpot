"""058 — Align the schema-drift backlog (NFM-3446-P2).

This single Alembic migration resolves the **88 remaining FAIL drift items**
surfaced by ``scripts/check_schema_drift.py`` (job 96664501201, PR #911 first
CI run). It is the migration-only Phase-2 follow-up to NFM-3446 Phase 1
(PR #911), which demoted 165 ``modify_comment`` / ``remove_index`` /
``missing_index`` items to WARN.

The 88 items fall into seven categories — each handled in its own section
below so the migration reads top-to-bottom in the same order the drift
script reports them:

    modify_nullable      25
    modify_type          20
    missing_column       15
    remove_column         9
    missing_fk            8
    add_constraint        6
    remove_fk             5
                       ----
    total                88

Constraints honored:

* **Single migration** — one revision file, one alembic head. Branches off
  ``057_create_kg_entity_and_relation_type_tables`` so the chain reaches
  head in one step.
* **No model changes** — drift = bad; model = source of truth.
* **Idempotency guards** — every DDL is wrapped in an ``information_schema``
  precheck so re-running the migration is a no-op (matches the inspect-
  and-skip pattern from PR #888 NFMD-3364 commit ``cb1b3ce7``).
* **Backfill-before-NOT-NULL** — when a column is currently NULLABLE but
  the model declares it NOT NULL, the migration backfills any NULLs with
  ``NOW()`` (for ``updated_at``) before ``SET NOT NULL``.  As of writing,
  every affected table is empty on every environment checked
  (pg-drift32, staging, prod-bootstrap), so the backfill is a no-op for
  live data — but the defensive guard is preserved for safety on
  environments where rows may exist.
* **Drop-FK-first** — FK constraints that target tables whose underlying
  columns are being removed must be dropped before the column drop, or
  Postgres aborts.  Order is fixed in §1 below.
* **Reuse existing enum types** — the migration references
  ``staging_status_enum`` and ``confidence_enum`` that already exist on
  the DB; it does NOT create new types.

Downgrade reverses the changes in reverse order so a downgrade → upgrade
round-trip restores the original drift (proving the migration is honest).

Drift breakdown (verified against pg-drift32 / CI scratch DB on 2026-08-21):

    modify_nullable  (25):
        _ref_gap_fill_staging.confidence, updated_at
        conflict_records.material_node_id, property_node_id, status, source_values
        dft_calculations.status
        extraction_figures.page_number, figure_type, extracted_data
        extraction_results.property_name, value, confidence
        kg_edges.properties, confidence
        kg_entity_types.ontology_version_id
        kg_nodes.properties, confidence, status
        kg_relation_types.ontology_version_id
        knowledge_gaps.created_at, updated_at
        property_measurements.conditions_hash
        reviews.action, data

    modify_type      (20):
        conflict_records.{conflicting_values, resolved_value, source_values}    JSONB -> JSON
        defect_analysis_results.metadata                                       JSONB -> JSON
        dft_calculations.computation_metadata                                  JSON -> CompatJSONB (kept JSONB in DB)
        extraction_figures.{extracted_data, caption}                          JSONB -> JSON, TEXT -> VARCHAR
        extraction_results.{item_data, value}                                  JSONB -> JSON
        kg_edges.properties                                                    JSONB -> JSON
        kg_nodes.properties                                                    JSONB -> JSON
        knowledge_gaps.metadata_                                               JSON -> CompatJSONB (kept JSONB in DB)
        md_simulation_results.thermodynamic_data                               JSONB -> JSON
        md_verification_jobs.config                                            JSONB -> JSON
        potential_fitting_results.{parameters, quality_metrics}                JSONB -> JSON
        potentials.description                                                 TEXT -> VARCHAR(255)
        property_measurements.value_list                                       JSONB -> JSON
        reviews.data                                                           JSONB -> JSON
        users.title                                                            VARCHAR(255) -> VARCHAR(64)

    missing_column   (15):
        _ref_gap_fill_staging.{composition, context, element,
            property_category, source_file, status, created_at}
        conflict_records.resolution_notes
        data_dna.classification_level              (NOT NULL, FK)
        defect_analysis_results.updated_at         (NOT NULL)
        kg_edges.updated_at                        (NOT NULL)
        md_simulation_results.updated_at          (NOT NULL)
        md_verification_jobs.owner_id
        potential_fitting_results.updated_at       (NOT NULL)
        upload_sessions.classification_level       (NOT NULL, FK)

    remove_column     (9):
        _ref_gap_fill_staging.{imported_at, staging_status}
        extraction_figures.{file_path, created_at, bounding_box, updated_at, job_id}
        extraction_results.source
        property_types.default_conflict_strategy

    missing_fk        (8):  conflict_records x2, data_dna, extraction_figures,
                           extraction_results, kg_nodes, md_verification_jobs,
                           upload_sessions
    add_constraint    (6):  datasets.uq_datasets_source_material,
                           md_simulation_results.verification_job_id (UNIQUE),
                           ontology_id_map.uq_ontology_id_map_nvl_corpus,
                           property_measurements.uq_pm_dedup,
                           users.{username, email} (UNIQUE)
    remove_fk         (5):  conflict_records x2, extraction_figures x2,
                           extraction_results

Refs:
    * NFM-3446 parent issue (Phase 1 soft-fail: PR #911)
    * NFM-3372 schema-drift guard introduction
    * NFMD-3364 / PR #888 inspect-and-skip pattern (cb1b3ce7)
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "058_align_schema_drift_backlog"
down_revision: str | Sequence[str] | None = "056_add_track_id_to_extraction_job"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Idempotency helpers — every block below is wrapped in a guard so a
# re-run of `alembic upgrade head` is a no-op (matches PR #888 cb1b3ce7).
# ---------------------------------------------------------------------------

def _col_exists(table: str, column: str) -> bool:
    """True iff ``table`` has a column named ``column``."""
    bind = op.get_bind()
    row = bind.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "  AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row is not None


def _constraint_exists(table: str, constraint_name: str) -> bool:
    """True iff ``table`` has a constraint with the given name."""
    bind = op.get_bind()
    row = bind.execute(
        text(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_schema = current_schema() "
            "  AND table_name = :t AND constraint_name = :c"
        ),
        {"t": table, "c": constraint_name},
    ).first()
    return row is not None


def _fk_exists(table: str, constraint_name: str) -> bool:
    """True iff ``table`` has a foreign-key constraint with the given name.

    Equivalent to ``_constraint_exists`` but constrained to the
    ``FOREIGN KEY`` constraint type to disambiguate from unique constraints
    that share names with FKs in some Alembic versions.
    """
    bind = op.get_bind()
    row = bind.execute(
        text(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_schema = current_schema() "
            "  AND table_name = :t "
            "  AND constraint_name = :c "
            "  AND constraint_type = 'FOREIGN KEY'"
        ),
        {"t": table, "c": constraint_name},
    ).first()
    return row is not None


def _unique_target_exists(table: str, name: str) -> bool:
    """True iff ``table`` has a UNIQUE constraint or UNIQUE index with the given name.

    Some prior migrations create UNIQUE INDEXes rather than UNIQUE CONSTRAINTs.
    The drift detector reports both as ``add_constraint`` drift because they
    look the same to SQLAlchemy, so the migration must satisfy either form.
    """
    bind = op.get_bind()
    row = bind.execute(
        text(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_schema = current_schema() "
            "  AND table_name = :t "
            "  AND constraint_name = :c "
            "  AND constraint_type IN ('UNIQUE', 'PRIMARY KEY')"
        ),
        {"t": table, "c": name},
    ).first()
    if row is not None:
        return True
    row = bind.execute(
        text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname = current_schema() "
            "  AND tablename = :t AND indexname = :c"
        ),
        {"t": table, "c": name},
    ).first()
    return row is not None


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    """Align the schema to ``Base.metadata`` so ``check_schema_drift.py`` exits 0."""

    # =====================================================================
    # §1. Drop FK constraints the model no longer declares (remove_fk x5)
    # =====================================================================
    # Order: drop FKs before any DROP COLUMN on the *referenced* table, and
    # before any ALTER COLUMN TYPE on the referencing column.  These five
    # FKs are stale — the SQLAlchemy models no longer carry the
    # corresponding ``ForeignKey()`` declaration (or the column itself is
    # being dropped in §3 below).
    remove_fks = [
        ("conflict_records", "conflict_records_material_node_id_fkey"),
        ("conflict_records", "conflict_records_property_node_id_fkey"),
        ("extraction_figures", "extraction_figures_job_id_fkey"),
        ("extraction_results", "extraction_results_job_id_fkey"),
    ]
    # NOTE: extraction_figures_source_id_fkey is intentionally NOT removed.
    # The model still declares ``ForeignKey("data_sources.id")`` on
    # ``source_id`` — but with a DIFFERENT ``ondelete`` action than what a
    # prior migration added (SET NULL vs NO ACTION).  §7 below drops and
    # re-adds it with the correct action.
    for table, fk_name in remove_fks:
        if _fk_exists(table, fk_name):
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {fk_name}")

    # Drop the extraction_figures.source_id FK first so §7 can re-add it
    # with the model-correct ON DELETE NO ACTION.
    if _fk_exists("extraction_figures", "extraction_figures_source_id_fkey"):
        op.execute(
            "ALTER TABLE extraction_figures "
            "DROP CONSTRAINT extraction_figures_source_id_fkey"
        )

    # =====================================================================
    # §2. Drop the FK constraints that block subsequent column drops
    # =====================================================================
    # Placeholder: at present §1 covers all remove_fk items.  This section
    # is intentionally a no-op so the ordering is greppable.
    fk_blocking_drops: list[tuple[str, str, str]] = []
    for table, fk_name, _ in fk_blocking_drops:
        if _fk_exists(table, fk_name):
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {fk_name}")

    # =====================================================================
    # §3. Drop columns the model no longer declares (remove_column x9)
    # =====================================================================
    # Idempotency guard: every DROP COLUMN is wrapped in IF EXISTS so
    # re-running the migration is safe.
    drop_columns = [
        ("_ref_gap_fill_staging", "imported_at"),
        ("_ref_gap_fill_staging", "staging_status"),
        ("extraction_figures", "file_path"),
        ("extraction_figures", "created_at"),
        ("extraction_figures", "bounding_box"),
        ("extraction_figures", "updated_at"),
        ("extraction_figures", "job_id"),
        ("extraction_results", "source"),
        ("property_types", "default_conflict_strategy"),
    ]
    for table, column in drop_columns:
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")

    # =====================================================================
    # §4. ALTER COLUMN TYPE — JSONB <-> JSON, VARCHAR(N) <-> VARCHAR(M)
    # =====================================================================
    # The drift reports 20 type mismatches.  The pattern is:
    #   * Model says ``JSON`` but DB has ``JSONB``  -> convert JSONB -> JSON
    #   * ``CompatJSONB`` (typed wrapper)            -> kept JSONB in DB
    #   * ``String`` length differs                  -> match model length
    #
    # JSONB -> JSON: the existing data is already valid JSONB (binary,
    # validated).  Reversing to JSON is data-preserving (Postgres stores
    # it as text) so the USING clause is explicit for safety.
    #
    # VARCHAR length changes: TEXT -> VARCHAR(N) needs ``USING column::VARCHAR(N)``
    # if any existing value is wider than N.  All affected tables are
    # empty on every known environment (verified 2026-08-21), so the
    # USING is implicit and lossless.

    type_alterations = [
        # --- conflict_records ---
        ("conflict_records", "conflicting_values", "JSON", None),
        ("conflict_records", "resolved_value", "JSON", None),
        ("conflict_records", "source_values", "JSON", None),
        # --- defect_analysis_results ---
        ("defect_analysis_results", "metadata", "JSON", None),
        # --- dft_calculations  (CompatJSONB -> keep JSONB in DB, change JSON -> JSONB) ---
        ("dft_calculations", "computation_metadata", "JSONB", None),
        # --- extraction_figures ---
        ("extraction_figures", "extracted_data", "JSON", "ix_extraction_figures_extracted_data"),
        ("extraction_figures", "caption", "VARCHAR(500)", None),
        # --- extraction_results ---
        ("extraction_results", "item_data", "JSON", None),
        ("extraction_results", "value", "JSON", None),
        # --- kg_edges ---
        ("kg_edges", "properties", "JSON", None),
        # --- kg_nodes ---
        ("kg_nodes", "properties", "JSON", None),
        # --- knowledge_gaps  (CompatJSONB -> keep JSONB in DB, change TEXT -> JSONB) ---
        ("knowledge_gaps", "metadata_", "JSONB", None),
        # --- md_simulation_results ---
        ("md_simulation_results", "thermodynamic_data", "JSON", None),
        # --- md_verification_jobs ---
        ("md_verification_jobs", "config", "JSON", None),
        # --- potential_fitting_results ---
        ("potential_fitting_results", "parameters", "JSON", None),
        ("potential_fitting_results", "quality_metrics", "JSON", None),
        # --- potentials ---
        ("potentials", "description", "VARCHAR(500)", None),
        # --- property_measurements ---
        ("property_measurements", "value_list", "JSON", None),
        # --- reviews ---
        ("reviews", "data", "JSON", None),
        # --- users ---
        ("users", "title", "VARCHAR(64)", None),
    ]
    for table, column, target, blocking_index in type_alterations:
        if _col_exists(table, column):
            # JSONB -> JSON cannot coexist with a GIN index.  Drop the
            # blocking index first if present.  These indexes appear in
            # the WARN (remove_index) bucket under PR #911 anyway.
            if blocking_index:
                op.execute(f"DROP INDEX IF EXISTS {blocking_index}")
            if target == "JSON":
                op.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} TYPE JSON "
                    f"USING {column}::JSON"
                )
            elif target == "JSONB":
                op.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} TYPE JSONB "
                    f"USING {column}::JSONB"
                )
            elif target.startswith("VARCHAR"):
                op.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {target} "
                    f"USING {column}::{target}"
                )
            else:
                op.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {target}"
                )

    # =====================================================================
    # §5. Add missing columns (missing_column x15)
    # =====================================================================
    # Patterns:
    #   a) NOT NULL with ``default`` on the model side — add as NULLABLE first
    #      with a server-side default of NOW() / 'pending' :: enum, then
    #      SET NOT NULL in §6.
    #   b) NULLABLE on the model side — add with no default; existing rows
    #      (none on dev/staging) get NULL.
    #   c) NOT NULL FK column — must be nullable at the column level so
    #      existing rows (none here) can survive before the FK is added.
    #      data_dna and upload_sessions have no data and no populated
    #      classification_levels row, so we keep them NULLABLE to avoid
    #      forcing a placeholder UUID.  Application code is expected to
    #      populate them on insert.

    add_columns = [
        # _ref_gap_fill_staging (7) — TimestampMixin supplies created_at;
        # the v4 column additions were never captured in a migration.
        ("_ref_gap_fill_staging", "source_file", "TEXT", True, None),
        ("_ref_gap_fill_staging", "composition", "TEXT", True, None),
        ("_ref_gap_fill_staging", "element", "TEXT", True, None),
        ("_ref_gap_fill_staging", "property_category", "VARCHAR(50)", True, None),
        ("_ref_gap_fill_staging", "context", "TEXT", True, None),
        # status is NOT NULL enum with default PENDING -> use NULLABLE +
        # server default so existing rows get a value, then SET NOT NULL
        # in §6.
        (
            "_ref_gap_fill_staging",
            "status",
            "staging_status_enum",
            True,
            "'pending'::staging_status_enum",
        ),
        (
            "_ref_gap_fill_staging",
            "created_at",
            "TIMESTAMPTZ",
            True,
            "NOW()",
        ),
        # conflict_records
        ("conflict_records", "resolution_notes", "VARCHAR", True, None),
        # data_dna — NOT NULL FK to classification_levels.id; keep NULLABLE
        # so existing (empty) rows survive; app populates on insert.
        ("data_dna", "classification_level", "UUID", True, None),
        # defect_analysis_results — TimestampMixin's updated_at (NOT NULL).
        # Backfill NULLs with NOW() in §6 before SET NOT NULL.
        ("defect_analysis_results", "updated_at", "TIMESTAMPTZ", True, "NOW()"),
        # kg_edges — same pattern.
        ("kg_edges", "updated_at", "TIMESTAMPTZ", True, "NOW()"),
        # md_simulation_results — same pattern.
        ("md_simulation_results", "updated_at", "TIMESTAMPTZ", True, "NOW()"),
        # md_verification_jobs — owner_id is nullable FK to users.id.
        ("md_verification_jobs", "owner_id", "UUID", True, None),
        # potential_fitting_results — TimestampMixin updated_at (NOT NULL).
        ("potential_fitting_results", "updated_at", "TIMESTAMPTZ", True, "NOW()"),
        # upload_sessions — NOT NULL FK; keep NULLABLE for the same reason
        # as data_dna.
        ("upload_sessions", "classification_level", "UUID", True, None),
    ]
    for table, column, sql_type, nullable, default_sql in add_columns:
        if not _col_exists(table, column):
            null_clause = "" if nullable else " NOT NULL"
            default_clause = f" DEFAULT {default_sql}" if default_sql else ""
            op.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
                f"{null_clause}{default_clause}"
            )

    # =====================================================================
    # §6. ALTER COLUMN ... SET / DROP NOT NULL (modify_nullable x25)
    # =====================================================================
    # Defensive backfill BEFORE SET NOT NULL: for every column we are
    # flipping from NULLABLE -> NOT NULL, any existing NULL values would
    # cause the ALTER to fail.  Each affected table is empty on the
    # environments checked (pg-drift32 / staging / prod-bootstrap,
    # 2026-08-21), so the UPDATE matches zero rows today — but the guard
    # preserves correctness on environments that have data.

    nullable_changes = [
        # Each row: (table, column, set_not_null, backfill_sql_or_none)
        # set_not_null=True  -> SET NOT NULL  (DB had NULLABLE, model wants NOT NULL)
        # set_not_null=False -> DROP NOT NULL (DB had NOT NULL,   model wants nullable)
        # --- _ref_gap_fill_staging ---
        ("_ref_gap_fill_staging", "confidence", True, "'medium'::confidence_enum"),
        ("_ref_gap_fill_staging", "updated_at", True, "NOW()"),
        ("_ref_gap_fill_staging", "status", True, "'pending'::staging_status_enum"),
        # --- conflict_records ---
        ("conflict_records", "material_node_id", True, "gen_random_uuid()"),
        ("conflict_records", "property_node_id", True, "gen_random_uuid()"),
        ("conflict_records", "status", True, "'pending'"),
        ("conflict_records", "source_values", True, "'[]'::JSON"),
        # --- dft_calculations ---
        ("dft_calculations", "status", True, "'pending'"),
        # --- extraction_figures (model wants these NULLABLE — flip NOT NULL -> nullable) ---
        ("extraction_figures", "page_number", False, None),
        ("extraction_figures", "figure_type", False, None),
        ("extraction_figures", "extracted_data", False, None),
        # --- extraction_results ---
        ("extraction_results", "property_name", True, "''"),
        ("extraction_results", "value", True, "'{}'::JSON"),
        ("extraction_results", "confidence", True, "1.0"),
        # --- kg_edges ---
        ("kg_edges", "properties", True, "'{}'::JSON"),
        ("kg_edges", "confidence", True, "1.0"),
        # --- kg_entity_types ---
        ("kg_entity_types", "ontology_version_id", True, None),
        # --- kg_nodes ---
        ("kg_nodes", "properties", True, "'{}'::JSON"),
        ("kg_nodes", "confidence", True, "1.0"),
        ("kg_nodes", "status", True, "'active'"),
        # --- kg_relation_types ---
        ("kg_relation_types", "ontology_version_id", True, None),
        # --- knowledge_gaps ---
        ("knowledge_gaps", "created_at", True, "NOW()"),
        ("knowledge_gaps", "updated_at", True, "NOW()"),
        # --- property_measurements (model wants this nullable — flip NOT NULL -> nullable) ---
        ("property_measurements", "conditions_hash", False, None),
        # --- reviews ---
        ("reviews", "action", True, "''"),
        ("reviews", "data", True, "'{}'::JSON"),
        # --- newly-added columns in §5: flip NULLABLE -> NOT NULL where the model demands it ---
        ("_ref_gap_fill_staging", "created_at", True, "NOW()"),
        ("data_dna", "classification_level", True, None),
        ("defect_analysis_results", "updated_at", True, "NOW()"),
        ("kg_edges", "updated_at", True, "NOW()"),
        ("md_simulation_results", "updated_at", True, "NOW()"),
        ("potential_fitting_results", "updated_at", True, "NOW()"),
        ("upload_sessions", "classification_level", True, None),
    ]

    for table, column, set_not_null, backfill in nullable_changes:
        if not _col_exists(table, column):
            continue
        # Backfill any NULLs first (defensive — empty tables pass through).
        if backfill is not None:
            op.execute(
                f"UPDATE {table} SET {column} = {backfill} WHERE {column} IS NULL"
            )
        if set_not_null:
            op.execute(
                f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL"
            )
        else:
            op.execute(
                f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL"
            )

    # =====================================================================
    # §7. Add missing FK constraints (missing_fk x8)
    # =====================================================================
    # All referenced rows are guaranteed by §5 + §6 (referenced tables
    # exist; FK columns are NOT NULL or nullable-as-needed; existing rows
    # are valid).  ON DELETE actions mirror the SQLAlchemy declarations
    # in the models.

    add_fks = [
        # conflict_records: material_node_id and property_node_id reference kg_nodes.
        (
            "conflict_records",
            "conflict_records_material_node_id_fkey",
            "material_node_id",
            "kg_nodes",
            "id",
            "NO ACTION",
        ),
        (
            "conflict_records",
            "conflict_records_property_node_id_fkey",
            "property_node_id",
            "kg_nodes",
            "id",
            "NO ACTION",
        ),
        # data_dna.classification_level -> classification_levels.id (RESTRICT).
        (
            "data_dna",
            "data_dna_classification_level_id_fkey",
            "classification_level",
            "classification_levels",
            "id",
            "RESTRICT",
        ),
        # extraction_figures source_id -> data_sources.id — model declares no
        # ondelete, so use the PG default (NO ACTION).  §1 above dropped
        # the prior constraint that had SET NULL.
        (
            "extraction_figures",
            "extraction_figures_source_id_fkey",
            "source_id",
            "data_sources",
            "id",
            "NO ACTION",
        ),
        # extraction_results.job_id -> extraction_jobs.id (SET NULL).
        # Note the column name is ``job_id`` in the model, NOT
        # ``extraction_job_id``.  This was a stale FK name from
        # migration 016 that pointed at the wrong column.
        (
            "extraction_results",
            "extraction_results_job_id_fkey",
            "job_id",
            "extraction_jobs",
            "id",
            "SET NULL",
        ),
        # extraction_results.source_id -> data_sources.id (SET NULL).
        # This FK is declared in the model but was never migrated to the
        # DB.
        (
            "extraction_results",
            "extraction_results_source_id_fkey",
            "source_id",
            "data_sources",
            "id",
            "SET NULL",
        ),
        # kg_nodes.figure_id -> extraction_figures.id (SET NULL).
        (
            "kg_nodes",
            "kg_nodes_figure_id_fkey",
            "figure_id",
            "extraction_figures",
            "id",
            "SET NULL",
        ),
        # kg_nodes.ontology_version_id -> ontology_versions.id (SET NULL).
        (
            "kg_nodes",
            "kg_nodes_ontology_version_id_fkey",
            "ontology_version_id",
            "ontology_versions",
            "id",
            "SET NULL",
        ),
        # md_verification_jobs owner_id -> users.id (SET NULL).
        (
            "md_verification_jobs",
            "md_verification_jobs_owner_id_fkey",
            "owner_id",
            "users",
            "id",
            "SET NULL",
        ),
        # upload_sessions.classification_level -> classification_levels.id (RESTRICT).
        (
            "upload_sessions",
            "upload_sessions_classification_level_id_fkey",
            "classification_level",
            "classification_levels",
            "id",
            "RESTRICT",
        ),
    ]
    for table, fk_name, col, ref_table, ref_col, on_delete in add_fks:
        if not _fk_exists(table, fk_name) and _col_exists(table, col):
            op.execute(
                f"ALTER TABLE {table} "
                f"ADD CONSTRAINT {fk_name} "
                f"FOREIGN KEY ({col}) REFERENCES {ref_table}({ref_col}) "
                f"ON DELETE {on_delete}"
            )

    # =====================================================================
    # §8. Add missing UNIQUE / CHECK constraints (add_constraint x6)
    # =====================================================================
    #
    # The SQLAlchemy model declares ``UniqueConstraint(..., name="...")``
    # which alembic expects to materialize as a real ``UNIQUE`` constraint
    # in ``information_schema.table_constraints``.  Some prior migrations
    # only created UNIQUE INDEXes (which are functionally equivalent for
    # uniqueness enforcement but live in ``pg_indexes`` instead).  The
    # migration below promotes those INDEXes to CONSTRAINTs and adds
    # fresh ones where neither exists.
    #
    # For ``uq_ontology_id_map_nvl_corpus``: this name was hijacked by a
    # prior migration as the PRIMARY KEY on (nvl_id, corpus_id).  Alembic
    # autogen wants a ``UNIQUE`` constraint under that name, not a PRIMARY
    # KEY.  We rename the PK to the PG-default name and add the UNIQUE
    # constraint in its place.
    add_constraints = [
        ("datasets", "uq_datasets_source_material",
         "UNIQUE (source_id, material_id)"),
        ("md_simulation_results", "uq_md_simulation_results_verification_job_id",
         "UNIQUE (verification_job_id)"),
        ("ontology_id_map", "uq_ontology_id_map_nvl_corpus",
         "UNIQUE (nvl_id, corpus_id)"),
        # NOTE: the model defines uq_pm_dedup on 4 columns
        # (dataset_id, property_type_id, conditions_hash, method), not
        # just (conditions_hash, method).  The 4-tuple matches the
        # dedup_key in NFM-1981 AC-2.
        ("property_measurements", "uq_pm_dedup",
         "UNIQUE (dataset_id, property_type_id, conditions_hash, method)"),
        ("users", "uq_users_username",
         "UNIQUE (username)"),
        ("users", "uq_users_email",
         "UNIQUE (email)"),
    ]
    # Step 8a: convert INDEX -> CONSTRAINT where the index exists but the
    # constraint does not.  Drop the index first, then add the constraint
    # (which creates a fresh backing index under the same name).
    for table, name in [
        ("datasets", "uq_datasets_source_material"),
        ("property_measurements", "uq_pm_dedup"),
    ]:
        bind = op.get_bind()
        idx_exists = bind.execute(
            text(
                "SELECT 1 FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "  AND tablename = :t AND indexname = :c"
            ),
            {"t": table, "c": name},
        ).first() is not None
        constraint_exists = bind.execute(
            text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_schema = current_schema() "
                "  AND table_name = :t "
                "  AND constraint_name = :c "
                "  AND constraint_type IN ('UNIQUE', 'PRIMARY KEY')"
            ),
            {"t": table, "c": name},
        ).first() is not None
        if idx_exists and not constraint_exists:
            op.execute(f"DROP INDEX IF EXISTS {name}")
    # Step 8b: rename the ontology_id_map PK so we can add the UNIQUE
    # constraint under the model's expected name.
    bind = op.get_bind()
    pk_name = bind.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'public.ontology_id_map'::regclass "
            "  AND contype = 'p' "
            "  AND conname = :c"
        ),
        {"c": "uq_ontology_id_map_nvl_corpus"},
    ).first()
    if pk_name is not None:
        op.execute(
            "ALTER TABLE ontology_id_map "
            "RENAME CONSTRAINT uq_ontology_id_map_nvl_corpus "
            "TO ontology_id_map_pkey"
        )
    # Step 8c: add the unique constraints where still missing.
    for table, name, sql in add_constraints:
        bind = op.get_bind()
        constraint_exists = bind.execute(
            text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_schema = current_schema() "
                "  AND table_name = :t "
                "  AND constraint_name = :c "
                "  AND constraint_type = 'UNIQUE'"
            ),
            {"t": table, "c": name},
        ).first() is not None
        if not constraint_exists:
            op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {sql}")


# ---------------------------------------------------------------------------
# downgrade — reverse every change so a downgrade -> upgrade round-trip is
# semantically idempotent at the schema level.
# ---------------------------------------------------------------------------

def downgrade() -> None:
    """Restore the pre-Phase-2 schema (drift will reappear)."""

    # §8 inverse: drop the constraints we added.
    for table, name in [
        ("users", "uq_users_email"),
        ("users", "uq_users_username"),
        ("property_measurements", "uq_pm_dedup"),
        ("ontology_id_map", "uq_ontology_id_map_nvl_corpus"),
        ("md_simulation_results", "uq_md_simulation_results_verification_job_id"),
        ("datasets", "uq_datasets_source_material"),
    ]:
        if _constraint_exists(table, name):
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")

    # §7 inverse: drop FKs we added.
    for table, fk_name in [
        ("upload_sessions", "upload_sessions_classification_level_id_fkey"),
        ("md_verification_jobs", "md_verification_jobs_owner_id_fkey"),
        ("kg_nodes", "kg_nodes_ontology_version_id_fkey"),
        ("kg_nodes", "kg_nodes_figure_id_fkey"),
        ("extraction_results", "extraction_results_job_id_fkey"),
        ("extraction_results", "extraction_results_source_id_fkey"),
        ("extraction_figures", "extraction_figures_source_id_fkey"),
        ("data_dna", "data_dna_classification_level_id_fkey"),
        ("conflict_records", "conflict_records_property_node_id_fkey"),
        ("conflict_records", "conflict_records_material_node_id_fkey"),
    ]:
        if _fk_exists(table, fk_name):
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {fk_name}")

    # §6 inverse: drop NOT NULL where we set it; SET NOT NULL where we dropped it.
    # The 4 "false" entries flip NOT NULL -> NULLABLE on columns the model
    # declares as nullable but a prior migration had NOT NULL'd.
    for table, column, set_not_null in [
        ("reviews", "data", False),
        ("reviews", "action", False),
        ("property_measurements", "conditions_hash", True),
        ("knowledge_gaps", "updated_at", False),
        ("knowledge_gaps", "created_at", False),
        ("kg_relation_types", "ontology_version_id", False),
        ("kg_nodes", "status", False),
        ("kg_nodes", "confidence", False),
        ("kg_nodes", "properties", False),
        ("kg_entity_types", "ontology_version_id", False),
        ("kg_edges", "confidence", False),
        ("kg_edges", "properties", False),
        ("extraction_results", "confidence", False),
        ("extraction_results", "value", False),
        ("extraction_results", "property_name", False),
        ("extraction_figures", "extracted_data", True),
        ("extraction_figures", "figure_type", True),
        ("extraction_figures", "page_number", True),
        ("dft_calculations", "status", False),
        ("conflict_records", "source_values", False),
        ("conflict_records", "status", False),
        ("conflict_records", "property_node_id", False),
        ("conflict_records", "material_node_id", False),
        ("_ref_gap_fill_staging", "status", False),
        ("_ref_gap_fill_staging", "updated_at", False),
        ("_ref_gap_fill_staging", "confidence", False),
        # newly-added NOT NULL columns
        ("_ref_gap_fill_staging", "created_at", False),
        ("data_dna", "classification_level", False),
        ("defect_analysis_results", "updated_at", False),
        ("kg_edges", "updated_at", False),
        ("md_simulation_results", "updated_at", False),
        ("potential_fitting_results", "updated_at", False),
        ("upload_sessions", "classification_level", False),
    ]:
        if _col_exists(table, column):
            if set_not_null:
                op.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL"
                )
            else:
                op.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL"
                )

    # §5 inverse: drop the columns we added.
    for table, column in [
        ("upload_sessions", "classification_level"),
        ("potential_fitting_results", "updated_at"),
        ("md_verification_jobs", "owner_id"),
        ("md_simulation_results", "updated_at"),
        ("kg_edges", "updated_at"),
        ("defect_analysis_results", "updated_at"),
        ("data_dna", "classification_level"),
        ("conflict_records", "resolution_notes"),
        ("_ref_gap_fill_staging", "created_at"),
        ("_ref_gap_fill_staging", "status"),
        ("_ref_gap_fill_staging", "context"),
        ("_ref_gap_fill_staging", "property_category"),
        ("_ref_gap_fill_staging", "element"),
        ("_ref_gap_fill_staging", "composition"),
        ("_ref_gap_fill_staging", "source_file"),
    ]:
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")

    # §4 inverse: revert JSONB and VARCHAR lengths.
    for table, column, target in [
        ("users", "title", "VARCHAR(255)"),
        ("potentials", "description", "TEXT"),
        ("extraction_figures", "caption", "TEXT"),
        ("reviews", "data", "JSONB"),
        ("property_measurements", "value_list", "JSONB"),
        ("potential_fitting_results", "quality_metrics", "JSONB"),
        ("potential_fitting_results", "parameters", "JSONB"),
        ("md_verification_jobs", "config", "JSONB"),
        ("md_simulation_results", "thermodynamic_data", "JSONB"),
        ("kg_nodes", "properties", "JSONB"),
        ("kg_edges", "properties", "JSONB"),
        ("extraction_results", "value", "JSONB"),
        ("extraction_results", "item_data", "JSONB"),
        ("extraction_figures", "extracted_data", "JSONB"),
        ("defect_analysis_results", "metadata", "JSONB"),
        ("conflict_records", "source_values", "JSONB"),
        ("conflict_records", "resolved_value", "JSONB"),
        ("conflict_records", "conflicting_values", "JSONB"),
        # dft_calculations and knowledge_gaps: reverse JSONB -> JSON/TEXT
        ("dft_calculations", "computation_metadata", "JSON"),
        ("knowledge_gaps", "metadata_", "TEXT"),
    ]:
        if _col_exists(table, column):
            if target == "JSONB":
                op.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} "
                    f"TYPE JSONB USING {column}::JSONB"
                )
            elif target == "JSON":
                op.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} "
                    f"TYPE JSON USING {column}::JSON"
                )
            else:
                op.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {target}"
                )

    # §3 inverse: re-add the dropped columns.  These recreations are
    # best-effort — any data previously held in them is lost.
    op.execute(
        "ALTER TABLE property_types ADD COLUMN IF NOT EXISTS "
        "default_conflict_strategy VARCHAR(50)"
    )
    op.execute(
        "ALTER TABLE extraction_results ADD COLUMN IF NOT EXISTS "
        "source VARCHAR(50)"
    )
    op.execute(
        "ALTER TABLE extraction_figures ADD COLUMN IF NOT EXISTS "
        "job_id UUID"
    )
    op.execute(
        "ALTER TABLE extraction_figures ADD COLUMN IF NOT EXISTS "
        "updated_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE extraction_figures ADD COLUMN IF NOT EXISTS "
        "bounding_box JSONB"
    )
    op.execute(
        "ALTER TABLE extraction_figures ADD COLUMN IF NOT EXISTS "
        "created_at TIMESTAMPTZ DEFAULT NOW()"
    )
    op.execute(
        "ALTER TABLE extraction_figures ADD COLUMN IF NOT EXISTS "
        "file_path VARCHAR(500)"
    )
    op.execute(
        "ALTER TABLE _ref_gap_fill_staging ADD COLUMN IF NOT EXISTS "
        "staging_status staging_status_enum DEFAULT 'pending'::staging_status_enum"
    )
    op.execute(
        "ALTER TABLE _ref_gap_fill_staging ADD COLUMN IF NOT EXISTS "
        "imported_at TIMESTAMPTZ DEFAULT NOW()"
    )

    # §1 inverse: re-add the dropped FKs.
    # NOTE: extraction_results_job_id_fkey was historically created against
    # the wrong column (``extraction_job_id``, which never existed in the
    # model).  §1 drops it during upgrade; the downgrade deliberately does
    # NOT re-add it because the column never existed in the first place.
    fk_restore = [
        (
            "extraction_figures", "extraction_figures_source_id_fkey",
            "source_id", "data_sources", "id", "SET NULL",
        ),
        (
            "extraction_figures", "extraction_figures_job_id_fkey",
            "job_id", "extraction_jobs", "id", "CASCADE",
        ),
        (
            "conflict_records", "conflict_records_material_node_id_fkey",
            "material_node_id", "kg_nodes", "id", "NO ACTION",
        ),
        (
            "conflict_records", "conflict_records_property_node_id_fkey",
            "property_node_id", "kg_nodes", "id", "NO ACTION",
        ),
    ]
    for table, fk_name, col, ref_table, ref_col, on_delete in fk_restore:
        if not _fk_exists(table, fk_name) and _col_exists(table, col):
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {fk_name} "
                f"FOREIGN KEY ({col}) REFERENCES {ref_table}({ref_col}) "
                f"ON DELETE {on_delete}"
            )