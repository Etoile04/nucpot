"""Dedup-keyed gh-issue alert fallback for the prod deploy-event collector.

NFM-5936 (child of NFM-5932, CTO Path-A adjudication 2026-11-10): the
``ALERT_WEBHOOK`` repo secret is EMPTY, so every collector alert has been
dropped for ~2 months — ``alert_bad_fragment`` / ``alert_append_failure`` /
``alert_pending_failure`` all no-op when the secret is unset. Setting the
secret exceeds the agent's authority (the nucpot-agent GitHub App
installation carries no ``secrets`` permission and the Feishu webhook URL
is human-held), so P4 ships the authorized fallback:

- Transport priority: the Feishu webhook when ``ALERT_WEBHOOK`` is set
  (unchanged behavior, so dropping the secret in later takes priority
  without a code change); otherwise a dedup-keyed GitHub issue.
- Dedup key: ONE OPEN issue per alert class — title
  ``[collector-alert] <class>``. The first fire creates it; later fires
  comment into it until a human closes it (closing resets the key and
  the next fire starts a fresh issue). PRs and closed issues never
  match.
- ``--self-test-alert``: fire ONE ``self-test`` class alert through the
  real transport and exit — the end-to-end probe for the executor
  context. Reads and writes NO collector state (no sync-state, no
  master JSONL, no failure sidecar, no SSH hop).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "okr"))

import prod_event_collector  # noqa: E402
from prod_event_collector import (  # noqa: E402
    ALERT_ISSUE_LABEL,
    ALERT_ISSUE_TITLE_PREFIX,
    GhBackend,
    _build_alert_issue_body,
    _gh_api,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to the fallback transport (webhook unset)."""
    monkeypatch.delenv("ALERT_WEBHOOK", raising=False)


