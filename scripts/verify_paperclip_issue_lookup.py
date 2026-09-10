#!/usr/bin/env python3
"""Acceptance gate for `paperclip_issue_lookup` — ADR-008 / NFM-2036 / NFM-4540.

Runs ten cases (six live, four spy/no-HTTP) and prints PASS / FAIL for each.
Exits non-zero if any case fails.

    python3 scripts/verify_paperclip_issue_lookup.py

| #  | Mode  | Setup                                       | Expected                          |
|----|-------|---------------------------------------------|-----------------------------------|
| 1  | spy   | no PAPERCLIP_API_KEY, lookup_issue          | AuthError raised, zero HTTP calls |
| 2  | spy   | key restored, BASE_URL not company-scoped   | WrongPath raised, zero HTTP calls |
| 3  | live  | lookup_issue("NFM-DOES-NOT-EXIST-9999")     | NotFound, distinct from errors    |
| 4  | live  | lookup_issue("NFM-1909")                    | Ok, 1 issue, pages_consumed == 1  |
| 5  | live  | lookup_issue("NFM-2113") — known blocked    | blockedBy present, non-empty      |
| 6  | live  | lookup_issue("NFM-2092") — known blocked    | blockedBy present, non-empty      |
| 7  | spy   | D1: lookup_issues(dict)                     | TypeError, zero HTTP calls        |
| 8  | spy   | D2: lookup_issues(status="done")            | TypeError, zero HTTP calls        |
| 9  | spy   | D3: lookup_issues(identifier=...)           | client-side filter, only matches  |
| 10 | spy   | D3: lookup_issues(parent_issue_id=...)      | client-side filter, only matches  |

Cases 1, 2, 7, and 8 must prove *no HTTP call was attempted*. They do that by
replacing the helper's `requests` module with a spy that records any call and
refuses to perform it — so a regression that moved the guards to after the
request would fail loudly rather than silently pass.

Cases 9 and 10 stub `requests.get` with a captured response so the regression
is deterministic and runs offline (no PAPERCLIP_API_URL needed).
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

# Known-blocked issues used by the trap-3 regression cases.
BLOCKED_IDENTIFIER_A = "NFM-2113"  # blockers: NFM-2110, NFM-2111, NFM-2112
BLOCKED_IDENTIFIER_B = "NFM-2092"  # multiple blockers


class SpyRequests:
    """Stands in for `requests`. Records calls; never performs one."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("helper opened an HTTP connection before its pre-flight guards ran")


class FakeResponse:
    """Captured response object — never the network. JSON-only shape."""

    def __init__(self, status_code: int, body) -> None:
        self.status_code = status_code
        self._body = body
        self.text = ""

    def json(self):
        return self._body


