#!/usr/bin/env python3
"""CI Issue Router: GitHub CI issues → Paperclip SRE Monitor.

Scans GitHub issues with ci-failure/deploy-failure labels and routes them
to Paperclip's SRE Monitor agent. Closes Paperclip issues when GitHub
issues are closed.

Runs as a Hermes cron job every 10 minutes. Silent when nothing to report.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import psycopg2

# --- Configuration ---
REPO = "Etoile04/nucpot"
COMPANY_ID = "ec7c0ded-5688-4002-8d0c-672597244875"
PROJECT_ID = "89da4552-72a9-4e0b-8938-400c90bcf743"
SRE_AGENT_ID = "2ee2415b-e43e-4806-888f-c231e60facaf"
LABELS = ["ci-failure", "deploy-failure"]

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 54329,
    "dbname": "paperclip",
    "user": "paperclip",
    "password": "paperclip",
}


def gh_issue_list(label: str, state: str, limit: int = 20) -> list[dict]:
    """Query GitHub issues via gh CLI."""
    try:
        result = subprocess.run(
            [
                "gh", "issue", "list",
                "--repo", REPO,
                "--label", label,
                "--state", state,
                "--json", "number,title,body,labels",
                "--limit", str(limit),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        return json.loads(result.stdout) if result.stdout.strip() else []
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        return []


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


def main() -> int:
    # Gather GitHub issues
    open_issues = merge_dedup(
        [gh_issue_list(label, "open") for label in LABELS]
    )
    closed_issues = merge_dedup(
        [gh_issue_list(label, "closed", limit=5) for label in LABELS]
    )

    if not open_issues and not closed_issues:
        # Silent: nothing to report
        return 0

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    closed_pc = 0
    created_pc = 0
    skipped_pc = 0

    # Close Paperclip issues whose GitHub counterparts are closed
    for gi in closed_issues:
        gh_number = gi["number"]
        cur.execute(
            """SELECT id, identifier, status FROM issues
               WHERE project_id = %s
                 AND (title LIKE %s OR description LIKE %s)
                 AND status NOT IN ('done', 'cancelled')""",
            (PROJECT_ID, f"%#{gh_number}%", f"%#{gh_number}%"),
        )
        for issue_id, identifier, _ in cur.fetchall():
            cur.execute(
                "UPDATE issues SET status = 'done', completed_at = NOW() WHERE id = %s",
                (issue_id,),
            )
            closed_pc += 1

    # Create Paperclip issues for new GitHub CI failures
    for gi in open_issues:
        gh_number = gi["number"]
        gh_title = gi["title"]
        gh_body = gi.get("body", "") or ""

        # Check if Paperclip issue already exists
        cur.execute(
            """SELECT id FROM issues
               WHERE project_id = %s
                 AND (title LIKE %s OR description LIKE %s)""",
            (PROJECT_ID, f"%#{gh_number}%", f"%#{gh_number}%"),
        )
        if cur.fetchone():
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
            (COMPANY_ID, PROJECT_ID, title, description, priority, SRE_AGENT_ID, next_num, identifier),
        )
        created_pc += 1

    conn.commit()
    conn.close()

    # Report only if something happened
    if created_pc > 0 or closed_pc > 0:
        print(f"CI Issue Router: created={created_pc}, closed={closed_pc}, skipped={skipped_pc}")
        if created_pc > 0:
            for gi in open_issues:
                print(f"  → Routed GitHub #{gi['number']}: {gi['title'][:70]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