class _GhApiRecorder:
    """Scriptable stand-in for the module-level ``_gh_api``."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = responses or {}

    def __call__(self, repo: str, *args: str, method: str = "GET", fields=None):
        endpoint = f"repos/{repo}/" + "/".join(args)
        self.calls.append(
            {"endpoint": endpoint, "method": method, "fields": dict(fields or {})}
        )
        key = f"{method} {endpoint}"
        scripted = self.responses.get(key)
        if isinstance(scripted, Exception):
            raise scripted
        if scripted is not None:
            return scripted
        if method == "GET":
            return []
        return {"number": 42, "id": 1}


@pytest.fixture()
def gh_recorder(monkeypatch: pytest.MonkeyPatch) -> _GhApiRecorder:
    recorder = _GhApiRecorder()
    monkeypatch.setattr(prod_event_collector, "_gh_api", recorder)
    return recorder


def _backend() -> GhBackend:
    return GhBackend("Etoile04/nucpot", "", "", "")


def _calls_to(recorder: _GhApiRecorder, suffix: str, method: str = "POST"):
    return [
        c for c in recorder.calls if c["endpoint"].endswith(suffix) and c["method"] == method
    ]


# ---------------------------------------------------------------------------
# _gh_api: method + form fields
# ---------------------------------------------------------------------------


class TestGhApiMethodAndFields:
    def test_get_is_default_with_single_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: dict[str, Any] = {}

        def fake_run(cmd, **_kwargs):
            recorded["cmd"] = list(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr="")

        monkeypatch.setattr(prod_event_collector.subprocess, "run", fake_run)
        out = _gh_api("O/R", "issues?state=open&per_page=100")
        assert out == {"ok": True}
        assert recorded["cmd"][:3] == ["gh", "api", "repos/O/R/issues?state=open&per_page=100"]
        assert "-X" not in recorded["cmd"]

    def test_post_adds_method_and_form_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: dict[str, Any] = {}

        def fake_run(cmd, **_kwargs):
            recorded["cmd"] = list(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout='{"number": 1}', stderr="")

        monkeypatch.setattr(prod_event_collector.subprocess, "run", fake_run)
        _gh_api("O/R", "issues", method="POST", fields={"title": "t", "labels[]": "x"})
        cmd = recorded["cmd"]
        assert cmd[:3] == ["gh", "api", "repos/O/R/issues"]
        assert cmd[3] == "-X" and cmd[4] == "POST"
        assert "-f" in cmd and "title=t" in cmd
        assert "-f" in cmd and "labels[]=x" in cmd

    def test_nonzero_exit_still_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd, **_kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        monkeypatch.setattr(prod_event_collector.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="boom"):
            _gh_api("O/R", "issues")


# ---------------------------------------------------------------------------
# Fallback transport: dedup-keyed issue create / comment
# ---------------------------------------------------------------------------


class TestAlertFallbackIssue:
    def test_creates_issue_when_no_open_match(self, gh_recorder: _GhApiRecorder) -> None:
        number = _backend()._alert_fallback_issue("bad-fragment", [("run_id", "123")])
        assert number == 42
        creates = _calls_to(gh_recorder, "issues")
        assert len(creates) == 1
        fields = creates[0]["fields"]
        assert fields["title"] == f"{ALERT_ISSUE_TITLE_PREFIX} bad-fragment"
        assert fields["labels[]"] == ALERT_ISSUE_LABEL
        assert "run_id" in fields["body"]

    def test_dedup_comments_into_existing_open_issue(self, gh_recorder: _GhApiRecorder) -> None:
        title = f"{ALERT_ISSUE_TITLE_PREFIX} bad-fragment"
        gh_recorder.responses["GET repos/Etoile04/nucpot/issues?state=open&per_page=100"] = [
            {"number": 7, "title": title, "state": "open"}
        ]
        number = _backend()._alert_fallback_issue("bad-fragment", [("run_id", "123")])
        assert number == 7
        assert _calls_to(gh_recorder, "issues") == []  # no create
        comments = _calls_to(gh_recorder, "comments")
        assert len(comments) == 1
        assert comments[0]["endpoint"] == "repos/Etoile04/nucpot/issues/7/comments"
        assert "run_id" in comments[0]["fields"]["body"]

    def test_dedup_scan_ignores_pull_requests(self, gh_recorder: _GhApiRecorder) -> None:
        title = f"{ALERT_ISSUE_TITLE_PREFIX} bad-fragment"
        gh_recorder.responses["GET repos/Etoile04/nucpot/issues?state=open&per_page=100"] = [
            {"number": 9, "title": title, "pull_request": {"url": "https://x/pr/9"}},
            {"number": 3, "title": "unrelated issue"},
        ]
        number = _backend()._alert_fallback_issue("bad-fragment", [("run_id", "1")])
        assert number == 42  # created fresh — the PR title match is ignored
        assert _calls_to(gh_recorder, "comments") == []

    def test_dedup_scan_ignores_other_class_titles(self, gh_recorder: _GhApiRecorder) -> None:
        gh_recorder.responses["GET repos/Etoile04/nucpot/issues?state=open&per_page=100"] = [
            {"number": 11, "title": f"{ALERT_ISSUE_TITLE_PREFIX} append-failure"}
        ]
        number = _backend()._alert_fallback_issue("bad-fragment", [("run_id", "1")])
        assert number == 42

    def test_create_retries_without_label_on_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, Any]] = []
        attempts = {"n": 0}

        def fake_gh_api(repo: str, *args: str, method: str = "GET", fields=None):
            calls.append(
                {"endpoint": "/".join(args), "method": method, "fields": dict(fields or {})}
            )
            if method == "GET":
                return []
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("could not add label")
            return {"number": 5}

        monkeypatch.setattr(prod_event_collector, "_gh_api", fake_gh_api)
        number = _backend()._alert_fallback_issue("append-failure", [("reason", "r")])
        assert number == 5
        assert attempts["n"] == 2
        assert "labels[]" in calls[-2]["fields"]
        assert "labels[]" not in calls[-1]["fields"]

    def test_transport_failure_is_swallowed_and_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_gh_api(repo: str, *args: str, **_kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(prod_event_collector, "_gh_api", fake_gh_api)
        assert _backend()._alert_fallback_issue("bad-fragment", [("run_id", "1")]) is None

    def test_swallows_every_exception_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exception subclasses blow up the transport, not the collector."""
        def fake_gh_api(repo: str, *args: str, **_kwargs):
            raise ValueError("unexpected shape")

        monkeypatch.setattr(prod_event_collector, "_gh_api", fake_gh_api)
        assert _backend()._alert_fallback_issue("bad-fragment", [("run_id", "1")]) is None


# ---------------------------------------------------------------------------
# Alert methods route webhook-first, fallback-second
# ---------------------------------------------------------------------------


