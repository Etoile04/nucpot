"""Neutral loader shared by the pytest gate and the markdown report CLI.

NFM-3531-A. Kept pytest-free so ``apps/api/scripts/ontology_coverage_report.py``
can import the same loader without dragging ``pytest`` into a runtime CLI
dependency graph.

The loader is byte-identical to the one ``extraction_prompt.
_build_ontology_standard_names_block`` consumes — see the test docstring for
the exact contract.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

# Resolve the canonical fixture relative to the repo root, not CWD, so the
# helper works under any pytest invocation (apps/api, repo root, IDE) and any
# CLI invocation (from repo root or from apps/api).
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_FIXTURE = (
    _REPO_ROOT / "apps" / "api" / "tests" / "fixtures" / "canonical_ontology.json"
)
_FIXTURE_ENV = "NFM_ONTOLOGY_COVERAGE_FIXTURE"


def canonical_fixture_path() -> Path:
    """Return the active canonical-ontology fixture path (env override wins)."""
    override = os.environ.get(_FIXTURE_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_FIXTURE


def load_ontology_payload(path: Path | None = None) -> dict:
    """Load the canonical ontology payload from disk.

    Raises ``FileNotFoundError`` if the fixture is missing — callers decide
    whether to skip, fail, or report.
    """
    fixture = path if path is not None else canonical_fixture_path()
    return json.loads(fixture.read_text(encoding="utf-8"))


def collect_ontology_property_names(ontology_data: dict) -> set[str]:
    """Replicate extraction_prompt._build_ontology_standard_names_block input.

    Mirrors the helper at
    ``apps/api/src/nfm_db/services/extraction_prompt.py`` so the coverage
    check tracks the same union of names that the extraction prompt sees:

        property_categories[].standard_properties
        union entity_types[].required_properties
    """
    names: set[str] = set()

    for pc in ontology_data.get("property_categories", []) or []:
        if not isinstance(pc, dict):
            continue
        for prop in pc.get("standard_properties", []) or []:
            if isinstance(prop, str) and prop:
                names.add(prop)

    for et in ontology_data.get("entity_types", []) or []:
        if not isinstance(et, dict):
            continue
        for prop in et.get("required_properties", []) or []:
            if isinstance(prop, str) and prop:
                names.add(prop)

    return names


def coverage_metrics(
    standard_keys: Iterable[str],
    ontology_names: Iterable[str],
) -> dict[str, object]:
    """Compute total / covered / missing counts and sorted missing list."""
    keys = sorted({k for k in standard_keys if isinstance(k, str) and k})
    ontology = {n for n in ontology_names if isinstance(n, str) and n}
    missing = [k for k in keys if k not in ontology]
    covered = [k for k in keys if k in ontology]
    total = len(keys)
    return {
        "total": total,
        "covered_count": len(covered),
        "missing_count": len(missing),
        "missing": missing,
        "covered": covered,
        "pct": (100.0 * len(covered) / total) if total else 0.0,
    }
