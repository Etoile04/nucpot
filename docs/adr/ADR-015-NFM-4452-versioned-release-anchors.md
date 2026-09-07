# ADR-015 — Versioned release anchors & deploy identity (NFM-4452)

| Field | Value |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-09-07 |
| **Author** | Hermes (本体专家), directed by 文杰 |
| **Scope** | Release/versioning semantics only — no change to merge workflow, CI gates, or deploy mechanics |

---

## 1. Context

The repo already has strong deploy *mechanics*: every main push deploys a
SHA-tagged image (`deploy_prod.sh`), rollback is one command
(`run-recovery.sh rollback --tag <sha>`), the G4a manifest records what is
live, and the G4b drift alarm diffs live state against it. What it lacked
was release *semantics*:

1. **No version anchor.** The last `v*` release tag (`v2026.618.0`, 2026-06-18)
   predates 1,938 commits. "Roll back to the previous good version" required
   manifest/CI archaeology instead of a `git describe` hop.
2. **Polluted tag namespace.** `canary/v2026.626.0-canary.*` (15 tags) and the
   `v2026.318~626` series point at Paperclip-upstream merge commits that are
   **not ancestors of main**. Any human or agent rolling back "by tag name"
   would target a commit that does not exist in this codebase's history.
3. **Deploy identity unobservable.** Neither the API nor web exposed the git
   SHA they run. The NFM-3835 class of incident (hot-patched container code
   passing every grep-style check) had no automatic detector on the
   "which code is live" axis.
4. **Tag-triggered duplicate deploys.** `on: push: tags: v*` re-ran this
   ~30 min workflow for the same SHA already deployed via main.

## 2. Decision

### §2 Release anchors (automated, per-deploy)

After `deploy-prod` **and** `smoke-test` succeed on a `main` push, the
`tag-released` job pushes a lightweight tag:

    released/<UTC YYYY.MMDD>-<sha8>      e.g. released/2026.0907-cce3bd79

- Best-effort **by design**: the tag is a convenience anchor layered on the
  system of record (G4a manifest + C6.1.1 deploy events). A failed push is a
  warning, never a red deploy.
- Host-side fallback: `scripts/tag_released.py` (stdlib-only) reconciles
  anchors from the manifest on any cron tick. Policies: live SHA must be an
  ancestor of `origin/main` (else skip — a rollback is in progress); never
  move or overwrite a tag (conflict = exit 4 = alarm); push failure = retry
  next tick (exit 5).
- Discovery: `git describe --match 'released/*' --abbrev=8 <ref>` returns the
  nearest anchor; rollback = `run-recovery.sh rollback --tag <that sha>`.

### §3 Batch release tags (manual, low-frequency)

When a batch of features is verified in prod, an operator cuts an annotated
tag `v2026.MM.N` whose message IS the release note: included NFM-#### list,
notable behavior changes, and the **migration list** (flagging destructive
ones). No CHANGELOG file — a second mutable doc always drifts; the tag
annotation is immutable and travels with the code. Batch tags carry **no
pipeline semantics** (see §5); cutting one does not deploy anything.

### §4 Deploy identity (observable + asserted)

- `docker/prod-api.Dockerfile` gains `ARG GIT_SHA` / `ENV NFM_GIT_SHA`;
  `deploy_prod.sh` passes `--build-arg GIT_SHA=${DEPLOY_SHA}`.
- `GET /api/v1/health` adds **`deploy_sha`** (nullable — `null` on images
  built before ADR-015 or outside the sanctioned path). Additive field;
  existing consumers of the five fixed keys are unaffected.
- CI `smoke-test` gains an identity assertion step: compare
  `/api/v1/health` → `deploy_sha` against `github.sha`. Missing field =
  inconclusive (warning, exit 0) until the GIT_SHA image is live; mismatch =
  file `[IDENTITY-DRIFT]` issue + fail the step. The G4b drift checker and
  manifest remain the hard gates for container state; this assertion covers
  the code-identity axis they do not see.

### §5 Tag namespace hygiene

- `on.push.tags` trigger **removed** — version tags no longer trigger
  deploys (main push remains the only automatic deploy path).
- Legacy tags (`canary/*`, `v2026.318~626`, `v0.1.0-ontofuel` era) are
  declared **non-anchor**: most point at non-main commits and MUST NOT be
  used as rollback targets. They are left in place (deleting shared tags
  breaks other clones) and excluded by construction: anchors live in the
  dedicated `released/*` namespace, and `git describe --match 'released/*'`
  never sees them.

## 6. Migration safety invariant (A4)

Database schema is **forward-fix only**:

1. Destructive migrations ship in expand-contract form (expand → deploy →
   contract after the new code is live), never as a single dropping step.
2. Destructive migrations create backup tables (`*_backup_<rev>`); the
   downgrade path restores from them (070 precedent) but downgrade is
   **reserved for the migration author's local/CI verification**, never a
   prod rollback tool.
3. Prod rollback = code rollback (SHA-pinned images) + schema forward
   compatibility. A release note that lists a destructive migration must
   confirm the shipped code tolerates the previous schema shape.
4. Lag detection stays with the existing runbook procedure
   (`alembic_version` vs `alembic heads` — prod-deploy.md §7).

## 7. Alternatives considered

- **Release branches (gitflow)** — rejected: too heavy for a
  single-operator + agents high-frequency-merge model; re-introduces branch
  topology after the 2026-09-02 rebase incident (`backup/cf42ebb7d-pre-rebase`);
  all existing guardrails (G4a/G4b, rollback, manifest) are built around
  "main + SHA".
- **CHANGELOG.md maintained per merge** — rejected: agents merge 10-20 PRs/day;
  a mutable second doc invites the same drift the runbook-vs-workflow split
  caused (prod-deploy.md header: "anything that drifts from the workflow is a
  bug"). Tag annotations are immutable.
- **Semantic versioning enforced on every deploy** — rejected: anchors are
  automated per-deploy, so version numbers would just re-encode dates; batch
  `v2026.MM.N` stays calendar-based and human-cut.

## 8. Consequences

- Rollback target discovery: archaeology → `git describe --match 'released/*'`.
- First anchor appears on the first green deploy after this change lands.
- The `notify` job's `refs/tags/v` branch is dead code by design (tags no
  longer trigger the workflow); left in place, costs one `if` evaluation.
- `deploy_sha == null` in `/health` after this deploy means the image was
  built by a deploy path that skipped §4 — investigate as an unsanctioned
  build, do not dismiss.
