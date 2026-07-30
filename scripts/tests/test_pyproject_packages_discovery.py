"""Tests for NFM-2239: confirm pip install of apps/api actually installs nfm_db.

The Docker images for the API (``docker/staging-api.Dockerfile``,
``docker/prod-api.Dockerfile``) ``pip install`` from
``apps/api/pyproject.toml``. Without explicit ``[tool.setuptools.packages]``
configuration, ``pip install .`` may produce only an empty metadata shell
(``nfm_db_api-*.dist-info``) and not the actual ``nfm_db/`` package
directory, causing every container to crash at startup with
``ModuleNotFoundError: No module named 'nfm_db'``.

These tests reproduce the install in an isolated venv and assert that
``nfm_db`` is importable and on-disk after ``pip install .``.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
import venv
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APPS_API_PYPROJECT = REPO_ROOT / "apps" / "api" / "pyproject.toml"
APPS_API_SRC = REPO_ROOT / "apps" / "api" / "src"


@pytest.fixture(scope="module")
def apps_api_pyproject_toml() -> dict:
    """Parsed apps/api/pyproject.toml — used to inspect package config."""
    with APPS_API_PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def test_apps_api_pyproject_declares_setuptools_packages(
    apps_api_pyproject_toml: dict,
) -> None:
    """pyproject.toml must explicitly discover the ``nfm_db`` package.

    Without this directive, ``pip install .`` from a slim environment can
    produce only an empty metadata shell, and ``import nfm_db`` fails.
    """
    setuptools_cfg = apps_api_pyproject_toml.get("tool", {}).get("setuptools", {})
    packages_value = setuptools_cfg.get("packages")

    has_explicit_list = isinstance(packages_value, list) and len(packages_value) > 0
    has_find_table = isinstance(packages_value, dict) and bool(packages_value)
    assert has_explicit_list or has_find_table, (
        "apps/api/pyproject.toml is missing [tool.setuptools.packages] or "
        "[tool.setuptools.packages.find] — pip install only ships the metadata shell."
    )


def _has_python() -> bool:
    return any(shutil.which(name) for name in ("python", "python3", "python3.12"))


@pytest.mark.skipif(
    not _has_python(),
    reason="python interpreter not on PATH (needed for venv creation)",
)
def test_pip_install_ships_importable_nfm_db(tmp_path: Path) -> None:
    """Reproduce the Dockerfile step: ``pip install .`` then ``import nfm_db``.

    This is the exact failure mode the issue describes: every container
    crashes at startup with ``ModuleNotFoundError: No module named 'nfm_db'``.
    The test simulates the Docker build by copying the same files into a
    temp dir, creating an isolated venv, and confirming that ``import
    nfm_db`` resolves to a real ``__init__.py`` in the installed package.
    """
    stage = tmp_path / "stage"
    stage.mkdir()
    shutil.copy(APPS_API_PYPROJECT, stage / "pyproject.toml")
    shutil.copytree(APPS_API_SRC, stage / "src")

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, symlinks=False).create(venv_dir)
    venv_python = venv_dir / "bin" / "python"
    venv_pip = venv_dir / "bin" / "pip"

    subprocess.run(
        [
            str(venv_pip),
            "install",
            "--quiet",
            "--disable-pip-version-check",
            "setuptools>=75.0",
            "wheel",
            "build",
        ],
        check=True,
        capture_output=True,
    )
    res = subprocess.run(
        [
            str(venv_pip),
            "install",
            "--quiet",
            "--disable-pip-version-check",
            "--no-build-isolation",
            ".",
        ],
        cwd=stage,
        check=False,
        capture_output=True,
    )
    if res.returncode != 0:
        pytest.fail(
            f"`pip install .` failed in clean venv. "
            f"stderr=\n{res.stderr.decode('utf-8', 'replace')[-2000:]}"
        )

    import_proc = subprocess.run(
        [str(venv_python), "-c", "import nfm_db; print(nfm_db.__file__)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert import_proc.returncode == 0, (
        f"`import nfm_db` raised after install — package not shipped. stderr={import_proc.stderr}"
    )
    resolved_path = Path(import_proc.stdout.strip())
    assert resolved_path.name == "__init__.py", import_proc.stdout
    assert resolved_path.parent.name == "nfm_db", (
        f"Expected nfm_db/__init__.py, got {resolved_path}"
    )
