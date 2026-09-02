"""Tests for NFM-4106 ``check_prod_migration.py`` pre-flight guard.

The guard is the structural counterpart of the social embargo on
applying destructive migrations to prod. It runs BEFORE
``alembic upgrade head`` inside the ephemeral prod-api container and
refuses unless the caller opts in with
``NFMD_PROD_MIGRATION_PERMITTED=1``. These tests cover the five
exit-code branches documented in the script's module docstring:

  0  permission granted AND image is at or ahead of the DB
  1  image is older than the DB (NFM-4063 analog)
  2  configuration / IO error
  3  permission denied (the new branch NFM-4106 adds)

The tests invoke the script as a subprocess rather than importing it,
mirroring the staging-guard test pattern in
``test_check_staging_revision.py``. Same reasoning: (a) the script's
import resolution is fragile under pytest's import-mode machinery,
(b) subprocess gives us exact coverage of the script's CLI surface,
which is what the deploy workflow actually invokes, and (c) we can
mock DB behaviour by replacing ``asyncpg`` on PATH for one branch
without monkey-patching the world.
"""


from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "apps" / "api" / "scripts" / "check_prod_migration.py"


# ---------------------------------------------------------------------------
# Helpers: build a real alembic script tree on disk
# ---------------------------------------------------------------------------


def _write_rev(versions_dir: Path, rev_id: str, down_revision: str | None) -> None:
    """Write a single alembic revision file alembic can parse."""
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


def _make_asyncpg_stub(bin_dir: Path, db_row: dict[str, str] | None) -> Path:
    """Build a stub ``asyncpg`` package on PATH that returns a fixed row.

    Mirrors the staging-guard fixture but is duplicated here so the two
    test files are independent — a failure in one cannot poison the
    other.
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
    permitted: str | None = None,
    audit_log_path: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the guard with a mocked asyncpg and report stdout/stderr."""
    bin_dir = Path("/tmp/check_prod_migration_test_bin")
    if bin_dir.exists():
        shutil.rmtree(bin_dir)
    bin_dir.mkdir(parents=True)
    if migrations_dir is None:
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
    if permitted is not None:
        env["NFMD_PROD_MIGRATION_PERMITTED"] = permitted
    if audit_log_path is not None:
        env["NFMD_PROD_MIGRATION_AUDIT_LOG"] = str(audit_log_path)
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )


def _read_audit_rows(path: Path) -> list[dict[str, object]]:
    """Return one dict per JSONL row in the audit log."""
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# Permission gate (the new NFM-4106 branch)
# ---------------------------------------------------------------------------


class TestPermissionGate:
    """Exit code 3 — the new branch NFM-4106 adds on top of NFM-4066.

    A QA / preview container pointed at ``nucpot-prod-db`` that runs
    ``alembic upgrade head`` without the explicit permission flag must
    be refused BEFORE the alembic scripts dir is read or the DB is
    touched. The flag is a literal string match on ``"1"`` — not
    truthy — so that ``"true"`` / ``"yes"`` / empty / unset all refuse.
    """

    def test_refuses_when_flag_unset(
        self, migrations_dir: Path, tmp_path: Path
    ) -> None:
        audit = tmp_path / "audit.log"
        result = _run_guard(
            db_row={"version_num": "aaa_initial"},
            migrations_dir=migrations_dir,
            permitted=None,
            audit_log_path=audit,
        )
        assert result.returncode == 3, (
            f"expected exit 3 (permission denied); got {result.returncode}; "
            f"stderr={result.stderr!r}"
        )
        assert "PERMISSION DENIED" in result.stderr
        assert "NFMD_PROD_MIGRATION_PERMITTED" in result.stderr
        assert "NFM-4106" in result.stderr

        rows = _read_audit_rows(audit)
        assert len(rows) == 1
        row = rows[0]
        assert row["outcome"] == "permission_denied"
        assert row["permission_granted"] is False
        assert "unset" in row["refusal_reason"]

    def test_refuses_when_flag_is_empty_string(
        self, migrations_dir: Path, tmp_path: Path
    ) -> None:
        audit = tmp_path / "audit.log"
        result = _run_guard(
            db_row={"version_num": "aaa_initial"},
            migrations_dir=migrations_dir,
            permitted="",
            audit_log_path=audit,
        )
        assert result.returncode == 3, result.stderr
        rows = _read_audit_rows(audit)
        assert rows[0]["outcome"] == "permission_denied"

    def test_refuses_when_flag_is_truthy_but_not_literal_one(
        self, migrations_dir: Path
    ) -> None:
        """``true`` / ``yes`` / ``1.0`` / ``on`` must NOT be accepted.

        A defensive contract — alembic env vars and bash booleans are
        notoriously loose. Literal ``"1"`` is the only accepted value
        so a typo or copy-paste from another tool cannot accidentally
        grant permission.
        """
        for bad_value in ("true", "True", "TRUE", "yes", "on", "1.0", "enabled"):
            result = _run_guard(
                db_row={"version_num": "aaa_initial"},
                migrations_dir=migrations_dir,
                permitted=bad_value,
            )
            assert result.returncode == 3, (
                f"value {bad_value!r} should refuse; got exit "
                f"{result.returncode}; stderr={result.stderr!r}"
            )
            assert "PERMISSION DENIED" in result.stderr


