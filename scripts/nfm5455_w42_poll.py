#!/usr/bin/env python3
"""NFM-5455 (W42 OKR standup) — one coverage poll round with the hollow gate.

Run from the NFM-5455 worktree root:  python3 scripts/nfm5455_w42_poll.py

The hollow gate (NFM-2454) is computable from collection reads alone: the
collection projection truncates descriptions at 1200c, so a shown length
≤1000 is exact (hollow), 1001–1199 is exact (filed), and 1200 means ≥1200
(fileed). "Filed" is gate-pass, status-independent, matching the W40/W41
close-out semantics. Full descriptions for the synthesis comment must come
from lookup_issue(child_uuid) bare-endpoint reads, never the collection.

Exit code is always 0; the one-line QUIET summary is safe to paste as a
poll comment on the parent.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paperclip_issue_lookup import lookup_issues  # noqa: E402

PARENT = "711af409-4a87-46d7-81cb-eb92d41513ab"  # NFM-5455
QUORUM = 12
CAP = 1200  # collection description truncation cap
HOLLOW_LIMIT = 1000

ROSTER = {
    "NFM-5456": "Lisa (CEO)",
    "NFM-5457": "CTO",
    "NFM-5458": "CPO",
    "NFM-5459": "Nuclear Domain Expert",
    "NFM-5460": "Lead Engineer",
    "NFM-5461": "Skills Architect",
    "NFM-5462": "Workflow Designer",
    "NFM-5463": "SRE Monitor",
    "NFM-5464": "Release Engineer",
    "NFM-5465": "Code Reviewer",
    "NFM-5466": "E2E QA Tester",
    "NFM-5467": "UXDesigner",
    "NFM-5468": "Research Lead",
    "NFM-5469": "Dr. Ingrid Novak",
    "NFM-5470": "Dr. Alexander Petrov",
}


def classify(row: dict) -> str:
    """FILED / HOLLOW / PENDING from shown description length + status."""
    shown = len(row.get("description") or "")
    status = row.get("status") or ""
    if shown >= HOLLOW_LIMIT + 1:
        return "FILED"
    if status in ("done", "in_review"):
        return "HOLLOW"
    return "PENDING"


def main() -> None:
    result = lookup_issues(parent_id=PARENT)
    if not hasattr(result, "issues"):
        print(f"LOOKUP FAILED: {result!r}")
        return
    rows = [r for r in result.issues if r.get("identifier") in ROSTER]

    buckets: dict[str, list[str]] = {"FILED": [], "HOLLOW": [], "PENDING": []}
    for row in sorted(rows, key=lambda r: r.get("identifier") or ""):
        ident = row.get("identifier")
        buckets[classify(row)].append(f"{ident} {ROSTER[ident]} ({row.get('status')})")

    for label in ("FILED", "HOLLOW", "PENDING"):
        print(f"== {label} ({len(buckets[label])}) ==")
        for entry in buckets[label]:
            print("  ", entry)

    filed = len(buckets["FILED"])
    print(f"\nQUIET: W42 coverage {filed}/{len(ROSTER)} filed, "
          f"{len(buckets['HOLLOW'])} hollow, {len(buckets['PENDING'])} pending — "
          f"{'QUORUM MET (>=12): build synthesis + disposition' if filed >= QUORUM else 'quorum pending'}")


if __name__ == "__main__":
    main()
