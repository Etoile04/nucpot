"""NFM-4548 — G1-B schema migration: structural + acceptance coverage.

Mirrors the ``test_migration_070_d2_dedup.py`` / ``test_migration_083_*``
pattern: chain + SQL structural checks against the migration source, and
``importlib``-based introspection to pin the dedupe tuple and the version
statuses.  No live Postgres is required — the migration uses GENERATED
ALWAYS ... STORED, partial-unique indexes, and JSONB, which are all
Postgres-only and not exercised by the SQLite test fixture.

Acceptance criteria covered:

* [AC-6] ``dataset_versions`` table is created with the four
  constraints (one CHECK + one NOT NULL-equivalent CHECK + one UNIQUE
  on (dataset_id, version_no) + one partial-unique "one released per
  dataset") and the four required indexes / FKs.
* [AC-9] ``dedupe_key`` is a GENERATED ALWAYS column on
  ``property_measurements``, and the partial-unique index
  ``uq_pm_dedupe_key`` enforces uniqueness on
  ``dedupe_key WHERE dataset_version_id IS NOT NULL`` (closes the
  NULL-hash leak in the pre-existing ``uq_pm_dedup``).
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "085_g1b_conditions_dataset_versions_dedupe.py"
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    return _MIGRATION_PATH.read_text()


@pytest.fixture(scope="module")
def migration_ast(migration_source: str) -> ast.Module:
    return ast.parse(migration_source)


@pytest.fixture(scope="module")
def migration_module():
    """Import the migration module without triggering alembic env."""
    spec = importlib.util.spec_from_file_location(
        "_nfm4548_migration_under_test", str(_MIGRATION_PATH)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestMigration085Chain:
    def test_file_exists(self):
        assert _MIGRATION_PATH.is_file()

    def test_revision_constant(self, migration_source: str) -> None:
        assert re.search(
            r'^revision:\s*str\s*=\s*"085_g1b_conditions_dataset_versions_dedupe"',
            migration_source,
            re.MULTILINE,
        ), "revision must equal '085_g1b_conditions_dataset_versions_dedupe'"

    def test_chains_off_084(self, migration_source: str) -> None:
        assert re.search(
            r"^down_revision:\s*str\s*\|\s*Sequence\[str\]\s*\|\s*None\s*=\s*"
            r'"084_potentials_list_partial_index"',
            migration_source,
            re.MULTILINE,
        ), "down_revision must be '084_potentials_list_partial_index'"


class TestMigration085Structural:
    def test_parses_as_valid_python(self, migration_ast: ast.Module) -> None:
        assert migration_ast.body

    def test_uses_alembic_op(self, migration_ast: ast.Module) -> None:
        names: set[str] = set()
        for node in ast.walk(migration_ast):
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                for alias in node.names:
                    names.add(alias.name)
        assert "op" in names

    def test_defines_upgrade_and_downgrade(self, migration_ast: ast.Module) -> None:
        functions = [
            n.name for n in migration_ast.body if isinstance(n, ast.FunctionDef)
        ]
        assert "upgrade" in functions
        assert "downgrade" in functions

    def test_no_do_blocks(self, migration_source: str) -> None:
        assert "DO $$" not in migration_source


class TestMigration085DatasetVersionsAC6:
    def test_creates_dataset_versions_table(self, migration_source: str) -> None:
        assert "op.create_table(" in migration_source
        assert '"dataset_versions"' in migration_source or (
            "'dataset_versions'" in migration_source
        )

    def test_dataset_versions_status_check_constraint(
        self, migration_module, migration_source: str
    ) -> None:
        assert tuple(migration_module._DATASET_VERSION_STATUSES) == (
            "draft",
            "released",
            "rolled_back",
            "superseded",
        ), "ADR-017 section 2.3 status tuple must match"
        assert "ck_dataset_versions_status" in migration_source

    def test_dataset_versions_version_no_check(
        self, migration_source: str
    ) -> None:
        assert "ck_dataset_versions_version_no" in migration_source
        assert "version_no >= 1" in migration_source

    def test_dataset_versions_unique_version_no_per_dataset(
        self, migration_source: str
    ) -> None:
        assert "uq_dataset_versions_dataset_version_no" in migration_source
        assert '"dataset_id"' in migration_source
        assert '"version_no"' in migration_source

    def test_one_released_version_per_dataset(self, migration_source: str) -> None:
        assert "uq_dataset_versions_one_released" in migration_source
        assert "status = 'released'" in migration_source or (
            "status='released'" in migration_source
        )

    def test_dataset_versions_index_status(self, migration_source: str) -> None:
        assert "idx_dataset_versions_status" in migration_source

    def test_dataset_versions_fk_to_datasets(self, migration_source: str) -> None:
        assert "datasets.id" in migration_source or (
            '"datasets"' in migration_source
        )

    def test_dataset_versions_row_and_source_ids_are_jsonb(
        self, migration_source: str
    ) -> None:
        assert "row_ids" in migration_source
        assert "source_ids" in migration_source
        assert "JSONB" in migration_source


class TestMigration085DedupeKeyAC9:
    def test_dedupe_key_components_constant(self, migration_module) -> None:
        assert hasattr(migration_module, "_DEDUPE_COMPONENTS")
        assert tuple(migration_module._DEDUPE_COMPONENTS) == (
            "dataset_id",
            "property_type_id",
            "source_id",
            "value_hash",
            "conditions_hash",
            "method",
        ), "ADR-017 section 2.6 superset decision (f)"

    def test_dedupe_key_column_is_generated_stored(
        self, migration_source: str
    ) -> None:
        assert "GENERATED ALWAYS AS" in migration_source or (
            "GENERATED ALWAYS" in migration_source
        )
        assert "STORED" in migration_source
        assert migration_source.count("GENERATED ALWAYS") >= 2

    def test_dedupe_key_partial_unique_index(self, migration_source: str) -> None:
        assert "uq_pm_dedupe_key" in migration_source
        assert "dataset_version_id IS NOT NULL" in migration_source

    def test_uq_pm_dedup_retained(self, migration_source: str) -> None:
        assert "DROP INDEX uq_pm_dedup" not in migration_source
        assert 'drop_constraint("uq_pm_dedup"' not in migration_source
        assert 'drop_constraint(\'uq_pm_dedup\'' not in migration_source

    def test_dedupe_key_expression_inlines_value_hash(
        self, migration_source: str
    ) -> None:
        assert "value_scalar::text" in migration_source
        assert "value_min::text" in migration_source
        assert "value_max::text" in migration_source
        assert "uncertainty::text" in migration_source
        assert migration_source.count("md5(") >= 2


class TestMigration085ConditionsExpansion:
    def test_conditions_column_is_jsonb_not_nullable(
        self, migration_source: str
    ) -> None:
        assert '"conditions"' in migration_source or (
            "'conditions'" in migration_source
        )
        assert "JSONB" in migration_source
        assert "nullable=False" in migration_source

    def test_high_frequency_columns_added(self, migration_source: str) -> None:
        for col in ("simulation_method", "model_name", "temp_k", "pressure_gpa"):
            assert col in migration_source

    def test_phase_is_not_a_column(self, migration_source: str) -> None:
        assert not re.search(
            r'sa\.(?:Column|column)\(\s*[\'"]phase[\'"]',
            migration_source,
        )

    def test_gin_index_on_conditions(self, migration_source: str) -> None:
        assert "idx_pm_conditions_gin" in migration_source
        assert "postgresql_using=" in migration_source
        assert '"gin"' in migration_source or "'gin'" in migration_source

    def test_backfill_preserves_raw_legacy_values(self, migration_source: str) -> None:
        assert "legacy_conditions" in migration_source
        assert "measurement_conditions" in migration_source
        assert "jsonb_agg" in migration_source
        assert "jsonb_strip_nulls" in migration_source


class TestMigration085DatasetIdentity:
    def test_literature_doi_column(self, migration_source: str) -> None:
        assert "literature_doi" in migration_source
        assert "VARCHAR(255)" in migration_source or (
            "String(length=255)" in migration_source
        )

    def test_literature_content_hash_column(self, migration_source: str) -> None:
        assert "literature_content_hash" in migration_source
        assert "VARCHAR(64)" in migration_source or (
            "String(length=64)" in migration_source
        )

    def test_doi_partial_unique_index(self, migration_source: str) -> None:
        assert "uq_datasets_literature_doi" in migration_source
        assert "literature_doi IS NOT NULL" in migration_source


class TestMigration085Reversibility:
    def test_downgrade_drops_dataset_versions_table(self, migration_source: str) -> None:
        downgrade_section = migration_source.split("def downgrade()")[1]
        assert '"dataset_versions"' in downgrade_section or (
            "'dataset_versions'" in downgrade_section
        )
        assert "drop_table" in downgrade_section

    def test_downgrade_drops_generated_columns(self, migration_source: str) -> None:
        downgrade_section = migration_source.split("def downgrade()")[1]
        assert "dedupe_key" in downgrade_section
        assert "value_hash" in downgrade_section

    def test_downgrade_drops_uq_pm_dedupe_key(self, migration_source: str) -> None:
        downgrade_section = migration_source.split("def downgrade()")[1]
        assert "uq_pm_dedupe_key" in downgrade_section

    def test_downgrade_drops_literature_identity_columns(
        self, migration_source: str
    ) -> None:
        downgrade_section = migration_source.split("def downgrade()")[1]
        assert "literature_doi" in downgrade_section
        assert "literature_content_hash" in downgrade_section

    def test_downgrade_drops_conditions_columns(self, migration_source: str) -> None:
        downgrade_section = migration_source.split("def downgrade()")[1]
        for col in (
            "conditions",
            "simulation_method",
            "model_name",
            "temp_k",
            "pressure_gpa",
            "dataset_version_id",
            "source_id",
        ):
            assert col in downgrade_section


class TestMigration085Documentation:
    def test_docstring_references_adr_017_section_2_3(
        self, migration_source: str
    ) -> None:
        assert "ADR-017" in migration_source and "2.3" in migration_source

    def test_docstring_references_adr_017_section_2_5(
        self, migration_source: str
    ) -> None:
        assert "ADR-017" in migration_source and "2.5" in migration_source

    def test_docstring_references_adr_017_section_2_6(
        self, migration_source: str
    ) -> None:
        assert "ADR-017" in migration_source and "2.6" in migration_source

    def test_docstring_references_adr_016_section_2_4(
        self, migration_source: str
    ) -> None:
        assert "ADR-016" in migration_source and "2.4" in migration_source

    def test_docstring_calls_out_decision_f_superset(
        self, migration_source: str
    ) -> None:
        assert "(f)" in migration_source
        assert "conditions_hash" in migration_source
        assert "strict superset" in migration_source or (
            "behaviour" in migration_source
        )

    def test_docstring_calls_out_decision_h_partial_uniqueness(
        self, migration_source: str
    ) -> None:
        assert "(g)" in migration_source
        assert "(h)" in migration_source
        assert "uq_pm_dedup" in migration_source