class TestAlertMethodRouting:
    def test_bad_fragment_uses_fallback_when_webhook_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: dict[str, Any] = {}

        def fake_fallback(self, alert_class, details):
            recorded["class"] = alert_class
            recorded["details"] = list(details)
            return 1

        monkeypatch.setattr(GhBackend, "_alert_fallback_issue", fake_fallback)
        _backend().alert_bad_fragment(600, "environment='staging' is not 'production'")
        assert recorded["class"] == "bad-fragment"
        assert ("run_id", "600") in recorded["details"]

    def test_append_failure_uses_fallback_when_webhook_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: dict[str, Any] = {}

        def fake_fallback(self, alert_class, details):
            recorded["class"] = alert_class
            return 1

        monkeypatch.setattr(GhBackend, "_alert_fallback_issue", fake_fallback)
        _backend().alert_append_failure("remote append failed (rc=1): boom")
        assert recorded["class"] == "append-failure"

    def test_pending_failure_class_carries_stage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: dict[str, Any] = {}

        def fake_fallback(self, alert_class, details):
            recorded["class"] = alert_class
            recorded["details"] = list(details)
            return 1

        monkeypatch.setattr(GhBackend, "_alert_fallback_issue", fake_fallback)
        _backend().alert_pending_failure(42473687433, "download", 4, "timed out")
        assert recorded["class"] == "pending-stall-download"
        assert ("run_id", "42473687433") in recorded["details"]
        assert ("consecutive failures", "4") in recorded["details"]

    def test_webhook_set_takes_priority_and_skips_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALERT_WEBHOOK", "https://open.feishu.test/hook")
        fired: list[str] = []

        def fake_fallback(self, alert_class, details):  # pragma: no cover - must not run
            fired.append(alert_class)
            return 1

        curl_calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            curl_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(GhBackend, "_alert_fallback_issue", fake_fallback)
        monkeypatch.setattr(prod_event_collector.subprocess, "run", fake_run)
        _backend().alert_bad_fragment(600, "bad")
        assert fired == []
        assert len(curl_calls) == 1
        assert curl_calls[0][0] == "curl"
        assert "https://open.feishu.test/hook" in curl_calls[0]

    def test_webhook_unset_never_builds_a_curl_command(
        self, gh_recorder: _GhApiRecorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        curl_calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            curl_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(prod_event_collector.subprocess, "run", fake_run)
        gh_recorder.responses["POST repos/Etoile04/nucpot/issues"] = {"number": 42}
        _backend().alert_append_failure("remote append failed (rc=1): boom")
        assert curl_calls == []  # no Feishu attempt without the secret
        assert _calls_to(gh_recorder, "issues") != []  # fallback owned the alert

    def test_fallback_failure_does_not_break_the_alert_caller(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_fallback(self, alert_class, details):
            raise RuntimeError("fallback transport exploded")

        monkeypatch.setattr(GhBackend, "_alert_fallback_issue", fake_fallback)
        # The alert methods themselves must never raise — the tripped state
        # (bad_run_ids / stalled exit) is the load-bearing surface.
        _backend().alert_bad_fragment(600, "bad")


# ---------------------------------------------------------------------------
# Alert body builder
# ---------------------------------------------------------------------------


class TestAlertIssueBody:
    def test_contains_class_timestamp_and_details(self) -> None:
        body = _build_alert_issue_body("bad-fragment", [("run_id", "123"), ("reason", "empty")])
        assert f"{ALERT_ISSUE_TITLE_PREFIX} bad-fragment" in body
        assert "fired_at (UTC)" in body
        assert "**run_id**: 123" in body
        assert "**reason**: empty" in body

    def test_repeat_fire_marks_the_comment(self) -> None:
        body = _build_alert_issue_body("bad-fragment", [("run_id", "1")], repeat=True)
        assert "Repeat fire" in body
        assert "dedup'd" in body

    def test_truncates_overlong_values(self) -> None:
        body = _build_alert_issue_body("append-failure", [("reason", "x" * 5000)])
        assert "x" * 400 in body
        assert "x" * 401 not in body

    def test_documents_dedup_and_transport_contract(self) -> None:
        body = _build_alert_issue_body("self-test", [("note", "n")])
        assert "ALERT_WEBHOOK" in body
        assert "NFM-5936" in body
        assert "close" in body.lower()


# ---------------------------------------------------------------------------
# --self-test-alert: end-to-end probe without touching host state
# ---------------------------------------------------------------------------


class TestSelfTestAlert:
    def test_fires_self_test_class_and_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        recorded: dict[str, Any] = {}

        def fake_fallback(self, alert_class, details):
            recorded["class"] = alert_class
            recorded["details"] = list(details)
            return 99

        monkeypatch.setattr(GhBackend, "_alert_fallback_issue", fake_fallback)
        state = tmp_path / "sync.json"
        master = tmp_path / "master.jsonl"
        rc = prod_event_collector.main(
            [
                "--self-test-alert",
                "--repo",
                "Etoile04/nucpot",
                "--sync-state",
                str(state),
                "--master-jsonl",
                str(master),
            ]
        )
        assert rc == 0
        assert recorded["class"] == "self-test"
        # The probe must not read or write ANY collector state.
        assert not state.exists()
        assert not master.exists()
        assert not (tmp_path / "sync-failures.json").exists()

    def test_returns_nonzero_when_transport_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_fallback(self, alert_class, details):
            return None

        monkeypatch.setattr(GhBackend, "_alert_fallback_issue", fake_fallback)
        rc = prod_event_collector.main(["--self-test-alert", "--repo", "Etoile04/nucpot"])
        assert rc == 1

    def test_does_not_require_ssh_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--ssh-target resolution must not gate the alert-only probe."""
        monkeypatch.delenv("NFMD_PROD_SSH_TARGET", raising=False)

        def fake_fallback(self, alert_class, details):
            return 7

        monkeypatch.setattr(GhBackend, "_alert_fallback_issue", fake_fallback)
        rc = prod_event_collector.main(["--self-test-alert", "--repo", "Etoile04/nucpot"])
        assert rc == 0

    def test_details_declare_test_provenance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: dict[str, Any] = {}

        def fake_fallback(self, alert_class, details):
            recorded["details"] = list(details)
            return 1

        monkeypatch.setattr(GhBackend, "_alert_fallback_issue", fake_fallback)
        prod_event_collector.main(["--self-test-alert", "--repo", "Etoile04/nucpot"])
        joined = json.dumps(recorded["details"])
        assert "NFM-5936" in joined
