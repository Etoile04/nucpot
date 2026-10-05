#!/usr/bin/env python3
"""PreCompletionMerge — advisory + flag-gated blocking hook (NFM-4847 / NFM-5314 / KR-WF-3).

Advisory mode (default, out-of-the-box): after a successful issue-status
write toward ``done``, re-read the issue's expanded blocker chain and warn
when unresolved blockers remain.

Blocking mode (opt-in per environment, NFM-5314): when ``PCMR_ENFORCE`` is
truthy, the SAME script is wired as a PreToolUse hook and denies the done-ward
write outright while the chain is unresolved.

Why (KR-WF-3)
--------------
W35/W36 lockout recurrences came from ``done``/merge PATCHes issued while
``blockedByIssueIds`` chains were unresolved (auth-boundary 403 playbook:
18+ instances; comment-drop/auth-flip cluster: 11 cases).  Surfacing the
chain at the moment of the write prevents the *class*, not the instance.
Advisory can only annotate after the fact; blocking prevents the write.

Design constraints
------------------
* Blockers are read ONLY via ``scripts/paperclip_issue_lookup.py``, which
  re-fetches the bare ``GET /api/issues/{uuid}`` payload.  The collection
  read strips ``blockedBy`` / ``terminalBlockers`` (trap-3, ADR-008 /
  NFM-2036) and would report a false "no blockers".
* Advisory (PostToolUse, always on): the warning goes to stderr with exit
  code 2, which feeds it back to the agent — a PostToolUse hook runs
  *after* the write landed, so exit 2 cannot un-write it.  This path is
  byte-identical to the W38 advisory v1 regardless of the flag.
* Blocking (PreToolUse, gated): with ``PCMR_ENFORCE`` on, a done-ward write
  with an unresolved chain is denied (stderr + exit 2 BEFORE the tool
  runs).  Blocking fires ONLY on genuine unresolved chains — a
  ``terminalBlockers``-only chain does not hold the write.  Fail-open:
  lookup failures and unknown identifiers never deny a write (the hook
  must not become the lockout class it guards against).
* Never raises into the write path: any internal failure degrades to a
  one-line stderr note and exit 0.

Flag / rollback (spec item 3)
-----------------------------
* ``PCMR_ENFORCE`` — default **OFF**.  Truthy: ``1``/``true``/``yes``/``on``
  (case-insensitive).  Declared ``"0"`` in the tracked
  ``.claude/settings.json`` ``env`` block; an environment opts in by
  setting ``PCMR_ENFORCE=1`` in ``.claude/settings.local.json`` (untracked,
  per-environment) or its session env.
* **Rollback = flag off**: unset ``PCMR_ENFORCE`` (or set ``0``) and the
  PreToolUse invocation returns to a silent exit-0 no-op — advisory
  behavior only, byte-identical to before the flag existed.  No code
  rollback needed.
* Intentional-carry hatch: in blocking mode, a Bash write whose command
  carries the literal marker ``# pcmr-carry`` is allowed through (the
  PostToolUse advisory still annotates).  Record the carry in a comment on
  the issue FIRST, then mark the write.
* Soak audit (AC3): every enforce-mode decision on a done-ward write
  appends one tab-separated line to ``.pcmr_enforce.log`` at the repo root
  (override with ``PCMR_LOG``; gitignored).  Advisory mode never writes it.

Modes
-----
* hook mode (default): a hook payload on stdin.  ``hook_event_name``
  selects the path: ``PreToolUse`` → pre-write gate (blocking when the
  flag is on, silent no-op when off); anything else (``PostToolUse`` or
  absent, for older runtimes) → the advisory path.  Relevant tools:
  ``TaskUpdate`` with a done-ward status, or ``Bash`` running a curl PATCH
  against ``/api/issues/...`` with a done-ward status body.
* ``--dry-run ID [ID...]``: replay the evaluation for live issues and print
  the verdict each write would have produced (AC1 evidence), including the
  collection-path contrast that demonstrates trap-3 and the blocking-mode
  verdict under ``PCMR_ENFORCE=1``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from paperclip_issue_lookup import (  # noqa: E402  (path bootstrap above)
    NotFound,
    Ok,
    lookup_issue,
    lookup_issues,
)

# TaskUpdate reports "completed"; raw PATCH bodies use Paperclip's "done".
DONE_STATUSES = frozenset({"done", "completed"})
# A blocker in a terminal state no longer holds the chain open.
RESOLVED_STATUSES = frozenset({"done", "cancelled"})

# Blocking-mode flag (NFM-5314). Default OFF; rollback = unset it.
ENFORCE_ENV = "PCMR_ENFORCE"
ENFORCE_TRUTHY = frozenset({"1", "true", "yes", "on"})
# Intentional-carry hatch for blocking mode (see module docstring).
CARRY_MARKER = "# pcmr-carry"
# Soak audit log (AC3): enforce-mode decisions only, one line per done-ward
# write. Override location with PCMR_LOG; default sits at the repo root and
# is gitignored. Advisory mode never writes it.
DEFAULT_LOG = Path(__file__).resolve().parents[2] / ".pcmr_enforce.log"


def audit_log_path(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    return Path(str(source.get("PCMR_LOG") or DEFAULT_LOG))


def audit(env: Mapping[str, str], line: str) -> None:
    """Append one enforce-mode decision line; never break the hook for logging."""
    try:
        with audit_log_path(env).open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip("\n") + "\n")
    except OSError:
        pass  # audit is best-effort by construction

# A Bash write counts only when it PATCHes an issue URL with a done-ward body.
_PATCH_VERB = re.compile(r"\bPATCH\b")
_ISSUE_URL = re.compile(r"/api/issues/([A-Za-z0-9-]+)")
_DONE_BODY = re.compile(r"[\"']status[\"']\s*:\s*[\"'](done|completed)[\"']", re.IGNORECASE)


@dataclass(frozen=True)
class Verdict:
    """Evaluation of one issue's blocker chain at (simulated) done-time."""

    identifier: str
    status: str | None
    unresolved: tuple[tuple[str, str], ...]
    terminal: tuple[str, ...]
    note: str = ""

    @property
    def clean(self) -> bool:
        return not self.unresolved and not self.terminal


