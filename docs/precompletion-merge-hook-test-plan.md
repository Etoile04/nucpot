# PreCompletionMerge Hook — Test Plan T0–T5

**Issue:** [NFM-3862](/NFM/issues/NFM-3862) (sibling of [NFM-3855](/NFM/issues/NFM-3855))
**Design doc:** [NFM-3853#document-precompletion-merge-hook](/NFM/issues/NFM-3853#document-precompletion-merge-hook)
**Hook implementation:** [NFM-3857](/NFM/issues/NFM-3857) — `api-middleware`
**ADK runtime mirror:** [NFM-3858](/NFM/issues/NFM-3858)
**Metric instrumentation:** [NFM-3859](/NFM/issues/NFM-3859)
**Backfill SQL:** [NFM-3860](/NFM/issues/NFM-3860)
**Branch protection:** [NFM-3861](/NFM/issues/NFM-3861)
**Parent epic:** [NFM-3853](/NFM/issues/NFM-3853)
**RCA:** [NFM-3850](/NFM/issues/NFM-3850)
**Ghost-merge recovery:** [NFM-3738](/NFM/issues/NFM-3738)
**Integration task:** [NFM-3863](/NFM/issues/NFM-3863)

This test plan covers every behavioral guarantee the `PreCompletionMerge` hook must
hold before it ships behind the `precompletion_merge_hook_enabled` feature flag in
production. Each scenario has a deterministic pass/fail signal, references the
blocking sibling (`api-middleware` per the constraint in [NFM-3862](/NFM/issues/NFM-3862)),
and ties back to an acceptance-criteria line in [NFM-3855](/NFM/issues/NFM-3855).

> **blockedBy:** every test in this plan depends on the `api-middleware` sibling
> ([NFM-3857](/NFM/issues/NFM-3857)) landing first. The hook surface (gate
> pseudo-code, 422 error envelope, bypass semantics) is locked at the design doc;
> the test plan is intentionally implementation-light so it does not drift from
> the API contract that NFM-3857 is shipping.

---

## Gate surface (referenced by every test)

The hook fires on `PATCH /api/issues/{id}` only when:

1. `payload.status == "done"` (terminal transition), AND
2. `is_merge_kind(issue)` returns `true` (title matches
   `^merge\s` AND (title contains `to (origin/)?main` OR description contains
   `gh pr merge`)), AND
3. The feature flag `precompletion_merge_hook_enabled` is `true` for the
   environment.

When fired, the hook runs:

```text
git -C <execution_workspace> merge-base --is-ancestor origin/<branch> origin/main
```

…and returns the structured 422 envelope below on non-zero exit.

### Error envelope

```json
HTTP/1.1 422 Unprocessable Entity
Content-Type: application/json

{
  "error": "merge_kind_unmerged_branch",
  "branch": "<feature_branch>",
  "evidence_command": "git merge-base --is-ancestor origin/<branch> origin/main",
  "hint": "Run the merge step or call ghost-merge recovery on NFM-3738.",
  "fix_path": "/NFM/issues/NFM-3738"
}
```

Three sibling error codes share the same envelope:

| `error` code                  | Cause                                                                  |
| ----------------------------- | ---------------------------------------------------------------------- |
| `merge_kind_unmerged_branch`  | `git merge-base --is-ancestor` returned non-zero — branch tip not on `main` |
| `merge_kind_missing_branch`   | `extract_feature_branch(issue)` returned `None` (title regex did not match) |
| `merge_kind_no_workspace`     | `resolve_execution_workspace(issue)` returned `None`                    |

### Bypass envelope (system actor only)

When `actor.type == "system"`, the hook **does not** gate the PATCH. It emits
`paperclip_precompletion_bypass_total{reason="system_actor"}` and passes the
request through unchanged. This is the only documented bypass.

---

## Test matrix

| ID | Scenario                                                       | Expected outcome                                                                  | Metric expectation                              | AC ref                                                  |
| -- | -------------------------------------------------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------- |
| T0 | Merge-kind issue, non-ancestor branch, agent actor             | `422 merge_kind_unmerged_branch` with `branch`/`evidence_command`/`hint`/`fix_path` | `paperclip_precompletion_merge_rejected_total` +1 | [NFM-3855 AC#1](/NFM/issues/NFM-3855)                |
| T1 | T0 fixture, but `actor.type == "system"`                       | PATCH succeeds (pass-through)                                                    | `paperclip_precompletion_bypass_total` +1         | [NFM-3855 AC#4](/NFM/issues/NFM-3855)                |
| T2 | Non-merge-kind issue (e.g. `[NFM-X] ship feature Y`) marked done | PATCH succeeds (pass-through), no 422                                            | neither metric                                  | [NFM-3855 AC#5](/NFM/issues/NFM-3855)                |
| T3 | Merge-kind issue, branch tip IS ancestor of `origin/main`      | PATCH succeeds (pass-through)                                                    | neither metric                                  | [NFM-3855 AC#5 (mirror)](/NFM/issues/NFM-3855)        |
| T4 | Cron backfill run on the NFM-3691 historical cluster           | `0` NEW phantom issues outside the NFM-3850 cluster; NFM-3738 stays unflagged    | ADR-010 D2 cron signature unchanged             | [NFM-3855 AC#8](/NFM/issues/NFM-3855), [NFM-3860](/NFM/issues/NFM-3860) |
| T5 | Feature flag `precompletion_merge_hook_enabled = false`        | Hook is a no-op for ALL of T0–T4                                                  | neither metric                                  | [NFM-3855 AC#7](/NFM/issues/NFM-3855)                |

Each row is expanded into a full scenario below. All scenarios assume the
`api-middleware` sibling ([NFM-3857](/NFM/issues/NFM-3857)) has shipped and is
the only code path that emits the gate — the ADK runtime mirror
([NFM-3858](/NFM/issues/NFM-3858)) is exercised by a parallel set of
runtime-mirror tests out of scope for this document.

---

## T0 — Synthetic merge-kind issue with non-ancestor branch

**Goal:** prove the hook refuses a `done` PATCH when the branch tip is not on
`main`.

**Fixture:**

```yaml
issue:
  identifier: NFM-TEST-T0-merge-unmerged
  title: "Merge NFM-T0-feature-branch to origin/main"
  description: "gh pr merge --auto"
  executionWorkspace:
    path: <a tmpdir git repo containing the NFM-T0-feature-branch ref but
           NOT merged into main>
  status: in_progress
  assigneeAgentId: <a normal agent UUID, type=agent>

patch:
  url: PATCH /api/issues/{id}
  body: { "status": "done" }
  actor: { "type": "agent", "id": "<agent-uuid>" }
```

**Setup commands (fixture builder, run once per CI lane):**

```bash
TMP=$(mktemp -d)
git -C "$TMP" init --bare main.git
git -C "$TMP/main.git" symbolic-ref HEAD refs/heads/main
git clone "$TMP/main.git" "$TMP/work"
git -C "$TMP/work" commit --allow-empty -m "root on main"
git -C "$TMP/work" checkout -b NFM-T0-feature-branch
git -C "$TMP/work" commit --allow-empty -m "feature work"
git -C "$TMP/work" push -u origin NFM-T0-feature-branch
# Crucially: do NOT merge to main.
```

**Asserted API response:**

```text
HTTP/1.1 422 Unprocessable Entity
Content-Type: application/json

{
  "error": "merge_kind_unmerged_branch",
  "branch": "NFM-T0-feature-branch",
  "evidence_command": "git merge-base --is-ancestor origin/NFM-T0-feature-branch origin/main",
  "hint": "Run the merge step or call ghost-merge recovery on NFM-3738.",
  "fix_path": "/NFM/issues/NFM-3738"
}
```

**Asserted side effects:**

- `paperclip_precompletion_merge_rejected_total{branch_prefix="NFM-"}` incremented by 1.
- `paperclip_precompletion_bypass_total` NOT incremented.
- Issue status remains `in_progress` (the PATCH was rejected before commit).
- Issue `comment_count` gains exactly 1 — the structured 422 trace appended as
  an audit comment by the hook middleware.

**Asserted fixture invariants:**

- The branch `NFM-T0-feature-branch` exists in the workspace.
- The commit at the branch tip is NOT reachable from `origin/main` per
  `git merge-base --is-ancestor origin/NFM-T0-feature-branch origin/main` (exit 1).

**Pass criterion:** response status, body, and metric delta all match.

---

## T1 — Same fixture as T0, but actor is `system`

**Goal:** prove the system-actor bypass emits the bypass metric but does NOT
gate the PATCH. This is the routine / recovery-script escape hatch.

**Fixture:** identical to T0.

**Patch:**

```text
PATCH /api/issues/{id}
Content-Type: application/json

{ "status": "done" }
```

with the actor header `X-Paperclip-Actor-Type: system` (the API also accepts
`actor.type: "system"` in the body; both must be honored).

**Asserted API response:**

```text
HTTP/1.1 200 OK
Content-Type: application/json

{ "id": "<issue-id>", "status": "done", ... }
```

**Asserted side effects:**

- `paperclip_precompletion_bypass_total{reason="system_actor"}` incremented by 1.
- `paperclip_precompletion_merge_rejected_total` NOT incremented.
- Issue status transitions `in_progress` → `done`.
- The audit trail MUST include a `[system-bypass]` tag in the bypass metric's
  labels so dashboards can split out deliberate cron merges from accidental
  bypass attempts.

**Pass criterion:** response status, metric delta, and status transition all
match.

> **Anti-pattern guard:** if the hook bypasses for any actor type other than
> `system`, this test fails. There is no documented bypass for human or agent
> actors. The only legitimate `system` callers at this writing are
> ghost-merge recovery ([NFM-3738](/NFM/issues/NFM-3738)) and the ADR-010 D2
> backfill cron.

---

## T2 — Non-merge-kind issue marked `done`

**Goal:** prove the hook does not interfere with the normal done-PATCH path on
issues that don't look like merges.

**Fixture:**

```yaml
issue:
  identifier: NFM-TEST-T2-not-merge
  title: "[NFM-T2] ship async-job refactor"
  description: "Refactor the job runner; ship via standard review pipeline."
  executionWorkspace: <any valid workspace>
  status: in_progress
  assigneeAgentId: <a normal agent UUID, type=agent>
```

**Patch:**

```text
PATCH /api/issues/{id}
{ "status": "done" }
```

**Asserted API response:** `200 OK`, status transitions to `done`.

**Asserted side effects:**

- Neither `paperclip_precompletion_*` metric is incremented.
- Hook code path is short-circuited at the `is_merge_kind` check.
- `comment_count` gains the normal single "marked done by …" comment — no
  422-trace audit comment is appended because the hook did not fire.

**Negative test (regression guard):** if the title or description changes to
match the `is_merge_kind` heuristic (e.g. `gh pr merge` is added to the body),
this test MUST flip to T0's outcome. Run a parameterized variant where the
description is mutated to include `gh pr merge` and confirm the hook now
rejects.

**Pass criterion:** pass-through + zero metric delta for the canonical case,
rejection for the parameterized `gh pr merge` description variant.

---

## T3 — Merge-kind issue with branch already ancestor of `main`

**Goal:** prove the hook passes through legitimate merges without false
positives.

**Fixture:**

```bash
TMP=$(mktemp -d)
git -C "$TMP" init --bare main.git
git -C "$TMP/main.git" symbolic-ref HEAD refs/heads/main
git clone "$TMP/main.git" "$TMP/work"
git -C "$TMP/work" commit --allow-empty -m "root"
git -C "$TMP/work" checkout -b NFM-T3-feature-branch
git -C "$TMP/work" commit --allow-empty -m "feature"
git -C "$TMP/work" push -u origin NFM-T3-feature-branch
# Actually merge it.
git -C "$TMP/work" checkout main
git -C "$TMP/work" merge --no-ff NFM-T3-feature-branch -m "Merge NFM-T3-feature-branch"
git -C "$TMP/work" push origin main
```

```yaml
issue:
  identifier: NFM-TEST-T3-already-merged
  title: "Merge NFM-T3-feature-branch to origin/main"
  description: "gh pr merge --auto"
  executionWorkspace:
    path: <the workspace above; NFM-T3-feature-branch IS an ancestor of origin/main>
  status: in_progress
```

**Asserted API response:** `200 OK`, status transitions to `done`.

**Asserted side effects:**

- Neither `paperclip_precompletion_*` metric is incremented.
- `git merge-base --is-ancestor origin/NFM-T3-feature-branch origin/main` exits 0
  inside the workspace — the test fixture MUST verify this directly before
  asserting the API response.

**Pass criterion:** response status and zero metric delta.

> **Negative test:** the same fixture with the merge commit reverted (`git
> revert -m 1 HEAD` followed by `git push origin main`) MUST flip this test to
> T0's outcome. This catches regressions where the hook confuses "branch exists
> on the remote" with "branch tip is an ancestor of main" — that confusion is
> one of the four RCA hypotheses in [NFM-3853](/NFM/issues/NFM-3853).

---

## T4 — Cron backfill on the NFM-3691 historical cluster

**Goal:** prove the backfill query in [NFM-3860](/NFM/issues/NFM-3860) does
not surface NEW phantoms outside the NFM-3850 cluster, and that NFM-3738 stays
unflagged.

**Inputs:**

- The SQL defined in [NFM-3860](/NFM/issues/NFM-3860), ported to the platform
  query layer.
- The ADR-010 D2 cron signature (commit `9aa9fff3`).
- The NFM-3691 historical cluster:
  NFM-3727, NFM-3729, NFM-3732, NFM-3733, NFM-3734, NFM-3735, NFM-3736,
  NFM-3737 (the 7 phantom siblings of [NFM-3850](/NFM/issues/NFM-3850)).
- The NFM-3850 cluster (treated as the "known good" ground truth): the 7
  identifiers above.
- The whitelist: [NFM-3738](/NFM/issues/NFM-3738) (the truthful in-flight merge
  that MUST NOT be flagged).

**Run procedure:**

1. Take a snapshot of `issues` matching the backfill WHERE clause BEFORE the
   cron run.
2. Execute the cron job once against the live database.
3. Take the same snapshot AFTER.
4. Diff the two snapshots.

**Asserted outcomes:**

- The diff contains exactly the 7 historical identifiers in the NFM-3850
  cluster. **Zero NEW identifiers** appear that are not already in the
  NFM-3850 cluster.
- [NFM-3738](/NFM/issues/NFM-3738) is NOT in the diff, despite matching the
  title pattern — the whitelist MUST take precedence over the
  comment-count heuristic. A test that fails to honor the whitelist is a
  false positive in the other direction and equally bad.
- The cron audit row is written with `paperclip_precompletion_backfill_run_total`
  incremented by 1 and labels `result="clean"` (no NEW phantoms) or
  `result="new_phantoms"` if the assertion fails.

**Negative test (regression guard):** if the WHERE clause is loosened (e.g.
comment_count > 0 instead of `= 0`), the test MUST surface new matches that
are NOT in the NFM-3850 cluster. This is the positive control that proves the
test would catch a real regression.

**Pass criterion:** diff = NFM-3850 cluster exactly; NFM-3738 never appears.

---

## T5 — Feature flag default-off

**Goal:** prove that with `precompletion_merge_hook_enabled = false`, the hook
is a complete no-op. This guards against the rollout: staging must enable the
flag explicitly; production must enable it after the 24h soak.

**Fixture:** identical to T0 (non-ancestor merge-kind branch). The flag is the
only difference.

**Setup:**

```bash
# Default OFF
export PRECOMPLETION_MERGE_HOOK_ENABLED=false

# Issue the same T0 PATCH
curl -X PATCH "$PAPERCLIP_API_URL/api/issues/$ID" \
  -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"status":"done"}'
```

**Asserted API response:** `200 OK`, status transitions to `done` — identical
to the no-hook baseline.

**Asserted side effects:**

- Neither `paperclip_precompletion_*` metric is incremented.
- Hook code path is short-circuited at the feature-flag check, BEFORE the
  `is_merge_kind` heuristic runs.
- No `comment_count` audit comment is appended.

**Pass criterion:** pass-through + zero metric delta + zero 422-trace audit
comments.

**Flag-rollout scenarios (covered by parameterized variants of T5):**

| Variant                                        | Expected behavior                                     |
| ---------------------------------------------- | ----------------------------------------------------- |
| `precompletion_merge_hook_enabled = false`     | Hook is a no-op for ALL of T0–T4                      |
| `precompletion_merge_hook_enabled = true`      | Hook fires; T0–T4 outcomes apply                      |
| Flag absent from environment configuration     | Treated as `false` (default-off is the safe default)   |
| Flag malformed (e.g. `enabled` typo)           | Treated as `false` (fail-closed)                      |

The "flag absent" and "flag malformed" rows are explicit because the hook
controls a destructive gate. Fail-open is a regression; fail-closed is the
correct default.

---

## Cross-test invariants (asserted once per CI lane)

These invariants are checked in a `pre_completion_merge_hook_test_setup` fixture
shared by T0–T5:

1. The `api-middleware` sibling ([NFM-3857](/NFM/issues/NFM-3857)) has shipped;
   otherwise the test lane is **blocked**, not failing — the test plan does
   not exercise code that doesn't exist.
2. The feature flag `precompletion_merge_hook_enabled` is queryable from the
   test harness (env var + runtime config) and the test lane is started in the
   `false` state (T5 first, then T0–T4 with the flag flipped).
3. The metrics `paperclip_precompletion_merge_rejected_total` and
   `paperclip_precompletion_bypass_total` are exposed by the metrics endpoint
   and increment monotonically across runs.
4. The 422 envelope field set (`error`, `branch`, `evidence_command`, `hint`,
   `fix_path`) is the union of every test's expected body; any test that drops
   a field fails the cross-test invariant.

---

## How to invoke ghost-merge recovery (T1 escape hatch)

When a legitimate merge is in flight and the agent needs to bypass the hook
without lying about the actor type, call the ghost-merge recovery flow on
[NFM-3738](/NFM/issues/NFM-3738):

1. Open a comment on the stuck merge-kind issue tagged `[GHOST-MERGE]` with:
   - The branch name (`origin/<branch>`).
   - The expected merge commit SHA (or `null` if the branch hasn't been merged
     yet — that's the whole point).
   - The actor who will execute the actual `gh pr merge` (usually the same
     agent that wrote the issue).
2. Wait for the recovery signal on [NFM-3738](/NFM/issues/NFM-3738). The
   recovery owner ([CTO agent](/PAP/agents/cto) or the [Release
   Engineer](/PAP/agents/release-engineer)) flips the issue's status through
   the system-actor path, which emits the `paperclip_precompletion_bypass_total`
   metric with `reason="system_actor"`.
3. The original issue's PATCH then succeeds, and the audit trail is intact.

This is the only documented bypass. Any other "the hook is blocking my real
merge" complaint routes through [NFM-3738](/NFM/issues/NFM-3738), never through
ad-hoc overrides.

---

## Out of scope (deferred to sibling issues)

| Capability                                  | Owner                                  |
| ------------------------------------------- | -------------------------------------- |
| ADK runtime middleware mirror tests         | [NFM-3858](/NFM/issues/NFM-3858)       |
| Metric dashboard wiring                     | [NFM-3859](/NFM/issues/NFM-3859)       |
| Cron integration / scheduling               | [NFM-3860](/NFM/issues/NFM-3860)       |
| GitHub branch protection status checks      | [NFM-3861](/NFM/issues/NFM-3861)       |
| End-to-end Feature-level review             | [NFM-3863](/NFM/issues/NFM-3863)       |

If a test reveals that the gate is too strict or too lax, the remediation path
is to amend the design document at [NFM-3853#document-precompletion-merge-hook](/NFM/issues/NFM-3853#document-precompletion-merge-hook)
before changing the implementation in [NFM-3857](/NFM/issues/NFM-3857).