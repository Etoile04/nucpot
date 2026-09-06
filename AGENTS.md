# AGENTS.md — rules for coding agents working in the nucpot repo

> **Read this file before committing in this repo.** It mirrors the contributor
> rule in [`CONTRIBUTING.md`](CONTRIBUTING.md) so the rule is in your context on
> load. For full rationale and the KR-1 metric discussion, see
> [`docs/architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md`](docs/architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md).

---

## Status (as of NFM-2204)

What is enforced right now versus what is still in flight:

| Control                            | Where it lives                                | Status (2026-07-31)                                                                                                |
| ---------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| **CI gate** on `pull_request` + `push` | `.github/workflows/commit-ref-gate.yml` ([NFM-2085](/NFM/issues/NFM-2085), commit `6fce970f`) | **Live on `main`.** A non-compliant PR or direct push (missing `NFM-###` and missing `[no-issue]`) will be red. The required-status context is named *Validate every non-merge commit subject in PR range*. `enforce_admins` is `true` (NFM-2204/R1) — admin bypass of required-status checks is no longer possible. |
| **Local `commit-msg` hook**        | `.githooks/commit-msg` ([NFM-2084](/NFM/issues/NFM-2084), commit `d58e2823`, PR #520)             | **Live on `main`.** Shipped in PR #520. `git config core.hooksPath .githooks` activates it immediately.              |
| **KR-2 metric** (`commit_efficiency.py`) | `scripts/okr/commit_efficiency.py` (`_ISSUE_REF_PATTERN`)                              | **Live.** Counts `[no-issue]` as structural waste per ADR-NFM-2081 §D2. Revision basis pinned to `origin/main --max-parents=1` (NFM-2204/R2). |

**What this means for you:** both controls are live on `main`. The CI gate
catches non-compliant PRs and direct pushes; the local hook provides instant
feedback at commit time once configured.

---

## The rule

Every commit **subject** on every branch merged into `main` must contain **either**:

- an `NFM-###` issue reference (e.g. `NFM-2081`), **or**
- the literal token `[no-issue]`.

PRs whose commit subjects contain neither will fail CI and
cannot merge. Direct pushes to `main` with non-compliant subjects will also
fail. The check runs against commit **subjects**; body and footers are
not inspected.

When you produce a commit message in this repo, you must do one of these two
things explicitly. Do not assume "no instruction" defaults to a passing build.

---

## The escape hatch — and what it actually costs

`[no-issue]` is a deliberate, **typed** opt-out for genuine chores (dependency
bumps, generated-file syncs, merge/revert commits). It is auditable in
`git log --oneline`.

It is **not** a free pass. Per ADR-NFM-2081 §D2:

- `[no-issue]` commits **still count as structural waste** in
  `scripts/okr/commit_efficiency.py` (KR-COMPANY-2).
- It buys you a passing CI check.
- It does **not** buy you a better KR-2 number.

| Path                                  | CI         | KR-2 impact         |
| ------------------------------------- | ---------- | ------------------- |
| Reference an issue (`NFM-###`)        | pass       | improves            |
| Use escape hatch (`[no-issue]`)       | pass       | **counts as waste** |
| Neither                               | **fail**   | counts as waste     |

If you can type an `NFM-###` reference, type it. The escape hatch is not a
shortcut — it is a record.

`git commit --no-verify` is **not** an escape hatch. It bypasses hooks silently
with no trace in history; CI will still catch the offending commit on the PR.

---

## The control: CI is the gate, the local hook is a convenience

**Authoritative check:** GitHub Actions on `pull_request` **and `push`**
(`.github/workflows/commit-ref-gate.yml`,
[NFM-2085](/NFM/issues/NFM-2085), commit `6fce970f`). It runs for every
non-merge commit in the PR range and on every direct push to `main`.
The required-status context is named *Validate every non-merge commit
subject in PR range*. It cannot be skipped by an unconfigured clone and
and cannot be bypassed with `--no-verify`. `enforce_admins` is `true`
(NFM-2204/R1), so admin direct-pushes are also subject to required-status
checks — there is no standing bypass.

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

Run this once per clone and once per worktree (`core.hooksPath` is per-repo
state). If you skip it, commits will still succeed locally — CI catches the
non-compliance when the PR opens.

---

## Exemptions (structural, not discretionary)

The following commit subjects are exempt without the `[no-issue]` token because
they cannot meaningfully carry one:

- **Merge commits** (more than one parent).
- **`Revert "…"`** commits — auto-generated by `git revert`; the gate exempts
  them structurally on the `Revert "` subject prefix.

Everything else — including TDD-cycle commits (`test: …`, `feat: …`,
`refactor: …`), drive-by refactors, and "fix typo" commits — needs a reference
or the token.

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

**Before merging or requesting review**, run:

```bash
gh pr update-branch <PR_NUMBER>
```

This reduces cascade CI waste. The hourly workflow handles this automatically,
but a manual update before merge/review is still good practice to avoid stale
review approvals being dismissed.

See [`docs/verification/PR-GOVERNANCE.md`](docs/verification/PR-GOVERNANCE.md) for the
full governance specification.

---

## Merge integrity — verify content, never trust the merge marker (NFM-4357)

A merged PR is NOT proof its changes landed. Twice on 2026-09-06 (#1200, #1201),
a squash merge recorded the PR title while the tree did NOT contain the diff —
the merge head was a commit from a different (stale / checked-out-over) branch.
The empty merge passed every gate: commit subject valid, CI green, state MERGED.

**Rule: after EVERY merge (batch or single), verify the change actually shipped:**

```bash
# content-level check (what matters), not metadata-level:
git fetch origin && git show origin/main:<changed/file> | grep -c '<unique new line>'
# or diff-count against the pre-merge sha:
gh api repos/<owner>/<repo>/compare/<prev>...main --jq '.files[].additions'
```

- `MERGED` state or green CI proves process, not content — CI ran on the PR
  head, but the squash commit may point elsewhere.
- Never batch-merge across branch switches. Checkout, re-verify the working
  tree still holds your edits (`grep` before `git commit`), push, open PR,
  merge — then re-verify on `origin/main` before starting the next PR.
- Multi-agent hosts: another agent can check out over your branch between your
  edit and your push. Re-verify immediately before every push.

---

## See also

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — the same rule written for human readers.
- [`docs/architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md`](docs/architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md)
  — accepted decision, full rationale, KR-1 metric discussion.
- `scripts/okr/commit_efficiency.py` — the metric implementation; `_ISSUE_REF_PATTERN`
  defines what counts as a reference.

# self-test marker line for NFM-4357 rule validation — safe to revert
