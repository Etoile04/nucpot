---
name: post-merge-ci-recovery
description: >-
  MANDATORY post-merge verification checklist. Prevents the #1 CI failure
  pattern: unresolved git conflict markers, alembic dual-head, stale format
  violations, and production container outages. Run after EVERY merge to main.
---

# Post-Merge CI Recovery Checklist

After merging ANY branch to main, run these 5 checks in order. Each takes
<30 seconds. Skipping any check risks cascading CI failures that take hours
to diagnose.

## When to Run

- After `git merge` + `git push origin main`
- After `gh pr merge`
- After any code lands on main via any path

## The 5 Checks

### 1. Git Conflict Markers (CRITICAL — 2 min to fix vs 2 hrs to diagnose)

```bash
git grep -l '<<<<<<< HEAD' -- 'src/' 'apps/' 'tests/' '*.py' '*.ts' '*.tsx'
```

**If any file appears:** The merge left unresolved conflicts. A single
`<<<<<<< HEAD` in a Python file causes `SyntaxError` at import time →
ALL backend tests fail with `ImportError` cascade.

**Fix:** Manually resolve the conflict in each file, keeping both sides'
changes where appropriate (especially decorator stacking on auth endpoints).

### 2. Alembic Single-Head

```bash
cd apps/api && uv run alembic heads | grep -c '(head)'
```

**Must return `1`.** If `2+`: two migration files share the same `revision`
ID. Rename the newer one to the next sequential number and chain it:

```python
# File: 015_add_user_fields.py (was 014)
revision: str = "015"
down_revision: str = "014"  # chain after the existing 014
```

Also verify NO migration contains manual `UPDATE alembic_version` — Alembic
manages the version table automatically.

### 3. Lint + Format + Type Check

```bash
cd apps/api && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
cd apps/web && npx tsc --noEmit
```

**Why:** Path-triggered CI workflows (Batch 2, Batch 3) check files that
the main CI may not cover. A pre-existing `ruff format` violation in
`test_phase_gate.py` can lie dormant for weeks until a commit touches a
monitored path.

**Fix:** `uv run ruff format <file>` — never add empty whitespace to
trigger workflows, always make a genuine fix.

### 4. Production Health

```bash
docker ps --format '{{.Names}} {{.Status}}' | grep nucpot-prod
curl -sI https://nucpot.dpdns.org | head -1
curl -s https://verify.nucpot.dpdns.org/api/v1/health
```

**If 502:** All containers are likely stopped. Restart:
```bash
cd ~/Projects/nucpot
docker compose -f docker-compose.prod.yml --env-file docker/.env.prod up -d --build
```

**If API still 502 after restart:** CF Tunnel port mismatch.
```bash
tail /Library/Logs/cloudflared-verify.log | grep "connection refused"
# Shows: originService=http://localhost:8002 → tells you the expected port
```
The tunnel ingress may expect a different port than the CI health check.
Expose both: `"8001:8000"` and `"8002:8000"` in docker-compose.

### 5. All CI Workflows Green

```bash
gh run list --limit 8 --json name,conclusion --jq '.[] | select(.conclusion=="failure") | .name'
```

**If any workflow shows failure:**
1. Check if it's stale (from before your merge) — compare timestamps
2. Path-triggered workflows may not re-run automatically
3. For stale failures: fix the actual issue in a monitored file (don't
   add whitespace) to trigger a fresh run

## Common Post-Auth-Unification Issues

If the merge involves auth changes (cookie auth, new auth dependencies):

- **E2E tests need cookie injection:** Specs navigating to `/admin/*` or
  `/review/*` need `access_token` + `blog_admin_token` cookies in
  `beforeEach`. Use `process.env.BASE_URL` for domain (NOT `page.url()`
  which returns `about:blank` before first navigation).
- **Backend tests get 401:** Add `autouse` fixture overriding auth
  dependencies. Use `@pytest.mark.no_auto_auth` on tests that assert 401.
- **E2E `networkidle` timeout:** Production pages with persistent
  connections never reach networkidle. Use `domcontentloaded` +
  `waitForTimeout(2000)`.
- **Login form HTML5 validation:** `type="email"` silently blocks
  non-email values. Use `test@example.com` in E2E tests.
- **Ant Design selector drift:** `.ant-segmented` may not exist in all
  versions. Use `getByText()` instead of CSS class selectors.

## Reference

Full incident report and fix sequence: see Hermes skill
`self-hosted-ci` → `references/nucpot-auth-unification-ci-recovery.md`
