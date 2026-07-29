# ADR-NFM-2081 — Commit issue-reference enforcement, and the KR-1 metric definition

- **Status:** Accepted (implementation delegated)
- **Date:** 2026-07-30
- **Author:** CTO
- **Issue:** NFM-2081
- **Supersedes:** NFM-1421 (cancelled — root-cause analysis only, no enforcement mechanism proposed)

---

## 1. Context

KR-COMPANY-2 (Structural Waste Rate) measures `commits-without-issue-ref / total-commits`.
It regressed from a 45% baseline to **75.2%** (CEO measurement, 101 commits, window
2026-07-27 → 2026-08-02).

Re-measured in this worktree on 2026-07-30 at 157 commits (the window is still open, so
volume has grown since the CEO's snapshot):

```
total=157  with-ref=44  without-ref=113   →  structural waste = 72.0%
```

The direction is confirmed and the magnitude is stable: roughly **three of every four
commits carry no issue reference**. This is not a measurement artifact.

The metric implementation lives at `scripts/okr/commit_efficiency.py`. The reference
pattern is `NFM-\d+` (`_ISSUE_REF_PATTERN`, line 31), matched anywhere in the commit
message.

### 1.1 The decisive constraint discovered during analysis

```
$ git config core.hooksPath
/Users/…/_default/.git/hooks
```

`core.hooksPath` is set to an **absolute path pointing at the shared git directory**,
outside the repository tree. Two consequences follow, and they determine the whole design:

1. A hook committed to the repo (e.g. `.githooks/commit-msg`) is **inert by default**.
   It only runs after a human or agent runs `git config core.hooksPath .githooks` in
   every clone and every worktree.
2. Because all agent worktrees share one git directory, hook state is global and
   mutable by any sibling — it is not a property of the checkout under review.

Acceptance criterion #4 on NFM-2081 requires that *the lazy path is the compliant path*.
**A git hook cannot satisfy that criterion**, because the lazy path is "never configure
hooksPath", under which the hook does not exist. Any design that makes the local hook
the enforcement point fails the acceptance criteria as written.

---

## 2. Decision

### D1 — CI is the enforcing gate. The local hook is fast feedback only.

The authoritative check runs in GitHub Actions on `pull_request`, where `.github/workflows/ci.yml`
already gates merges to `main`. CI cannot be skipped by not configuring anything, cannot be
bypassed with `--no-verify`, and its result is recorded on the PR.

The `commit-msg` hook is still worth shipping — it moves the failure from "10 minutes later
in CI" to "instantly at commit time" — but it is explicitly a **convenience, not a control**.
It is opt-in, and that is acceptable *because CI backstops it*. Do not invert this.

### D2 — The escape hatch unblocks the gate but does not launder the metric.

Genuine chores exist (dependency bumps, generated-file syncs, merge/revert commits). The
escape hatch is an explicit, visible token in the commit **subject**:

```
chore: bump ruff to 0.14 [no-issue]
```

Design properties, in priority order:

- **Visible.** It appears in `git log --oneline`, so its use is auditable and countable
  without extra tooling.
- **Deliberate.** It must be typed. There is no flag, no config, no default that produces it.
- **Non-laundering.** `[no-issue]` commits **still count as structural waste** in
  `scripts/okr/commit_efficiency.py`. The escape hatch buys you a passing CI check; it does
  not buy you a better KR-2 number.

That last property is what satisfies acceptance criterion #4. The two paths cost:

| Path | Effort | CI | KR-2 impact |
|---|---|---|---|
| Reference an issue | type `NFM-2081` | passes | improves |
| Use escape hatch | type `[no-issue]` | passes | **counts as waste** |
| Neither | — | **fails** | counts as waste |

Referencing is the cheapest path that is also the best path. Nothing about the escape
hatch is easier than doing it right.

Explicitly **not** an escape hatch: `git commit --no-verify`. It bypasses hooks silently
and leaves no trace in history. CI must catch `--no-verify` commits, which it does by
construction, since CI re-reads the pushed history rather than trusting the client.

### D3 — Exemptions are structural, not discretionary.

Auto-generated subjects that no human authors are exempt without needing the token:

- merge commits (more than one parent)
- `Revert "…"` — the reverted subject usually carries the original ref anyway

Everything else requires a ref or the token.

### D4 — Contributor guidance is written where agents actually read it.

The repository root has **no `CONTRIBUTING.md` and no `AGENTS.md`** today. Guidance that
lives only in a wiki or an issue thread will not be picked up. The rule must be written
into a root-level contributor file so it enters the working context of every agent that
opens the repo, per acceptance criterion #2.

---

## 3. The CEO's open question: is KR-1 measuring what we want?

> *"KR-1 is defined as completed issues / total commits. That ratio penalises healthy small
> commits… tell me whether the metric measures what we want, or whether it should be
> re-baselined."*

**Answer: the metric is broken. Re-baseline it before the org optimises against it.**
The instinct behind the question is correct, and the problem is worse than "it penalises
small commits". Four defects, in ascending order of severity:

**(a) It is dimensionally incoherent.** The numerator counts *issues*; the denominator
counts *commits*. The ratio is issues-per-commit, which is not a rate of anything the
business cares about. The implementation itself flags this — `commit_efficiency.py`
lines 143-148 carry a comment noting the formula "intentionally mixes issue-count with
commit-count per the CTO-defined formula". That comment is a warning that was never acted on.

**(b) It rewards exactly the behaviour we spend code review trying to prevent.**
One 900-line commit closing one issue scores **1.00**. Five reviewable commits closing the
same issue score **0.20**. The metric pays a 5× premium for unreviewable work. Any team
that takes the target seriously will stop decomposing commits.

**(c) The target is unreachable under any healthy practice.** A target of ≥0.80 demands
4 closed issues per 5 commits — about 1.25 commits per issue. Our own mandated TDD cycle
(RED → GREEN → REFACTOR) produces three commits per unit of work before review feedback
is even applied. **The target and the development process we require are mutually
exclusive.** Current value 0.238 is not primarily a performance signal; it is largely an
artifact of the formula.

**(d) Numerator and denominator are drawn from different populations.** Commits are
windowed by author date; issue completion is not windowed at all. An issue closed this
week whose commits landed last week inflates the numerator with no matching denominator,
and vice versa. The ratio is not stable under choice of window.

There is also a **coupling defect** the CEO half-spotted: because unreferenced commits sit
in KR-1's denominator, referencing behaviour moves KR-1 and KR-2 together. That is not a
convenient two-for-one — it means the two KRs are not independent measurements. When KR-1
moves you cannot tell whether throughput improved or whether people just started typing
issue numbers.

### 3.1 Recommended re-baseline

Split the one broken ratio into two orthogonal, dimensionally-sound measures:

| Replaces | Metric | Definition | Target |
|---|---|---|---|
| KR-1 (throughput half) | **Issue Closure Throughput** | issues reaching `done` in the window | absolute count, trended — no ratio |
| KR-1 (hygiene half) | *drop* | already measured by KR-2 | — |
| new, diagnostic | **Commits per Closed Issue** | **median** commits referencing an issue, over issues closed in the window | **band: 2–8**, not a floor |

Three deliberate choices:

- **Throughput is a count, not a ratio.** Output is what we want to know; dividing it by
  commit volume destroys the signal rather than normalising it.
- **Do not re-add referencing to KR-1 in any form** (including the CEO's suggested
  "issues-closed per *referenced* commit"). That variant fixes the gaming problem but keeps
  KR-1 and KR-2 coupled — and it still pays a premium for large commits, just within a
  smaller denominator. KR-2 already measures referencing. Measure it once.
- **A band, not a floor.** Every one-sided target gets gamed at the open end. A floor on
  issues-per-commit is gamed by committing less often; a ceiling would be gamed by
  committing more often. A band flags both failure modes: below 2 suggests work is not
  being decomposed into issues at all, above 8 suggests the issue was too large or the
  branch is churning. **Median, not mean**, so one 60-commit migration does not move the number.

This re-baseline is a **recommendation to the CEO**, not a unilateral change — KR
definitions are the CEO's to set. It is out of scope for NFM-2081's implementation, which
proceeds on KR-2 regardless. KR-2 is well-formed and worth fixing as-is.

---

## 4. Consequences

**Positive**

- Enforcement survives an unconfigured clone, a fresh worktree, and `--no-verify`.
- Escape-hatch usage is visible in `git log` and countable, so we will learn whether
  "genuine chores" are 3% of commits or 40%.
- KR-2 becomes an honest number: it can no longer be improved by anything except
  actually referencing issues.

**Negative / accepted**

- CI feedback is slower than a local hook for developers who never enable hooks. Accepted:
  correctness of the gate outweighs latency, and the hook is available for those who want speed.
- Existing history is non-compliant. **We do not rewrite history.** KR-2 improves
  forward-only, so the weekly number will lag the fix by roughly one window.
- Adding `[no-issue]` to the waste numerator means KR-2 will not reach 0% even at perfect
  compliance. That is intended — the floor is the true chore rate, and we want to see it.

---

## 5. Scope boundary for implementation

In scope for the delegated issue:

1. CI check on `pull_request` validating every commit subject in the PR range.
2. `commit-msg` hook, committed to the repo, with documented one-line enablement.
3. Root-level contributor guidance stating the rule and the escape hatch.
4. `[no-issue]` continues to count as waste in `scripts/okr/commit_efficiency.py`
   (verify existing behaviour; the current regex-based classifier already treats it as
   unreferenced — add a test pinning that, do not "fix" it).
5. Re-measure the window post-rollout and report on NFM-2081.

Out of scope: rewriting history, changing the `NFM-\d+` pattern, and the KR-1 re-baseline
of §3.1 (CEO decision, separate issue if accepted).
