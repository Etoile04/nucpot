"""Standalone CLI for the V1 vs V2 extraction prompt snapshot-diff harness.

Run from the repo root (or anywhere — paths are resolved absolutely):

    python apps/api/tests/extraction/run_snapshot_diff.py \\
        --output docs/verification/NFM-3531-v1-v2-baseline.md

Use this script AFTER [NFM-3531-C](/NFM/issues/NFM-3531) merges to
regenerate the baseline against the integrated branch and surface any
precision/recall regression that the V1 -> V2 prompt swap introduced.
Diff the new report against the committed baseline to identify which
fixtures now diverge.

Exit codes:

* ``0`` — report written successfully (PASS or PASS-WITH-NOTES).
* ``1`` — report written but FAIL verdict (regression vs baseline).
* ``2`` — runtime error before the report could be written.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Allow running this script directly via ``python`` without first
# installing the ``nfm_db`` package: insert ``apps/api/src`` (where
# ``nfm_db`` lives) and ``apps/api`` (where ``tests.extraction.*``
# imports resolve) onto sys.path.
_HERE = Path(__file__).resolve()
_API_SRC = _HERE.parents[3] / "src"  # apps/api/src
_API_ROOT = _HERE.parents[2]  # apps/api
for _p in (str(_API_SRC), str(_API_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.extraction.snapshot_diff import (  # noqa: E402
    REPO_ROOT,
    SnapshotReport,
    build_snapshot_report,
    render_baseline_markdown,
)


def _default_output() -> Path:
    return REPO_ROOT / "docs" / "verification" / "NFM-3531-v1-v2-baseline.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=_default_output(),
        help="Where to write the Markdown baseline report.",
    )
    parser.add_argument(
        "--also-json",
        type=Path,
        default=None,
        help="Optional path to also dump the structured SnapshotReport as JSON.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human-readable summary on stdout.",
    )
    args = parser.parse_args(argv)

    start = time.monotonic()
    try:
        report = build_snapshot_report()
    except Exception as exc:  # noqa: BLE001
        print(f"snapshot_diff: failed to build report: {exc}", file=sys.stderr)
        return 2

    elapsed = time.monotonic() - start

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_baseline_markdown(report), encoding="utf-8")

    if args.also_json is not None:
        args.also_json.parent.mkdir(parents=True, exist_ok=True)
        args.also_json.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if not args.quiet:
        verdict = (
            "FAIL"
            if report.fixtures_diverged_count > 0
            else "PASS"
            if report.prompt_identical
            else "PASS-WITH-NOTES"
        )
        print(
            f"snapshot_diff: {verdict} | "
            f"v1={len(report.v1_prompt.encode('utf-8'))}b "
            f"v2={len(report.v2_prompt.encode('utf-8'))}b | "
            f"identical={report.prompt_identical} | "
            f"fixtures_covered={report.fixtures_covered_by_v2}/"
            f"{report.fixtures_covered_total} | "
            f"coverage_diverged={report.fixtures_diverged_count} | "
            f"elapsed={elapsed:.2f}s"
        )
        print(f"snapshot_diff: wrote {args.output}")

    return 1 if report.fixtures_diverged_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
