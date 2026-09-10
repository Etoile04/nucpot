#!/usr/bin/env python3
"""Acceptance gate for `paperclip_issue_lookup` — ADR-008 / NFM-2036.

Runs the twelve cases from the arch-spec (and NFM-4538) against the live `$PAPERCLIP_API_URL`
and prints PASS / FAIL for each. Exits non-zero if any case fails.

    python3 scripts/verify_paperclip_issue_lookup.py

| # | Setup                                     | Expected                          |
|---|-------------------------------------------|-----------------------------------|
| 1 | no PAPERCLIP_API_KEY, lookup_issue        | AuthError raised, zero HTTP calls |
| 2 | key restored, BASE_URL not company-scoped | WrongPath raised, zero HTTP calls |
| 3 | lookup_issue("NFM-DOES-NOT-EXIST-9999")   | NotFound, distinct from errors    |
| 4 | lookup_issue("NFM-1909")                  | Ok, 1 issue, pages_consumed == 1  |
| 5 | lookup_issue("NFM-1909") expanded payload | expanded-only keys present        |
| 6 | a blocked issue discovered at run time    | blockedBy non-empty              |

Cases 1 and 2 must prove *no HTTP call was attempted*. They do that by
replacing the helper's `requests` module with a spy that records any call and
refuses to perform it — so a regression that moved the guards to after the
request would fail loudly rather than silently pass.

Cases 7-12 cover NFM-4538: the safety property has a second half, and both
halves were breakable from ordinary call sites.

| #  | Setup                                | Expected                              |
|----|--------------------------------------|---------------------------------------|
| 7  | lookup_issues({'parentId': uuid})    | FilterError raised, zero HTTP calls   |
| 8  | lookup_issues(status="done")         | FilterError raised, zero HTTP calls   |
| 9  | lookup_issues(status=["done"])       | Ok, non-empty, every row status=done  |
| 10 | lookup_issues(parent_id=<uuid>)      | Ok, every row parentId == that uuid   |
| 11 | lookup_issues(identifier="NFM-3880") | Ok, exactly that identifier           |
| 12 | lookup_issues(bogus_filter=...)      | TypeError, zero HTTP calls            |

Case 7 is the *non-empty* silent failure: a dict lands on the `q` positional and
degrades into a relevance-ranked fuzzy search that returns real, plausible,
entirely unrelated issues. Case 8 is the *empty* silent failure: `",".join("done")`
is `"d,o,n,e"`, a garbage filter matching nothing — indistinguishable from the
genuine empty result the module's contract promises it can only ever mean.
"""

# ruff: noqa: B904

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paperclip_issue_lookup as plu

LIVE_IDENTIFIER = "NFM-1909"
ABSENT_IDENTIFIER = "NFM-DOES-NOT-EXIST-9999"
NON_COMPANY_SCOPED_URL = "https://paperclip.invalid/api/issues"

# Fields the bare per-issue endpoint returns and the collection projection
# strips. Key *presence* is the trap-3 property; values may legitimately be empty.
EXPANDED_ONLY_FIELDS = ("blockedBy", "blocks", "ancestors", "planDocument", "workProducts")

# NFM-4538 fixtures. NFM-3880 ("OKR Weekly Standup — Week 36") is long-closed
# with a stable fan-out of 36 children, so it is safe to assert against.
PARENT_UUID = "a5e2d689-af8a-4d40-801b-66148c1dc88f"
PARENT_IDENTIFIER = "NFM-3880"
PARENT_MIN_CHILDREN = 30  # actual 36; asserted as a floor so new children can't break the gate


