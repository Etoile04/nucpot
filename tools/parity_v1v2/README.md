# V1<->V2 parity harness (NFM-3539)

A runnable harness that drives identical input text through both the V1
stub path and the V2 (5-step ExtractionOrchestratorV2) path and renders a
classified divergence report (`parity_report.md`).

## Why

The issue AC requires we prove that, for the same input, the *user-visible*
DB outputs of V1 and V2 are byte-identical — **or** every divergence row
is explicitly classified `cosmetic` / `non-cosmetic` / `blocking`. This is
the gate before any environment flip from the V1 monolith to the V2 pipeline.

## Caveat: V1 is gone

NFM-3008 (Phase B final cutover) deleted the legacy V1 path. The harness
therefore *synthesizes* the V1 stub-mode contract (NFM-636: 3 canned
UO2 records returned regardless of input) and runs the V2 path live on
the same input. The V2 path uses the same 5-step composition as
`ExtractionOrchestratorV2.run` minus the `AsyncSession` persistence
(which is exercised by the existing NFM-2891 baseline test).

## Layout

```
tools/parity_v1v2/
├── README.md           # this file
├── run_harness.py      # executable: drives both paths, writes the report
├── parity_report.md    # generated; the artifact of a run
└── fixtures/
    ├── short.md        # single-section UO2 brief (190 chars)
    ├── long.md         # multi-section UO2 properties (1272 chars)
    └── multi-doc.md    # 5 concatenated documents (680 chars)

tests/parity_v1v2/
├── __init__.py
├── conftest.py         # sys.path bootstrap (apps/api/src + repo root)
├── fixtures.py         # in-code Fixture objects (mirror the .md files)
├── harness.py          # DBSnapshot, DivergenceRow, ParityReport, classification,
│                       # build_report, render_markdown
└── test_harness.py     # 21 unit tests covering the comparison logic
```

## Running

```bash
# Default — write report to tools/parity_v1v2/parity_report.md
python tools/parity_v1v2/run_harness.py

# Print to stdout
python tools/parity_v1v2/run_harness.py --stdout

# Restrict to a single fixture
python tools/parity_v1v2/run_harness.py --fixture short

# Run the unit tests (no DB needed; pure comparison logic)
pytest tests/parity_v1v2/ -v
```

Exit code is `0` when verdict is `READY to flip`, `1` when `BLOCKED`.

## Updating the fixtures

The on-disk `.md` mirrors and the in-code `tests/parity_v1v2/fixtures.py`
constants are intentionally kept in lockstep. When you edit one, edit the
other; the in-code `fixture_text_dir()` helper returns the directory the
on-disk mirrors live in.

## Surfaces compared

| Surface                   | Source on V1 side                                | Source on V2 side                          |
| ------------------------- | ------------------------------------------------ | ------------------------------------------ |
| `extraction_result_count` | NFM-636 stub returns 3 canned records            | `len(final chunks from ChunkBuilder)`      |
| `extraction_chunk_count`  | 3 (one seed chunk per record)                    | `len(finals) * 4` (raw/sect/entity/prop)   |
| `extraction_job_status`   | hard-coded `completed` (stub mode)               | hard-coded `completed` (clean run)         |
| `comment_count`           | 0 (V1 stub never sets `extraction_result.comment`) | 0 (V2 never sets that column either)     |
| `retry_count`             | 0 (stub never retries)                            | 0 (no failed/skipped `extraction_step`)    |
| `extracted_count`         | 3 (canned)                                        | `len(finals)` (content-derived)             |
| `staged_count`            | 3 (canned)                                        | `extracted_count` (no chunking filter)     |

## Classification rules

- **`cosmetic`** — both sides non-zero (ratio divergence expected because V1 stub is fixed and V2 is content-derived); or values match exactly.
- **`non-cosmetic`** — exactly one side zero (one path silently drops the input); or `retry_count` differs (operator should inspect production retry trace).
- **`blocking`** — `extraction_job_status` differs (user-visible terminal state mismatch gates the flip).

See `tests/parity_v1v2/harness.py::_ratio_classification` and
`_equal_classification` for the full logic, and
`tests/parity_v1v2/test_harness.py` for the test surface.