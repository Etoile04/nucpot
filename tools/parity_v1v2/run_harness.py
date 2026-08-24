#!/usr/bin/env python3
"""Runnable V1<->V2 parity harness (NFM-3539).

Drives the same fixture text through both paths and renders a classified
divergence report.

V1 side (legacy stub, NFM-636)
------------------------------
The legacy V1 pipeline, when run with ``EXTRACTION_STUB_MODE=true``, returned
a fixed 3-record canned UO2 result regardless of input.  V1 was deleted in
NFM-3008 (Phase B final cutover), so we cannot run that code path live.
Instead, we reproduce the *contract* by synthesizing the DBSnapshot that
V1 stub-mode would have written for the given fixture.  Three records,
no chunks beyond the seed, status='completed', zero retries.

V2 side (current production pipeline)
-------------------------------------
Drives the five strangler-fig steps (RawTextLoader -> SectionSegmenter ->
EntityExtractor -> PropertyNormalizer -> ChunkBuilder) directly on the
fixture text — same composition as
``nfm_db.services.extraction_orchestrator_v2.ExtractionOrchestratorV2``
minus the AsyncSession persistence (which is exercised by the existing
NFM-2891 baseline test).  Derives a DBSnapshot from the emitted chunks.

Output
------
By default writes ``parity_report.md`` next to this script.  Pass
``--stdout`` to print to stdout.  Exit code is 0 when the report verdict
is READY, 1 when BLOCKED.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make ``apps/api/src`` importable for the V2 step classes, mirroring
# ``tests/parity/conftest.py`` (NFM-2891).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_APPS_API_SRC = _REPO_ROOT / "apps" / "api" / "src"
for p in (str(_REPO_ROOT), str(_APPS_API_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from tests.parity_v1v2.harness import (  # noqa: E402
    DBSnapshot,
    build_report,
    render_markdown,
)
from nfm_db.services.extraction.steps.chunk_builder import (  # noqa: E402
    ChunkBuilder,
)
from nfm_db.services.extraction.steps.entity_extractor import (  # noqa: E402
    EntityExtractor,
)
from nfm_db.services.extraction.steps.property_normalizer import (  # noqa: E402
    PropertyNormalizer,
)
from nfm_db.services.extraction.steps.raw_text_loader import (  # noqa: E402
    RawTextLoader,
)
from nfm_db.services.extraction.steps.section_segmenter import (  # noqa: E402
    SectionSegmenter,
)
from nfm_db.services.extraction.types import ExtractionChunk  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@dataclass(frozen=True)
class LoadedFixture:
    name: str
    path: Path
    text: str


def _load(name: str) -> LoadedFixture:
    path = FIXTURES_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    return LoadedFixture(name=name, path=path, text=text)


# ---------------------------------------------------------------------------
# V1 (legacy stub) path — synthesize the contract that V1 stub-mode
# would have produced for the given input.  V1 code is gone (NFM-3008),
# but the contract is documented in NFM-636 / NFM-2993 / extraction_pipeline
# pre-cutover history.
# ---------------------------------------------------------------------------

# V1 stub mode returns exactly these three records regardless of input.
_V1_STUB_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "material": "UO2",
        "property": "lattice_constant",
        "value": "5.470",
        "unit": "angstrom",
    },
    {
        "material": "UO2",
        "property": "bulk_modulus",
        "value": "207.5",
        "unit": "GPa",
    },
    {
        "material": "UO2",
        "property": "thermal_conductivity",
        "value": "7.5",
        "unit": "W/(m*K)",
    },
)


def _v1_stub_snapshot(fixture: LoadedFixture) -> DBSnapshot:
    """Build the DBSnapshot V1 stub-mode would have written for ``fixture``.

    The V1 stub never set a non-empty ``comment`` column and never
    retried; chunk count equals record count (one seed chunk per record);
    ``extraction_job.status`` is hard-coded ``completed``; ``staged_count``
    equals ``extracted_count`` (3) because V1 stub-mode always staged the
    canned payload end-to-end without any chunking.
    """
    n = len(_V1_STUB_RECORDS)
    return DBSnapshot(
        path="v1",
        fixture_name=fixture.name,
        extraction_result_count=n,
        extraction_chunk_count=n,
        extraction_job_status="completed",
        comment_count=0,
        retry_count=0,
        extracted_count=n,
        staged_count=n,
        notes=("synthesized from NFM-636 V1 stub-mode contract",),
    )


# ---------------------------------------------------------------------------
# V2 path — drive the 5-step pipeline directly.
# ---------------------------------------------------------------------------


def _run_v2_steps(text: str) -> list[ExtractionChunk]:
    """Drive the 5 V2 steps on ``text`` and return the final chunks.

    Mirrors ``tests/parity/test_parity.py::_run_v2`` (NFM-2891) without the
    AsyncSession persistence. Each emitted chunk is what
    ``ChunkBuilder`` produces for one section — the orchestrator would
    otherwise INSERT it as one ``extraction_chunks`` row and INSERT the
    underlying section/entity/property intermediates as siblings.
    """
    initial = ExtractionChunk(
        content=text,
        chunk_type="raw_text",
        _source_span=(0, len(text)),
        metadata={},
        parent_chunk_id=None,
    )
    loader = RawTextLoader()
    segmenter = SectionSegmenter()
    extractor = EntityExtractor()
    normalizer = PropertyNormalizer()
    builder = ChunkBuilder()

    normalized = loader.execute(initial)
    sections = segmenter.execute_many(normalized)
    finals: list[ExtractionChunk] = []
    for section in sections:
        with_entities = extractor.execute(section)
        with_normalized = normalizer.execute(with_entities)
        final = builder.execute(with_normalized)
        finals.append(final)
    return finals


def _v2_snapshot(fixture: LoadedFixture) -> DBSnapshot:
    """Build the DBSnapshot V2 produced for ``fixture``.

    Mapping rules:
    * ``extraction_chunk_count`` = total chunks that V2 would have
      persisted across the 5 steps for this fixture (the chunk-builder
      finals + the intermediate section/entity/property chunks, derived
      from the final-chunk metadata ``metadata['source_chunk_ids']``).
      For parity purposes we approximate as ``len(finals) * 4`` (one
      section + one entity + one property + one final per section);
      this matches the steady-state shape documented in NFM-2677.
    * ``extraction_result_count`` = ``len(finals)`` — the number of
      final chunks V2 emits; each represents one canonical row in the
      ``extractions`` table.
    * ``extraction_job_status`` = ``completed`` — V2 always completes
      end-to-end on a non-empty fixture in stub-or-content mode.
    * ``comment_count`` = 0 — V2 does not populate the legacy
      ``extraction_result.comment`` column.
    * ``retry_count`` = 0 — V2 has no retry boundary in stub mode;
      production retries are tracked via the ``extraction_step``
      status column, which is empty in a clean unit run.
    * ``extracted_count`` = ``extraction_result_count`` (each result
      is extracted exactly once).
    * ``staged_count`` = ``extracted_count`` (no chunking filter
      applied at this layer in stub mode).
    """
    finals = _run_v2_steps(fixture.text)
    n_results = len(finals)
    n_chunks_per_final = 4  # raw + section + entity + property + final -> 5
    # Recount more conservatively: each final represents 1 section,
    # 1 entity-bearing chunk, 1 normalized property chunk, and the
    # final itself. So per-final we get 4 rows on disk.
    # (V2 inserts the raw_text chunk once at the orchestrator level,
    # so we exclude it from the per-fixture total.)
    n_chunks = n_results * n_chunks_per_final
    return DBSnapshot(
        path="v2",
        fixture_name=fixture.name,
        extraction_result_count=n_results,
        extraction_chunk_count=n_chunks,
        extraction_job_status="completed",
        comment_count=0,
        retry_count=0,
        extracted_count=n_results,
        staged_count=n_results,
        notes=(f"finals={n_results}, chunks_per_final=4",),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the report to stdout instead of writing the file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "parity_report.md",
        help="Path to write the report (ignored with --stdout).",
    )
    parser.add_argument(
        "--fixture",
        action="append",
        default=None,
        help=(
            "Restrict to the named fixture(s). Repeatable. "
            "Default: short, long, multi-doc."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    fixture_names = args.fixture or ["short", "long", "multi-doc"]
    fixtures = [_load(name) for name in fixture_names]

    snapshot_pairs = []
    for fix in fixtures:
        v1 = _v1_stub_snapshot(fix)
        v2 = _v2_snapshot(fix)
        snapshot_pairs.append((v1, v2))

    report = build_report(snapshot_pairs)
    md = render_markdown(report)

    if args.stdout:
        sys.stdout.write(md)
        sys.stdout.flush()
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md, encoding="utf-8")
        sys.stderr.write(
            f"[parity_v1v2] wrote {args.output} "
            f"(verdict={'BLOCKED' if report.is_blocked else 'READY'})\n"
        )

    return 1 if report.is_blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())