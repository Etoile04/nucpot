#!/usr/bin/env python3
"""NFM-5955 C1 guardrail — aggregate model-import counter.

Counts every consumer-side ``from nfm_db.models[.<module>] import ...``
statement across the six consumer roots named in NFM-5955:

    apps/api/src, apps/api/tests, apps/api/scripts, apps/api/e2e,
    scripts (repo root), e2e (repo root, absent today),
    apps/mcp-server

The sixth root (``apps/mcp-server``) is the review-patch addition: it is an
independent uv workspace outside the apps/api PR CI scope, so its direct
import (``apps/mcp-server/tests/test_tools_integration.py:357``) would break
silently when C1 finally deletes/renames the model paths and no CI job would
go red. The counter makes it visible.

Counting rules (the 口径 shared with tests/test_models_public_surface.py —
this module is imported by that test, so the two cannot drift):

* A "statement" is one matched ``from nfm_db.models... import ...`` clause
  (parenthesized multi-line forms count once).
* Files under the ``nfm_db/models/`` package itself are EXCLUDED: the
  package's own re-export imports (``models/__init__.py`` → atomic modules)
  and intra-package imports of ``Base`` are the direct path under guard,
  not consumers of it.
* Package-bind forms (``import nfm_db.models[.<module>]`` and
  ``from nfm_db import models``) are NOT counted as per-root statements —
  they bind the package rather than names from it. The outside-scope sweep
  reports them in a separate ``bind_forms`` channel, repo-wide including
  files inside the consumer roots (a from-form match in the same file
  would otherwise mask them). One live bind-form import exists today:
  apps/api/src/nfm_db/api/v1/extraction_gaps.py:234 (function-local
  ontology_version bind). The counter itself is excluded from the sweep
  via SWEEP_SELF_EXCLUDE — its documentation quotes the guarded forms,
  and the measuring stick is not a consumer.

Exit code is 0 whenever the scan itself completes — the counter is a
published metric (the Q5 recovery gate reads it), not a pass/fail gate.
Non-zero exit codes mean the scan could not run (repo layout broken).

Usage::

    python scripts/count_model_imports.py            # human table
    python scripts/count_model_imports.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Consumer roots, repo-root-relative. ``e2e`` (repo root) does not exist
#: today; it is kept so the counter's shape matches the NFM-5955 spec and
#: a future root-level e2e tree is counted automatically.
CONSUMER_ROOTS: tuple[str, ...] = (
    "apps/api/src",
    "apps/api/tests",
    "apps/api/scripts",
    "apps/api/e2e",
    "scripts",
    "e2e",
    "apps/mcp-server",
)

#: Known import site of ``nfm_db.models`` that is the alembic assembly
#: path itself, not consumer surface. (``tests/conftest.py`` is NOT listed:
#: it lives inside the ``apps/api/tests`` consumer root and is counted —
#: its Base/BlogRole/User imports break on a package move like any other.)
#: Reported under outside-scope as ``assembly`` instead of ``stray``.
ASSEMBLY_SITES: tuple[str, ...] = ("apps/api/migrations/env.py",)

#: Directory pruning for the outside-scope sweep (noise / non-Python-run trees).
_SWEEP_PRUNE_DIRS = frozenset(
    {
        ".git",
        ".github",
        ".next",
        ".paperclip",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
    }
)

# One ``from nfm_db.models[.module] import <clause>`` statement. The clause is
# either a parenthesized (possibly multi-line) block or everything up to a
# comment / newline, including backslash continuations.
MODELS_IMPORT_RE = re.compile(
    r"from\s+nfm_db\.models(?:\.[A-Za-z_][A-Za-z0-9_]*)?\s+import\s+"
    r"(\((?:[^)]*)\)|[^\n(#]+(?:\\\n[^\n(#]+)*)"
)

_COMMENT_RE = re.compile(r"#.*")
_ALIAS_RE = re.compile(r"\s+as\s+\S+$")

# Package-bind forms: import the models package/submodules directly instead
# of names from it. Line-anchored (re.MULTILINE) so quoted mentions that do
# not start a line (docs, comments) never match. The ``from nfm_db import
# <clause>`` branch only counts when the clause actually binds ``models`` —
# the caller resolves that via parse_import_names.
PACKAGE_BIND_RE = re.compile(
    r"^\s*import\s+(?P<module>nfm_db\.models(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\b"
    r"|^\s*from\s+nfm_db\s+import\s+(?P<clause>\((?:[^)]*)\)|[^\n(#]+)",
    re.MULTILINE,
)

#: The measuring stick itself is never a consumer: the counter's docstring
#: quotes the guarded import forms, so it is excluded from the sweep (and
#: from the public-surface test's bind scan, which imports this constant —
#: the two cannot drift).
SWEEP_SELF_EXCLUDE: frozenset[str] = frozenset({"scripts/count_model_imports.py"})


@dataclass
class RootCount:
    """Per-root tally for one consumer root."""

    root: str
    exists: bool
    statements: int = 0
    names: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)


def parse_import_names(clause: str) -> list[str]:
    """Extract the imported names from one ``from ... import`` clause.

    Handles parenthesized blocks, per-line comments, ``as`` aliases (the
    original name is what counts — it is the symbol that must stay
    importable) and empty tokens from trailing commas.
    """
    text = clause.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    names: list[str] = []
    for token in _COMMENT_RE.sub("", text).split(","):
        token = token.strip()
        if not token:
            continue
        names.append(_ALIAS_RE.sub("", token).strip())
    return [name for name in names if name]


def is_models_package_file(repo_rel_posix: str) -> bool:
    """True when the file lives inside a ``nfm_db/models/`` package tree.

    Such files are the guarded surface itself; their imports are
    intra-package wiring, not consumer usage.
    """
    return "nfm_db/models/" in repo_rel_posix


def iter_py_files(root: Path) -> Iterator[Path]:
    """Yield ``*.py`` files under ``root`` in stable sorted order.

    Prunes environment/cache trees (``.venv``, ``__pycache__``, ...) so a
    locally synced uv workspace inside a consumer root cannot inflate the
    counts with vendored site-packages matches.
    """
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*.py")):
        if not path.is_file():
            continue
        if any(part in _SWEEP_PRUNE_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def scan_root(repo_root: Path, root_rel: str) -> RootCount:
    """Scan one consumer root and tally model-import statements/names."""
    root = repo_root / root_rel
    tally = RootCount(root=root_rel, exists=root.is_dir())
    for path in iter_py_files(root):
        rel = path.relative_to(repo_root).as_posix()
        if is_models_package_file(rel):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in MODELS_IMPORT_RE.finditer(text):
            tally.statements += 1
            # Statement counts stay raw (rg 口径); the distinct-name set is
            # filtered to real identifiers so text matches inside string
            # literals / docstrings (e.g. interpreter snippets) do not
            # pollute the published name surface.
            tally.names.update(
                name for name in parse_import_names(match.group(1)) if name.isidentifier()
            )
            tally.files.add(rel)
    return tally


def _bind_module_path(text: str) -> str | None:
    """First package/submodule-bind module path in ``text``, if any.

    ``import nfm_db.models[.<module>]`` yields the dotted module path;
    ``from nfm_db import <clause>`` yields ``nfm_db.models`` only when the
    clause actually binds ``models``. Both bind the package (or a
    submodule) rather than names from it, so the statement counter —
    which keys on ``from``-import clauses — structurally cannot see them.
    """
    for match in PACKAGE_BIND_RE.finditer(text):
        if match.group("module"):
            return match.group("module")
        clause = match.group("clause") or ""
        if "models" in parse_import_names(clause):
            return "nfm_db.models"
    return None


def sweep_outside_roots(repo_root: Path, counted_files: set[str]) -> dict[str, list[str]]:
    """Repo-wide sweep for model imports outside (or orthogonal to) the roots.

    Returns ``{"stray": [...], "assembly": [...], "bind_forms": [...]}``:

    * ``stray`` — new files importing ``nfm_db.models`` via ``from``-forms
      that no consumer root counted (the exact failure mode that hid
      ``apps/mcp-server`` from the original five-root scan). Triage: either
      promote the file's tree into CONSUMER_ROOTS or add it to
      ASSEMBLY_SITES.
    * ``assembly`` — the known bootstrap imports in ASSEMBLY_SITES.
    * ``bind_forms`` — package/submodule-bind imports (PACKAGE_BIND_RE).
      Unlike ``stray``, this channel scans files inside the consumer roots
      too: a from-form match in the same file would otherwise mask a bind
      form the statement counter can never see (extraction_gaps.py today).
    """
    stray: list[str] = []
    assembly: list[str] = []
    bind_forms: list[str] = []
    for path in sorted(repo_root.rglob("*.py")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root).as_posix()
        if rel in SWEEP_SELF_EXCLUDE or is_models_package_file(rel):
            continue
        # Prune on components RELATIVE to the repo root: pruning on absolute
        # parts would silently neuter the sweep in any checkout located
        # under a directory named like a prune entry (e.g. .paperclip).
        if any(part in _SWEEP_PRUNE_DIRS for part in path.relative_to(repo_root).parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if _bind_module_path(text) is not None:
            bind_forms.append(rel)
        if rel in counted_files:
            continue
        if MODELS_IMPORT_RE.search(text):
            if rel in ASSEMBLY_SITES:
                assembly.append(rel)
            else:
                stray.append(rel)
    return {"stray": stray, "assembly": assembly, "bind_forms": bind_forms}


def collect_counts(repo_root: Path = REPO_ROOT) -> tuple[list[RootCount], set[str]]:
    """Scan every consumer root; return tallies and the union file set."""
    tallies = [scan_root(repo_root, root_rel) for root_rel in CONSUMER_ROOTS]
    counted = set().union(*(tally.files for tally in tallies)) if tallies else set()
    return tallies, counted


def format_report(tallies: list[RootCount], outside: dict[str, list[str]]) -> str:
    """Render the human-readable per-root table."""
    lines: list[str] = []
    lines.append("NFM-5955 C1 model-import counter (direct nfm_db.models imports)")
    lines.append("")
    total = 0
    for tally in tallies:
        if not tally.exists:
            lines.append(f"  {tally.root:<24} MISSING (0)")
            continue
        total += tally.statements
        lines.append(f"  {tally.root:<24} {tally.statements:>4}  ({len(tally.names)} names)")
    lines.append(f"  {'TOTAL':<24} {total:>4}")
    lines.append("")
    lines.append(f"stray (outside six-root scope, triage): {len(outside['stray'])}")
    for rel in outside["stray"]:
        lines.append(f"  - {rel}")
    lines.append(f"assembly (known bootstrap imports): {len(outside['assembly'])}")
    for rel in outside["assembly"]:
        lines.append(f"  - {rel}")
    lines.append(f"bind forms (package/submodule-bind imports): {len(outside['bind_forms'])}")
    for rel in outside["bind_forms"]:
        lines.append(f"  - {rel}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    if not REPO_ROOT.is_dir():
        print(f"repo root not found: {REPO_ROOT}", file=sys.stderr)
        return 2

    tallies, counted = collect_counts(REPO_ROOT)
    outside = sweep_outside_roots(REPO_ROOT, counted)

    if args.json:
        payload = {
            "roots": [
                {
                    "root": tally.root,
                    "exists": tally.exists,
                    "statements": tally.statements,
                    "distinct_names": len(tally.names),
                    "names": sorted(tally.names),
                    "files": sorted(tally.files),
                }
                for tally in tallies
            ],
            "outside_scope": outside,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(format_report(tallies, outside))
    return 0


if __name__ == "__main__":
    sys.exit(main())