def _from_taskupdate(tool_input: dict[str, Any]) -> str | None:
    status = tool_input.get("status")
    if isinstance(status, str) and status.strip().lower() in DONE_STATUSES:
        task_id = tool_input.get("taskId") or tool_input.get("id")
        if isinstance(task_id, str) and task_id.strip():
            return task_id.strip()
    return None


def _from_bash(tool_input: dict[str, Any]) -> str | None:
    command = tool_input.get("command")
    if not isinstance(command, str):
        return None
    if not _PATCH_VERB.search(command) or not _DONE_BODY.search(command):
        return None
    match = _ISSUE_URL.findall(command)
    return match[-1] if match else None


def target_issue(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Return the issue id a done-ward write targeted, else ``None``."""
    if tool_name == "TaskUpdate":
        return _from_taskupdate(tool_input)
    if tool_name == "Bash":
        return _from_bash(tool_input)
    return None


def evaluate(identifier: str) -> Verdict:
    """Read the expanded blocker chain for ``identifier`` via the sanctioned helper."""
    result = lookup_issue(identifier)
    if isinstance(result, NotFound):
        return Verdict(identifier, None, (), (), note="identifier not found — skipped")
    if not isinstance(result, Ok):
        return Verdict(identifier, None, (), (), note=f"lookup failed: {type(result).__name__}")

    issue = result.issues[0]
    blocked_by = issue.get("blockedBy") or []
    unresolved = tuple(
        (str(b.get("identifier") or b.get("id") or "?"), str(b.get("status") or "?"))
        for b in blocked_by
        if str(b.get("status") or "").lower() not in RESOLVED_STATUSES
    )
    terminal = tuple(
        str(b.get("identifier") or b.get("id") or "?")
        for b in (issue.get("terminalBlockers") or [])
    )
    return Verdict(
        identifier=str(issue.get("identifier") or identifier),
        status=issue.get("status"),
        unresolved=unresolved,
        terminal=terminal,
    )


def render_warning(verdict: Verdict) -> str:
    """The advisory block fed back to the agent after a done-ward write."""
    lines = [
        f"⚠ PreCompletionMerge: {verdict.identifier} patched toward done "
        "but the blocker chain is unresolved (KR-WF-3 / NFM-4847).",
    ]
    if verdict.unresolved:
        lines.append(f"  unresolved blockers ({len(verdict.unresolved)}):")
        lines.extend(f"    - {ident} [{status}]" for ident, status in verdict.unresolved)
    if verdict.terminal:
        lines.append(f"  terminal blockers ({len(verdict.terminal)}):")
        lines.extend(f"    - {ident}" for ident in verdict.terminal)
    lines.append(
        "  Advisory only (v1). Resolve the chain, or record the intentional "
        "carry before merge/close — see memory: nfm-4847-precompletion-merge-hook."
    )
    return "\n".join(lines)


def render_block(verdict: Verdict) -> str:
    """The blocking denial fed back to the agent BEFORE a done-ward write."""
    lines = [
        f"⛔ PreCompletionMerge (enforce): done-ward write to {verdict.identifier} "
        "DENIED — blocker chain unresolved (KR-WF-3 / NFM-5314).",
        f"  unresolved blockers ({len(verdict.unresolved)}):",
    ]
    lines.extend(f"    - {ident} [{status}]" for ident, status in verdict.unresolved)
    lines.extend(
        [
            "  Resolve the chain first, then re-issue the write.",
            "  Intentional carry: comment the carry on the issue, then re-issue "
            f"with `{CARRY_MARKER}` in the command.",
            "  Rollback: unset PCMR_ENFORCE (default OFF) — see memory: "
            "nfm-5314-pcmr-enforce-blocking-variant.",
        ]
    )
    return "\n".join(lines)


def enforce_enabled(env: Mapping[str, str] | None = None) -> bool:
    """PCMR_ENFORCE gate — default OFF; advisory stays the out-of-box behavior."""
    source = os.environ if env is None else env
    return str(source.get(ENFORCE_ENV, "")).strip().lower() in ENFORCE_TRUTHY


def collection_blocked_by(identifier: str) -> str:
    """Contrast read: what the collection path would have reported (trap-3)."""
    result = lookup_issues(identifier=identifier)
    if isinstance(result, Ok) and result.issues:
        raw = result.issues[0].get("blockedBy")
        return "absent/stripped" if not raw else f"{len(raw)} (unexpected — not stripped?)"
    return f"unavailable ({type(result).__name__})"


def run_hook(payload: dict[str, Any], env: Mapping[str, str] | None = None) -> int:
    """Hook-mode entry: PreToolUse → gate (flag-gated), else advisory."""
    event = str(payload.get("hook_event_name") or "")
    if event == "PreToolUse":
        if enforce_enabled(env):
            return run_pre_write(payload, env)
        return 0  # flag off — silent no-op; advisory lives on PostToolUse only
    return run_post_write(payload)


def run_pre_write(payload: dict[str, Any], env: Mapping[str, str] | None = None) -> int:
    """Blocking gate (PCMR_ENFORCE on): deny done-ward writes with open chains.

    Fail-open everywhere: only a verified unresolved chain denies the write.
    """
    source = os.environ if env is None else env
    tool_input = payload.get("tool_input")
    identifier = target_issue(str(payload.get("tool_name") or ""), tool_input or {})
    if identifier is None:
        return 0

    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if isinstance(command, str) and CARRY_MARKER in command:
        print(
            f"[pcmr] {identifier}: intentional carry ({CARRY_MARKER.strip()}) — "
            "write allowed; PostToolUse advisory will annotate",
            file=sys.stderr,
        )
        audit(source, f"carry\t{identifier}\twrite allowed (intentional carry)")
        return 0

    try:
        verdict = evaluate(identifier)
    except Exception as exc:
        print(f"[pcmr] pre-write evaluation failed for {identifier}: {exc} — fail-open", file=sys.stderr)
        audit(source, f"fail-open\t{identifier}\tevaluation error: {exc}")
        return 0

    if verdict.note:
        print(f"[pcmr] {verdict.identifier}: {verdict.note} — fail-open", file=sys.stderr)
        audit(source, f"fail-open\t{verdict.identifier}\t{verdict.note}")
        return 0
    if verdict.unresolved:
        print(render_block(verdict), file=sys.stderr)
        audit(source, f"BLOCK\t{verdict.identifier}\tunresolved: " + ", ".join(f"{i}[{s}]" for i, s in verdict.unresolved))
        return 2
    terminal_note = " (+ terminal-only, does not hold)" if verdict.terminal else ""
    print(f"[pcmr] {verdict.identifier}: chain clean — done-ward write allowed{terminal_note}", file=sys.stderr)
    audit(source, f"allow\t{verdict.identifier}\tchain clean{terminal_note}")
    return 0


def run_post_write(payload: dict[str, Any]) -> int:
    """Advisory path (PostToolUse or legacy payloads): warn, never block, never crash."""
    tool_input = payload.get("tool_input")
    identifier = target_issue(str(payload.get("tool_name") or ""), tool_input or {})
    if identifier is None:
        return 0

    try:
        verdict = evaluate(identifier)
    except Exception as exc:
        print(f"[pcmr] evaluation failed for {identifier}: {exc}", file=sys.stderr)
        return 0

    if verdict.note:
        print(f"[pcmr] {verdict.identifier}: {verdict.note}", file=sys.stderr)
        return 0
    if verdict.clean:
        print(f"[pcmr] {verdict.identifier}: blocker chain clean (0 unresolved)")
        return 0
    print(render_warning(verdict), file=sys.stderr)
    return 2


def run_dry_run(identifiers: list[str]) -> int:
    """AC1 evidence: replay the verdict for each live done-PATCH target."""
    for identifier in identifiers:
        try:
            verdict = evaluate(identifier)
            contrast = collection_blocked_by(identifier)
        except Exception as exc:
            print(f"{identifier}: SKIPPED — evaluation error: {exc}")
            continue
        if verdict.note:
            print(f"{verdict.identifier}: SKIPPED — {verdict.note}")
            continue
        chain = ", ".join(f"{i}[{s}]" for i, s in verdict.unresolved) or "-"
        term = ", ".join(verdict.terminal) or "-"
        would_warn = "WARN (exit 2)" if not verdict.clean else "clean (exit 0)"
        would_block = "BLOCK (exit 2, write denied)" if verdict.unresolved else "allow"
        print(
            f"{verdict.identifier} status={verdict.status} | unresolved=[{chain}] "
            f"| terminal=[{term}] | collection-path blockedBy: {contrast} "
            f"| advisory: {would_warn} | enforce: {would_block}"
        )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) > 2 and argv[1] == "--dry-run":
        return run_dry_run(argv[2:])
    if len(argv) == 2 and argv[1] == "--dry-run":
        print("usage: pre_completion_merge.py --dry-run ID [ID...]", file=sys.stderr)
        return 2
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # not a hook payload — stay silent and advisory
    if not isinstance(payload, dict):
        return 0
    return run_hook(payload)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
