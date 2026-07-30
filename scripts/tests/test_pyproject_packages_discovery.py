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


# JSON config files that must be packaged alongside the nfm_db Python module.
# `nfm_db.core.property_catalog._CONFIG_PATH` and the phase-mapping consumer
# both resolve these from `nfm_db/config/*.json` next to the installed
# `nfm_db/__init__.py`. If setuptools only ships `.py` files, every container
# crashes at startup with `FileNotFoundError: .../nfm_db/config/property_mapping.json`.
_PACKAGE_DATA_JSONS: tuple[str, ...] = ("property_mapping.json", "phase_mapping.json")


@pytest.fixture(scope="module")
def installed_apps_api_venv(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Install ``apps/api`` into an isolated venv once per module.

    Returns ``(venv_python, site_packages_dir)``. Multiple tests share this
    fixture so the slow ``pip install .`` runs only once.
    """
    tmp_path = tmp_path_factory.mktemp("nfm_db_install")
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

    # Resolve site-packages for the venv (different paths on POSIX vs Windows).
    site_proc = subprocess.run(
        [str(venv_python), "-c", "import site; print(site.getsitepackages()[0])"],
        capture_output=True,
        text=True,
        check=True,
    )
    site_packages = Path(site_proc.stdout.strip())
    return venv_python, site_packages


@pytest.mark.skipif(
    not _has_python(),
    reason="python interpreter not on PATH (needed for venv creation)",
)
def test_pip_install_ships_importable_nfm_db(installed_apps_api_venv: tuple[Path, Path]) -> None:
    """Reproduce the Dockerfile step: ``pip install .`` then ``import nfm_db``.

    This is the exact failure mode the issue describes: every container
    crashes at startup with ``ModuleNotFoundError: No module named 'nfm_db'``.
    The test simulates the Docker build by copying the same files into a
    temp dir, creating an isolated venv, and confirming that ``import
    nfm_db`` resolves to a real ``__init__.py`` in the installed package.
    """
    venv_python, _site_packages = installed_apps_api_venv
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


@pytest.mark.skipif(
    not _has_python(),
    reason="python interpreter not on PATH (needed for venv creation)",
)
def test_pip_install_ships_nfm_db_package_data_json(
    installed_apps_api_venv: tuple[Path, Path],
) -> None:
    """``property_mapping.json`` / ``phase_mapping.json`` must ship in the wheel.

    ``nfm_db.core.property_catalog._CONFIG_PATH`` (and the phase-mapping
    consumer) load these from ``nfm_db/config/*.json`` next to the
    installed ``__init__.py``. Setuptools only ships Python files by
    default, so without an explicit ``[tool.setuptools.package-data]``
    (or ``include_package_data = True`` + ``MANIFEST.in``), every
    container crashes at startup with::

        FileNotFoundError: .../site-packages/nfm_db/config/property_mapping.json

    This test is the regression for the second half of the NFM-2239
    acceptance criteria: the staging image's installed package was
    shipping the Python module correctly but missing the JSON config.
    The assertion MUST fail before the package-data fix and pass after
    — that is what makes it a true RED→GREEN test for this fix.
    """
    venv_python, site_packages = installed_apps_api_venv
    installed_config_dir = site_packages / "nfm_db" / "config"
    missing = tuple(
        name for name in _PACKAGE_DATA_JSONS if not (installed_config_dir / name).is_file()
    )
    assert not missing, (
        "Installed nfm_db package is missing required JSON package data: "
        f"{missing}. Without [tool.setuptools.package-data] in "
        "apps/api/pyproject.toml, every container crashes at startup with "
        "FileNotFoundError on nfm_db/config/*.json. See NFM-2239."
    )

    # Also confirm the JSONs are well-formed — a 0-byte file would still pass
    # is_file() but fail to load.
    load_proc = subprocess.run(
        [
            str(venv_python),
            "-c",
            (
                "import json, nfm_db.core.property_catalog as pc;"
                "data = json.load(open(pc._CONFIG_PATH));"
                "assert 'property_aliases' in data, data;"
                "print('ok', len(data['property_aliases']), 'aliases')"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert load_proc.returncode == 0, (
        f"`nfm_db.config.property_mapping.json` failed to parse in installed package. "
        f"stderr={load_proc.stderr}"
    )
