"""Loader for the production Owen2023 Material-label snapshot (NFM-4048 AC-2).

Kept pytest-free so ``scripts/nfm-4048-refresh-owen2023-label-snapshot.py``
can import the same loader/serializer without dragging ``pytest`` into a
runtime CLI dependency graph — mirrors the ``ontology_coverage`` helper
convention in this directory.

Why a snapshot at all
---------------------
NFM-4037's audit-pin test only checked its own allowlist for internal
consistency (QA warnings W1/W2), so five labels that production actually
holds were never regression-protected.  CI has no production database, so
the pin cannot query ``kg_nodes`` live.  A checked-in snapshot of the prod
corpus plus a cross-check against the pin table gives the same guarantee:
if the corpus grows and someone refreshes the snapshot, the suite fails
until the pin table is extended.

The snapshot is an *observation* — it carries labels and provenance only.
Label -> ``element_system`` expectations live in ``_OWEN2023_LABELS`` in
``apps/api/tests/test_kg_to_staging_bridge.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Resolve the fixture relative to the repo root, not CWD, so the helper works
# under any pytest invocation (apps/api, repo root, IDE) and any CLI invocation.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_SNAPSHOT = (
    _REPO_ROOT / "apps" / "api" / "tests" / "fixtures" / "owen2023_material_labels.json"
)
_SNAPSHOT_ENV = "NFM_OWEN2023_LABEL_SNAPSHOT"

#: Owen2023 datasource UUID in production ``kg_nodes``.
OWEN2023_SOURCE_ID = "9320cb50-eb65-4178-8d2e-c56aeb848b21"

#: ``element_system`` values the F8 scorecard predicates accept.  Pinned from
#: ``apps/api/src/nfm_db/audit/f8_scorecard_v050.sql`` (commit ``c55bcf572``):
#: ``sr.element_system = 'UO2'`` and ``sr.element_system IN ('UO2+Cr',
#: 'U-Cr-O')``.  A label that canonicalises outside this set is invisible to
#: every F8 check.
#
#: Traceability note (NFM-4051 CR MEDIUM): the cited SQL lives on the
#: unmerged branch ``origin/NFM-4005-amend-scorecard-sql-bridge-union`` as
#: of NFM-4048; the values are correct (verified by CR reading the SQL),
#: but the citation will dangle until NFM-4005 lands.  Once that ships,
#: consider parsing the predicates out of the SQL so this frozenset cannot
#: silently diverge from the scorecard it mirrors.
F8_ACCEPTED_ELEMENT_SYSTEMS: frozenset[str] = frozenset({"UO2", "UO2+Cr", "U-Cr-O"})

#: Prod Owen2023 labels that intentionally canonicalise OUTSIDE the F8 buckets.
#: ``Cr2O3`` is the secondary chromia phase the paper reports alongside the UO2
#: matrix — it is not a UO2 matrix, so coercing it into ``'UO2+Cr'`` would put a
#: row on a bucket that misdescribes its chemistry.  Pass-through is correct;
#: the F8 scorecard simply does not score chromia.
NON_F8_PROD_LABELS: frozenset[str] = frozenset({"Cr2O3"})

_REQUIRED_KEYS = (
    "schema_version",
    "source_id",
    "node_type",
    "captured_at",
    "label_count",
    "labels",
)


def snapshot_path() -> Path:
    """Return the active snapshot path (``NFM_OWEN2023_LABEL_SNAPSHOT`` wins)."""
    override = os.environ.get(_SNAPSHOT_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_SNAPSHOT


def load_snapshot(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the prod-corpus snapshot.

    Raises ``FileNotFoundError`` if the snapshot is missing and ``ValueError``
    if it is structurally invalid — callers decide whether to skip or fail.
    A silently-malformed snapshot would weaken the audit pin to a no-op, which
    is exactly the failure mode NFM-4048 exists to close.
    """
    fixture = path if path is not None else snapshot_path()
    try:
        payload = json.loads(fixture.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{fixture} is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"{fixture}: expected a JSON object, got {type(payload)}")

    missing = [key for key in _REQUIRED_KEYS if key not in payload]
    if missing:
        raise ValueError(f"{fixture}: snapshot missing required keys {missing}")

    labels = payload["labels"]
    if not isinstance(labels, list) or not labels:
        raise ValueError(f"{fixture}: 'labels' must be a non-empty list")
    if any(not isinstance(label, str) or not label.strip() for label in labels):
        raise ValueError(f"{fixture}: every entry in 'labels' must be a non-empty str")
    if len(set(labels)) != len(labels):
        raise ValueError(f"{fixture}: 'labels' contains duplicates")
    if payload["label_count"] != len(labels):
        raise ValueError(
            f"{fixture}: label_count={payload['label_count']} disagrees with "
            f"len(labels)={len(labels)} — refresh the snapshot rather than "
            "hand-editing it"
        )

    return payload


def snapshot_labels(path: Path | None = None) -> tuple[str, ...]:
    """Return the snapshot's prod labels, sorted by codepoint.

    Sorting here (rather than trusting the database's ``ORDER BY``) keeps the
    fixture byte-stable across PostgreSQL collations, so a refresh on a
    differently-collated shard produces no spurious diff.
    """
    return tuple(sorted(load_snapshot(path)["labels"]))


def build_snapshot(
    labels: list[str],
    *,
    captured_at: str,
    captured_from: str,
    source_id: str = OWEN2023_SOURCE_ID,
    newest_node_created_at: str | None = None,
    template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a refreshed snapshot payload, preserving the existing preamble.

    The ``_comment`` documentation field is carried over from ``template``
    so a refresh never silently drops the provenance narrative a reader
    needs to trust the file.  ``source_id``, ``source_name``, and
    ``query`` are rebuilt from the caller's ``source_id`` argument (NFM-4051
    CR LOW fix): the previous version read them from ``template``, so
    ``--source-id <other-uuid>`` would silently write foreign labels under
    the existing snapshot's provenance while the ``provenance is complete``
    guard still passed against the unchanged ``query`` field.

    ``node_type`` is intentionally NOT taken from ``source_id`` — it is a
    fixed project convention (``Material``), and ``build_snapshot`` is not
    the place to broaden it.
    """
    base = dict(template or {})
    unique = sorted(set(labels))
    return {
        **base,
        "schema_version": base.get("schema_version", 1),
        "source_id": source_id,
        "source_name": _infer_source_name(source_id),
        "node_type": base.get("node_type", "Material"),
        "query": (
            f"SELECT DISTINCT label FROM kg_nodes WHERE source_id = "
            f"'{source_id}' AND node_type = 'Material'"
        ),
        "captured_at": captured_at,
        "captured_from": captured_from,
        "newest_node_created_at": newest_node_created_at,
        "label_count": len(unique),
        "labels": unique,
    }


def _infer_source_name(source_id: str) -> str:
    """Return a human-readable source name for the snapshot's ``source_name``.

    Falls back to ``"source-<uuid-prefix>"`` so the field is always populated
    (the fixture loader does not require it, but the test
    ``test_snapshot_provenance_is_complete`` reads provenance to verify
    refresh reproducibility).  When the source_id is the canonical Owen2023
    one, return ``"Owen2023"`` so refreshed snapshots stay byte-stable
    against the checked-in fixture.
    """
    if source_id == OWEN2023_SOURCE_ID:
        return "Owen2023"
    return f"source-{source_id[:8]}"


def dump_snapshot(payload: dict[str, Any]) -> str:
    """Serialize a snapshot payload deterministically (trailing newline)."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
