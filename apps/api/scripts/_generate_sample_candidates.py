#!/usr/bin/env python3
"""Generate a synthetic candidates snapshot for diff_priority_ranking (NFM-3578).

Produces 80 candidates with deterministic-but-varied signals so the
top-50 delta table shows realistic moves into/out of the cutoff.

Run:
    cd apps/api && python scripts/_generate_sample_candidates.py \\
        --out tests/fixtures/sample_priority_candidates.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=80)
    args = parser.parse_args(argv)

    candidates = []
    for i in range(args.count):
        # Realistic-ish signal mix.
        ontology = (math.sin(i * 1.3) + 1) / 2  # [0,1]
        atf = (math.cos(i * 0.7) + 1) / 2  # [0,1]
        citation = (math.sin(i * 2.1 + 0.5) + 1) / 2  # [0,1]

        # Old heuristic is a synthetic weighted blend with the *opposite*
        # bias — favors low-ontology + high-atf candidates (the old behaviour
        # that the new formula corrects). This guarantees visible rank churn.
        score_old = round(0.7 * atf + 0.2 * citation + 0.1 * ontology, 4)

        candidates.append(
            {
                "candidate_id": f"cand-{i:03d}",
                "score_old": score_old,
                "signals": {
                    "ontology": round(ontology, 4),
                    "atf": round(atf, 4),
                    "citation": round(citation, 4),
                },
            }
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"candidates": candidates}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {len(candidates)} candidates to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
