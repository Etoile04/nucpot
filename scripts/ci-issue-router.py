#!/usr/bin/env python3
"""CI Issue Router: GitHub CI issues → Paperclip SRE Monitor.

Scans GitHub issues with ci-failure/deploy-failure labels and routes them
to Paperclip's SRE Monitor agent. Closes Paperclip issues when GitHub
issues are closed.

Runs as a Hermes cron job every 10 minutes. Silent when nothing to report.

Dedup layers (2026-11-07, dsh-adoption M1 Task 4):
  1. state file (CI_ROUTER_STATE, default ~/.hermes/state/ci-issue-router-
     router-state.json) — fast-path skip for already-routed gh numbers, and
     reopen detection (gh open again after auto-close).
  2. PG existence regex (#<n> with digit boundary) — second defense when the
     state file is lost; unchanged from previous behavior.
  3. pg_advisory_xact_lock around card creation — serializes the NFM-<n>
     max+1 allocation so two overlapping ticks cannot double-issue numbers
     or double-create cards.
"""

# ruff: noqa: B007, F841, N999

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

import psycopg2

# --- Configuration ---
REPO = "Etoile04/nucpot"
COMPANY_ID = "ec7c0ded-5688-4002-8d0c-672597244875"
PROJECT_ID = "89da4552-72a9-4e0b-8938-400c90bcf743"
SRE_AGENT_ID = "2ee2415b-e43e-4806-888f-c231e60facaf"
LABELS = ["ci-failure", "deploy-failure"]
STATE_PATH = os.environ.get(
    "CI_ROUTER_STATE",
    os.path.expanduser("~/.hermes/state/ci-issue-router-state.json"),
)
ADVISORY_LOCK_KEY = 0x63697274  # "cirt" — identifies this router's critical section


def load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"routed": {}, "closed": []}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_PATH)


DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 54329,
    "dbname": "paperclip",
    "user": "paperclip",
    "password": "paperclip",
}


def gh_issue_list(label: str, state: str, limit: int = 20) -> tuple[list[dict], int]:
    """Query GitHub issues via gh CLI.

    Returns (issues, failure_count). Failures are never silent: each one
    prints a stderr WARNING and increments the count so main() can exit
    non-zero. Before this, a gh failure (EOF/TLS/proxy rot) returned []
    indistinguishable from "no issues", so the 10-min cron reported
    SILENT forever while actually blind.
    """
    failures = 0
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                REPO,
                "--label",
                label,
                "--state",
                state,
                "--json",
                "number,title,body,labels",
                "--limit",
                str(limit),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(
                f"CI Issue Router: WARNING: gh issue list failed "
                f"(label={label}, state={state}): "
                f"{(result.stderr or result.stdout or '').strip()[:200]}",
                file=sys.stderr,
            )
            failures += 1
            return [], failures
        return (json.loads(result.stdout) if result.stdout.strip() else []), failures
    except json.JSONDecodeError as exc:
        print(
            f"CI Issue Router: WARNING: gh issue list returned invalid JSON "
            f"(label={label}, state={state}): {exc}",
            file=sys.stderr,
        )
        return [], failures + 1
    except subprocess.TimeoutExpired:
        print(
            f"CI Issue Router: WARNING: gh issue list timed out "
            f"(label={label}, state={state})",
            file=sys.stderr,
        )
        return [], failures + 1
    except FileNotFoundError:
        print(
            "CI Issue Router: WARNING: gh CLI not found on PATH — "
            "router cannot see GitHub at all",
            file=sys.stderr,
        )
        return [], failures + 1


def merge_dedup(lists: list[list[dict]]) -> list[dict]:
    """Merge issue lists and deduplicate by issue number."""
    seen: set[int] = set()
    merged: list[dict] = []
    for lst in lists:
        for item in lst:
            num = item.get("number")
            if num and num not in seen:
                merged.append(item)
                seen.add(num)
    return merged


def extract_sha(title: str, body: str) -> str | None:
    """Extract 7-char commit SHA from issue title or body."""
    match = re.search(r"\(([0-9a-f]{7})\)", title)
    if match:
        return match.group(1)
    match = re.search(r"SHA:\s*([0-9a-f]{7})", body or "")
    if match:
        return match.group(1)
    return None


