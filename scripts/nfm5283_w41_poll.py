#!/usr/bin/env python3
"""NFM-5283 W41 — poll standup children for filings (one round).

Hollow gate (NFM-2454): description <= 1000 chars is NOT a filing.
The collection endpoint truncates description at 1200 chars, which is still
decisive for the gate: shown<1200 -> true length; shown==1200 -> true >=1200.
"""
import sys

sys.path.insert(0, "scripts")
from paperclip_issue_lookup import lookup_issues, Ok  # noqa: E402

PARENT = "d1cdcfa2-0314-46eb-ada5-8b881749ebd6"

result = lookup_issues(parent_id=PARENT)
match result:
    case Ok(issues=rows):
        pass
    case _:
        print("lookup failed:", result)
        sys.exit(1)

filed, pending, hollow = [], [], []
for row in sorted(rows, key=lambda r: r.get("identifier") or ""):
    name = (row.get("title") or "").replace("[Standup Week 41] ", "").split(" — ")[0]
    status = row.get("status") or "?"
    desc_len = len(row.get("description") or "")
    ident = row.get("identifier")
    bucket = (filed if desc_len > 1000 else hollow) if status in ("done", "in_review") else pending
    bucket.append((ident, name, status, desc_len))

print(f"children={len(rows)} filed={len(filed)} hollow={len(hollow)} pending={len(pending)}")
for ident, name, status, ln in filed:
    print(f"  FILED   {ident} {name:22s} {status:10s} desc={ln}")
for ident, name, status, ln in hollow:
    print(f"  HOLLOW  {ident} {name:22s} {status:10s} desc={ln}")
for ident, name, status, ln in pending:
    print(f"  pending {ident} {name:22s} {status:10s} desc={ln}")
sys.exit(0)
