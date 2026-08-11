"""Parametrized V2 extraction parity regression vs. deterministic baseline.

Drives the V2 extraction pipeline (``RawTextLoader → SectionSegmenter →
EntityExtractor → PropertyNormalizer → ChunkBuilder``) over each
fixture under ``tests/fixtures/parity/baseline/<fixture-id>/`` and
compares the resulting ``final`` chunks against the per-fixture
``expected.json`` golden snapshot.

Acceptance thresholds (from the design spec §5):

* **Schema** — exact-key set equality on top-level chunk fields.
* **Named-entity extraction** — F1 ≥ 0.90 over ``entities.formulas``.
* **Relation extraction** — F1 ≥ 0.85 over ``entities.measurements``,
  with a 1 % relative tolerance for the numeric component of physical
  quantities (the suffix unit must match exactly).
* **Property extraction** — F1 ≥ 0.85 over ``entities.properties``.

References:

* NFM-2891 — integration task that owns the baseline directory
  (``tests/fixtures/parity/baseline/``).
* NFM-2890 — staging parity decision (this suite replaces the prior
  V1 stub comparison because V1 stub mode returns canned data and
  staging has no LLM keys).
* NFM-2875 (T1) — V2 extraction pipeline reviews / follow-ups.

Run:

    EXTRACTION_STUB_MODE=true pytest -m parity

The ``-m parity`` selector runs only this suite; without it the rest
of the regression suite continues to run and parity stays out of the
fast path (``pytest -m 'not parity'``).
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import time
import uuid
from collections.abc import Iterable
from pathlib import Path

import pytest

from nfm_db.services.extraction.steps.chunk_builder import ChunkBuilder
from nfm_db.services.extraction.steps.entity_extractor import EntityExtractor
from nfm_db.services.extraction.steps.property_normalizer import PropertyNormalizer
from nfm_db.services.extraction.steps.raw_text_loader import RawTextLoader
from nfm_db.services.extraction.steps.section_segmenter import SectionSegmenter
from nfm_db.services.extraction.types import ExtractionChunk

# ---------------------------------------------------------------------------
# Thresholds — keep names stable so the structured diff report is greppable.
# ---------------------------------------------------------------------------

NAMED_ENTITY_F1_THRESHOLD: float = 0.90
RELATION_F1_THRESHOLD: float = 0.85
PROPERTY_F1_THRESHOLD: float = 0.85

# 1 % relative tolerance for physical-quantity comparisons (spec §5).
NUMERIC_RELATIVE_TOLERANCE: float = 0.01

STUB_MODE_ENV: str = "EXTRACTION_STUB_MODE"

# Repository-root baseline directory (resolved absolutely from __file__,
# not from pytest's cwd, so the suite works whether invoked from the
# repo root or from ``apps/api/``).
REPO_ROOT: Path = Path(__file__).resolve().parents[3]
BASELINE_DIR: Path = REPO_ROOT / "tests" / "fixtures" / "parity" / "baseline"
ARTIFACTS_DIR: Path = REPO_ROOT / "tests" / "artifacts" / "parity"


# ---------------------------------------------------------------------------
# Pre-condition gate: EXTRACTION_STUB_MODE must be ``"true"`` (NFM-2890).
# Skipping (rather than failing) so ``pytest -m 'not parity'`` is unchanged
# for callers who only want the fast suite.
# ---------------------------------------------------------------------------


def _require_stub_mode() -> None:
    current = os.environ.get(STUB_MODE_ENV)
    if (current or "").strip().lower() != "true":
        pytest.skip(
            f"Parity suite requires {STUB_MODE_ENV}=true (got {current!r}). "
            f"Re-run with: {STUB_MODE_ENV}=true pytest -m parity"
        )


# ---------------------------------------------------------------------------
# V2 pipeline driver — mirrors the orchestrator composition exactly without
# instantiating it (the orchestrator only adds AsyncSession persistence and
# job-id tagging, neither of which is relevant to a regression test).
# ---------------------------------------------------------------------------


def _run_v2_pipeline(source_text: str) -> list[ExtractionChunk]:
    """Apply all 5 V2 steps and return the list of ``final`` chunks."""
    loader = RawTextLoader()
    segmenter = SectionSegmenter()
    extractor = EntityExtractor()
    normalizer = PropertyNormalizer()
    builder = ChunkBuilder()

    parent = ExtractionChunk(
        content=source_text,
        chunk_type="raw_text",
        _source_span=(0, len(source_text)),
        metadata={},
        parent_chunk_id=None,
    )
    normalized = loader.execute(parent)
    sections = segmenter.execute_many(normalized)
    finals: list[ExtractionChunk] = []
    for section in sections:
        with_entities = extractor.execute(section)
        with_properties = normalizer.execute(with_entities)
        finals.append(builder.execute(with_properties))
    return finals


def _chunk_to_jsonable(chunk: ExtractionChunk) -> dict:
    """Convert an ``ExtractionChunk`` into a JSON-serializable baseline dict.

    Mirrors the ``expected.json`` shape: ``_source_span`` is a 2-list
    (matches the on-the-wire serializer), ``metadata`` and
    ``parent_chunk_id`` pass through unchanged.
    """
    payload = dataclasses.asdict(chunk)
    payload["_source_span"] = list(chunk._source_span)
    return payload


# ---------------------------------------------------------------------------
# Comparison helpers — kept as plain functions so the structured diff report
# can be assembled in one place without entangling it with pytest asserts.
# ---------------------------------------------------------------------------


_NUMERIC_LEADING_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(.*)$")


def _split_numeric(measurement: str) -> tuple[float | None, str]:
    """Return ``(numeric_value, unit_suffix)`` for a measurement string."""
    match = _NUMERIC_LEADING_RE.match(measurement)
    if not match:
        return None, measurement.strip()
    return float(match.group(1)), match.group(2).strip()


def _f1(expected: Iterable[str], actual: Iterable[str]) -> tuple[float, float, float, list[str], list[str]]:
    """Set F1 over two string lists.

    Returns ``(f1, precision, recall, missing, spurious)`` sorted and
    de-duplicated. Comparison is case-sensitive exact string equality
    so callers can post-process any near-misses themselves.
    """
    exp_set = set(expected)
    act_set = set(actual)
    intersection = exp_set & act_set
    missing = sorted(exp_set - act_set)
    spurious = sorted(act_set - exp_set)
    if not intersection:
        return 0.0, 0.0, 0.0, missing, spurious
    precision = len(intersection) / len(act_set) if act_set else 0.0
    recall = len(intersection) / len(exp_set) if exp_set else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return f1, precision, recall, missing, spurious


def _f1_with_numeric_tolerance(
    expected: Iterable[str],
    actual: Iterable[str],
    relative_tolerance: float,
) -> tuple[float, float, float, list[str], list[str], list[dict]]:
    """Set F1 with 1 % relative numeric tolerance for quantity strings.

    A string pair matches when either the strings are byte-equal **or**
    both contain a parseable numeric prefix whose values are within
    ``relative_tolerance * max(|a|,|b|)`` *and* whose non-numeric
    suffixes match exactly.

    Returns ``(f1, precision, recall, missing, spurious, mismatches)``
    where ``mismatches`` is a list of ``{"expected": ...}`` dicts for
    every expected-only element that could not be matched even after
    the tolerance check — useful for the diff report.
    """
    exp_list = sorted(set(expected))
    act_list = sorted(set(actual))
    act_remaining = set(act_list)

    matched_pairs: list[tuple[str, str]] = []
    mismatches: list[dict] = []

    for exp_str in exp_list:
        # Fast path: byte-equal match.
        if exp_str in act_remaining:
            act_remaining.discard(exp_str)
            matched_pairs.append((exp_str, exp_str))
            continue
        # Slow path: scan for a tolerance-aware match.
        en, eu = _split_numeric(exp_str)
        candidate: str | None = None
        for act_str in sorted(act_remaining):
            an, au = _split_numeric(act_str)
            if en is None or an is None:
                continue
            if au != eu:
                continue
            denom = max(abs(en), abs(an))
            if denom == 0 or abs(en - an) <= relative_tolerance * denom:
                candidate = act_str
                break
        if candidate is not None:
            act_remaining.discard(candidate)
            matched_pairs.append((exp_str, candidate))
        else:
            mismatches.append({"expected": exp_str})

    tp = len(matched_pairs)
    fp = len(act_remaining)
    fn = len(exp_list) - tp

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    missing = sorted(set(exp_list) - {m[0] for m in matched_pairs})
    spurious = sorted(act_remaining)
    return f1, precision, recall, missing, spurious, mismatches


def _schema_key_diff(expected_chunk: dict, actual_chunk: dict) -> dict:
    """Return any extra / missing top-level keys between expected & actual."""
    expected_keys = set(expected_chunk)
    actual_keys = set(actual_chunk)
    return {
        "missing_keys": sorted(expected_keys - actual_keys),
        "extra_keys": sorted(actual_keys - expected_keys),
    }


# ---------------------------------------------------------------------------
# Failure reporter — emits a structured diff JSON to ``tests/artifacts/parity/``
# so a CI run can attach the diff to a comment without re-running pytest.
# ---------------------------------------------------------------------------


def _emit_structured_diff(
    fixture_id: str,
    dimension_failures: list[dict],
    schema_diffs: list[dict],
    actual_chunk: dict,
    expected_chunk: dict,
) -> Path:
    """Write the structured failure diff for one fixture."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    out_path = ARTIFACTS_DIR / f"{run_id}.json"
    payload = {
        "fixture": fixture_id,
        "run_id": run_id,
        "dimensions": dimension_failures,
        "schema_diffs": schema_diffs,
        "actual_chunk": actual_chunk,
        "expected_chunk": expected_chunk,
    }
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return out_path


