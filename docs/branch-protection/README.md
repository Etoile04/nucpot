# Branch-Protection Spec — Companion Notes

This directory holds the declarative GitHub branch-protection spec for
`Etoile04/nucpot`, closing the **defense-in-depth layer 3** gap identified
in the [PreCompletionMerge hook design](../..//issues/NFM-3853#document-precompletion-merge-hook)
(NFM-3853 document revision `bdb101c8`).

## Files

| File | Purpose |
|---|---|
| `required-status-checks.yml` | Declarative spec mirroring the GitHub Branch-Protection REST API payload. Apply via `gh api` or `PUT /repos/Etoile04/nucpot/branches/{branch}/protection`. |
| `README.md` (this file) | Rationale, contract, apply checklist, and open-question resolution. |

## Layer 3 of the defense-in-depth matrix

Per the design doc, four layers catch the phantom-pass pattern:

| Layer | Catches | Bypass risk |
|---|---|---|
| 1. Paperclip API pre-PATCH middleware | All agent runs via API key | Service tokens; bypass-by-design for system actor (audit metric) |
| 2. ADK runtime middleware hook | Agent runs via Claude/Codex adapters | Direct API calls |
| 3. **GitHub branch protection (THIS SPEC)** | **All PR merges (authoritative git-side)** | **Direct git push, force-push** |
| 4. Cron audit (ADR-010 D2) | Existing phantoms within 24h | Audit window |

The API-layer hook is the primary defense. Layer 3 is the structural
backstop — even if layer 1 is bypassed (system actor, misconfiguration),
a non-ancestor branch **cannot land on `main`** because GitHub refuses
the merge.

## Required-check pipeline signature

The "API hook's pipeline signature" referenced in NFM-3861 is the
required-status-check **job name** that GitHub uses to identify a check
in `required_status_checks.contexts`.

The PreCompletionMerge hook is mirrored at two layers:

1. **Paperclip API middleware** (NFM-3857, in_progress) — returns `422
   merge_kind_unmerged_branch` when an issue's `status=done` PATCH
   references a branch whose tip is not an ancestor of `origin/main`.

2. **GitHub Actions gate** — a new workflow
   `.github/workflows/precompletion-merge-gate.yml` whose **job name is
   `precompletion-merge-gate`**. The workflow runs `git merge-base
   --is-ancestor origin/<branch> origin/main` for each PR targeting
   `main` and fails when the branch is not an ancestor. This is the
   value that goes into `required_status_checks.contexts`.

The two layers share the same gate logic. They differ only in where the
check fires:

- Layer 1 fires when an agent PATCHes the issue to `done` — catches
  *phantom merges* (the agent ran `gh pr merge` and reported success
  but the merge never happened).
- Layer 3 fires when a PR is opened/updated — catches *unmerged branches
  before the merge button is even enabled* (catches the same root
  cause one stage earlier, and also catches direct-API merge attempts
  that didn't go through a PR).

## Q4 — Force-push guard

The design doc's open question Q4 reads:

> **Force-push guard** — agents with force-push access to main could
> collapse ancestry. Pair the hook with GitHub branch protection
> requiring signed linear history.

**Resolution.** Combine three GitHub branch-protection settings on
`main`:

| Setting | Value | Why |
|---|---|---|
| `required_signatures.enabled` | `true` | Every commit on `main` must be GPG/SSH-signed. Provenance trail for audit. |
| `required_linear_history.enabled` | `true` | History must be a straight line — merge commits are forbidden. Squash-merge only. |
| `allow_force_pushes.enabled` | `false` | **The principal control.** No human, no bot, no admin token can run `git push --force` against `main`. |

The combination is the structural answer to Q4: even if an agent
acquires admin token and runs `git push --force`, GitHub refuses the
push. The hook in layer 1 cannot be bypassed by force-push either —
the only way to get code onto `main` is a non-fast-forward squash-merge
from a PR with the gate green.

### Agent-pushable branches (NFM-*, fix/*)

For agent worktree branches the rule is narrower — we deny
`allow_force_pushes` but allow non-linear history. Rebases are a
legitimate part of the agent workflow (Lead Engineer rebases per the
AGENTS.md §3 Step 0 rule), but a force-pushed branch tip is still
disallowed unless the agent uses `--force-with-lease` against a known
remote ref. This catches the failure mode where an agent re-pushes an
old SHA that has since been rebased by another agent, collapsing two
agents' work into one ancestry.

### Why not just `required_signatures` alone?

A signed commit can still be a force-pushed re-write of an existing
commit. The signature proves *someone* signed it, not that the
commit is in the linear history an auditor expects. The three-setting
combination — `required_signatures` + `required_linear_history` +
`allow_force_pushes=false` — is what makes the history
**append-only and tamper-evident**.

## Apply checklist (for Release Engineer / SRE Monitor)

1. Confirm the PreCompletionMerge hook is in production
   ([NFM-3857](https://paperclip/NFM/issues/NFM-3857) merged).
2. Land `.github/workflows/precompletion-merge-gate.yml` with job name
   exactly `precompletion-merge-gate` (the contract string).
3. Apply the `main` block from `required-status-checks.yml`:
   ```bash
   gh api -X PUT \
     repos/Etoile04/nucpot/branches/main/protection \
     --input /tmp/nucpot-main-protection.json
   ```
   The JSON file is a 1:1 conversion of the YAML in this directory.
4. Verify via:
   ```bash
   gh api repos/Etoile04/nucpot/branches/main/protection \
     | jq '.required_status_checks.contexts, .enforce_admins,
            .required_signatures.enabled, .required_linear_history.enabled,
            .allow_force_pushes.enabled'
   ```
   Expected:
   ```json
   ["API Tests with Coverage",
    "Validate every non-merge commit subject in PR range",
    "precompletion-merge-gate"]
   true
   true
   true
   false
   ```
5. Smoke-test: open a draft PR; the
   `PreCompletion Merge Gate / precompletion-merge-gate` check must
   appear as required. Close without merge — the check status goes
   `pending → neutral`, never `success`.
6. Comment on NFM-3861 with the SHA of the applied config + the JSON
   verification output.

## Coordination

- **RE hand-off UUID:** `32cfff52-c625-4734-9206-e191ff7f5fc6`
- **SRE hand-off UUID:** `2ee2415b-e43e-4806-888f-c231e60facaf`
- **Parent issue:** [NFM-3853](https://paperclip/NFM/issues/NFM-3853) (Phantom-pass RCA + prevention hook)
- **Grand-parent issue:** [NFM-3855](https://paperclip/NFM/issues/NFM-3855) (Implement PreCompletionMerge hook)
- **Sibling spec:** NFM-3857 (API middleware, in_progress), NFM-3858 (ADK runtime middleware, blocked on NFM-3857)

## Out of scope for this issue

- Application of the rules to GitHub (RE/SRE).
- Implementation of the `precompletion-merge-gate.yml` workflow itself
  (companion to NFM-3857; lands with the integration task, not here).
- Any code change to the Paperclip API or ADK runtime.

This issue is a **spec / sign-off** deliverable. Its merge does not
block the integration merge in NFM-3855; it may land in parallel.