# ---------------------------------------------------------------------------
# Happy path: permission granted, image at or ahead of DB
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_ok_when_permission_granted_and_db_revision_in_image(
        self, migrations_dir: Path, tmp_path: Path
    ) -> None:
        audit = tmp_path / "audit.log"
        result = _run_guard(
            db_row={"version_num": "bbb_add_widgets"},
            migrations_dir=migrations_dir,
            permitted="1",
            audit_log_path=audit,
        )
        assert result.returncode == 0, (
            f"expected exit 0; got {result.returncode}; stderr={result.stderr!r}"
        )
        assert "bbb_add_widgets" in result.stderr

        rows = _read_audit_rows(audit)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "ok"
        assert rows[0]["permission_granted"] is True
        assert rows[0]["db_revision"] == "bbb_add_widgets"

    def test_ok_when_db_revision_is_at_image_head(
        self, migrations_dir: Path
    ) -> None:
        result = _run_guard(
            db_row={"version_num": "ccc_seed_things"},
            migrations_dir=migrations_dir,
            permitted="1",
        )
        assert result.returncode == 0, result.stderr

    def test_ok_when_db_revision_is_below_image_head(
        self, migrations_dir: Path
    ) -> None:
        result = _run_guard(
            db_row={"version_num": "aaa_initial"},
            migrations_dir=migrations_dir,
            permitted="1",
        )
        assert result.returncode == 0, result.stderr

    def test_ok_when_db_has_no_alembic_version_row(
        self, migrations_dir: Path, tmp_path: Path
    ) -> None:
        audit = tmp_path / "audit.log"
        result = _run_guard(
            db_row=None,
            migrations_dir=migrations_dir,
            permitted="1",
            audit_log_path=audit,
        )
        assert result.returncode == 0, result.stderr
        assert "fresh DB" in result.stderr

        rows = _read_audit_rows(audit)
        assert rows[0]["outcome"] == "ok_fresh_db"
        assert rows[0]["db_revision"] is None


# ---------------------------------------------------------------------------
# Image older than DB (NFM-4063 analog — exit code 1)
# ---------------------------------------------------------------------------


class TestImageOlderThanDb:
    """Even WITH permission, the script refuses if the image is older
    than the DB. This is the NFM-4063 reproduction path: a stale image
    would crash alembic with ``Can't locate revision identified by X``.
    The NFM-4106 guard surfaces a self-diagnosing verdict instead.
    """

    def test_refuses_when_db_revision_unknown_to_image(
        self, migrations_dir: Path, tmp_path: Path
    ) -> None:
        audit = tmp_path / "audit.log"
        result = _run_guard(
            db_row={"version_num": "ddd_post_image_revision"},
            migrations_dir=migrations_dir,
            permitted="1",
            audit_log_path=audit,
        )
        assert result.returncode == 1, (
            f"expected exit 1; got {result.returncode}; stderr={result.stderr!r}"
        )
        assert "ddd_post_image_revision" in result.stderr
        assert "REFUSING TO START" in result.stderr
        assert "IMAGE IS OLDER THAN DB" in result.stderr
        assert "Rebuild and redeploy" in result.stderr

        rows = _read_audit_rows(audit)
        assert rows[0]["outcome"] == "image_older_than_db"
        assert rows[0]["permission_granted"] is True
        assert "ddd_post_image_revision" in rows[0]["refusal_reason"]