# ---------------------------------------------------------------------------
# Parametrization: one test per baseline fixture directory.
# ---------------------------------------------------------------------------


def _list_fixture_ids() -> list[str]:
    """Return fixture IDs (sub-directory names) under the baseline root."""
    if not BASELINE_DIR.is_dir():
        return []
    return sorted(p.name for p in BASELINE_DIR.iterdir() if p.is_dir())


@pytest.fixture(scope="module", autouse=True)
def _stub_mode_guard() -> None:
    """Skip the entire parity module if stub mode is not enabled."""
    _require_stub_mode()


@pytest.mark.parity
@pytest.mark.parametrize("fixture_id", _list_fixture_ids())
def test_v2_extraction_matches_baseline(fixture_id: str) -> None:
    """Run V2 stub-mode over ``fixture_id`` and assert parity with ``expected.json``."""
    fixture_dir = BASELINE_DIR / fixture_id
    source_text = (fixture_dir / "source.txt").read_text(encoding="utf-8")
    expected_chunks: list[dict] = json.loads(
        (fixture_dir / "expected.json").read_text(encoding="utf-8")
    )

    actual_chunks = _run_v2_pipeline(source_text)
    actual_jsonable = [_chunk_to_jsonable(c) for c in actual_chunks]

    dimension_failures: list[dict] = []
    schema_diffs: list[dict] = []

    # ----- Schema: same number of final chunks -----
    if len(expected_chunks) != len(actual_jsonable):
        dimension_failures.append(
            {
                "dimension": "chunk_count",
                "expected": len(expected_chunks),
                "actual": len(actual_jsonable),
            }
        )
        for idx in range(max(len(expected_chunks), len(actual_jsonable))):
            schema_diffs.append(
                {
                    "index": idx,
                    "expected_present": idx < len(expected_chunks),
                    "actual_present": idx < len(actual_jsonable),
                }
            )
        per_chunk_pairs: list[tuple[dict, dict]] = []
    else:
        per_chunk_pairs = list(zip(expected_chunks, actual_jsonable))

    for chunk_idx, (exp_chunk, act_chunk) in enumerate(per_chunk_pairs):
        key_diff = _schema_key_diff(exp_chunk, act_chunk)
        if key_diff["missing_keys"] or key_diff["extra_keys"]:
            schema_diffs.append({"index": chunk_idx, **key_diff})

        exp_entities = exp_chunk.get("metadata", {}).get("entities", {})
        act_entities = act_chunk.get("metadata", {}).get("entities", {})

        # ----- Named entities (chemical formulas) -----
        ef1, eprec, erec, emissing, espurious = _f1(
            exp_entities.get("formulas", []),
            act_entities.get("formulas", []),
        )
        if ef1 < NAMED_ENTITY_F1_THRESHOLD:
            dimension_failures.append(
                {
                    "fixture": fixture_id,
                    "chunk_index": chunk_idx,
                    "dimension": "named_entity",
                    "f1": ef1,
                    "precision": eprec,
                    "recall": erec,
                    "threshold": NAMED_ENTITY_F1_THRESHOLD,
                    "missing_entities": emissing,
                    "spurious_entities": espurious,
                }
            )

        # ----- Properties (canonical snake_case names) -----
        pf1, pprec, prec, pmissing, pspurious = _f1(
            exp_entities.get("properties", []),
            act_entities.get("properties", []),
        )
        if pf1 < PROPERTY_F1_THRESHOLD:
            dimension_failures.append(
                {
                    "fixture": fixture_id,
                    "chunk_index": chunk_idx,
                    "dimension": "property",
                    "f1": pf1,
                    "precision": pprec,
                    "recall": prec,
                    "threshold": PROPERTY_F1_THRESHOLD,
                    "missing_entities": pmissing,
                    "spurious_entities": pspurious,
                }
            )

        # ----- Relations (measurements, with 1 % numeric tolerance) -----
        (
            rf1,
            rprec,
            rrec,
            rmissing,
            rspurious,
            rmismatches,
        ) = _f1_with_numeric_tolerance(
            exp_entities.get("measurements", []),
            act_entities.get("measurements", []),
            NUMERIC_RELATIVE_TOLERANCE,
        )
        if rf1 < RELATION_F1_THRESHOLD:
            dimension_failures.append(
                {
                    "fixture": fixture_id,
                    "chunk_index": chunk_idx,
                    "dimension": "relation",
                    "f1": rf1,
                    "precision": rprec,
                    "recall": rrec,
                    "threshold": RELATION_F1_THRESHOLD,
                    "missing_entities": rmissing,
                    "spurious_entities": rspurious,
                    "property_mismatches": rmismatches,
                }
            )

    if dimension_failures or schema_diffs:
        artifact_path = _emit_structured_diff(
            fixture_id=fixture_id,
            dimension_failures=dimension_failures,
            schema_diffs=schema_diffs,
            actual_chunk=actual_jsonable[0] if actual_jsonable else {},
            expected_chunk=expected_chunks[0] if expected_chunks else {},
        )
        pytest.fail(
            "V2 parity regression for fixture "
            f"{fixture_id!r}: {len(dimension_failures)} dimension failure(s), "
            f"{len(schema_diffs)} schema diff(s). "
            f"Structured diff: {artifact_path}"
        )
