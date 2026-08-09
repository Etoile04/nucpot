"""NFM-2647: offline verification of migration 050 (dispatch tracking columns).

Verifies that migration 050 correctly adds four nullable dispatch-tracking
columns and a status index to the data_collection_requests table.

Tests are offline (no live PostgreSQL) following the project convention
established in test_migration_044_ontology_version.py.
"""

from __future__ import annotations

import re

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

REVISION = "050_add_dispatch_tracking_to_dcr"
DOWN_REVISION = "049_add_ontology_version_to_extraction_job"
MIGRATION_PATH = f"migrations/versions/{REVISION}.py"

COLUMNS = ("dispatched_at", "dispatched_path", "dispatch_status", "result_reference")
INDEX_NAME = "ix_dcr_dispatch_status"


@pytest.fixture(scope="module")
def script_directory() -> ScriptDirectory:
    """Load the Alembic script directory for offline chain analysis."""
    return ScriptDirectory.from_config(Config("alembic.ini"))


@pytest.fixture(scope="module")
def migration_source() -> str:
    """Read the migration source file as text."""
    with open(MIGRATION_PATH) as handle:
        return handle.read()


def _extract_sa_column_blocks(source: str) -> dict[str, str]:
    """Extract sa.Column(name, body) blocks from source with balanced parens.

    Returns mapping of column name → body string (between outer parens of sa.Column).
    """
    blocks: dict[str, str] = {}
    i = 0
    while i < len(source):
        idx = source.find("sa.Column(", i)
        if idx == -1:
            break
        start = idx + len("sa.Column(")
        # Find matching close paren, balancing depth
        depth = 1
        pos = start
        while pos < len(source) and depth > 0:
            ch = source[pos]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            pos += 1
        if depth != 0:
            break  # Unbalanced — stop searching
        # Inside [start, pos) — first positional arg is the column name string literal
        inner = source[start : pos - 1]
        # Strip leading whitespace and a possible quote
        stripped = inner.lstrip()
        if stripped.startswith('"'):
            # Find end of string
            end_quote = stripped.find('"', 1)
            name = stripped[1:end_quote]
        else:
            i = pos
            continue
        blocks[name] = inner
        i = pos
    return blocks


class TestMigrationChain:
    """Chain integrity: 050 linearises off 049 and remains a single head."""

    def test_revision_loadable(self, script_directory: ScriptDirectory) -> None:
        rev = script_directory.get_revision(REVISION)
        assert rev is not None, f"Migration {REVISION!r} not registered"
        assert rev.revision == REVISION

    def test_down_revision_is_049(self, script_directory: ScriptDirectory) -> None:
        """050 must chain off 049 to maintain a linear history."""
        rev = script_directory.get_revision(REVISION)
        assert rev is not None
        assert rev.down_revision == DOWN_REVISION, (
            f"Expected down_revision={DOWN_REVISION!r}, got {rev.down_revision!r}"
        )

    def test_single_head(self, script_directory: ScriptDirectory) -> None:
        """Exactly one head, or `alembic upgrade head` fails at deploy."""
        heads = script_directory.get_heads()
        assert len(heads) == 1, (
            f"Expected exactly 1 alembic head, got {len(heads)}: {heads!r}"
        )

    def test_050_is_ancestor_of_head(self, script_directory: ScriptDirectory) -> None:
        heads = script_directory.get_heads()
        ancestors = {
            rev.revision for rev in script_directory.iterate_revisions(heads[0], "base")
        }
        assert REVISION in ancestors, (
            f"{REVISION!r} is not an ancestor of head {heads[0]!r}"
        )

    def test_no_duplicate_revisions(self, script_directory: ScriptDirectory) -> None:
        revisions = [rev.revision for rev in script_directory.walk_revisions()]
        assert len(revisions) == len(set(revisions)), "Duplicate revision ids found"


class TestUpgrade:
    """upgrade() adds all four columns and the dispatch_status index."""

    def test_adds_all_four_columns(self, migration_source: str) -> None:
        upgrade = migration_source.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
        for col in COLUMNS:
            assert f'"{col}"' in upgrade, (
                f"upgrade() must add column {col!r} to data_collection_requests"
            )

    def test_all_columns_nullable(self, migration_source: str) -> None:
        upgrade = migration_source.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
        col_blocks = _extract_sa_column_blocks(upgrade)
        assert len(col_blocks) >= len(COLUMNS), (
            f"Expected at least {len(COLUMNS)} sa.Column() calls, found {len(col_blocks)}"
        )
        for col in COLUMNS:
            assert col in col_blocks, (
                f"Column {col!r} must be declared via sa.Column() in upgrade()"
            )
            assert "nullable=True" in col_blocks[col], (
                f"Column {col!r} must be nullable for backward compatibility"
            )

    def test_creates_dispatch_status_index(self, migration_source: str) -> None:
        assert INDEX_NAME in migration_source, (
            f"upgrade() must create index {INDEX_NAME!r} for dispatch_status filtering"
        )
        assert '["dispatch_status"]' in migration_source, (
            "Index must be on the dispatch_status column"
        )

    def test_targets_correct_table(self, migration_source: str) -> None:
        upgrade = migration_source.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
        assert upgrade.count("data_collection_requests") >= len(COLUMNS), (
            "All add_column calls must target data_collection_requests"
        )

    def test_column_types_match_spec(self, migration_source: str) -> None:
        """Verify column type declarations match the issue spec."""
        assert "sa.DateTime(timezone=True)" in migration_source, (
            "dispatched_at must be DateTime(timezone=True)"
        )
        assert 'sa.String(50)' in migration_source, (
            "dispatched_path must be String(50)"
        )
        assert 'sa.String(20)' in migration_source, (
            "dispatch_status must be String(20)"
        )
        assert 'sa.String(500)' in migration_source, (
            "result_reference must be String(500)"
        )


class TestDowngrade:
    """downgrade() removes the index and all four columns."""

    def test_drops_dispatch_status_index(self, migration_source: str) -> None:
        downgrade = migration_source.split("def downgrade", 1)[1]
        assert INDEX_NAME in downgrade, (
            f"downgrade() must drop index {INDEX_NAME!r}"
        )

    def test_removes_all_four_columns(self, migration_source: str) -> None:
        downgrade = migration_source.split("def downgrade", 1)[1]
        for col in COLUMNS:
            assert f'"{col}"' in downgrade, (
                f"downgrade() must remove column {col!r}"
            )

    def test_drops_index_before_columns(self, migration_source: str) -> None:
        """Index must be dropped before columns to avoid constraint errors."""
        downgrade = migration_source.split("def downgrade", 1)[1]
        assert downgrade.index("drop_index") < downgrade.index("drop_column"), (
            "downgrade() must drop the index before dropping columns"
        )
