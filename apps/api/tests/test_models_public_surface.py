"""NFM-5955 C1 guardrail — dual-side models public surface test.

Side 1 (I4a): every name listed in ``nfm_db.models.__all__`` is importable
from the aggregate package surface.

Side 2 (I4b): every name any consumer directly imports from
``nfm_db.models`` or ``nfm_db.models.<module>`` across the six consumer
roots (apps/api/{src,tests,scripts,e2e}, scripts, e2e, apps/mcp-server)
is importable from its defining atomic module path.

The direct-import name list is GENERATED at test time by scanning consumer
sources with the exact extractor used by scripts/count_model_imports.py
(this test imports that module's primitives, so the two cannot drift). It
is deliberately NOT a hardcoded snapshot: a frozen name list is blind to
newly added direct imports and would reproduce the very hole this guard
exists to close (NFM-5955 review patch — same rationale as rg itself).
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from types import ModuleType

from count_model_imports import (
    CONSUMER_ROOTS,
    MODELS_IMPORT_RE,
    is_models_package_file,
    iter_py_files,
    parse_import_names,
)

_REPO_MARKER = "pnpm-workspace.yaml"


def _repo_root() -> Path:
    """Locate the repository root by its workspace marker file."""
    for parent in Path(__file__).resolve().parents:
        if (parent / _REPO_MARKER).is_file():
            return parent
    raise AssertionError(f"repo root (containing {_REPO_MARKER}) not found above {__file__}")


def _scan_direct_surface(repo_root: Path) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Walk the consumer roots and map each imported name to its files.

    Returns ``(names_to_files, root_statement_counts)`` using the shared
    extractor from scripts/count_model_imports.py. Non-identifier tokens
    (text matches inside string literals / docstrings) are skipped — they
    are not real import surface.
    """
    names_to_files: dict[str, set[str]] = {}
    root_statements: dict[str, int] = {}
    for root_rel in CONSUMER_ROOTS:
        root = repo_root / root_rel
        statements = 0
        for path in iter_py_files(root):
            rel = path.relative_to(repo_root).as_posix()
            if is_models_package_file(rel):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in MODELS_IMPORT_RE.finditer(text):
                statements += 1
                for name in parse_import_names(match.group(1)):
                    if not name.isidentifier():
                        continue
                    names_to_files.setdefault(name, set()).add(rel)
        root_statements[root_rel] = statements
    return names_to_files, root_statements


def _definition_re(name: str) -> re.Pattern[str]:
    """Source-level definition pattern: class/function def or assignment."""
    escaped = re.escape(name)
    return re.compile(
        rf"^(?:class|async def|def)\s+{escaped}\b"  # class or function
        rf"|^{escaped}\s*(?::[^=\n]+)?=",  # assignment / annotated assignment
        re.MULTILINE,
    )


def _atomic_home(
    name: str,
    atomic_sources: dict[str, str],
    atomic_modules: dict[str, ModuleType],
    init_source: str,
    models_pkg: ModuleType,
) -> str | None:
    """Resolve ``name`` to its defining module path, verifying importability.

    Preference order: a submodule stem (``from pkg import submodule`` binds
    the module and works whenever the atomic module exists), an atomic
    module with a source-level definition (and a live attribute), the
    package ``__init__`` itself, then any module that re-exports the name
    at runtime.
    """
    if name in atomic_modules:
        return f"nfm_db.models.{name} (submodule)"
    pattern = _definition_re(name)
    for stem, source in sorted(atomic_sources.items()):
        module = atomic_modules.get(stem)  # orphan file → not package surface
        if module is not None and pattern.search(source) and hasattr(module, name):
            return f"nfm_db.models.{stem}"
    if pattern.search(init_source) and hasattr(models_pkg, name):
        return "nfm_db.models.__init__"
    for stem, module in sorted(atomic_modules.items()):
        if hasattr(module, name):
            return f"nfm_db.models.{stem}"
    return None


def test_models_public_surface_dual_side() -> None:
    """``__all__`` and the scanned direct-import surface must both import."""
    repo_root = _repo_root()
    models_pkg = importlib.import_module("nfm_db.models")

    # -- Side 1 (I4a): every __all__ name resolves on the package surface. --
    all_names = list(getattr(models_pkg, "__all__", []))
    assert all_names, "nfm_db.models.__all__ is empty — package surface is broken"
    missing_from_surface = sorted(name for name in all_names if not hasattr(models_pkg, name))
    assert not missing_from_surface, (
        f"__all__ names not importable from the package surface: {missing_from_surface}"
    )

    # -- Side 2 (I4b): every consumer direct-import name resolves from its --
    # -- atomic module path (scanner shared with the CI import counter).   --
    names_to_files, root_statements = _scan_direct_surface(repo_root)

    # Vacuous-pass guards: the scan must have seen real consumer surface.
    assert names_to_files, "scan found no direct model imports — scanner or layout broken"
    assert root_statements.get("apps/api/src", 0) > 0, (
        "apps/api/src yielded zero matches — scanner is broken"
    )

    package_dir = Path(models_pkg.__file__).resolve().parent
    # Text-scan every models/*.py (orphan files included — harmless on disk),
    # but only RESOLVE against modules the package actually loads (sys.modules
    # after the __init__ import). Blindly importing every *.py would execute
    # orphan modules that re-define live tables on the shared metadata —
    # e.g. the legacy duplicate kg_node.py raises InvalidRequestError — which
    # is itself exactly the defect class this guardrail watches for.
    atomic_sources = {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(package_dir.glob("*.py"))
        if path.stem != "__init__"
    }
    atomic_modules: dict[str, ModuleType] = {
        key.rsplit(".", 1)[-1]: sys.modules[key]
        for key in sorted(sys.modules)
        if key.startswith("nfm_db.models.") and "." not in key.rsplit(".", 1)[-1]
    }
    init_source = (package_dir / "__init__.py").read_text(encoding="utf-8")

    unresolvable: list[str] = []
    for name, files in sorted(names_to_files.items()):
        home = _atomic_home(name, atomic_sources, atomic_modules, init_source, models_pkg)
        if home is None:
            referencing = ", ".join(sorted(files)[:3])
            unresolvable.append(f"{name} (imported by {referencing})")
    assert not unresolvable, (
        "Direct-import names with no importable atomic module path — C1's package move "
        f"must keep these importable: {'; '.join(unresolvable)}"
    )