class CapturingRequests:
    """Stands in for `requests` but returns a pre-canned response. Records calls."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        self.calls.append(
            {
                "url": url,
                "params": dict(params or {}),
                "headers": dict(headers or {}),
                "timeout": timeout,
            }
        )
        return self.response


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
        raise AssertionError(f"blockedBy is empty list/missing — trap-3 not fixed: {blocked_by!r}")

    blockers = [b.get("identifier", b.get("id", "?")) for b in blocked_by]
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
        raise AssertionError(f"blockedBy empty/missing for {BLOCKED_IDENTIFIER_B}: {blocked_by!r}")

    blockers = [b.get("identifier", b.get("id", "?")) for b in blocked_by]
    return f"blockedBy present ({len(blockers)} blockers): {blockers}"


# --- NFM-4540 regression cases (D1, D2, D3) -----------------------------------
#
# These four cases are pure spy/no-HTTP: they stub `requests.get` with a
# CapturingRequests so the helper never touches the network, then assert that
# the safety-property guards reject the silent-wrong-result paths. Cases 7
# and 8 expect TypeError; cases 9 and 10 expect client-side re-verification
# to drop rows that the server would silently have returned unfiltered.


# A captured response body: 5 rows where 3 actually match `parent_issue_id`
# "parent-uuid" and where 1 of those also matches `identifier="NFM-MATCH-1"`.
# The other rows are unrelated — they are what the server silently returned
# under the false pretence of being filtered (the D3 trap).
SAMPLE_PARENT_UUID = "11111111-2222-3333-4444-555555555555"
SAMPLE_IDENTIFIER = "NFM-MATCH-1"

_SAMPLE_ROWS = [
    {"id": "row-a", "identifier": "NFM-MATCH-1", "parentId": SAMPLE_PARENT_UUID},
    {"id": "row-b", "identifier": "NFM-MATCH-1", "parentId": "different-parent"},
    {"id": "row-c", "identifier": "NFM-MATCH-1", "parentId": SAMPLE_PARENT_UUID},
    {"id": "row-d", "identifier": "NFM-NOISE", "parentId": SAMPLE_PARENT_UUID},
    {"id": "row-e", "identifier": "NFM-NOISE", "parentId": "yet-another-parent"},
]


def _restore_real_requests() -> None:
    """Restore real requests module + base URL after a CapturingRequests run."""
    plu.requests = _REAL_REQUESTS
    plu.BASE_URL = _REAL_BASE_URL


# Module-level snapshot of the real module state so we can restore after the
# capture runs. Set at import time (the helper is a library import — its
# module reference is stable for the rest of the process).
_REAL_REQUESTS = plu.requests
_REAL_BASE_URL = plu.BASE_URL


def case_7_dict_q_raises() -> str:
    """D1: lookup_issues({'parentIssueId': ...}) raises — never silently degrades."""
    capturing = CapturingRequests(FakeResponse(200, []))
    plu.requests = capturing
    try:
        try:
            plu.lookup_issues({"parentIssueId": "ignored-by-server"})
        except TypeError as err:
            msg = str(err)
            if "parent_issue_id" not in msg and "identifier" not in msg:
                raise AssertionError(
                    f"TypeError did not name a correct keyword: {msg}"
                )
            if capturing.calls:
                raise AssertionError(
                    f"helper opened {len(capturing.calls)} HTTP call(s) "
                    "before the q-shape guard rejected"
                )
            return f"TypeError raised pre-flight, 0 HTTP calls — {err}"
        raise AssertionError("expected TypeError, got a successful return")
    finally:
        _restore_real_requests()


def case_8_str_status_raises() -> str:
    """D2: lookup_issues(status='done') raises — never silently returns Ok([])."""
    capturing = CapturingRequests(
        FakeResponse(200, [{"id": "x", "identifier": "NFM-X", "status": "done"}])
    )
    plu.requests = capturing
    try:
        try:
            result = plu.lookup_issues(status="done")
        except TypeError as err:
            if capturing.calls:
                raise AssertionError(
                    f"helper opened {len(capturing.calls)} HTTP call(s) "
                    "before the status-shape guard rejected"
                )
            msg = str(err)
            if "list" not in msg.lower():
                raise AssertionError(
                    f"TypeError did not mention 'list': {msg}"
                )
            return f"TypeError raised pre-flight, 0 HTTP calls — {err}"
        # Per the AC: "raises OR returns the correct rows". Either disposition
        # is acceptable; this case exercises the explicit-raise disposition.
        raise AssertionError(
            f"expected TypeError OR correct filtered rows, got {result!r}"
        )
    finally:
        _restore_real_requests()


def case_9_identifier_client_side_filter() -> str:
    """D3 (identifier): only rows whose identifier field exactly matches are returned."""
    capturing = CapturingRequests(FakeResponse(200, _SAMPLE_ROWS))
    plu.requests = capturing
    try:
        result = plu.lookup_issues(identifier=SAMPLE_IDENTIFIER)
        if not isinstance(result, plu.Ok):
            raise AssertionError(f"expected Ok, got {result!r}")
        returned_ids = [row.get("identifier") for row in result.issues]
        if returned_ids != ["NFM-MATCH-1", "NFM-MATCH-1", "NFM-MATCH-1"]:
            raise AssertionError(
                f"client-side identifier filter failed: got {returned_ids!r}, "
                "expected 3x NFM-MATCH-1 (row-d / row-e dropped by D3 guard)"
            )
        if not capturing.calls:
            raise AssertionError("expected at least one HTTP call (server still queried)")
        return f"Ok({len(result.issues)} rows after client-side filter: {returned_ids})"
    finally:
        _restore_real_requests()


def case_10_parent_issue_id_client_side_filter() -> str:
    """D3 (parent_issue_id): only rows whose parentId field exactly matches are returned."""
    capturing = CapturingRequests(FakeResponse(200, _SAMPLE_ROWS))
    plu.requests = capturing
    try:
        result = plu.lookup_issues(parent_issue_id=SAMPLE_PARENT_UUID)
        if not isinstance(result, plu.Ok):
            raise AssertionError(f"expected Ok, got {result!r}")
        returned_parents = sorted({row.get("parentId") for row in result.issues})
        if returned_parents != [SAMPLE_PARENT_UUID]:
            raise AssertionError(
                f"client-side parentId filter failed: parents={returned_parents!r}, "
                "expected only the SAMPLE_PARENT_UUID (row-b / row-e dropped by D3 guard)"
            )
        if len(result.issues) != 3:
            raise AssertionError(
                f"expected 3 rows under SAMPLE_PARENT_UUID, got {len(result.issues)}: "
                f"{[r.get('id') for r in result.issues]}"
            )
        return f"Ok({len(result.issues)} rows after parentId filter: parents={returned_parents})"
    finally:
        _restore_real_requests()


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
    (
        "7",
        "D1: lookup_issues({...}) -> TypeError, 0 HTTP calls (regression guard)",
        case_7_dict_q_raises,
    ),
    (
        "8",
        "D2: lookup_issues(status='done') -> TypeError, 0 HTTP calls (regression guard)",
        case_8_str_status_raises,
    ),
    (
        "9",
        f"D3 identifier: lookup_issues(identifier={SAMPLE_IDENTIFIER!r}) client-side filter",
        case_9_identifier_client_side_filter,
    ),
    (
        "10",
        f"D3 parent_issue_id: lookup_issues(parent_issue_id={SAMPLE_PARENT_UUID!r}) client-side filter",
        case_10_parent_issue_id_client_side_filter,
    ),
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
        print("GATE FAILED — the helper is not done until all four cases pass.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
