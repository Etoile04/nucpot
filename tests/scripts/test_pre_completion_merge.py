"""Unit tests for the PreCompletionMerge hook (NFM-4847 advisory / NFM-5314 blocking).

All lookups are monkeypatched — these tests are offline and exercise exactly
the surfaces that must not regress: done-ward write detection, blocker-chain
verdicts, the advisory exit-code policy (warn, never crash, never block), and
the flag-gated blocking gate (PCMR_ENFORCE: deny only verified unresolved
chains, fail-open, silent no-op when off).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "hooks"
sys.path.insert(0, str(_HOOKS_DIR))

import pre_completion_merge as pcm  # noqa: E402
from paperclip_issue_lookup import NotFound, Ok  # noqa: E402


def _issue(
    identifier: str = "NFM-1",
    status: str = "done",
    blocked: list[dict[str, Any]] | None = None,
    terminal: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "identifier": identifier,
        "status": status,
        "blockedBy": blocked or [],
        "terminalBlockers": terminal or [],
    }


def _ok(issue: dict[str, Any]) -> Ok:
    return Ok(issues=[issue], pages_consumed=1)


# --- done-ward write detection ------------------------------------------------


def test_taskupdate_done_detected() -> None:
    assert pcm.target_issue("TaskUpdate", {"taskId": "NFM-4847", "status": "done"}) == "NFM-4847"


def test_taskupdate_completed_detected() -> None:
    assert pcm.target_issue("TaskUpdate", {"taskId": "uuid-1", "status": "completed"}) == "uuid-1"


def test_taskupdate_non_done_ignored() -> None:
    assert pcm.target_issue("TaskUpdate", {"taskId": "NFM-1", "status": "in_progress"}) is None


def test_taskupdate_missing_id_ignored() -> None:
    assert pcm.target_issue("TaskUpdate", {"status": "done"}) is None


def test_bash_curl_patch_done_detected() -> None:
    command = (
        'curl -s -X PATCH "$PAPERCLIP_API_URL/api/issues/NFM-4749" '
        "-H 'Content-Type: application/json' -d '{\"status\": \"done\"}'"
    )
    assert pcm.target_issue("Bash", {"command": command}) == "NFM-4749"


def test_bash_get_ignored() -> None:
    command = 'curl -s "$PAPERCLIP_API_URL/api/issues/NFM-4749"'
    assert pcm.target_issue("Bash", {"command": command}) is None


def test_bash_patch_other_status_ignored() -> None:
    command = (
        'curl -s -X PATCH "$PAPERCLIP_API_URL/api/issues/NFM-4749" -d \'{"status": "in_progress"}\''
    )
    assert pcm.target_issue("Bash", {"command": command}) is None


def test_other_tool_ignored() -> None:
    assert pcm.target_issue("Write", {"file_path": "/tmp/x", "content": "done"}) is None


# --- statement-scoped target inference (NFM-5562) ---------------------------------


def test_bash_get_after_patch_does_not_steal_target() -> None:
    # Live soak false positive (2026-10-05): the done-write went to a clean
    # chain while a trailing GET merely read a blocked issue — the old
    # last-URL-anywhere rule denied the write as targeting the co-mention.
    command = (
        'curl -s -X PATCH "$PAPERCLIP_API_URL/api/issues/NFM-CLEAN" '
        "-d '{\"status\": \"done\"}' && "
        'curl -s "$PAPERCLIP_API_URL/api/issues/NFM-BLOCKED" | jq .blockedBy'
    )
    assert pcm.target_issue("Bash", {"command": command}) == "NFM-CLEAN"


def test_bash_get_before_patch_does_not_steal_target() -> None:
    command = (
        'curl -s "$PAPERCLIP_API_URL/api/issues/NFM-BLOCKED" | jq . ; '
        'curl -s -X PATCH "$PAPERCLIP_API_URL/api/issues/NFM-CLEAN" '
        "-d '{\"status\": \"done\"}'"
    )
    assert pcm.target_issue("Bash", {"command": command}) == "NFM-CLEAN"


def test_bash_done_body_via_variable_still_detected() -> None:
    # The body lives in an assignment statement; the write statement carries
    # it as -d "$BODY" — variable tracking keeps the write in scope.
    command = (
        "BODY='{\"status\": \"done\"}' ; "
        'curl -s -X PATCH "$PAPERCLIP_API_URL/api/issues/NFM-5323" -d "$BODY"'
    )
    assert pcm.target_issue("Bash", {"command": command}) == "NFM-5323"


def test_bash_done_literal_without_patch_url_is_not_a_write() -> None:
    command = (
        "echo '{\"status\": \"done\"}' ; "
        'curl -s "$PAPERCLIP_API_URL/api/issues/NFM-BLOCKED"'
    )
    assert pcm.target_issue("Bash", {"command": command}) is None


def test_bash_multiline_patch_then_get_targets_patch_line() -> None:
    command = (
        'curl -s -X PATCH "$PAPERCLIP_API_URL/api/issues/NFM-A" -d \'{"status": "done"}\'\n'
        'curl -s "$PAPERCLIP_API_URL/api/issues/NFM-B"'
    )
    assert pcm.target_issue("Bash", {"command": command}) == "NFM-A"


def test_bash_line_continuation_kept_in_one_statement() -> None:
    command = (
        "curl -s -X PATCH \\\n  '$PAPERCLIP_API_URL/api/issues/NFM-1' \\\n"
        "  -d '{\"status\": \"done\"}'"
    )
    assert pcm.target_issue("Bash", {"command": command}) == "NFM-1"


def test_bash_separator_inside_quotes_does_not_split() -> None:
    command = (
        'curl -s -X PATCH "$PAPERCLIP_API_URL/api/issues/NFM-1" '
        "-d '{\"status\": \"done\", \"note\": \"a;b&c|d\"}'"
    )
    assert pcm.target_issue("Bash", {"command": command}) == "NFM-1"


def test_bash_body_from_file_fails_open() -> None:
    # -d @file carries no literal body in the command — undetectable, so the
    # gate allows and the advisory skips (documented fail-open shape).
    command = 'curl -s -X PATCH "$PAPERCLIP_API_URL/api/issues/NFM-1" -d @body.json'
    assert pcm.target_issue("Bash", {"command": command}) is None


def test_pretooluse_enforce_allows_write_with_trailing_read_of_blocked_issue(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Gate-level regression: the exact live shape (clean-chain done-write +
    # trailing GET of a blocked issue) must now be allowed, and the blocked
    # co-mention must never even be evaluated.
    def _lookup(ident: str) -> Ok:
        assert ident == "NFM-CLEAN", f"must not evaluate co-mentioned {ident}"
        return _ok(_issue(identifier="NFM-CLEAN"))

    monkeypatch.setattr(pcm, "lookup_issue", _lookup)
    command = (
        'curl -s -X PATCH "$PAPERCLIP_API_URL/api/issues/NFM-CLEAN" '
        "-d '{\"status\": \"done\"}' && "
        'curl -s "$PAPERCLIP_API_URL/api/issues/NFM-BLOCKED" | jq .blockedBy'
    )
    code = pcm.run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        },
        env={"PCMR_ENFORCE": "1"},
    )
    err = capsys.readouterr().err
    assert code == 0
    assert "write allowed" in err


# --- verdicts -----------------------------------------------------------------


def test_evaluate_unresolved_open_blocker(monkeypatch: pytest.MonkeyPatch) -> None:
    issue = _issue(blocked=[{"identifier": "NFM-4829", "status": "in_progress"}])
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(issue))
    verdict = pcm.evaluate("NFM-4749")
    assert verdict.unresolved == (("NFM-4829", "in_progress"),)
    assert not verdict.clean


def test_evaluate_cancelled_blocker_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    issue = _issue(blocked=[{"identifier": "NFM-9", "status": "cancelled"}])
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(issue))
    assert pcm.evaluate("NFM-1").clean


def test_evaluate_terminal_blockers_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    issue = _issue(terminal=[{"identifier": "NFM-8", "status": "cancelled"}])
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(issue))
    verdict = pcm.evaluate("NFM-1")
    assert verdict.terminal == ("NFM-8",)
    assert not verdict.clean


def test_evaluate_notfound_is_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: NotFound(identifier=ident))
    verdict = pcm.evaluate("NFM-4040")
    assert "not found" in verdict.note
    assert verdict.clean  # a note is not a warning


# --- advisory exit-code policy --------------------------------------------------


def test_run_hook_warns_exit_2_on_unresolved(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    issue = _issue(blocked=[{"identifier": "NFM-4829", "status": "in_progress"}])
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(issue))
    code = pcm.run_hook(
        {"tool_name": "TaskUpdate", "tool_input": {"taskId": "NFM-4749", "status": "done"}}
    )
    err = capsys.readouterr().err
    assert code == 2
    assert "NFM-4829" in err and "unresolved" in err


def test_run_hook_clean_exit_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(_issue()))
    code = pcm.run_hook(
        {"tool_name": "TaskUpdate", "tool_input": {"taskId": "NFM-1", "status": "done"}}
    )
    assert code == 0
    assert "clean" in capsys.readouterr().out


def test_run_hook_lookup_failure_degrades_to_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _raise(ident: str) -> None:
        raise RuntimeError("auth boundary moved")

    monkeypatch.setattr(pcm, "lookup_issue", _raise)
    code = pcm.run_hook(
        {"tool_name": "TaskUpdate", "tool_input": {"taskId": "NFM-1", "status": "done"}}
    )
    assert code == 0
    assert "evaluation failed" in capsys.readouterr().err


def test_run_hook_irrelevant_payload_silent(capsys: pytest.CaptureFixture[str]) -> None:
    code = pcm.run_hook({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "" and captured.err == ""


# --- trap-3 contrast ------------------------------------------------------------


def test_collection_path_reports_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pcm,
        "lookup_issues",
        lambda **kw: _ok({"identifier": "NFM-1"}),  # no blockedBy key at all
    )
    assert pcm.collection_blocked_by("NFM-1") == "absent/stripped"


def test_render_warning_lists_both_sections() -> None:
    verdict = pcm.Verdict(
        identifier="NFM-1",
        status="done",
        unresolved=(("NFM-2", "in_progress"),),
        terminal=("NFM-3",),
    )
    text = pcm.render_warning(verdict)
    assert "NFM-2 [in_progress]" in text and "NFM-3" in text and "NFM-4847" in text


# --- blocking mode (NFM-5314 / PCMR_ENFORCE) -------------------------------------


def test_enforce_flag_default_off() -> None:
    assert not pcm.enforce_enabled({})  # unset
    assert not pcm.enforce_enabled({"PCMR_ENFORCE": ""})
    assert not pcm.enforce_enabled({"PCMR_ENFORCE": "0"})
    assert not pcm.enforce_enabled({"PCMR_ENFORCE": "off"})


def test_enforce_flag_truthy_values() -> None:
    for raw in ("1", "true", "YES", "On", " 1 "):
        assert pcm.enforce_enabled({"PCMR_ENFORCE": raw})


def test_pretooluse_flag_off_is_silent_noop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(ident: str) -> None:
        raise AssertionError("flag off must not touch the API")

    monkeypatch.setattr(pcm, "lookup_issue", _boom)
    code = pcm.run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "NFM-1", "status": "done"},
        },
        env={},
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "" and captured.err == ""


def test_pretooluse_enforce_denies_unresolved_chain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    issue = _issue(blocked=[{"identifier": "NFM-5259", "status": "in_review"}])
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(issue))
    code = pcm.run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "NFM-5257", "status": "done"},
        },
        env={"PCMR_ENFORCE": "1"},
    )
    err = capsys.readouterr().err
    assert code == 2
    assert "DENIED" in err and "NFM-5259 [in_review]" in err and "NFM-5314" in err


def test_pretooluse_enforce_allows_clean_chain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(_issue()))
    code = pcm.run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "NFM-1", "status": "done"},
        },
        env={"PCMR_ENFORCE": "1"},
    )
    err = capsys.readouterr().err
    assert code == 0
    assert "write allowed" in err


def test_pretooluse_enforce_terminal_only_does_not_hold(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # terminalBlockers alone is not an unresolved chain — blocking never fires.
    issue = _issue(terminal=[{"identifier": "NFM-8", "status": "cancelled"}])
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(issue))
    code = pcm.run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "NFM-1", "status": "done"},
        },
        env={"PCMR_ENFORCE": "1"},
    )
    err = capsys.readouterr().err
    assert code == 0
    assert "terminal-only" in err and "write allowed" in err


def test_pretooluse_enforce_failopen_on_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _raise(ident: str) -> None:
        raise RuntimeError("auth boundary moved")

    monkeypatch.setattr(pcm, "lookup_issue", _raise)
    code = pcm.run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "NFM-1", "status": "done"},
        },
        env={"PCMR_ENFORCE": "1"},
    )
    assert code == 0
    assert "fail-open" in capsys.readouterr().err


def test_pretooluse_enforce_failopen_on_notfound(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: NotFound(identifier=ident))
    code = pcm.run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "NFM-4040", "status": "done"},
        },
        env={"PCMR_ENFORCE": "1"},
    )
    assert code == 0
    assert "fail-open" in capsys.readouterr().err


def test_pretooluse_carry_marker_allows_write(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(ident: str) -> None:
        raise AssertionError("carry hatch must not need a chain read")

    monkeypatch.setattr(pcm, "lookup_issue", _boom)
    command = (
        'curl -s -X PATCH "$PAPERCLIP_API_URL/api/issues/NFM-5257" '
        "-d '{\"status\": \"done\"}' # pcmr-carry"
    )
    code = pcm.run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        },
        env={"PCMR_ENFORCE": "1"},
    )
    err = capsys.readouterr().err
    assert code == 0
    assert "intentional carry" in err


def test_posttooluse_unaffected_by_flag_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Flag on must not change the PostToolUse advisory contract (AC2).
    issue = _issue(blocked=[{"identifier": "NFM-4829", "status": "in_progress"}])
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(issue))
    code = pcm.run_hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "NFM-4749", "status": "done"},
        },
        env={"PCMR_ENFORCE": "1"},
    )
    err = capsys.readouterr().err
    assert code == 2
    assert "Advisory only" in err and "NFM-4847" in err and "DENIED" not in err


def test_legacy_payload_without_event_is_advisory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Older runtimes omit hook_event_name — must stay advisory, never block.
    issue = _issue(blocked=[{"identifier": "NFM-4829", "status": "in_progress"}])
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(issue))
    code = pcm.run_hook(
        {"tool_name": "TaskUpdate", "tool_input": {"taskId": "NFM-4749", "status": "done"}},
        env={"PCMR_ENFORCE": "1"},
    )
    assert code == 2
    assert "Advisory only" in capsys.readouterr().err


def test_render_block_lists_chain_and_rollback() -> None:
    verdict = pcm.Verdict(
        identifier="NFM-5257",
        status="blocked",
        unresolved=(("NFM-5259", "in_review"),),
        terminal=(),
    )
    text = pcm.render_block(verdict)
    assert "NFM-5257" in text and "NFM-5259 [in_review]" in text
    assert "DENIED" in text and "PCMR_ENFORCE" in text and pcm.CARRY_MARKER in text


# --- soak audit log (AC3) ---------------------------------------------------------


def test_enforce_decisions_are_audited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "soak.log"
    issue = _issue(identifier="NFM-5257", blocked=[{"identifier": "NFM-5259", "status": "in_review"}])
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(issue))
    env = {"PCMR_ENFORCE": "1", "PCMR_LOG": str(log)}
    blocked = pcm.run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "NFM-5257", "status": "done"},
        },
        env=env,
    )
    assert blocked == 2
    line = log.read_text().splitlines()[-1]
    # NFM-5562: leading ISO-8601 UTC timestamp column, then the decision row.
    stamp, decision, identifier, rest = line.split("\t", 3)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp)
    assert decision == "BLOCK" and identifier == "NFM-5257"
    assert "NFM-5259[in_review]" in rest


def test_audit_allow_line_carries_timestamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "soak.log"
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(_issue()))
    code = pcm.run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "NFM-1", "status": "done"},
        },
        env={"PCMR_ENFORCE": "1", "PCMR_LOG": str(log)},
    )
    assert code == 0
    stamp, decision, identifier, _rest = log.read_text().splitlines()[-1].split("\t", 3)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp)
    assert decision == "allow" and identifier == "NFM-1"


# --- launch-path anchor (NFM-5562) -------------------------------------------------


def test_settings_hook_command_anchors_launch_path() -> None:
    # The cwd trap (2026-10-05 soak): "./scripts/..." resolves against the
    # Bash tool's persistent cwd, and a PreToolUse launch failure is itself
    # blocking.  Every wired hook command must carry the workspace anchor
    # between ${CLAUDE_PROJECT_ROOT:-...} and the bare "." last resort.
    settings_path = _HOOKS_DIR.parents[1] / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for event in ("PreToolUse", "PostToolUse")
        for entry in settings["hooks"][event]
        for hook in entry["hooks"]
    ]
    assert commands, "PCMR hooks must stay wired for both events"
    for command in commands:
        assert "${CLAUDE_PROJECT_ROOT:-${PAPERCLIP_WORKSPACE_WORKTREE_PATH:-.}}" in command


def test_advisory_mode_never_audits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "soak.log"
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(_issue()))
    code = pcm.run_hook(
        {"tool_name": "TaskUpdate", "tool_input": {"taskId": "NFM-1", "status": "done"}},
        env={"PCMR_LOG": str(log)},  # flag absent
    )
    assert code == 0
    assert not log.exists()


def test_audit_failure_never_breaks_the_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Unwritable log target must degrade silently, not deny or crash.
    monkeypatch.setattr(pcm, "lookup_issue", lambda ident: _ok(_issue()))
    code = pcm.run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "NFM-1", "status": "done"},
        },
        env={"PCMR_ENFORCE": "1", "PCMR_LOG": str(tmp_path / "no" / "such" / "dir" / "x.log")},
    )
    assert code == 0
    assert "write allowed" in capsys.readouterr().err
