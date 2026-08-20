# NFM-3434 — Release-note correction for NFM-3355 fix attribution

**Date:** 2026-08-21
**Authority:** NFM-3433 triage (parent), NFM-3422 behavioural test re-run (independent verification)
**Issue:** [NFM-3434](/NFM/issues/NFM-3434)
**Scope:** docs-only — clarifies the user-visible-fix attribution for the "new literature
doesn't appear in /literature list" report ([NFM-3355](/NFM/issues/NFM-3355)).

---

## Release note

- The user-visible fix for the "new literature doesn't appear in /literature list" symptom
  ([NFM-3355](/NFM/issues/NFM-3355)) was the React Query refactor in
  [NFM-3366](/NFM/issues/NFM-3366) plus the subsequent `nucpot-prod-web` container rebuild
  (stale-container class, see [NFM-3307](/NFM/issues/NFM-3307)) — **not** the
  `invalidateQueries({ ..., exact: false })` edit shipped by
  [NFM-3406](/NFM/issues/NFM-3406) / NFM-3408 (commit `3fa34f05`). `@tanstack/query-core@5.101.4`'s
  `invalidateQueries` already takes the prefix-match branch when `exact` is omitted (an
  omitted `exact` is identical to `false`); the `exact: false` annotation is a harmless
  documentation of intent and matches the v5 default. NFM-3406's description premise
  ("exact match by default") is therefore misleading — see the correction comment
  attached to NFM-3406 and the NFM-3433 triage notes.

---

## What this file is *not*

- Not a re-open of [NFM-3355](/NFM/issues/NFM-3355). The CPO recorded environmental
  closure on NFM-3355; the root cause is the NFM-3307-class stale container, not the
  `exact: false` code change.
- Not a code change. `apps/web/src/components/LiteratureManager.tsx` is intentionally
  left alone — the `exact: false` annotation is correct and harmless.
- Not an amendment of commit `3fa34f05`. History stays append-only; this is a follow-up
  docs commit.

---

## Evidence

- Independent empirical verification of the v5 default (5/5 behavioural tests pass
  WITHOUT `exact: false`, 4/5 fail with `exact: true`) — NFM-3422 branch, commit
  `37b7cf9e`, plus Code Reviewer comment `4166c048` reaching the same conclusion.
- Triage notes and disposition: [NFM-3433](/NFM/issues/NFM-3433) (parent).
- Stale-container-removes-bug precedent: [NFM-3307](/NFM/issues/NFM-3307).

---

## Cross-references

- Original cache-invalidation fix (description misleading): [NFM-3406](/NFM/issues/NFM-3406)
- Independent QA follow-up (behavioural tests): [NFM-3422](/NFM/issues/NFM-3422)
- Original user-visible React Query refactor: [NFM-3366](/NFM/issues/NFM-3366)
- Stale-container class of bug: [NFM-3307](/NFM/issues/NFM-3307)
- Closed user-facing symptom: [NFM-3355](/NFM/issues/NFM-3355)