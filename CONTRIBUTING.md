# Contributing to the Nuclear Fuel Materials Database (nucpot)

Welcome. This file is the on-disk entry point for humans **and** agents opening the
repo. The rule below is mandatory and is enforced by CI. It is also restated in
`AGENTS.md` at the repo root so coding agents pick it up on context load.

For the full rationale, scope, and the KR-1 re-baseline discussion, see
[`docs/architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md`](docs/architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md).

---

## The rule (one line)

Every commit **subject** on every branch merged into `main` must contain **either**:

- an `NFM-###` issue reference (e.g. `NFM-2081`), **or**
- the literal token `[no-issue]`.

PRs whose commit subjects contain neither of these will fail CI on `pull_request`
and cannot merge.

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

**Authoritative check:** GitHub Actions on `pull_request`
(`.github/workflows/ci.yml`). It cannot be skipped by failing to configure
anything and cannot be bypassed with `--no-verify`. The PR records the result.

**Local hook (opt-in, fast feedback):** a `commit-msg` hook is shipped in
`.githooks/`. It moves the failure from "10 minutes later in CI" to "instantly
at commit time", but it is a convenience — not the control. Until it is
enabled, `git commit` will succeed locally and CI will reject later. This is
acceptable, and it is the intended design: a hook cannot satisfy the
"lazy path is the compliant path" criterion on its own, because the lazy path
is "never configure `hooksPath`", under which the hook does not exist.

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
- **`Revert "…"`** commits — the reverted subject usually carries the original
  reference anyway.

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

## See also

- [`AGENTS.md`](AGENTS.md) — same rule, written for coding-agent context loading.
- [`docs/architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md`](docs/architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md)
  — accepted decision, full rationale, KR-1 metric discussion.
- `scripts/okr/commit_efficiency.py` — the metric implementation; `_ISSUE_REF_PATTERN`
  defines what counts as a reference.
