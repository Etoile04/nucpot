#!/usr/bin/env python3
"""NFM-5283 W41 — close-out synthesis: gather + digest filed standup children.

Run at quorum (>=12/15 filed) or at the Fri 2026-10-09 17:00 +08 gate.
Emits a coverage roll-up plus a per-reporter KR digest to stdout; the
close-out comment and disposition decision are authored from this output.

Hollow gate (NFM-2454): description <= 1000 chars is NOT a filing.
Collection truncates descriptions at 1200c, so filed children are re-fetched
individually for the full text (lookup_issue trap-3 path).
"""
import re
import sys

sys.path.insert(0, "scripts")
from paperclip_issue_lookup import lookup_issue, lookup_issues, Ok  # noqa: E402

PARENT = "d1cdcfa2-0314-46eb-ada5-8b881749ebd6"
QUORUM = 12
KR_BULLET = re.compile(r"^\s*-\s*\*\*(KR-[A-Za-z0-9_.\-]+)\b[^*]*\*\*\s*:?\s*(.+)$", re.M)
KR_TABLE = re.compile(r"^\s*\|\s*\*\*(KR-[A-Za-z0-9_.\-]+)\**\s*\|(.+)$", re.M)
GRADES = (
    ("\U0001f534", "RED"),
    ("\U0001f7e1", "YELLOW"),
    ("\U0001f7e2", "GREEN"),
    ("⚡", "RISK"),
)
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def compress(text: str, cap: int = 220) -> str:
    """Markdown-link to bare label, collapse whitespace, cap length."""
    flat = re.sub(r"\s+", " ", LINK.sub(r"\1", text)).strip()
    return flat if len(flat) <= cap else flat[: cap - 1] + "…"


def kr_digest(desc: str) -> list[str]:
    rows = []
    found = KR_BULLET.findall(desc) + KR_TABLE.findall(desc)
    for kr_id, rest in found:
        rest = rest.replace("|", " —") if rest.count("|") >= 2 else rest
        grade = next((name for emoji, name in GRADES if emoji in rest), "")
        rows.append(f"{kr_id}: {grade + ' | ' if grade else ''}{compress(rest)}")
    return rows


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
    uuid = row.get("id")
    entry = (row.get("identifier"), name, status, desc_len, uuid)
    bucket = (filed if desc_len > 1000 else hollow) if status in ("done", "in_review") else pending
    bucket.append(entry)

print(f"W41 NFM-5283 synthesis — filed {len(filed)}/15, hollow {len(hollow)}, pending {len(pending)}")
print(f"QUORUM (>=12/15): {'REACHED' if len(filed) >= QUORUM else 'NOT REACHED'}\n")

for ident, name, status, _, uuid in filed:
    full = lookup_issue(uuid)
    match full:
        case Ok(issues=[iss]):
            desc = iss.get("description") or ""
        case _:
            print(f"### {name} ({ident}, {status}) — FULL FETCH FAILED: {full}")
            continue
    print(f"### {name} ({ident}, {status}, {len(desc)}c)")
    digest = kr_digest(desc)
    if digest:
        for row in digest:
            print(f"- {row}")
    else:
        print(f"- (no KR lines matched) head: {compress(desc, 200)}")
    print()

for ident, name, status, ln in [(i, n, s, l) for i, n, s, l, _ in hollow]:
    print(f"### HOLLOW {name} ({ident}, {status}, desc={ln}) — does NOT count as filed")
for ident, name, status, ln, _ in pending:
    print(f"pending {ident} {name} {status} desc={ln}")

gate = (
    "done" if len(filed) >= QUORUM else ("in_review + gap list" if len(filed) >= 8 else "blocked @CEO")
)
print(f"\nCLOSE-OUT GATE per routine rev 7 (applies Fri 17:00 +08 / quorum): {gate}")
sys.exit(0)
