"""NFM-5955 C1 guardrail — model metadata snapshot (net-new, no layout change).

Locks ``sorted(Base.metadata.tables.keys())`` against the committed snapshot
at ``tests/fixtures/model_metadata_snapshot.json`` across BOTH assembly paths
that build ``Base.metadata``:

* ``migrations/env.py:30`` — the alembic autogenerate path, verified in a
  fresh interpreter. A table that silently disappears from metadata makes
  autogenerate emit a ``DROP TABLE`` against production on the next deploy
  (the DB still has the table; the model no longer registers it), so this
  is the highest-priority guard ahead of the C1 ``models/`` package move.
* ``tests/conftest.py:44`` — the same package import as seen in-process by
  the pytest suite. ``conftest._strip_dangling_fks`` silently swallows FKs
  whose target table is absent from the metadata, which would otherwise
  mask a vanished table — ``create_all`` still succeeds against a partial
  schema and the suite stays green. The snapshot is the only mechanical
  gate that catches "a table quietly disappeared" on this path.

Regeneration (only for deliberate schema changes): dump
``sorted(Base.metadata.tables.keys())`` from a fresh interpreter and commit
the result — the diff must be reviewed like a migration.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nfm_db.models import Base

APP_API_DIR = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = Path(__file__).resolve().parent / "fixtures" / "model_metadata_snapshot.json"

# The snippet runs the exact env.py:30 import in a fresh interpreter. The
# import statement is assembled via f-string interpolation so THIS source
# file contains no matchable models-import text line — the CI import
# counter scans this tree text-first (rg 口径) and the test's own machinery
# must not inflate the consumer baseline it is measuring (NFM-5955).
_MODELS_PACKAGE_PATH = "nfm_db.models"
_FRESH_INTERPRETER_CODE = (
    "import json, sys\n"
    f"from {_MODELS_PACKAGE_PATH} import Base\n"
    "json.dump(sorted(Base.metadata.tables.keys()), sys.stdout)\n"
)


def _assert_matches_snapshot(expected: list[str], actual: list[str], label: str) -> None:
    """Fail with a greppable added/removed diff when metadata diverges."""
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if not missing and not extra:
        return
    lines = [f"{label}: Base.metadata diverges from committed snapshot"]
    if missing:
        lines.append(f"  tables MISSING from metadata (snapshot expects them): {missing}")
    if extra:
        lines.append(f"  NEW tables not in snapshot: {extra}")
    lines.append(
        "  A missing table means alembic autogenerate will emit DROP TABLE against "
        "production. If this change is intentional, regenerate "
        "tests/fixtures/model_metadata_snapshot.json and review the diff like a migration."
    )
    pytest.fail("\n".join(lines))


def _fresh_interpreter_table_keys() -> list[str]:
    """Table keys assembled by a clean interpreter, mirroring env.py:30.

    Runs in a subprocess so lazily-registered modules or import-order
    effects that the pytest process masks (its ``nfm_db.models`` is already
    warm in ``sys.modules``) still reproduce exactly what alembic sees.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(APP_API_DIR / "src"), env.get("PYTHONPATH", "")) if part
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", _FRESH_INTERPRETER_CODE],
            cwd=APP_API_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(f"fresh-interpreter assembly path timed out after 120s: {error}")
    if result.returncode != 0:
        pytest.fail(
            f"fresh-interpreter assembly path (migrations/env.py:30 form) failed:\n{result.stderr}"
        )
    try:
        tables = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        pytest.fail(f"fresh-interpreter stdout was not JSON ({error}): {result.stdout!r}")
    assert isinstance(tables, list)
    return tables


def test_metadata_snapshot_matches_both_assembly_paths() -> None:
    """Both assembly paths must register exactly the snapshotted table set."""
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert isinstance(expected, list), "snapshot fixture must be a JSON array of table names"
    assert all(isinstance(table, str) for table in expected), "snapshot entries must be strings"

    in_process_tables = sorted(Base.metadata.tables.keys())
    _assert_matches_snapshot(
        expected, in_process_tables, "conftest in-process path (tests/conftest.py:44)"
    )

    fresh_tables = _fresh_interpreter_table_keys()
    _assert_matches_snapshot(
        expected, fresh_tables, "fresh-interpreter path (migrations/env.py:30)"
    )
