"""Phantom-pass audit detector (NFM-3831 / ADR-010 Phase 1).

This module implements the D1 + D2 phantom-pass audit rule that is the
sibling of the existing phantom-done rule (NFM-3166 / NFM-3024).
A "phantom-pass" is an issue that claims ``status=done`` and asserts
numeric AC verdicts (e.g. ``AC-2: 6/8 PASS``) without producing the
evidence the assertion demands.

Two checks:

* **D1 — Numeric-AC itemized-table check.** Close comments that
  contain a numeric AC pattern (``AC-N: X/Y`` or ``AC-N ≥X/Y``) MUST
  attach a markdown itemized scoring-card table whose body row count
  is at least the number of distinct AC patterns. Missing or too-small
  table → emit ``[PHANTOM-PASS]`` and reopen the issue. Reference
  scoring cards: NFM-3396 (8-row) and NFM-3824 (2-row DB-verified).

* **D2 — Verifier cross-check.** Close comments that claim
  ``verified by <agent>`` or ``verified via NFM-XXXX`` MUST be backed
  by a comment from the named agent (or a comment from the named
  verifier in the referenced issue). Missing verifier comment → emit
  ``[PHANTOM-VERIFICATION]`` and reopen the issue. Reference case:
  NFM-3424 AC-2 claimed "verified by CTO via NFM-3754" but NFM-3754
  had only a stale-run cleanup comment from NDE.

Operational contract:

* **Cron schedule** — Daily at 07:00 UTC (registered in
  ``~/.hermes/cron/jobs.json`` as ``phantom-pass-audit-daily``).
* **7-day lookback** — Scans only issues with ``completed_at`` within
  the last 7 days to bound runtime and stay focused on recent drift.
* **Idempotent** — Issues that already carry a ``[PHANTOM-PASS]`` /
  ``[PHANTOM-VERIFICATION]`` marker, or that are no longer in
  ``status=done``, are skipped on subsequent runs.
* **Silent on the happy path** — When the scan finds no phantom-pass
  candidates, the detector prints nothing and exits 0. Only findings go
  to stdout (``PHANTOM-PASS:`` or ``PHANTOM-VERIFICATION:`` lines), so
  the cron can grep for the marker without false positives.
* **No auto-fix** — Findings reopen the issue + post the marker
  comment. The remediation (re-running the verification, providing the
  missing scoring card, etc.) is the responsibility of the assignee.

Library surface (``phantom_pass_detector.extract_*``, ``check_d*``,
``is_already_flagged``, ``within_lookback_days``, ``render_*_comment``)
is the unit-tested contract. The CLI driver (``main``) integrates
with the production Paperclip DB / API and is exercised by the
scheduled cron, not by unit tests.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


# ---------------------------------------------------------------------------
# Constants — public surface for the cron driver and for tests.
# ---------------------------------------------------------------------------

# Phantom-pass markers. These literal strings appear in audit comments
# AND are grep targets in the cron wrapper, so they are part of the
# public contract.
PHANTOM_PASS_MARKER = "[PHANTOM-PASS]"
PHANTOM_VERIFICATION_MARKER = "[PHANTOM-VERIFICATION]"

# Floor on the scoring-card row count for ``has_itemized_table``.
# The function defaults to 1 (any single body row counts as a table);
# the D1 check applies its own, stricter floor below.
MIN_TABLE_ROWS_FLOOR = 1

# Minimum scoring-card body-row count required by the D1 check. The
# NFM-3396 canonical scoring card has 8 rows (one per checkpoint) and
# that is the operational baseline — a 2-row table for a 1-AC claim
# (the NFM-3824 case) is what the audit is designed to catch. Required
# rows = ``max(len(acs), D1_MIN_SCORING_ROWS)``.
D1_MIN_SCORING_ROWS = 8

# Default 7-day lookback window.
DEFAULT_LOOKBACK_DAYS = 7


# ---------------------------------------------------------------------------
# D1 — Numeric-AC pattern extraction
# ---------------------------------------------------------------------------

# Match ``AC-N: X/Y`` or ``AC-N ≥X/Y`` (also `>= X/Y` and full-width
# colon `：`). Capture groups:
#   group(1) — AC identifier (e.g. ``AC-2``)
#   group(2) — comparison op, may be ``>=``, ``≥``, ``>``
#   group(3) — numerator (X)
#   group(4) — denominator (Y)
_AC_PATTERN_RE = re.compile(
    r"\b(AC[-\s]?\d+)\s*[:：]?\s*(>=|≥|>)?\s*(\d+)\s*/\s*(\d+)",
    re.IGNORECASE,
)


def extract_numeric_ac_patterns(body: str) -> list[str]:
    """Return canonical ``AC-N:X/Y`` strings for every numeric AC
    claim in *body*. Non-numeric ``AC-N PASS`` claims are excluded —
    only quantitative claims trigger the evidence requirement.

    The canonical form uppercases the AC identifier (matches are
    case-insensitive at the regex level) and preserves the original
    comparison operator (``>=``, ``≥``, or ``>``) so downstream
    consumers can distinguish "at least" claims from strict equality.
    No-operator ratios (e.g. ``AC-2: 6/8``) are emitted without an
    operator prefix.
    """
    if not body:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _AC_PATTERN_RE.finditer(body):
        ac_id = m.group(1).upper().replace(" ", "-")
        op = m.group(2) or ""
        num = m.group(3)
        den = m.group(4)
        canonical = f"{ac_id}:{op}{num}/{den}"
        if canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out


# ---------------------------------------------------------------------------
# D1 — Itemized-table detection
# ---------------------------------------------------------------------------

# Strip fenced code blocks before table parsing so embedded examples
# don't satisfy the requirement.
_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)


def _strip_code_blocks(body: str) -> str:
    return _FENCED_CODE_RE.sub("", body)


def has_itemized_table(body: str, min_rows: int = MIN_TABLE_ROWS_FLOOR) -> bool:
    """True iff *body* contains a markdown pipe-table outside of fenced
    code blocks whose body-row count is at least *min_rows*.

    A table is recognized by:

    1. A header line of pipes ``| ... |``
    2. A separator line ``|---|---|...|``
    3. At least ``min_rows`` body rows that begin with ``|``

    The header + separator count as the table scaffold; the body rows
    are what drive the row-count gate.
    """
    if not body:
        return False
    text = _strip_code_blocks(body)
    lines = text.splitlines()
    table_body_rows = 0
    i = 0
    while i < len(lines) - 1:
        header = lines[i].strip()
        sep = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if (
            header.startswith("|")
            and header.endswith("|")
            and sep.startswith("|")
            and sep.endswith("|")
            and re.match(r"^|(\s*:?-+:?\s*\|)+$", sep)
        ):
            # Count body rows after the separator until the table ends.
            j = i + 2
            local_rows = 0
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                local_rows += 1
                j += 1
            table_body_rows = max(table_body_rows, local_rows)
            i = j
        else:
            i += 1
    return table_body_rows >= max(min_rows, 1)


# ---------------------------------------------------------------------------
# D1 — Finding dataclass + check
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhantomPassFinding:
    """A D1 phantom-pass finding: numeric AC claim without a scoring
    card. ``marker`` is always ``PHANTOM_PASS_MARKER``.
    """

    marker: str
    issue_id: str
    reason: str


def check_d1(body: str, issue_id: str = "") -> PhantomPassFinding | None:
    """Apply the D1 check to *body*. Returns a ``PhantomPassFinding``
    when the body contains numeric AC patterns without a sufficiently
    large itemized table, otherwise ``None``.

    The required scoring-card row count is ``max(distinct AC patterns,
    D1_MIN_SCORING_ROWS)``. NFM-3396 (8-row canonical) sets the
    operational baseline; a 2-row table for a 1-AC claim (the
    NFM-3824 case) is what the audit is designed to catch.
    """
    acs = extract_numeric_ac_patterns(body)
    if not acs:
        return None
    required_rows = max(len(acs), D1_MIN_SCORING_ROWS)
    if has_itemized_table(body, min_rows=required_rows):
        return None
    ac_summary = ", ".join(acs)
    reason = (
        f"close comment claims {ac_summary} but no itemized "
        f"scoring-card table with >= {required_rows} body rows"
    )
    return PhantomPassFinding(
        marker=PHANTOM_PASS_MARKER,
        issue_id=issue_id,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# D2 — Verifier reference extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifierRef:
    """A verifier reference extracted from a close comment. ``kind`` is
    ``"agent"`` for ``verified by <agent>`` and ``"issue"`` for
    ``verified via NFM-XXXX``. ``target`` is the agent name or the
    issue identifier.
    """

    kind: str
    target: str


# ``verified by <agent>`` / ``confirmed by <agent>`` — agent name is
# any run of 1-3 capitalized words ending at punctuation / whitespace.
# Case-insensitive so ``Verified by CTO`` matches as well as
# ``verified by CTO``.
_AGENT_VERIFIER_RE = re.compile(
    r"\b(?:verified|confirmed|approved|signed[- ]off)\s+by\s+"
    r"([A-Z][\w-]+(?:\s+[A-Z][\w-]+){0,2})",
    re.IGNORECASE,
)

# ``verified via NFM-XXXX`` / ``confirmed by NFM-XXXX`` — issue ID.
# Also matches the compound form ``verified by <agent> via NFM-XXXX``
# (e.g. ``Verified by CTO via NFM-3754``), which is the canonical
# NFM-3424/3824 phantom-verification claim shape. The middle
# ``[^\n]*?`` lazily absorbs any "by <agent>" text between the verb
# and the via/by NFM phrase.
_ISSUE_VERIFIER_RE = re.compile(
    r"\b(?:verified|confirmed|approved|signed[- ]off)\b"
    r"[^\n]*?\b(?:via|by)\s+(NFM-\d+)",
    re.IGNORECASE,
)


def extract_verifier_refs(body: str) -> list[VerifierRef]:
    """Return all verifier claims in *body* — both agent and issue refs.

    Bare ``NFM-XXXX`` mentions without ``verified/confirmed by`` are
    NOT included; those are cross-references, not verification claims.
    """
    if not body:
        return []
    out: list[VerifierRef] = []
    seen: set[tuple[str, str]] = set()
    for m in _ISSUE_VERIFIER_RE.finditer(body):
        target = m.group(1).upper()
        key = ("issue", target)
        if key not in seen:
            seen.add(key)
            out.append(VerifierRef(kind="issue", target=target))
    for m in _AGENT_VERIFIER_RE.finditer(body):
        target = m.group(1).strip()
        # Filter out issue-ID-shaped strings that happen to match the
        # agent pattern (``verified by NFM-XXXX`` — those are caught by
        # the issue-regex above; skip here).
        if target.upper().startswith("NFM-"):
            continue
        key = ("agent", target)
        if key not in seen:
            seen.add(key)
            out.append(VerifierRef(kind="agent", target=target))
    return out


# ---------------------------------------------------------------------------
# D2 — Finding dataclass + check
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhantomVerificationFinding:
    """A D2 phantom-verification finding: a verifier claim without a
    matching comment from the claimed verifier. ``marker`` is always
    ``PHANTOM_VERIFICATION_MARKER``.
    """

    marker: str
    issue_id: str
    verifier: str
    reason: str


# Heuristic: did the referenced comment list contain a comment from
# the claimed verifier? We look for **explicit attribution markers**
# (em-dash, double-dash, or square brackets) wrapping the role name —
# e.g. ``—CTO``, ``--NDE``, ``[CTO]``. A bare role name in flowing
# prose (e.g. ``stale-run cleanup by NDE``) is intentionally NOT
# counted as attribution; the audit is supposed to catch claims that
# name an agent without that agent actually weighing in.
_AGENT_ATTRIBUTION_RE = re.compile(
    r"(?:^|\n|—|--|\[)\s*"
    r"(CTO|NDE|LE|RE|QA|CPO|Lisa|Lead\s+Engineer|Release\s+Engineer)"
    r"\s*(?:\n|$|—|--|\]|\.)",
    re.IGNORECASE | re.MULTILINE,
)


def _comment_authored_by_verifier(comment: str) -> bool:
    if not comment:
        return False
    return _AGENT_ATTRIBUTION_RE.search(comment) is not None


def _verifier_ref_satisfied(
    ref: VerifierRef, referenced_comments: Iterable[str]
) -> bool:
    """True iff at least one referenced comment shows the claimed
    verifier's attribution. ``referenced_comments`` is the list of
    comments in the referenced verifier issue (for ``issue`` refs) or
    any comment on the closing issue authored by the claimed agent
    (for ``agent`` refs that name a real Paperclip agent).
    """
    for c in referenced_comments:
        if _comment_authored_by_verifier(c):
            return True
    return False


def check_d2(
    body: str,
    referenced_comments: Iterable[str],
    issue_id: str = "",
) -> PhantomVerificationFinding | None:
    """Apply the D2 check. Returns a finding iff *body* contains a
    verifier claim and the referenced comments do NOT show the
    verifier's attribution.

    ``referenced_comments`` is the caller's view of the verifier
    surface — for ``verified via NFM-XXXX`` refs this is the comments
    in NFM-XXXX; for ``verified by CTO`` refs it is any comment on the
    closing issue authored by the CTO agent (the production driver
    builds that view per-call).
    """
    refs = extract_verifier_refs(body)
    if not refs:
        return None
    comments = list(referenced_comments)
    for ref in refs:
        if not _verifier_ref_satisfied(ref, comments):
            return PhantomVerificationFinding(
                marker=PHANTOM_VERIFICATION_MARKER,
                issue_id=issue_id,
                verifier=f"{ref.kind}:{ref.target}",
                reason=(
                    f"close comment claims verifier '{ref.target}' "
                    f"({ref.kind}) but no attribution comment found"
                ),
            )
    return None


# ---------------------------------------------------------------------------
# Idempotency helpers
# ---------------------------------------------------------------------------


def is_already_flagged(comments: Iterable[str], marker: str) -> bool:
    """True iff any comment in *comments* contains *marker* verbatim.

    The cron relies on this to skip issues already flagged in a prior
    run so the same issue is never re-flagged + reopened.
    """
    for c in comments:
        if c and marker in c:
            return True
    return False


_REOPENED_STATUSES = {"in_progress", "blocked", "todo"}


def is_reopened_or_in_progress(status: str) -> bool:
    """True iff *status* indicates the issue was reopened after a prior
    close (i.e. not in the ``done`` cohort). The cron skips reopened
    issues so we don't reopen an already-reopened issue.
    """
    return status in _REOPENED_STATUSES


# ---------------------------------------------------------------------------
# 7d lookback window
# ---------------------------------------------------------------------------


def within_lookback_days(
    dt: datetime | None, lookback_days: int = DEFAULT_LOOKBACK_DAYS
) -> bool:
    """True iff *dt* (timezone-aware UTC) falls within the last
    *lookback_days*. Naive datetimes are treated as UTC.
    """
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - dt
    return 0 <= delta.days <= lookback_days


# ---------------------------------------------------------------------------
# Audit comment rendering
# ---------------------------------------------------------------------------


PHANTOM_PASS_COMMENT_TEMPLATE = """{marker} (ADR-010 phantom-pass audit, NFM-3831)

