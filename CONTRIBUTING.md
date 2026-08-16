# Contributing to the Nuclear Fuel Materials Database (nucpot)

Welcome. This file is the on-disk entry point for humans **and** agents opening the
repo. The rule below is mandatory and is enforced by CI. It is also restated in
`AGENTS.md` at the repo root so coding agents pick it up on context load.

For the full rationale, scope, and the KR-1 re-baseline discussion, see
[`docs/architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md`](docs/architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md).

---

## Status (as of NFM-2204)

This section tracks what is live right now versus what is still in flight, so
contributors do not assume a control exists when it does not.

| Control                            | Where it lives                                | Status (2026-07-31)                                                                                                |
| ---------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| **CI gate** on `pull_request` + `push` | `.github/workflows/commit-ref-gate.yml` ([NFM-2085](/NFM/issues/NFM-2085), commit `6fce970f`) | **Live on `main`.** A non-compliant PR or direct push (missing `NFM-###` and missing `[no-issue]`) will be red. The required-status context is named *Validate every non-merge commit subject in PR range*. Note: `enforce_admins` is still `false`, so an admin direct-push can bypass the status check — see NFM-2204/R1. |
| **Local `commit-msg` hook**        | `.githooks/commit-msg` ([NFM-2084](/NFM/issues/NFM-2084), commit `d58e2823`, PR #520)             | **Live on `main`.** Shipped in PR #520. `git config core.hooksPath .githooks` activates it immediately.              |
| **KR-2 metric** (`commit_efficiency.py`) | `scripts/okr/commit_efficiency.py` (`_ISSUE_REF_PATTERN`)                              | **Live.** Counts `[no-issue]` as structural waste per ADR-NFM-2081 §D2. Revision basis pinned to `origin/main --max-parents=1` (NFM-2204/R2). |

**Practical consequence today:** both controls are live on `main`. The CI gate
catches non-compliant PRs and direct pushes; the local hook provides instant
feedback at commit time once configured.

---

## The rule (one line)

Every commit **subject** on every branch merged into `main` must contain **either**:

- an `NFM-###` issue reference (e.g. `NFM-2081`), **or**
- the literal token `[no-issue]`.

PRs whose commit subjects contain neither of these will fail CI and
cannot merge.

This applies to commit **subjects** only. Body and footers are not checked.

---

## The escape hatch — and what it actually costs

The literal token `[no-issue]` is a deliberate, **typed** opt-out for genuine chores
(dependency bumps, generated-file syncs, merge/revert commits). It is auditable
in `git log --oneline` and countable without extra tooling.

It is **not** a free pass. Per ADR-NFM-2081 §D2:

- `[no-issue]` commits **still count as structural waste** in
  `scripts/okr/commit_efficiency.py` (KR-COMPANY-2).
- It buys you a passing CI check.
- It does **not** buy you a better KR-2 number.

The two compliant paths compared to the failing one:

| Path                                  | CI         | KR-2 impact         |
| ------------------------------------- | ---------- | ------------------- |
| Reference an issue (`NFM-###`)        | pass       | improves            |
| Use escape hatch (`[no-issue]`)       | pass       | **counts as waste** |
| Neither                               | **fail**   | counts as waste     |

`git commit --no-verify` is **not** an escape hatch. It bypasses hooks silently
with no trace in history; CI will still catch the offending commit on the PR.

---

## The control: CI is the gate, the local hook is a convenience

**Authoritative check:** GitHub Actions on `pull_request` **and `push`**
(`.github/workflows/commit-ref-gate.yml`,
[NFM-2085](/NFM/issues/NFM-2085), commit `6fce970f`). It runs for every
non-merge commit in the PR range and on every direct push to `main`.
The required-status context is named *Validate every non-merge commit
subject in PR range*. It cannot be skipped by failing to configure anything
and cannot be bypassed with `--no-verify`. Note: `enforce_admins` is still
`false` (tracked as NFM-2204/R1), so an admin direct-push can bypass the
status check.

**Local hook (opt-in, fast feedback):** the `commit-msg` hook shipped on
`main` in PR #520 ([NFM-2084](/NFM/issues/NFM-2084), commit `d58e2823`).
It moves the failure from "minutes later in CI" to "instantly at commit
time". It is a convenience — not the control. Run
`git config core.hooksPath .githooks` once per clone (and per worktree) to
activate it.

### Enable the local hook (one line)

```bash
git config core.hooksPath .githooks
```

Run this once per clone (and per worktree, since `core.hooksPath` is per-repo
configuration). Re-run it after cloning a fresh worktree to restore the hook.

---

## Exemptions (structural, not discretionary)

The following commit subjects are exempt without the `[no-issue]` token because
they cannot meaningfully carry one:

- **Merge commits** (more than one parent).
- **`Revert "…"`** commits — auto-generated by `git revert`; the gate exempts
  them structurally on the `Revert "` subject prefix.

Everything else needs a reference or the token. TDD-generated commit subjects,
drive-by refactors, "fix typo" — all of them.

---

## Examples

### ✅ Pass — issue reference

```
fix(NFM-2013): silent ingestion failure fixes
```

```
feat: wire coverage emission + CI artifact upload + aggregator (NFM-2047)
```

### ✅ Pass — escape hatch (CI passes; KR-2 still counts it as waste)

```
chore: bump ruff to 0.14 [no-issue]
```

### ❌ Fail — neither reference nor token

```
chore: bump ruff to 0.14
```

```
fix: typo in service_accounts.md
```

A commit that looks like the "fail" row will be rejected by CI on the PR.
A commit that looks like the middle row will pass CI; the KR-2 dashboard will
show it as waste.

---

## Database migrations

**Migration branching:** New migrations must branch from the current `main` HEAD revision, not from a stale base. Always run `alembic heads` locally before pushing to verify single-head state. CI enforces this check on every PR — a forked migration graph will fail the build.

---

## PR governance (as of NFM-3137)

**Branch protection on `main`** requires:

1. **1 approving review** with `dismiss_stale_reviews: true` — if main advances
   after your approval, the approval is dismissed and re-review is required.
2. **3 required status checks** (Frontend, Backend, commit-ref-gate).
3. `strict: true` + `enforce_admins: true` — no bypass path exists.

**Auto-update workflow** (`.github/workflows/update-stale-prs.yml`) runs hourly
and merges main into any PR whose `mergeable_state` is `BEHIND`. This keeps PRs
up-to-date so that CI only runs for truly new changes, not for every main
advance.

**Before opening a PR or requesting review**, run:

```bash
gh pr update-branch <PR_NUMBER>
```

This reduces cascade CI waste. The hourly workflow handles this automatically,
but a manual update before merge/review is still good practice to avoid stale
review approvals being dismissed.

See [`docs/verification/PR-GOVERNANCE.md`](docs/verification/PR-GOVERNANCE.md) for the
full governance specification.

### Merge Queue + Auto-Merge Workflow

When the merge queue is enabled (per [NFM-3264](/NFM/issues/NFM-3264)),
PRs can be merged automatically once CI passes — no manual merge step
required.

**Enable auto-merge when opening a PR:**

```bash
gh pr merge --auto --squash <PR_NUMBER>
```

This tells GitHub to squash-merge the PR as soon as all required status
checks pass and the PR is not blocked by other queue entries.

**Merge queue flow:**

```
PR opened → update-branch (sync with main) → enter merge queue → CI runs → squash merge to main
```

1. `update-branch` keeps the PR rebased on the latest `main` (handled by
   `.github/workflows/update-stale-prs.yml` hourly, or manually via
   `gh pr update-branch`).
2. The merge queue serialises PRs so that each merges against a known-good
   `main` tip — no fast-forward races, no CI waste from cascade commits.
3. GitHub runs the full set of required status checks (Frontend, Backend,
   commit-ref-gate) on the queued commit.
4. On green CI, the PR is squash-merged automatically.

**Constraint — `enforce_admins: false`:**

`enforce_admins` must remain `false` for the auto-merge workflow to
function. This means repo admins retain a bypass path for status checks
and review requirements. Use it sparingly — the merge queue exists to
guarantee every commit on `main` has passed CI.

---

## See also

- [`AGENTS.md`](AGENTS.md) — same rule, written for coding-agent context loading.
- [`docs/architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md`](docs/architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md)
  — accepted decision, full rationale, KR-1 metric discussion.
- `scripts/okr/commit_efficiency.py` — the metric implementation; `_ISSUE_REF_PATTERN`
  defines what counts as a reference.
