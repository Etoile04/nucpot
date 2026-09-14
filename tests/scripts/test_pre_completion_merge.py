"""Unit tests for the PreCompletionMerge advisory hook (NFM-4847 / KR-WF-3).

All lookups are monkeypatched — these tests are offline and exercise exactly
the surfaces that must not regress: done-ward write detection, blocker-chain
verdicts, and the advisory exit-code policy (warn, never crash, never block).
"""

from __future__ import annotations

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
