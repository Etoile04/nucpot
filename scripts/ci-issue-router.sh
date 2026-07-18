#!/usr/bin/env bash
# CI Issue Router: GitHub CI issues → Paperclip SRE Monitor
# Scans GitHub issues with ci-failure/deploy-failure labels and routes them to Paperclip
# Closes Paperclip issues when GitHub issues are closed.
set -euo pipefail

REPO="Etoile04/nucpot"
COMPANY_ID="ec7c0ded-5688-4002-8d0c-672597244875"
PROJECT_ID="89da4552-72a9-4e0b-8938-400c90bcf743"
SRE_AGENT_ID="2ee2415b-e43e-4806-888f-c231e60facaf"
DB_HOST="127.0.0.1"
DB_PORT="54329"
DB_NAME="paperclip"
DB_USER="paperclip"
DB_PASS="paperclip"

# Get open GitHub issues with CI/deploy failure labels (query each label separately, then merge)
CI_OPEN=$(gh issue list --repo "$REPO" --label ci-failure --state open --json number,title,body,labels --limit 20 2>/dev/null || echo "[]")
DEPLOY_OPEN=$(gh issue list --repo "$REPO" --label deploy-failure --state open --json number,title,body,labels --limit 20 2>/dev/null || echo "[]")
OPEN_ISSUES_JSON=$(python3 -c "
import json, sys
ci = json.loads('''$CI_OPEN''')
deploy = json.loads('''$DEPLOY_OPEN''')
seen = set()
merged = []
for i in ci + deploy:
    if i['number'] not in seen:
        merged.append(i)
        seen.add(i['number'])
print(json.dumps(merged))
")

# Get recently closed GitHub issues (for closing Paperclip issues)
CI_CLOSED=$(gh issue list --repo "$REPO" --label ci-failure --state closed --json number,title --limit 5 2>/dev/null || echo "[]")
DEPLOY_CLOSED=$(gh issue list --repo "$REPO" --label deploy-failure --state closed --json number,title --limit 5 2>/dev/null || echo "[]")
CLOSED_ISSUES_JSON=$(python3 -c "
import json
ci = json.loads('''$CI_CLOSED''')
deploy = json.loads('''$DEPLOY_CLOSED''')
seen = set()
merged = []
for i in ci + deploy:
    if i['number'] not in seen:
        merged.append(i)
        seen.add(i['number'])
print(json.dumps(merged))
")

echo "Open GitHub CI issues: $(echo "$OPEN_ISSUES_JSON" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))')"
echo "Recently closed GitHub CI issues: $(echo "$CLOSED_ISSUES_JSON" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))')"

# Process in Python with DB access
env -u PYTHONPATH python3 << PYEOF
import json, sys, re, psycopg2
from datetime import datetime, timezone

DB_HOST = "$DB_HOST"
DB_PORT = int("$DB_PORT")
DB_NAME = "$DB_NAME"
DB_USER = "$DB_USER"
DB_PASS = "$DB_PASS"
COMPANY_ID = "$COMPANY_ID"
PROJECT_ID = "$PROJECT_ID"
SRE_AGENT_ID = "$SRE_AGENT_ID"

open_issues = json.loads('''$OPEN_ISSUES_JSON''')
closed_issues = json.loads('''$CLOSED_ISSUES_JSON''')

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
cur = conn.cursor()

# Close Paperclip issues whose GitHub counterparts are closed
for gi in closed_issues:
    gh_number = gi["number"]
    gh_title = gi["title"]
    # Find Paperclip issue referencing this GitHub issue number
    cur.execute("""
        SELECT id, identifier, status FROM issues
        WHERE project_id = %s
          AND (title LIKE %s OR description LIKE %s)
          AND status NOT IN ('done', 'cancelled')
    """, (PROJECT_ID, f"%#{gh_number}%", f"%#{gh_number}%"))
    rows = cur.fetchall()
    for row in rows:
        issue_id, identifier, status = row
        cur.execute("""
            UPDATE issues SET status = 'done', completed_at = NOW()
            WHERE id = %s
        """, (issue_id,))
        print(f"  Closed Paperclip {identifier} (GitHub #{gh_number} was closed)")

created = 0
skipped = 0
for gi in open_issues:
    gh_number = gi["number"]
    gh_title = gi["title"]
    gh_body = gi.get("body", "") or ""

    # Extract SHA from title (format: ... (SHA: abc1234) or ...abc1234))
    sha_match = re.search(r'\(([0-9a-f]{7})\)', gh_title)
    if not sha_match:
        sha_match = re.search(r'SHA:\s*([0-9a-f]{7})', gh_body)
    sha = sha_match.group(1) if sha_match else None

    # Check if Paperclip issue already exists for this GitHub issue
    cur.execute("""
        SELECT id, identifier, status FROM issues
        WHERE project_id = %s
          AND (title LIKE %s OR description LIKE %s)
    """, (PROJECT_ID, f"%{gh_number}%", f"%{gh_number}%"))
    existing = cur.fetchone()

    if existing:
        skipped += 1
        continue

    # Determine priority
    is_deploy = "deploy" in gh_title.lower()
    priority = "high" if is_deploy else "medium"

    # Create Paperclip issue
    title = f"[SRE] GitHub #{gh_number}: {gh_title}"
    description = f"""GitHub CI issue: https://github.com/Etoile04/nucpot/issues/{gh_number}

{gh_body[:1000]}

**Auto-routed to SRE Monitor by ci-issue-router cron.**
This issue will auto-close when the GitHub issue is closed."""

    cur.execute("""
        INSERT INTO issues (
            company_id, project_id, title, description, status, priority,
            assignee_agent_id, created_by_user_id, created_at, updated_at,
            issue_number, identifier, work_mode, request_depth
        )
        VALUES (
            %s, %s, %s, %s, 'todo', %s,
            %s, NULL, NOW(), NOW(),
            0, 'SRE-AUTO', 'standard', 0
        )
        RETURNING id
    """, (COMPANY_ID, PROJECT_ID, title, description, priority, SRE_AGENT_ID))

    issue_id = cur.fetchone()[0]

    # Generate proper identifier (NFM-xxxx)
    cur.execute("""
        SELECT COALESCE(MAX(issue_number), 0) + 1 FROM issues
        WHERE project_id = %s AND issue_number > 0
    """, (PROJECT_ID,))
    next_num = cur.fetchone()[0]
    identifier = f"NFM-{next_num}"
    cur.execute("""
        UPDATE issues SET issue_number = %s, identifier = %s WHERE id = %s
    """, (next_num, identifier, issue_id))

    created += 1
    print(f"  Created {identifier}: {gh_title[:60]}")

conn.commit()
conn.close()

print(f"\nSummary: created={created}, skipped={skipped}")
PYEOF
