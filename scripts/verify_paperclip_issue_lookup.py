#!/usr/bin/env python3
"""Acceptance gate for `paperclip_issue_lookup` — ADR-008 / NFM-2036.

Runs the six cases from the arch-spec against the live `$PAPERCLIP_API_URL`
and prints PASS / FAIL for each. Exits non-zero if any case fails.

    python3 scripts/verify_paperclip_issue_lookup.py

| # | Setup                                     | Expected                          |
|---|-------------------------------------------|-----------------------------------|
| 1 | no PAPERCLIP_API_KEY, lookup_issue        | AuthError raised, zero HTTP calls |
| 2 | key restored, BASE_URL not company-scoped | WrongPath raised, zero HTTP calls |
| 3 | lookup_issue("NFM-DOES-NOT-EXIST-9999")   | NotFound, distinct from errors    |
| 4 | lookup_issue("NFM-1909")                  | Ok, 1 issue, pages_consumed == 1  |
| 5 | lookup_issue("NFM-2113") — known blocked  | blockedBy present, non-empty      |
| 6 | lookup_issue("NFM-2092") — known blocked  | blockedBy present, non-empty      |

Cases 1 and 2 must prove *no HTTP call was attempted*. They do that by
replacing the helper's `requests` module with a spy that records any call and
refuses to perform it — so a regression that moved the guards to after the
request would fail loudly rather than silently pass.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paperclip_issue_lookup as plu  # noqa: E402

LIVE_IDENTIFIER = "NFM-1909"
ABSENT_IDENTIFIER = "NFM-DOES-NOT-EXIST-9999"
NON_COMPANY_SCOPED_URL = "https://paperclip.invalid/api/issues"

# Known-blocked issues used by the trap-3 regression cases.
BLOCKED_IDENTIFIER_A = "NFM-2113"  # blockers: NFM-2110, NFM-2111, NFM-2112
BLOCKED_IDENTIFIER_B = "NFM-2092"  # multiple blockers


class SpyRequests:
    """Stands in for `requests`. Records calls; never performs one."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError(
            "helper opened an HTTP connection before its pre-flight guards ran"
        )


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
    return (
        f"NotFound(identifier={result.identifier!r}, "
        f"http_status={result.http_status})"
    )


def case_4_live_issue() -> str:
    """The live issue the CTO called deleted resolves to exactly one Ok row."""
    result = plu.lookup_issue(LIVE_IDENTIFIER)
    if not isinstance(result, plu.Ok):
        raise AssertionError(f"expected Ok, got {result!r}")
    if len(result.issues) != 1:
        raise AssertionError(f"expected exactly 1 issue, got {len(result.issues)}")
    if result.pages_consumed != 1:
        raise AssertionError(
            f"expected pages_consumed == 1, got {result.pages_consumed}"
        )

    issue = result.issues[0]
    if issue.get("identifier") != LIVE_IDENTIFIER:
        raise AssertionError(f"wrong issue returned: {issue.get('identifier')!r}")

    return (
        f"Ok(issues=[{issue['identifier']} status={issue.get('status')!r}], "
        f"pages_consumed={result.pages_consumed}) id={issue.get('id')}"
    )


def case_5_blocked_issue_a() -> str:
    """NFM-2113 returns expanded payload with blockedBy present (trap-3)."""
    result = plu.lookup_issue(BLOCKED_IDENTIFIER_A)
    if not isinstance(result, plu.Ok):
        raise AssertionError(f"expected Ok, got {result!r}")
    if len(result.issues) != 1:
        raise AssertionError(f"expected 1 issue, got {len(result.issues)}")

    issue = result.issues[0]
    blocked_by = issue.get("blockedBy")
    if blocked_by is None:
        raise AssertionError(
            f"blockedBy is key-absent — trap-3 not fixed: "
            f"keys present: {[k for k in issue if k.startswith('block')]}"
        )
    if not isinstance(blocked_by, list) or len(blocked_by) == 0:
        raise AssertionError(
            f"blockedBy is empty list/missing — trap-3 not fixed: {blocked_by!r}"
        )

    blockers = [
        b.get("identifier", b.get("id", "?"))
        for b in blocked_by
    ]
    return f"blockedBy present: {blockers}"


def case_6_blocked_issue_b() -> str:
    """NFM-2092 returns expanded payload with non-empty blockedBy (trap-3)."""
    result = plu.lookup_issue(BLOCKED_IDENTIFIER_B)
    if not isinstance(result, plu.Ok):
        raise AssertionError(f"expected Ok, got {result!r}")
    if len(result.issues) != 1:
        raise AssertionError(f"expected 1 issue, got {len(result.issues)}")

    issue = result.issues[0]
    blocked_by = issue.get("blockedBy")
    if blocked_by is None:
        raise AssertionError(
            f"blockedBy is key-absent — trap-3 not fixed for {BLOCKED_IDENTIFIER_B}"
        )
    if not isinstance(blocked_by, list) or len(blocked_by) == 0:
        raise AssertionError(
            f"blockedBy empty/missing for {BLOCKED_IDENTIFIER_B}: {blocked_by!r}"
        )

    blockers = [
        b.get("identifier", b.get("id", "?"))
        for b in blocked_by
    ]
    return f"blockedBy present ({len(blockers)} blockers): {blockers}"


CASES = [
    ("1", "missing PAPERCLIP_API_KEY -> AuthError, no HTTP", case_1_missing_key),
    ("2", "non-company-scoped BASE_URL -> WrongPathError, no HTTP", case_2_wrong_path),
    ("3", f"{ABSENT_IDENTIFIER} -> NotFound", case_3_not_found),
    ("4", f"{LIVE_IDENTIFIER} -> Ok(1 issue)", case_4_live_issue),
    (
        "5",
        f"{BLOCKED_IDENTIFIER_A} -> blockedBy present (trap-3 regression)",
        case_5_blocked_issue_a,
    ),
    (
        "6",
        f"{BLOCKED_IDENTIFIER_B} -> blockedBy non-empty (trap-3 regression)",
        case_6_blocked_issue_b,
    ),
]


def main() -> int:
    print("paperclip-issue-lookup acceptance gate — ADR-008 / NFM-2036")
    print(f"endpoint: {plu.BASE_URL or '(unset)'}\n")

    failures = 0
    for number, description, run in CASES:
        try:
            detail = run()
        except Exception as err:  # noqa: BLE001 — the gate reports, never crashes
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
        print("GATE FAILED — the helper is not done until all four cases pass.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