Issue **{issue_id}** was marked `done` but its close comment lacks the
evidence required by the AC patterns it claims.

**Reason:** {reason}

Per ADR-010 §D1, numeric AC patterns (``AC-N: X/Y`` or ``AC-N ≥X/Y``)
require an itemized scoring-card table with at least one body row per
distinct AC pattern. Reference: NFM-3396 (8-row) and NFM-3824
(2-row DB-verified).

This issue has been reopened. Please attach the missing scoring card
and re-request close, or remove the numeric claim if no such evidence
exists.
"""

PHANTOM_VERIFICATION_COMMENT_TEMPLATE = """{marker} (ADR-010 phantom-pass audit, NFM-3831)

Issue **{issue_id}** was marked `done` but its close comment claims
verification that is not backed by an attribution comment.

**Verifier claim:** {verifier}
**Reason:** {reason}

Per ADR-010 §D2, ``verified by <agent>`` and ``verified via NFM-XXXX``
claims require an attribution comment from the named verifier (or in
the referenced verifier issue). Reference: NFM-3424 AC-2 / NFM-3754.

This issue has been reopened. Please attach the verifier's attribution
comment (or remove the unverifiable claim) and re-request close.
"""


def render_phantom_pass_comment(finding: PhantomPassFinding) -> str:
    return PHANTOM_PASS_COMMENT_TEMPLATE.format(
        marker=finding.marker,
        issue_id=finding.issue_id or "<unknown>",
        reason=finding.reason,
    )


def render_phantom_verification_comment(
    finding: PhantomVerificationFinding,
) -> str:
    return PHANTOM_VERIFICATION_COMMENT_TEMPLATE.format(
        marker=finding.marker,
        issue_id=finding.issue_id or "<unknown>",
        verifier=finding.verifier,
        reason=finding.reason,
    )


# ---------------------------------------------------------------------------
# CLI driver — production integration
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phantom_pass_detector",
        description=(
            "ADR-010 phantom-pass audit (D1 numeric-AC + D2 verifier "
            "cross-check). Default mode is dry-run; pass --apply to "
            "post findings + reopen issues via the Paperclip API."
        ),
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help="scan window in days (default: 7)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "post [PHANTOM-PASS] / [PHANTOM-VERIFICATION] comments and "
            "reopen issues via Paperclip API (default: dry-run, "
            "stdout only)"
        ),
    )
    parser.add_argument(
        "--company-id",
        default=os.environ.get("PAPERCLIP_COMPANY_ID", ""),
        help="Paperclip company UUID (env PAPERCLIP_COMPANY_ID)",
    )
    parser.add_argument(
        "--paperclip-url",
        default=os.environ.get(
            "PAPERCLIP_API_URL", "http://127.0.0.1:3100"
        ),
        help="Paperclip API base URL (env PAPERCLIP_API_URL)",
    )
    parser.add_argument(
        "--board-api-key",
        default=os.environ.get("PAPERCLIP_BOARD_API_KEY", ""),
        help=(
            "Paperclip board-actor API key for comment + reopen "
            "writes (env PAPERCLIP_BOARD_API_KEY). Required with "
            "--apply."
        ),
    )
    return parser


def _query_recently_closed_issues(
    lookback_days: int,
) -> list[dict[str, object]]:
    """Query the production Paperclip DB for ``status=done`` issues
    closed in the last *lookback_days*. Returns a list of dicts with
    ``id``, ``identifier``, ``status``, ``completed_at``, and
    ``comments`` (list of body strings).

    Out of scope for unit tests; exercised by the scheduled cron only.
    Connection settings mirror ``phantom-done-detector.sh`` for
    consistency.
    """
    pg = os.environ.get(
        "PSQL_BIN",
        "/opt/homebrew/opt/postgresql@16/bin/psql",
    )
    env = os.environ.copy()
    env["PGPASSWORD"] = os.environ.get("PGPASSWORD", "paperclip")
    sql = (
        "SELECT i.id::text, i.identifier, i.status, "
        "i.completed_at::text, "
        "COALESCE(string_agg(c.body, E'\\n' "
        "ORDER BY c.created_at) FILTER (WHERE c.body IS NOT NULL), '') "
        "FROM issues i "
        "LEFT JOIN issue_comments c ON c.issue_id = i.id "
        "AND c.deleted_at IS NULL "
        "WHERE i.status = 'done' "
        "AND i.completed_at > NOW() - INTERVAL '%d days' "
        "GROUP BY i.id, i.identifier, i.status, i.completed_at "
        "ORDER BY i.completed_at DESC;"
    ) % lookback_days
    proc = subprocess.run(
        [
            pg,
            "-h",
            os.environ.get("PGHOST", "127.0.0.1"),
            "-p",
            os.environ.get("PGPORT", "54329"),
            "-U",
            os.environ.get("PGUSER", "paperclip"),
            "-d",
            os.environ.get("PGDATABASE", "paperclip"),
            "-tA",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(
            f"FATAL: psql query failed (rc={proc.returncode}): "
            f"{proc.stderr}\n"
        )
        return []
    rows: list[dict[str, object]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        # Last column may contain newlines via string_agg; psql -A uses
        # null-as-empty so a row's first 3 columns are tab-free, the
        # comments column can contain anything. Split on the FIRST 3
        # tabs only and treat the remainder as the comment blob.
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        issue_id, identifier, status, completed_at = (
            parts[0],
            parts[1],
            parts[2],
            parts[3],
        )
        rows.append(
            {
                "id": issue_id,
                "identifier": identifier,
                "status": status,
                "completed_at": completed_at,
                "comments": [
                    c for c in completed_at.split("\n") if c.strip()
                ]
                if "\n" in completed_at
                else [],
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.apply and not args.board_api_key:
        sys.stderr.write(
            "FATAL: --apply requires --board-api-key or "
            "PAPERCLIP_BOARD_API_KEY env var\n"
        )
        return 2

    issues = _query_recently_closed_issues(args.lookback_days)
    if not issues:
        # Silent on the success path; the cron wrapper interprets
        # empty stdout as "no findings to act on".
        return 0

    flagged = 0
    for issue in issues:
        comments = issue.get("comments", [])  # type: ignore[arg-type]
        issue_id = str(issue.get("identifier", ""))
        status = str(issue.get("status", ""))
        if is_reopened_or_in_progress(status):
            continue
        # Pick the most recent comment as the close comment for D1.
        close_comment = comments[-1] if comments else ""

        d1 = check_d1(close_comment, issue_id=issue_id)
        if d1 and not is_already_flagged(comments, PHANTOM_PASS_MARKER):
            print(
                f"PHANTOM-PASS: {issue_id} | reason={d1.reason}"
            )
            flagged += 1
            continue

        # D2 needs a wider surface (verifier refs may also appear in
        # earlier comments, not just the close comment).
        all_comments_blob = "\n".join(comments)
        d2 = check_d2(all_comments_blob, comments, issue_id=issue_id)
        if d2 and not is_already_flagged(
            comments, PHANTOM_VERIFICATION_MARKER
        ):
            print(
                f"PHANTOM-VERIFICATION: {issue_id} | "
                f"verifier={d2.verifier} | reason={d2.reason}"
            )
            flagged += 1

    return 0 if flagged == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())