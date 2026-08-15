"""Parity test: V2 extraction output must equal curated reference baseline.

NFM-2891 / NFM-2875 T1. Replaces the prior V1-vs-V2 parity check
(which was meaningless on staging because V1 stub mode returns canned
data and no LLM keys are configured there). The reference baseline is
deterministic, auditable, and human-validated against the source
papers — see ``tests/parity/README.md`` for the full rationale.

The test invokes the real V2 step classes (RawTextLoader →
SectionSegmenter → EntityExtractor → PropertyNormalizer →
ChunkBuilder), then compares the resulting chunks structurally
against the per-fixture ``expected.json`` snapshot. Any drift in the
V2 implementation causes a hard failure with a structured diff
report that names the offending chunk field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nfm_db.services.extraction.steps.chunk_builder import ChunkBuilder
from nfm_db.services.extraction.steps.entity_extractor import EntityExtractor
from nfm_db.services.extraction.steps.property_normalizer import PropertyNormalizer
from nfm_db.services.extraction.steps.raw_text_loader import RawTextLoader
from nfm_db.services.extraction.steps.section_segmenter import SectionSegmenter
from nfm_db.services.extraction.types import ExtractionChunk

# Path: tests/parity/ → tests/fixtures/parity/baseline/
BASELINE_DIR = (
    Path(__file__).resolve().parent.parent / "fixtures" / "parity" / "baseline"
)


def _run_v2(text: str) -> list[ExtractionChunk]:
    """Drive the 5 V2 steps over ``text``; return the list of final chunks.

    This mirrors the V2 pipeline composition exactly (see
    ``apps/api/src/nfm_db/services/extraction_orchestrator_v2.py``)
    without instantiating the full orchestrator — the orchestrator's
    only extra responsibilities are AsyncSession persistence and
    job_id tagging, neither of which is relevant to a unit test.
    """
    loader = RawTextLoader()
    segmenter = SectionSegmenter()
    extractor = EntityExtractor()
    normalizer = PropertyNormalizer()
    builder = ChunkBuilder()

    initial = ExtractionChunk(
        content=text,
        chunk_type="raw_text",
        _source_span=(0, len(text)),
        metadata={},
        parent_chunk_id=None,
    )
    normalized = loader.execute(initial)
    sections = segmenter.execute_many(normalized)
    finals: list[ExtractionChunk] = []
    for section in sections:
        with_entities = extractor.execute(section)
        with_normalized = normalizer.execute(with_entities)
        final = builder.execute(with_normalized)
        finals.append(final)
    return finals


def _chunk_to_dict(chunk: ExtractionChunk) -> dict[str, Any]:
    """Convert an ExtractionChunk into a JSON-serializable baseline dict.

    The baseline JSON uses ``list`` for ``_source_span`` (matches
    what the V2 orchestrator emits on the wire via SQLAlchemy
    serialization). ``metadata`` and ``parent_chunk_id`` pass through
    as-is.
    """
    return {
        "content": chunk.content,
        "chunk_type": chunk.chunk_type,
        "_source_span": list(chunk._source_span),
        "metadata": chunk.metadata,
        "parent_chunk_id": chunk.parent_chunk_id,
    }


def _discover_fixtures() -> list[tuple[str, Path, Path]]:
    """Return [(fixture_name, source_path, expected_path), ...] sorted."""
    out: list[tuple[str, Path, Path]] = []
    if not BASELINE_DIR.exists():
        return out
    for fixture_dir in sorted(BASELINE_DIR.iterdir()):
        if not fixture_dir.is_dir():
            continue
        source = fixture_dir / "source.txt"
        expected = fixture_dir / "expected.json"
        if source.exists() and expected.exists():
            out.append((fixture_dir.name, source, expected))
    return out


def _fixture_id(value: Any) -> str:
    """Pytest ID function: humanize the parametrize tuple."""
    if isinstance(value, str):
        return value
    if isinstance(value, Path):
        return value.parent.name
    return str(value)


def _structured_diff(expected: Any, actual: Any) -> list[str]:
    """Return a list of human-readable diff lines.

    Walks both trees depth-first. Lists compare element-by-element;
    dicts compare key-by-key. Scalars compare by value. Stops at the
    first 25 leaf-level mismatches to keep failure output readable.
    """
    lines: list[str] = []
    _walk_diff(expected, actual, path="$", lines=lines, limit=25)
    return lines


def _walk_diff(
    expected: Any,
    actual: Any,
    path: str,
    lines: list[str],
    limit: int,
) -> None:
    if len(lines) >= limit:
        lines.append(f"... (diff truncated at {limit} entries)")
        return
    if type(expected) is not type(actual):
        lines.append(
            f"{path}: type {type(actual).__name__} != {type(expected).__name__}"
        )
        return
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                lines.append(
                    f"{path}.{key}: unexpected key in actual = {actual[key]!r}"
                )
            elif key not in actual:
                lines.append(
                    f"{path}.{key}: missing from actual (expected {expected[key]!r})"
                )
            else:
                _walk_diff(expected[key], actual[key], f"{path}.{key}", lines, limit)
        return
    if isinstance(expected, list):
        if len(expected) != len(actual):
            lines.append(
                f"{path}: list length {len(actual)} != expected {len(expected)}"
            )
        for i, (e_item, a_item) in enumerate(zip(expected, actual, strict=False)):
            _walk_diff(e_item, a_item, f"{path}[{i}]", lines, limit)
        return
    if expected != actual:
        lines.append(f"{path}: actual={actual!r} != expected={expected!r}")


# Cache discovery at import time so the parametrize happens once.
_FIXTURES = _discover_fixtures()


@pytest.mark.parity
@pytest.mark.parametrize(
    "fixture_name,source_path,expected_path",
    _FIXTURES,
    ids=[_fixture_id(name) for (name, _, _) in _FIXTURES],
)
def test_v2_output_matches_baseline(
    fixture_name: str,
    source_path: Path,
    expected_path: Path,
) -> None:
    """V2 extraction of ``source.txt`` must equal the curated ``expected.json``."""
    text = source_path.read_text(encoding="utf-8")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    actual_chunks = _run_v2(text)
    actual = [_chunk_to_dict(c) for c in actual_chunks]

    if actual != expected:
        diff = _structured_diff(expected, actual)
        joined = "\n".join(f"    {line}" for line in diff)
        pytest.fail(
            f"V2 output diverged from baseline for fixture {fixture_name!r}.\n"
            f"  source:    {source_path}\n"
            f"  expected:  {expected_path}\n"
            f"  diff:\n{joined}\n"
            f"If this divergence is intentional, regenerate the baseline with\n"
            f"  python tools/parity_baseline/compute_expected.py "
            f"{source_path} {expected_path}\n"
            f"after human review."
        )
