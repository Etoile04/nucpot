"""Tests for NFM-4066 ``check_staging_revision.py`` pre-flight guard.

The guard is the highest-value piece of NFM-4066: it turns the bare
``Can't locate revision identified by X`` alembic crash into a self-
diagnosing "image is older than DB" message. These tests cover the four
exit-code branches documented in the script's module docstring.

The tests invoke the script as a subprocess rather than importing it,
because (a) the script's import resolution is fragile when run under
pytest's import-mode machinery, (b) subprocess gives us exact coverage
of the script's CLI surface (which is what the staging-api container
actually invokes), and (c) we can mock DB behaviour by replacing
``asyncpg`` on PATH for one branch without monkey-patching the world.
"""

# ruff: noqa: SIM105, SIM108, PLR2006

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "apps" / "api" / "scripts" / "check_staging_revision.py"


# ---------------------------------------------------------------------------
# Helpers: build a real alembic script tree on disk
# ---------------------------------------------------------------------------


def _write_rev(versions_dir: Path, rev_id: str, down_revision: str | None) -> None:
    """Write a single alembic revision file alembic can parse.

    alembic's ``ScriptDirectory`` only needs to import the module and read
    ``revision`` / ``down_revision`` — we don't need real upgrade/downgrade
    functions because this script never executes them.
    """
    body = (
        f"revision = {rev_id!r}\n"
        f"down_revision = {down_revision!r}\n"
    )
    (versions_dir / f"{rev_id}.py").write_text(body)


def _write_script_dir(migrations_dir: Path, head_chain: list[str]) -> None:
    """Create a linear chain of revisions under ``migrations_dir/versions``."""
    versions = migrations_dir / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    prev: str | None = None
    for rev in head_chain:
        _write_rev(versions, rev, prev)
        prev = rev


@pytest.fixture
def migrations_dir(tmp_path: Path) -> Path:
    """A real alembic script tree with three linear revisions."""
    md = tmp_path / "migrations"
    _write_script_dir(md, ["aaa_initial", "bbb_add_widgets", "ccc_seed_things"])
    return md


# ---------------------------------------------------------------------------
# Helpers: invoke the script with a fake asyncpg on PATH
# ---------------------------------------------------------------------------


def _make_stub(bin_dir: Path, name: str, body: str) -> Path:
    p = bin_dir / name
    p.write_text("#!/usr/bin/env python3\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _make_asyncpg_stub(bin_dir: Path, db_row: dict[str, str] | None) -> Path:
    """Build a stub ``asyncpg`` package on PATH that returns a fixed row.

    The stub is a stand-alone module named ``asyncpg`` placed in a
    dedicated directory that we prepend to PYTHONPATH. It defines a
    minimal ``connect()`` that returns an awaitable whose ``fetchrow``
    yields the configured row (or None).
    """
    pkg_dir = bin_dir / "asyncpg_pkg"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "asyncpg.py").write_text(
        "class _Conn:\n"
        "    def __init__(self):\n"
        f"        self._row = {db_row!r}\n"
        "    async def fetchrow(self, _q):\n"
        "        return self._row\n"
        "    async def close(self):\n"
        "        return None\n"
        "async def connect(*_a, **_kw):\n"
        "    return _Conn()\n"
    )
    return pkg_dir


