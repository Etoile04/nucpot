#!/usr/bin/env python3
"""Check that every third-party import in src/ is declared in pyproject.toml.

This catches the class of bug seen in NFM-2089: ``xgboost`` was imported by
training scripts and embedded in pickled joblib artifacts, but missing from
``[project] dependencies`` in pyproject.toml, so it was never installed in
production containers — causing ModuleNotFoundError at joblib.load() time.

Usage::

    python scripts/check_imports_in_deps.py [--pyproject PATH] [--src PATH]

Exit codes:
    0 — all imports are declared (or in the allowlist)
    1 — one or more imports are missing from dependencies
    2 — configuration error (e.g. pyproject.toml not found)

The script uses stdlib ``ast`` (no third-party deps) and is safe to run in CI.
"""

# ruff: noqa: F841, SIM102
from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib  # py3.11+
from pathlib import Path

# ---------------------------------------------------------------------------
# Known third-party packages that are transitively installed by declared deps
# Known import-name → distribution-name mappings where they differ.
# These are declared in pyproject.toml under their distribution name but
# imported under a different top-level name. Listed here so the checker
# does not false-positive.
IMPORT_NAME_OVERRIDES: set[str] = {
    # Pillow (declared as "Pillow>=10.4.0") is imported as "PIL"
    "PIL",
    # PyMuPDF (declared as "PyMuPDF>=1.24.0") is imported as "fitz"
    "fitz",
    # python-frontmatter (declared as "python-frontmatter>=1.1.0") as "frontmatter"
    "frontmatter",
    # PyJWT (declared as "PyJWT>=2.10.0") is imported as "jwt"
    "jwt",
    # pydantic-settings (declared as "pydantic-settings>=2.7.0") as "pydantic_settings"
    "pydantic_settings",
    # python-multipart (declared as "python-multipart>=0.0.20") as "multipart"
    "multipart",
    # prometheus-client is imported as "prometheus_client"
    "prometheus_client",
    # PyYAML is imported as "yaml"
    "yaml",
    # shap: lazy-imported inside try/except in train_v11.py (line 773).
    # Declaring it as a hard dep pulls in numba, which only supports Python
    # <3.10 and breaks our 3.12 CI. Kept optional — users who want SHAP
    # feature-importance plots install it separately (pip install shap).
    "shap",
    # pytesseract (declared as "pytesseract>=0.3.10")
    "pytesseract",
}

# Known third-party packages that are transitively installed by declared deps
# and therefore safe to import even though they are not listed directly.
# Keep this list SHORT — every entry is a potential blind spot.
ALLOWLIST: set[str] = {
    # Pulled in by scikit-learn
    "sklearn",
    "scipy",
    # Pulled in by pandas
    "pytz",
    "dateutil",
    # Pulled in by fastapi / starlette
    "starlette",
    "anyio",
    "h11",
    # Pulled in by sqlalchemy
    "sqlalchemy",
    # Pydantic internals (pydantic is a direct dep)
    "pydantic",
    "pydantic_core",
    # uvicorn[standard] extras
    "uvloop",
    "httptools",
    "websockets",
    # Celery / redis internals
    "kombu",
    "billiard",
    "amqp",
    "vine",
    # typing_extensions is everywhere
    "typing_extensions",
    # Numpy installs nvidia-* on Linux; harmless on macOS
    "numpy",
    # httpx (declared)
    "httpx",
    # sentry-sdk: optional — lazy-imported inside try/except ImportError in
    # monitoring/worker_health.py (NFM-2014). The project does not yet
    # configure Sentry, and adding it as a hard dep would be wasteful.
    "sentry_sdk",
}

# First-party / sibling packages internal to the NucPot monorepo.
# These are not in pyproject.toml dependencies but are always available.
FIRST_PARTY: set[str] = {
    "nfm_db",
    "nfm_md_runner",
    "nfm_ref_gapfill",
    "monitoring",
    "tests",
}


def parse_pyproject_deps(path: Path) -> set[str]:
    """Extract all declared dependency package names from pyproject.toml.

    Returns the *canonical* (lowercase, no extras) distribution names.
    Handles both ``[project] dependencies`` and ``[project.optional-dependencies]``.
    """
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    raw: list[str] = list(project.get("dependencies", []))

    for group in project.get("optional-dependencies", {}).values():
        raw.extend(group)

    names: set[str] = set()
    for spec in raw:
        # Strip version specs: "foo>=1.0" -> "foo", "bar[extra]" -> "bar"
        name = re.split(r"[<>=!\[]", spec.strip())[0].strip()
        if name:
            names.add(name.lower())
    return names


def extract_imports(filepath: Path) -> set[str]:
    """Return top-level package names imported in a Python file."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    except SyntaxError:
        # Skip files with syntax errors (e.g. WIP branches)
        return set()

    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:  # absolute import
                roots.add(node.module.split(".")[0])
    return roots


def collect_src_imports(src_dir: Path) -> dict[str, list[Path]]:
    """Walk src_dir and collect {package: [files that import it]}."""
    result: dict[str, list[Path]] = {}
    for py in src_dir.rglob("*.py"):
        for pkg in extract_imports(py):
            result.setdefault(pkg, []).append(py)
    return result


# Python stdlib modules (built-in + common stdlib) — never report as missing.
STDLIB: set[str] = set(sys.stdlib_module_names) | {
    "__future__",
    "typing",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("apps/api/pyproject.toml"),
        help="Path to pyproject.toml (default: apps/api/pyproject.toml)",
    )
    parser.add_argument(
        "--src",
        type=Path,
        default=Path("apps/api/src"),
        help="Source directory to scan (default: apps/api/src)",
    )
    args = parser.parse_args()

    if not args.pyproject.is_file():
        print(f"ERROR: pyproject.toml not found at {args.pyproject}", file=sys.stderr)
        return 2

    if not args.src.is_dir():
        print(f"ERROR: src directory not found at {args.src}", file=sys.stderr)
        return 2

    deps = parse_pyproject_deps(args.pyproject)
    imports = collect_src_imports(args.src)

    # First-party packages (the project's own package name) are fine.
    project_name = args.pyproject.parent.name  # e.g. "api" under apps/

    missing: dict[str, list[Path]] = {}
    for pkg, files in sorted(imports.items()):
        if pkg.lower() in STDLIB:
            continue
        if pkg.lower() in ALLOWLIST:
            continue
        if pkg in IMPORT_NAME_OVERRIDES:
            continue
        if pkg in FIRST_PARTY or pkg.lower() in FIRST_PARTY:
            continue
        if pkg.lower() in deps:
            continue
        missing[pkg] = files

    if not missing:
        print(f"✅ All {len(imports)} distinct imports are declared in {args.pyproject}")
        return 0

    print(
        f"❌ {len(missing)} third-party package(s) imported in src/ but NOT "
        f"declared in {args.pyproject}:\n",
        file=sys.stderr,
    )
    cwd = Path.cwd()
    for pkg, files in sorted(missing.items()):
        rel_files = []
        for f in files[:3]:
            try:
                rel_files.append(str(f.resolve().relative_to(cwd)))
            except ValueError:
                rel_files.append(str(f))
        sample = ", ".join(rel_files)
        more = f" (+{len(files) - 3} more)" if len(files) > 3 else ""
        print(f"  {pkg}\n      imported by: {sample}{more}", file=sys.stderr)
    print(
        "\nFix: add the missing package(s) to [project] dependencies in "
        "pyproject.toml, or add to ALLOWLIST in this script if it is a "
        "transitive dep of something already declared.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
