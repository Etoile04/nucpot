"""Tests for scripts/check_prod_ratelimit_storage.py (NFM-4681).

The script parses docker-compose.prod.yml and refuses to pass if the
``api`` service's ``RATE_LIMIT_STORAGE_URI`` drifts away from the
shared-Redis contract. These tests pin the parser behaviour so a future
refactor cannot silently regress to a per-process ``memory://`` counter
on the 4-worker prod API.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_prod_ratelimit_storage.py"


def _run_with_compose(content: str) -> subprocess.CompletedProcess[str]:
    """Run the script against an inline compose YAML."""
    tmp_compose = REPO_ROOT / "docker-compose.ratelimit_test.yml"
    tmp_compose.write_text(textwrap.dedent(content).lstrip("\n"))
    try:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--compose-file", str(tmp_compose)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        tmp_compose.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "compose",
    [
        pytest.param(
            """
            services:
              api:
                environment:
                  RATE_LIMIT_STORAGE_URI: redis://nucpot-prod-redis:6379/2
            """,
            id="string-env-dict-form",
        ),
        pytest.param(
            """
            services:
              api:
                environment:
                  - RATE_LIMIT_STORAGE_URI=redis://nucpot-prod-redis:6379/2
            """,
            id="string-env-list-form",
        ),
    ],
)
def test_passes_on_redis_db_2(compose: str) -> None:
    """Script exits 0 when storage URI points at Redis DB 2."""
    proc = _run_with_compose(compose)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "OK" in proc.stdout


def test_fails_when_storage_uri_missing() -> None:
    """Script exits 1 when the env var is absent from the api service."""
    proc = _run_with_compose(
        """
        services:
          api:
            environment:
              REDIS_HOST: nucpot-prod-redis
        """
    )
    assert proc.returncode == 1
    assert "RATE_LIMIT_STORAGE_URI" in proc.stderr


def test_fails_on_memory_uri() -> None:
    """Script exits 2 when storage URI falls back to memory://."""
    proc = _run_with_compose(
        """
        services:
          api:
            environment:
              RATE_LIMIT_STORAGE_URI: memory://
        """
    )
    assert proc.returncode == 2
    assert "memory://" in proc.stderr


def test_fails_on_celery_db() -> None:
    """Script rejects Redis DB 0/1 because those host Celery."""
    for db in ("0", "1"):
        proc = _run_with_compose(
            f"""
            services:
              api:
                environment:
                  RATE_LIMIT_STORAGE_URI: redis://nucpot-prod-redis:6379/{db}
            """
        )
        assert proc.returncode == 2, f"DB {db} should be rejected"
        assert f"DB '{db}'" in proc.stderr or "DBs" in proc.stderr


def test_fails_on_malformed_uri() -> None:
    """Script rejects URIs that aren't redis://host:port/<db>."""
    proc = _run_with_compose(
        """
        services:
          api:
            environment:
              RATE_LIMIT_STORAGE_URI: not-a-redis-uri
        """
    )
    assert proc.returncode == 2


def test_fails_on_missing_compose_file(tmp_path: Path) -> None:
    """Script exits 1 when the compose file is missing."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--compose-file", str(tmp_path / "absent.yml")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "not found" in proc.stderr


def test_prod_compose_in_repo_is_well_formed() -> None:
    """The committed docker-compose.prod.yml must pass the check."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "RATE_LIMIT_STORAGE_URI=redis://nucpot-prod-redis:6379/2" in proc.stdout