def gh_ref_pattern(gh_number: int) -> str:
    """Postgres regex matching `#<n>` only when not followed by another digit.

    Substring LIKE '%#1118%' also matches hex colors such as #111827 in
    unrelated issues, which silently suppressed routing of GitHub #1118
    (found 2026-09-03). Use an anchored regex for dedup and auto-close.
    """
    return f"#{gh_number}([^0-9]|$)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report decisions (create/close/skip/reopened) but write nothing "
        "— no PG writes, no state file changes",
    )
    args = parser.parse_args()
    dry = args.dry_run

    # Gather GitHub issues (never silently: each gh failure prints a stderr
    # WARNING from gh_issue_list)
    gh_failures = 0
    open_lists: list[list[dict]] = []
    closed_lists: list[list[dict]] = []
    for label in LABELS:
        issues, failures = gh_issue_list(label, "open")
        open_lists.append(issues)
        gh_failures += failures
        issues, failures = gh_issue_list(label, "closed", limit=5)
        closed_lists.append(issues)
        gh_failures += failures
    open_issues = merge_dedup(open_lists)
    closed_issues = merge_dedup(closed_lists)

    if gh_failures and gh_failures == 2 * len(LABELS):
        # Every gh call failed — the router is fully blind this tick. Exit
        # non-zero so the cron layer records the failure instead of a
        # look-alike SILENT success.
        print(
            f"CI Issue Router: ERROR: all {gh_failures} gh issue list calls "
            f"failed — routing skipped this tick (GitHub blind)",
            file=sys.stderr,
        )
        return 1

    if not open_issues and not closed_issues:
        # Silent: nothing to report
        return 0

    state = load_state()
    routed: dict = state.get("routed", {})
    closed_seen: list = state.get("closed", [])

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Serialize the whole read-allocate-insert tick so two overlapping runs
    # cannot double-issue NFM identifiers or double-create cards. Taken FIRST
    # (before any row locks) for a consistent lock order — no advisory↔row
    # deadlock cycle. Held until commit/rollback.
    if not dry:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))

    closed_pc = 0
    created_pc = 0
    skipped_pc = 0
    reopened: list[int] = []

    # Close Paperclip issues whose GitHub counterparts are closed
    for gi in closed_issues:
        gh_number = gi["number"]
        if str(gh_number) in closed_seen:
            continue  # state-file fast path: already auto-closed in a past tick
        pattern = gh_ref_pattern(gh_number)
        cur.execute(
            """SELECT id, identifier, status FROM issues
               WHERE project_id = %s
                 AND (title ~ %s OR description ~ %s)
                 AND status NOT IN ('done', 'cancelled')""",
            (PROJECT_ID, pattern, pattern),
        )
        rows = cur.fetchall()
        for issue_id, identifier, _ in rows:
            if not dry:
                cur.execute(
                    "UPDATE issues SET status = 'done', completed_at = NOW() WHERE id = %s",
                    (issue_id,),
                )
            closed_pc += 1
            print(f"  → would close {identifier} (GitHub #{gh_number} closed)"
                  if dry else f"  → closed {identifier} (GitHub #{gh_number} closed)")
        if rows and not dry:
            closed_seen.append(str(gh_number))

    # Create Paperclip issues for new GitHub CI failures
    for gi in open_issues:
        gh_number = gi["number"]
        gh_title = gi["title"]
        gh_body = gi.get("body", "") or ""

        # Defense 1: state file fast path
        if str(gh_number) in routed:
            skipped_pc += 1
            continue

        # Defense 2: PG existence regex (state file lost/cleared)
        pattern = gh_ref_pattern(gh_number)
        cur.execute(
            """SELECT id, status FROM issues
               WHERE project_id = %s
                 AND (title ~ %s OR description ~ %s)""",
            (PROJECT_ID, pattern, pattern),
        )
        existing = cur.fetchall()
        if existing:
            # Reopen detection: GitHub issue open again but our card is
            # terminal (auto-closed earlier). Surface it — never auto-reopen
            # (revival is gated; see dsh-adoption M3 revival_gate).
            if all(status in ("done", "cancelled") for _, status in existing):
                reopened.append(gh_number)
            skipped_pc += 1
            continue

        sha = extract_sha(gh_title, gh_body)
        is_deploy = "deploy" in gh_title.lower()
        priority = "high" if is_deploy else "medium"

        title = f"[SRE] GitHub #{gh_number}: {gh_title}"
        description = (
            f"GitHub CI issue: https://github.com/{REPO}/issues/{gh_number}\n\n"
            f"{gh_body[:1000]}\n\n"
            f"**Auto-routed to SRE Monitor by ci-issue-router cron.**\n"
            f"This issue will auto-close when the GitHub issue is closed."
        )

        if dry:
            created_pc += 1
            print(f"  → would create card: {title[:70]}")
            continue

        # (allocation is serialized by the advisory lock taken at tick start)
        # Get next issue number from identifier (global, not per-project — unique constraint)
        cur.execute(
            """SELECT identifier FROM issues
               WHERE identifier ~ '^NFM-[0-9]+$'""",
        )
        max_num = 0
        for (ident,) in cur.fetchall():
            match = re.search(r"NFM-(\d+)", ident or "")
            if match:
                max_num = max(max_num, int(match.group(1)))
        next_num = max_num + 1
        identifier = f"NFM-{next_num}"

        cur.execute(
            """INSERT INTO issues (
                company_id, project_id, title, description, status, priority,
                assignee_agent_id, created_at, updated_at,
                issue_number, identifier, work_mode, request_depth
            ) VALUES (
                %s, %s, %s, %s, 'todo', %s,
                %s, NOW(), NOW(),
                %s, %s, 'standard', 0
            )""",
            (
                COMPANY_ID,
                PROJECT_ID,
                title,
                description,
                priority,
                SRE_AGENT_ID,
                next_num,
                identifier,
            ),
        )
        created_pc += 1
        routed[str(gh_number)] = identifier

    if dry:
        conn.rollback()
        conn.close()
    else:
        conn.commit()
        conn.close()
        state["routed"] = routed
        state["closed"] = closed_seen
        save_state(state)

    # Report only if something happened
    if created_pc > 0 or closed_pc > 0 or reopened:
        parts = [f"created={created_pc}", f"closed={closed_pc}", f"skipped={skipped_pc}"]
        if dry:
            parts.append("dry-run")
        print("CI Issue Router: " + ", ".join(parts))
        if created_pc > 0 and not dry:
            for gi in open_issues:
                print(f"  → Routed GitHub #{gi['number']}: {gi['title'][:70]}")
        for n in reopened:
            print(
                f"  ⚠️ GitHub #{n} REOPENED after auto-close — Paperclip card is "
                f"terminal; revival is operator-gated, not auto-reopened"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