def _run_guard(
    *,
    db_row: dict[str, str] | None,
    migrations_dir: Path | None,
    database_url: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the guard with a mocked asyncpg and report stdout/stderr."""
    bin_dir = Path("/tmp/check_staging_revision_test_bin")
    if bin_dir.exists():
        shutil.rmtree(bin_dir)
    bin_dir.mkdir(parents=True)
    # Stub the alembic migrations dir on disk by writing migrations at the
    # path the script reads (ALEMBIC_MIGRATIONS_DIR).
    if migrations_dir is None:
        # Use a path that does not exist to exercise the CONFIG ERROR branch.
        migrations_env = "/nonexistent/migrations"
    else:
        migrations_env = str(migrations_dir)
    asyncpg_pkg = _make_asyncpg_stub(bin_dir, db_row)

    env = {
        **os.environ,
        "PYTHONPATH": str(asyncpg_pkg) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "ALEMBIC_MIGRATIONS_DIR": migrations_env,
    }
    if database_url is None:
        env["NFM_DATABASE_URL"] = "postgresql://test/test"
    else:
        env["NFM_DATABASE_URL"] = database_url
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Exit-code / branch coverage
# ---------------------------------------------------------------------------


class TestExitCodes:
    def test_ok_when_db_revision_is_in_image(self, migrations_dir: Path) -> None:
        result = _run_guard(
            db_row={"version_num": "bbb_add_widgets"},
            migrations_dir=migrations_dir,
        )
        assert result.returncode == 0, (
            f"expected exit 0; got {result.returncode}; stderr={result.stderr!r}"
        )
        assert "bbb_add_widgets" in result.stderr

    def test_ok_when_db_revision_is_at_image_head(self, migrations_dir: Path) -> None:
        result = _run_guard(
            db_row={"version_num": "ccc_seed_things"},
            migrations_dir=migrations_dir,
        )
        assert result.returncode == 0, result.stderr

    def test_ok_when_db_revision_is_below_image_head(self, migrations_dir: Path) -> None:
        result = _run_guard(
            db_row={"version_num": "aaa_initial"},
            migrations_dir=migrations_dir,
        )
        assert result.returncode == 0, result.stderr

    def test_refuses_when_db_revision_is_unknown_to_image(
        self,
        migrations_dir: Path,
    ) -> None:
        """NFM-4063 reproduction: DB is at a revision the image doesn't know."""
        result = _run_guard(
            db_row={"version_num": "ddd_post_image_revision"},
            migrations_dir=migrations_dir,
        )
        assert result.returncode == 1, (
            f"expected exit 1; got {result.returncode}; stderr={result.stderr!r}"
        )
        # The verdict line MUST mention the offending revision and the image's
        # head so the on-call operator can correlate against ``origin/main``
        # without rerunning the deploy.
        assert "ddd_post_image_revision" in result.stderr
        assert "REFUSING TO START" in result.stderr
        assert "IMAGE IS OLDER THAN DB" in result.stderr
        assert "Rebuild and redeploy" in result.stderr

    def test_ok_when_db_has_no_alembic_version_row(self, migrations_dir: Path) -> None:
        """Fresh DB: alembic will stamp base. Not an error."""
        result = _run_guard(
            db_row=None,
            migrations_dir=migrations_dir,
        )
        assert result.returncode == 0, result.stderr
        assert "fresh DB" in result.stderr

    def test_configuration_error_when_database_url_missing(
        self,
        migrations_dir: Path,
    ) -> None:
        """Container misconfiguration: refuse loudly, not silently."""
        result = _run_guard(
            db_row={"version_num": "aaa_initial"},
            migrations_dir=migrations_dir,
            database_url="",
        )
        assert result.returncode == 2, result.stderr
        assert "NFM_DATABASE_URL" in result.stderr

    def test_configuration_error_when_migrations_dir_missing(self) -> None:
        """Image build did not COPY migrations into the container."""
        result = _run_guard(
            db_row={"version_num": "aaa_initial"},
            migrations_dir=None,
        )
        assert result.returncode == 2, result.stderr
        assert "Migrations directory not found" in result.stderr

    def test_db_connectivity_error_does_not_swallow_exception(self) -> None:
        """A DB connectivity error must surface a clear verdict, exit 2.

        We force the connect() stub to raise so we exercise the exception
        branch in ``_run``.
        """
        bin_dir = Path("/tmp/check_staging_revision_test_bin2")
        if bin_dir.exists():
            shutil.rmtree(bin_dir)
        bin_dir.mkdir(parents=True)
        asyncpg_pkg = bin_dir / "asyncpg_pkg"
        asyncpg_pkg.mkdir()
        (asyncpg_pkg / "asyncpg.py").write_text(
            "class _Err(Exception): pass\n"
            "def connect(*_a, **_kw):\n"
            "    raise _Err('db went away')\n"
        )
        migrations_dir = bin_dir / "migrations"
        _write_script_dir(migrations_dir, ["aaa_initial"])

        env = {
            **os.environ,
            "PYTHONPATH": str(asyncpg_pkg),
            "ALEMBIC_MIGRATIONS_DIR": str(migrations_dir),
            "NFM_DATABASE_URL": "postgresql://test/test",
        }
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2, result.stderr
        assert "DB CONNECTIVITY ERROR" in result.stderr
        assert "db went away" in result.stderr

    def test_subprocess_returns_2_with_clear_stderr(self, tmp_path: Path) -> None:
        """Even with no environment setup, the script reports a clear error.

        Regression check: when a deploy hook invokes the script directly
        without setting NFM_DATABASE_URL, we want exit code 2 (not a Python
        traceback) so the hook's own error path can surface the diagnosis.
        """
        env = {**os.environ, "PATH": os.environ.get("PATH", "")}
        env.pop("NFM_DATABASE_URL", None)
        env["ALEMBIC_MIGRATIONS_DIR"] = str(tmp_path / "nope")

        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2, (
            f"expected exit 2; got {result.returncode}; stderr={result.stderr!r}"
        )
        assert "Traceback" not in result.stderr
        assert (
            "NFM_DATABASE_URL" in result.stderr
            or "Migrations directory not found" in result.stderr
        )


# ---------------------------------------------------------------------------
# Loader smoke test: real alembic integrations against the repo's own tree
# ---------------------------------------------------------------------------


def test_real_repo_migrations_load_cleanly(tmp_path: Path) -> None:
    """Sanity: the loader integrates with alembic's actual ScriptDirectory.

    We invoke the script with the *real* repo migrations dir and a stub
    asyncpg whose row matches the real head revision. This catches
    ``Config`` / ``ScriptDirectory`` integration regressions (hardest to
    mock accurately) without needing a live DB.
    """
    real_migrations = REPO_ROOT / "apps" / "api" / "migrations"
    if not real_migrations.exists():
        pytest.skip("Repo migrations dir not present in this checkout")

    # Parse ``revision = "..."`` out of every migration file so the stub
    # row matches a real alembic revision id. The filename stem and the
    # revision id are NOT the same — ``064_widen_property_measurements_numeric.py``
    # contains ``revision = '065_widen_property_measurements_numeric'``.
    import re

    rev_ids: list[str] = []
    rev_re = re.compile(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
    for p in real_migrations.glob("versions/*.py"):
        if p.name == "__init__.py" or p.stem == "scratch":
            continue
        m = rev_re.search(p.read_text())
        if m:
            rev_ids.append(m.group(1))
    assert rev_ids, "expected to parse at least one alembic revision id"

    # Simulate the DB at the image's head — this is the path a fresh
    # ``alembic upgrade head`` would actually take (idempotent on a
    # healthy stack) and proves the script can walk the full graph.
    db_row = {"version_num": "068_v050_seed_melting_point_alias"}

    result = _run_guard(
        db_row=db_row,
        migrations_dir=real_migrations,
    )
    assert result.returncode == 0, (
        f"expected exit 0 against real migrations; got {result.returncode}; "
        f"stderr={result.stderr!r}"
    )
    # The script must mention at least one head revision in its stderr
    # (proves it walked the real graph, not just stubbed it).
    assert "head(s)" in result.stderr
    # And must confirm the real head we asked it to validate.
    assert "068_v050_seed_melting_point_alias" in result.stderr