class SpyRequests:
    """Stands in for `requests`. Records calls; never performs one."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("helper opened an HTTP connection before its pre-flight guards ran")


class Guard:
    """Swaps in the spy, then restores real module and env state."""

    def __init__(self) -> None:
        self.spy = SpyRequests()
        self._requests = plu.requests
        self._base_url = plu.BASE_URL
        self._key = os.environ.get("PAPERCLIP_API_KEY")

    def __enter__(self) -> SpyRequests:
        plu.requests = self.spy
        return self.spy

    def __exit__(self, *exc) -> None:
        plu.requests = self._requests
        plu.BASE_URL = self._base_url
        if self._key is None:
            os.environ.pop("PAPERCLIP_API_KEY", None)
        else:
            os.environ["PAPERCLIP_API_KEY"] = self._key


def case_1_missing_key() -> str:
    """AuthError raised locally, with no HTTP call attempted."""
    with Guard() as spy:
        os.environ.pop("PAPERCLIP_API_KEY", None)
        try:
            result = plu.lookup_issue(LIVE_IDENTIFIER)
        except plu.AuthError as err:
            if spy.calls:
                raise AssertionError(f"{len(spy.calls)} HTTP call(s) attempted")
            if not err.preflight:
                raise AssertionError("AuthError was not flagged as pre-flight")
            return f"AuthError raised pre-flight, 0 HTTP calls — {err}"
        raise AssertionError(f"expected AuthError, got {result!r}")


def case_2_wrong_path() -> str:
    """WrongPath raised for a non-company-scoped URL, with no HTTP call."""
    with Guard() as spy:
        os.environ["PAPERCLIP_API_KEY"] = "restored-key-for-case-2"
        plu.BASE_URL = NON_COMPANY_SCOPED_URL
        try:
            result = plu.lookup_issues(q="x")
        except plu.WrongPathError as err:
            if spy.calls:
                raise AssertionError(f"{len(spy.calls)} HTTP call(s) attempted")
            if "/api/companies/" not in err.hint:
                raise AssertionError("hint does not name the correct path")
            return f"WrongPathError raised, 0 HTTP calls — {err}"
        raise AssertionError(f"expected WrongPathError, got {result!r}")


def case_3_not_found() -> str:
    """A genuinely absent identifier is NotFound — not an error, not Ok."""
    result = plu.lookup_issue(ABSENT_IDENTIFIER)
    if isinstance(result, (plu.AuthError, plu.WrongPath, plu.ApiError)):
        raise AssertionError(f"NotFound not distinct from errors: {result!r}")
    if not isinstance(result, plu.NotFound):
        raise AssertionError(f"expected NotFound, got {result!r}")
    return f"NotFound(identifier={result.identifier!r}, http_status={result.http_status})"


def case_4_live_issue() -> str:
    """The live issue the CTO called deleted resolves to exactly one Ok row."""
    result = plu.lookup_issue(LIVE_IDENTIFIER)
    if not isinstance(result, plu.Ok):
        raise AssertionError(f"expected Ok, got {result!r}")
    if len(result.issues) != 1:
        raise AssertionError(f"expected exactly 1 issue, got {len(result.issues)}")
    if result.pages_consumed != 1:
        raise AssertionError(f"expected pages_consumed == 1, got {result.pages_consumed}")

    issue = result.issues[0]
    if issue.get("identifier") != LIVE_IDENTIFIER:
        raise AssertionError(f"wrong issue returned: {issue.get('identifier')!r}")

    return (
        f"Ok(issues=[{issue['identifier']} status={issue.get('status')!r}], "
        f"pages_consumed={result.pages_consumed}) id={issue.get('id')}"
    )


def case_5_expanded_fields_present() -> str:
    """`lookup_issue` returns the expanded payload, not the stripped collection row.

    Trap-3 is about *key presence*: the collection projection omits these keys
    entirely, so a caller reading blockers through it concludes "no blockers".
    The bare per-issue endpoint includes them — possibly empty, which is a
    different and honest answer.

    This case deliberately asserts presence rather than non-emptiness. The
    previous version pinned two "known blocked" issues (NFM-2113, NFM-2092) and
    asserted `blockedBy` was non-empty; `blockedBy` lists only *unresolved*
    blockers, so the gate went red the moment those blockers closed. It had been
    red on `main` for some time. Presence is the property that actually holds
    forever.
    """
    result = plu.lookup_issue(LIVE_IDENTIFIER)
    if not isinstance(result, plu.Ok) or len(result.issues) != 1:
        raise AssertionError(f"expected Ok with 1 issue, got {result!r}")
    expanded = result.issues[0]

    missing = [k for k in EXPANDED_ONLY_FIELDS if k not in expanded]
    if missing:
        raise AssertionError(f"expanded payload is missing {missing} — trap-3 not fixed")

    # Prove the collection really does strip them, so the case cannot pass vacuously.
    listed = plu.lookup_issues(identifier=LIVE_IDENTIFIER)
    if not isinstance(listed, plu.Ok) or not listed.issues:
        raise AssertionError(f"could not fetch the collection row for comparison: {listed!r}")
    stripped = [k for k in EXPANDED_ONLY_FIELDS if k not in listed.issues[0]]
    if not stripped:
        raise AssertionError(
            "collection row carries every expanded field — the fixture can no longer "
            "distinguish the two endpoints, so this case proves nothing"
        )

    return f"expanded has all of {EXPANDED_ONLY_FIELDS}; collection strips {stripped}"


def case_6_live_blocked_issue() -> str:
    """A currently-blocked issue reports its blockers — discovered, never pinned.

    The blocked issue is found at run time, so this keeps the strong
    non-emptiness assertion without a hardcoded fixture that rots. An empty
    board is a legitimate state and is reported, not failed.
    """
    listed = plu.lookup_issues(status=["blocked"])
    if not isinstance(listed, plu.Ok):
        raise AssertionError(f"expected Ok listing blocked issues, got {listed!r}")
    if not listed.issues:
        return "no blocked issues on the board right now — nothing to assert (not a failure)"

    ident = listed.issues[0].get("identifier")
    result = plu.lookup_issue(ident)
    if not isinstance(result, plu.Ok) or len(result.issues) != 1:
        raise AssertionError(f"expected Ok with 1 issue for {ident}, got {result!r}")

    blocked_by = result.issues[0].get("blockedBy")
    if blocked_by is None:
        raise AssertionError(f"blockedBy is key-absent for {ident} — trap-3 not fixed")
    if not isinstance(blocked_by, list) or not blocked_by:
        raise AssertionError(
            f"{ident} has status=blocked but blockedBy is {blocked_by!r} — either trap-3 "
            "regressed or the issue is blocked with no first-class blocker"
        )

    blockers = [b.get("identifier", b.get("id", "?")) for b in blocked_by]
    return f"{ident} (discovered) reports {len(blockers)} blocker(s): {blockers}"



def case_7_dict_q() -> str:
    """A dict-shaped filter call must raise, not fuzzy-search (NFM-4538 D1).

    Before the fix this returned ``Ok`` with 12 real, plausible, entirely
    unrelated issues — the caller has no way to tell them from true children.
    """
    with Guard() as spy:
        try:
            result = plu.lookup_issues({"parentId": PARENT_UUID})
        except plu.FilterError as err:
            if spy.calls:
                raise AssertionError(f"{len(spy.calls)} HTTP call(s) attempted before the guard")
            if "parent_id" not in str(err):
                raise AssertionError(f"error does not name the correct keyword: {err}")
            return f"FilterError raised pre-flight, 0 HTTP calls — {err}"
        rows = len(result.issues) if isinstance(result, plu.Ok) else "?"
        raise AssertionError(
            f"dict silently accepted as fuzzy-search text, got {type(result).__name__} "
            f"with {rows} unrelated rows"
        )


def case_8_str_status() -> str:
    """A bare string status must raise, not shred into "d,o,n,e" (NFM-4538 D2).

    Before the fix this returned ``Ok(issues=[])`` while 3768 issues matched —
    the exact shape the module's contract says can only mean "no matches".
    """
    with Guard() as spy:
        try:
            result = plu.lookup_issues(status="done")
        except plu.FilterError as err:
            if spy.calls:
                raise AssertionError(f"{len(spy.calls)} HTTP call(s) attempted before the guard")
            if '["done"]' not in str(err):
                raise AssertionError(f"error does not show the list-shaped fix: {err}")
            return f"FilterError raised pre-flight, 0 HTTP calls — {err}"
        rows = len(result.issues) if isinstance(result, plu.Ok) else "?"
        raise AssertionError(
            f'status="done" silently accepted, got {type(result).__name__} with {rows} rows '
            "(an empty Ok here is the contract violation this case exists to catch)"
        )


def case_9_list_status() -> str:
    """The correct list-shaped status call still works — guards against over-correction."""
    result = plu.lookup_issues(status=["done"])
    if not isinstance(result, plu.Ok):
        raise AssertionError(f"expected Ok, got {result!r}")
    if not result.issues:
        raise AssertionError("expected a non-empty result for status=['done']")

    wrong = [i.get("identifier") for i in result.issues if i.get("status") != "done"]
    if wrong:
        raise AssertionError(f"{len(wrong)} row(s) are not done: {wrong[:5]}")

    return f"Ok({len(result.issues)} rows, all status=done, truncated={result.truncated})"


def case_10_parent_id() -> str:
    """`parent_id` returns only true children, verified client-side (NFM-4538 D3)."""
    result = plu.lookup_issues(parent_id=PARENT_UUID)
    if not isinstance(result, plu.Ok):
        raise AssertionError(f"expected Ok, got {result!r}")

    wrong = [i.get("identifier") for i in result.issues if i.get("parentId") != PARENT_UUID]
    if wrong:
        raise AssertionError(
            f"{len(wrong)} row(s) are not children of {PARENT_IDENTIFIER}: {wrong[:5]}"
        )
    if len(result.issues) < PARENT_MIN_CHILDREN:
        raise AssertionError(
            f"expected >= {PARENT_MIN_CHILDREN} children of {PARENT_IDENTIFIER}, "
            f"got {len(result.issues)}"
        )

    return f"Ok({len(result.issues)} rows, every parentId == {PARENT_IDENTIFIER})"


def case_11_identifier() -> str:
    """`identifier` exact-matches locally — the server silently ignores the param."""
    result = plu.lookup_issues(identifier=PARENT_IDENTIFIER)
    if not isinstance(result, plu.Ok):
        raise AssertionError(f"expected Ok, got {result!r}")

    idents = [i.get("identifier") for i in result.issues]
    if idents != [PARENT_IDENTIFIER]:
        raise AssertionError(f"expected exactly [{PARENT_IDENTIFIER!r}], got {idents[:8]}")

    return f"Ok(exactly {idents}) — server-side param ignored, local exact-match held"


def case_12_unknown_filter() -> str:
    """An unknown filter kwarg must fail loudly rather than degrade to unfiltered.

    ``parentIssueId`` is the specific wrong name that started this: the server
    silently drops unknown query params, so it returned a full unfiltered page
    that reads as "these are the children".
    """
    with Guard() as spy:
        try:
            result = plu.lookup_issues(parentIssueId=PARENT_UUID)
        except TypeError as err:
            if spy.calls:
                raise AssertionError(f"{len(spy.calls)} HTTP call(s) attempted before the guard")
            return f"TypeError raised pre-flight, 0 HTTP calls — {err}"
        raise AssertionError(
            f"unknown filter silently accepted, got {type(result).__name__} "
            "(server drops unknown params and returns an UNFILTERED page)"
        )


CASES = [
    ("1", "missing PAPERCLIP_API_KEY -> AuthError, no HTTP", case_1_missing_key),
    ("2", "non-company-scoped BASE_URL -> WrongPathError, no HTTP", case_2_wrong_path),
    ("3", f"{ABSENT_IDENTIFIER} -> NotFound", case_3_not_found),
    ("4", f"{LIVE_IDENTIFIER} -> Ok(1 issue)", case_4_live_issue),
    (
        "5",
        f"{LIVE_IDENTIFIER} -> expanded fields present, collection strips them (trap-3)",
        case_5_expanded_fields_present,
    ),
    (
        "6",
        "a discovered blocked issue reports its blockers (trap-3)",
        case_6_live_blocked_issue,
    ),
    ("7", "dict as positional q -> FilterError, no HTTP (NFM-4538 D1)", case_7_dict_q),
    ("8", 'status="done" -> FilterError, no HTTP (NFM-4538 D2)', case_8_str_status),
    ("9", 'status=["done"] -> Ok, every row done (no over-correction)', case_9_list_status),
    ("10", f"parent_id={PARENT_IDENTIFIER} -> Ok, every row is a child", case_10_parent_id),
    ("11", f"identifier={PARENT_IDENTIFIER} -> Ok, exactly that row", case_11_identifier),
    ("12", "unknown filter kwarg -> TypeError, no HTTP (NFM-4538 D3)", case_12_unknown_filter),
]


def main() -> int:
    print("paperclip-issue-lookup acceptance gate — ADR-008 / NFM-2036")
    print(f"endpoint: {plu.BASE_URL or '(unset)'}\n")

    failures = 0
    for number, description, run in CASES:
        try:
            detail = run()
        except Exception as err:
            failures += 1
            print(f"FAIL  case {number}: {description}")
            print(f"      {type(err).__name__}: {err}")
            if os.environ.get("VERBOSE"):
                traceback.print_exc()
        else:
            print(f"PASS  case {number}: {description}")
            print(f"      {detail}")

    total = len(CASES)
    print(f"\n{total - failures}/{total} cases passed")
    if failures:
        print("GATE FAILED — the helper is not done until every case passes.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