# ---------------------------------------------------------------------------
# Configuration / IO errors (exit code 2)
# ---------------------------------------------------------------------------


class TestConfigurationErrors:
    def test_configuration_error_when_database_url_missing(
        self, migrations_dir: Path
    ) -> None:
        result = _run_guard(
            db_row={"version_num": "aaa_initial"},
            migrations_dir=migrations_dir,
            permitted="1",
            database_url="",
        )
        assert result.returncode == 2, result.stderr
        assert "NFM_DATABASE_URL" in result.stderr

    def test_configuration_error_when_migrations_dir_missing(
        self, tmp_path: Path
    ) -> None:
        audit = tmp_path / "audit.log"
        result = _run_guard(
            db_row={"version_num": "aaa_initial"},
            migrations_dir=None,
            permitted="1",
            audit_log_path=audit,
        )
        assert result.returncode == 2, result.stderr
        assert "Migrations directory not found" in result.stderr

        rows = _read_audit_rows(audit)
        assert rows[0]["outcome"] == "config_error"

    def test_db_connectivity_error_does_not_swallow_exception(
        self, tmp_path: Path
    ) -> None:
        """A DB connectivity error must surface a clear verdict, exit 2."""
        bin_dir = Path("/tmp/check_prod_migration_test_bin2")
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
        audit = tmp_path / "audit.log"

        env = {
            **os.environ,
            "PYTHONPATH": str(asyncpg_pkg),
            "ALEMBIC_MIGRATIONS_DIR": str(migrations_dir),
            "NFM_DATABASE_URL": "postgresql://test/test",
            "NFMD_PROD_MIGRATION_PERMITTED": "1",
            "NFMD_PROD_MIGRATION_AUDIT_LOG": str(audit),
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

        rows = _read_audit_rows(audit)
        assert rows[0]["outcome"] == "db_connectivity_error"

    def test_subprocess_returns_2_with_clear_stderr(
        self, tmp_path: Path
    ) -> None:
        """Even with no environment setup, the script reports a clear error."""
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
# Audit log row schema
# ---------------------------------------------------------------------------


class TestAuditLog:
    """Every invocation must leave a JSONL row the on-call runbook can grep."""

    def test_audit_row_records_operator_and_image_tag(
        self, migrations_dir: Path, tmp_path: Path
    ) -> None:
        audit = tmp_path / "audit.log"
        result = _run_guard(
            db_row={"version_num": "bbb_add_widgets"},
            migrations_dir=migrations_dir,
            permitted="1",
            audit_log_path=audit,
            extra_env={
                "NFMD_OPERATOR": "ci-12345",
                "PROD_IMAGE_TAG": "abc1234",
            },
        )
        assert result.returncode == 0, result.stderr
        rows = _read_audit_rows(audit)
        assert rows[0]["operator"] == "ci-12345"
        assert rows[0]["image_tag"] == "abc1234"
        assert rows[0]["issue"] == "NFM-4106"
        assert rows[0]["script"] == "check_prod_migration.py"

    def test_audit_row_appends_for_repeated_invocations(
        self, migrations_dir: Path, tmp_path: Path
    ) -> None:
        audit = tmp_path / "audit.log"
        for db_rev in ("aaa_initial", "bbb_add_widgets"):
            result = _run_guard(
                db_row={"version_num": db_rev},
                migrations_dir=migrations_dir,
                permitted="1",
                audit_log_path=audit,
            )
            assert result.returncode == 0, result.stderr
        rows = _read_audit_rows(audit)
        assert len(rows) == 2
        assert [r["db_revision"] for r in rows] == [
            "aaa_initial",
            "bbb_add_widgets",
        ]


# ---------------------------------------------------------------------------
# DSN normalisation (NFM-4077 analog — SQLAlchemy ``+asyncpg`` DSN)
# ---------------------------------------------------------------------------


class TestDsnNormalization:
    """NFM-4077 fixed ``postgresql+asyncpg://`` for the staging guard;
    the prod guard must do the same so SQLAlchemy DSNs work."""

    def test_dsn_with_asyncpg_driver_is_accepted(self, migrations_dir: Path) -> None:
        result = _run_guard(
            db_row={"version_num": "ccc_seed_things"},
            migrations_dir=migrations_dir,
            permitted="1",
            database_url="postgresql+asyncpg://nfm:secret@db:5432/nfm_db",
        )
        assert result.returncode == 0, (
            f"expected exit 0 against +asyncpg DSN; got {result.returncode}; "
            f"stderr={result.stderr!r}"
        )
        assert "invalid DSN" not in result.stderr

    def test_dsn_with_psycopg2_driver_is_accepted(self, migrations_dir: Path) -> None:
        result = _run_guard(
            db_row={"version_num": "ccc_seed_things"},
            migrations_dir=migrations_dir,
            permitted="1",
            database_url="postgresql+psycopg2://nfm:secret@db:5432/nfm_db",
        )
        assert result.returncode == 0, result.stderr
        assert "invalid DSN" not in result.stderr


# ---------------------------------------------------------------------------
# Loader smoke test: real alembic integrations against the repo's own tree
# ---------------------------------------------------------------------------


def test_real_repo_migrations_load_cleanly(tmp_path: Path) -> None:
    """Sanity: the loader integrates with alembic's actual ScriptDirectory.

    Mirrors the staging-guard smoke test — proves the script can walk
    the real repo migration graph when granted permission, without
    needing a live DB.
    """
    real_migrations = REPO_ROOT / "apps" / "api" / "migrations"
    if not real_migrations.exists():
        pytest.skip("Repo migrations dir not present in this checkout")

    # Parse ``revision = "..."`` out of every migration file so the
    # stub row matches a real alembic revision id. The filename stem
    # and the revision id are NOT the same — ``070_foo.py`` contains
    # ``revision = '070_foo'``.
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

    # Pick the highest-sorted revision as a stand-in for "image head"
    # — this is the path a fresh ``alembic upgrade head`` would
    # actually take (idempotent on a healthy stack) and proves the
    # script can walk the full graph.
    db_row = {"version_num": sorted(rev_ids)[-1]}

    result = _run_guard(
        db_row=db_row,
        migrations_dir=real_migrations,
        permitted="1",
    )
    assert result.returncode == 0, (
        f"expected exit 0 against real migrations; got {result.returncode}; "
        f"stderr={result.stderr!r}"
    )
    assert "head(s)" in result.stderr
    assert sorted(rev_ids)[-1] in result.stderr


# ---------------------------------------------------------------------------
# prod_migrate.sh wiring check
# ---------------------------------------------------------------------------


def test_prod_migrate_sh_passes_the_permission_flag() -> None:
    """The deploy-time script must explicitly opt in to the guard.

    Regression check: a future refactor of ``prod_migrate.sh`` that
    drops ``-e NFMD_PROD_MIGRATION_PERMITTED=1`` would silently
    re-open the structural hole NFM-4106 closes. Lock the contract.
    """
    script_path = REPO_ROOT / "scripts" / "prod_migrate.sh"
    assert script_path.exists(), "scripts/prod_migrate.sh missing"
    body = script_path.read_text()
    assert "NFMD_PROD_MIGRATION_PERMITTED=1" in body, (
        "prod_migrate.sh must pass -e NFMD_PROD_MIGRATION_PERMITTED=1 to "
        "the ephemeral prod-api container — without it, the NFM-4106 guard "
        "refuses every invocation and deploys silently break."
    )
    assert "check_prod_migration.py" in body, (
        "prod_migrate.sh must invoke check_prod_migration.py before "
        "alembic upgrade head — that's the load-bearing step."
    )


def test_prod_api_dockerfile_bakes_the_guard() -> None:
    """The prod image must ship the guard script so the deploy workflow
    can invoke it from any ephemeral container built off this image."""
    dockerfile = REPO_ROOT / "docker" / "prod-api.Dockerfile"
    assert dockerfile.exists(), "docker/prod-api.Dockerfile missing"
    body = dockerfile.read_text()
    assert "check_prod_migration.py" in body, (
        "prod-api.Dockerfile must COPY check_prod_migration.py into "
        "/usr/local/bin/ so the NFM-4106 deploy path can invoke it."
    )
