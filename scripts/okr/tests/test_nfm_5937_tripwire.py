"""Master-stall tripwire for the prod deploy-event capture lane.

NFM-5937 (child of NFM-5932, P5): warn when the newest master event is
older than the stall threshold (7 days — parent NFM-5931 acceptance
text) WHILE completed ``Production Deployment`` runs exist in that same
window — the signature of "deploys ran, capture silently stalled".
The check rides the collector executor payload (verified-bytes
provenance invariant) and alarms through the NFM-5936 transport
priority: ``ALERT_WEBHOOK`` when set, else the dedup-keyed
``[collector-alert] master-stall`` gh-issue fallback.

The tripwire is read-only against the master JSONL — master writes stay
flock/reviewed-only (parent NFM-5932 hard constraint). An empty or
unreadable master file leaves the age condition undefined: the tripwire
does NOT fire (the collect-path ``bad-fragment`` / ``append-failure``
alerts own total-capture loss), and it says so on stdout.

Forced-old-state proof door: ``--tripwire-max-age-days 0`` collapses the
age gate so a live in-window run trips the alert without writing
anything to master — the end-to-end probe for this lane.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "okr"))

import prod_event_collector  # noqa: E402
from prod_event_collector import (  # noqa: E402
    ALERT_CLASS_MASTER_STALL,
    ALERT_ISSUE_LABEL,
    ALERT_ISSUE_TITLE_PREFIX,
    MASTER_STALL_MAX_AGE_DAYS,
    DeployRunRecord,
    GhBackend,
    master_stall_tripwire,
    newest_master_event_ts,
)

UTC = timezone.utc  # noqa: UP017 — mirror the module's 3.9-compat alias

NOW = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to the fallback transport (webhook unset)."""
    monkeypatch.delenv("ALERT_WEBHOOK", raising=False)


class _FakeBackend:
    """Records tripwire alerts; serves a scripted in-window run list."""

    def __init__(self, window_runs: list[DeployRunRecord] | None = None) -> None:
        self.window_runs = window_runs or []
        self.alerts: list[dict[str, Any]] = []

    def list_deploy_runs_in_window(
        self, since: datetime, *, until: datetime
    ) -> list[DeployRunRecord]:
        self.since = since
        self.until = until
        return self.window_runs

    def alert_master_stall(
        self, newest_ts: datetime, age: timedelta, window_runs: list[DeployRunRecord]
    ) -> None:
        self.alerts.append(
            {"newest_ts": newest_ts, "age": age, "window_runs": list(window_runs)}
        )


def _event_line(ts: str, event_id: str = "0b247cd2-2116-4b3a-9a8f-2eaef70db153") -> str:
    return (
        f'{{"event_id":"{event_id}","ts":"{ts}","environment":"production",'
        f'"triggered_by":"Etoile04","commit_sha":"{"a" * 40}",'
        f'"first_pass_success":true,"health_gate_first_poll_passed":false,'
        f'"rollback_triggered":false,"skip_flag_used":false,"duration_ms":1000}}'
    )


def _write_master(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "master-deploy-events.jsonl"
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# newest_master_event_ts
# ---------------------------------------------------------------------------


class TestNewestMasterEventTs:
    def test_missing_file_is_none(self, tmp_path: Path) -> None:
        assert newest_master_event_ts(tmp_path / "absent.jsonl") is None

    def test_empty_file_is_none(self, tmp_path: Path) -> None:
        path = tmp_path / "master.jsonl"
        path.write_text("", encoding="utf-8")
        assert newest_master_event_ts(path) is None

    def test_single_line_parses(self, tmp_path: Path) -> None:
        path = _write_master(tmp_path, [_event_line("2026-11-08T12:49:40Z")])
        assert newest_master_event_ts(path) == datetime(2026, 11, 8, 12, 49, 40, tzinfo=UTC)

    def test_out_of_order_lines_return_the_max(self, tmp_path: Path) -> None:
        path = _write_master(
            tmp_path,
            [
                _event_line("2026-11-08T12:49:40Z"),
                _event_line("2026-11-01T00:00:00Z", event_id="0b247cd2-2116-4b3a-9a8f-2eaef70db154"),
                _event_line("2026-11-05T06:00:00Z", event_id="0b247cd2-2116-4b3a-9a8f-2eaef70db155"),
            ],
        )
        assert newest_master_event_ts(path) == datetime(2026, 11, 8, 12, 49, 40, tzinfo=UTC)

    def test_garbage_tail_falls_back_to_last_parseable(self, tmp_path: Path) -> None:
        # A torn final line (mid-append read without the flock) must not
        # hide the newest complete event.
        path = _write_master(
            tmp_path,
            [
                _event_line("2026-11-08T12:49:40Z"),
                '{"event_id":"torn-truncated-jso',
            ],
        )
        assert newest_master_event_ts(path) == datetime(2026, 11, 8, 12, 49, 40, tzinfo=UTC)

    def test_blank_lines_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "master.jsonl"
        path.write_text("\n\n" + _event_line("2026-11-08T12:49:40Z") + "\n\n", encoding="utf-8")
        assert newest_master_event_ts(path) == datetime(2026, 11, 8, 12, 49, 40, tzinfo=UTC)


# ---------------------------------------------------------------------------
# master_stall_tripwire condition logic
# ---------------------------------------------------------------------------


class TestMasterStallTripwireCondition:
    def test_fires_when_old_and_deploys_ran(self, tmp_path: Path) -> None:
        path = _write_master(tmp_path, [_event_line("2026-11-03T12:00:00Z")])  # 8d old
        window = [DeployRunRecord(42957760934, datetime(2026, 11, 11, 4, 13, tzinfo=UTC), "success")]
        backend = _FakeBackend(window_runs=window)
        assert master_stall_tripwire(
            backend, master_jsonl_path=path, now=NOW
        ) is True
        assert len(backend.alerts) == 1
        assert backend.alerts[0]["newest_ts"] == datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)
        assert backend.alerts[0]["window_runs"] == window

    def test_silent_when_young_even_with_deploys(self, tmp_path: Path) -> None:
        path = _write_master(tmp_path, [_event_line("2026-11-08T12:49:40Z")])  # ~3d old
        window = [DeployRunRecord(42957760934, datetime(2026, 11, 11, 4, 13, tzinfo=UTC), "success")]
        backend = _FakeBackend(window_runs=window)
        assert master_stall_tripwire(backend, master_jsonl_path=path, now=NOW) is False
        assert backend.alerts == []

    def test_silent_when_old_but_no_deploys_in_window(self, tmp_path: Path) -> None:
        # The exact stall shape the P3 backfill closed: capture dead, but
        # nobody deployed either — nothing to warn about yet.
        path = _write_master(tmp_path, [_event_line("2026-11-03T12:00:00Z")])
        backend = _FakeBackend(window_runs=[])
        assert master_stall_tripwire(backend, master_jsonl_path=path, now=NOW) is False
        assert backend.alerts == []

    def test_boundary_age_exactly_threshold_does_not_fire(self, tmp_path: Path) -> None:
        # "age > 7d" is strict: exactly 7 days old is not a stall.
        path = _write_master(tmp_path, [_event_line("2026-11-04T12:00:00Z")])  # exactly 7d
        window = [DeployRunRecord(1, datetime(2026, 11, 10, tzinfo=UTC), "success")]
        backend = _FakeBackend(window_runs=window)
        assert master_stall_tripwire(backend, master_jsonl_path=path, now=NOW) is False

    def test_zero_threshold_forces_fire_for_the_probe(self, tmp_path: Path) -> None:
        # The forced-old-state door: --tripwire-max-age-days 0.
        path = _write_master(tmp_path, [_event_line("2026-11-08T12:49:40Z")])
        window = [DeployRunRecord(42957760934, datetime(2026, 11, 11, 4, 13, tzinfo=UTC), "failure")]
        backend = _FakeBackend(window_runs=window)
        assert (
            master_stall_tripwire(
                backend, master_jsonl_path=path, now=NOW, max_age_days=0
            )
            is True
        )

    def test_undefined_master_leaves_condition_undefined(self, tmp_path: Path) -> None:
        # Empty/missing master: the collect-path alerts own total-capture
        # loss; the age rule cannot evaluate, so the tripwire stays silent
        # and says why on stdout.
        backend = _FakeBackend(
            window_runs=[DeployRunRecord(1, datetime(2026, 11, 10, tzinfo=UTC), "success")]
        )
        assert master_stall_tripwire(
            backend, master_jsonl_path=tmp_path / "absent.jsonl", now=NOW
        ) is False
        assert backend.alerts == []

    def test_window_is_anchored_at_newest_event_strictly(
        self, tmp_path: Path
    ) -> None:
        path = _write_master(tmp_path, [_event_line("2026-11-03T12:00:00Z")])
        backend = _FakeBackend(window_runs=[])
        master_stall_tripwire(backend, master_jsonl_path=path, now=NOW)
        assert backend.since == datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)
        assert backend.until == NOW


# ---------------------------------------------------------------------------
# GhBackend.list_deploy_runs_in_window (scripted _gh_api)
# ---------------------------------------------------------------------------


class _GhApiRecorder:
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = responses or {}

    def __call__(self, repo: str, *args: str, method: str = "GET", fields=None):
        endpoint = f"repos/{repo}/" + "/".join(args)
        self.calls.append({"endpoint": endpoint, "method": method})
        scripted = self.responses.get(endpoint)
        if isinstance(scripted, Exception):
            raise scripted
        return scripted if scripted is not None else {}


def _run_item(run_id: int, created: str, name: str = "Production Deployment",
              status: str = "completed", conclusion: str = "success") -> dict[str, Any]:
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "created_at": created,
    }


class TestListDeployRunsInWindow:
    def test_lists_only_completed_production_deployment_in_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _GhApiRecorder(
            {
                "repos/Etoile04/nucpot/actions/runs?created=>=2026-11-03T12:00:00Z&per_page=100&page=1": {
                    "workflow_runs": [
                        _run_item(42957760934, "2026-11-11T04:13:00Z"),
                        _run_item(42550621741, "2026-11-09T02:23:01Z", conclusion="failure"),
                        _run_item(111, "2026-11-02T00:00:00Z"),  # before window
                        _run_item(222, "2026-11-11T09:00:00Z", status="in_progress"),
                        _run_item(333, "2026-11-11T10:00:00Z", name="Some Other Workflow"),
                    ]
                }
            }
        )
        monkeypatch.setattr(prod_event_collector, "_gh_api", recorder)
        backend = GhBackend("Etoile04/nucpot", "", "", "")
        records = backend.list_deploy_runs_in_window(
            datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC),
            until=datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC),
        )
        assert {r.run_id for r in records} == {42550621741, 42957760934}
        assert {r.conclusion for r in records} == {"failure", "success"}

    def test_paginates_until_short_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        full_page = {"workflow_runs": [_run_item(1, "2026-11-04T00:00:00Z")] * 100}
        tail_page = {"workflow_runs": [_run_item(2, "2026-11-06T00:00:00Z")]}
        recorder = _GhApiRecorder(
            {
                "repos/Etoile04/nucpot/actions/runs?created=>=2026-11-03T12:00:00Z&per_page=100&page=1": full_page,
                "repos/Etoile04/nucpot/actions/runs?created=>=2026-11-03T12:00:00Z&per_page=100&page=2": tail_page,
            }
        )
        monkeypatch.setattr(prod_event_collector, "_gh_api", recorder)
        backend = GhBackend("Etoile04/nucpot", "", "", "")
        records = backend.list_deploy_runs_in_window(
            datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC),
            until=datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC),
        )
        assert {r.run_id for r in records} == {1, 2}


# ---------------------------------------------------------------------------
# alert_master_stall transport (mirror of the NFM-5936 contract)
# ---------------------------------------------------------------------------


class TestAlertMasterStallTransport:
    def test_webhook_unset_uses_dedup_fallback_with_class(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: dict[str, Any] = {}

        def fake_fallback(self, alert_class, details):
            recorded["class"] = alert_class
            recorded["details"] = list(details)
            return 1

        monkeypatch.setattr(GhBackend, "_alert_fallback_issue", fake_fallback)
        backend = GhBackend("Etoile04/nucpot", "", "", "")
        backend.alert_master_stall(
            datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC),
            timedelta(days=8),
            [DeployRunRecord(42957760934, NOW, "success")],
        )
        assert recorded["class"] == "master-stall"
        joined = dict(recorded["details"])
        assert joined["newest_master_event_ts"] == "2026-11-03T12:00:00Z"
        assert "8.00" in joined["event_age_days"]
        assert "42957760934" in joined["deploy_runs_in_window"]

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
            import subprocess

            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(GhBackend, "_alert_fallback_issue", fake_fallback)
        monkeypatch.setattr(prod_event_collector.subprocess, "run", fake_run)
        backend = GhBackend("Etoile04/nucpot", "", "", "")
        backend.alert_master_stall(
            datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC),
            timedelta(days=8),
            [DeployRunRecord(1, NOW, "success")],
        )
        assert fired == []
        assert len(curl_calls) == 1
        assert curl_calls[0][0] == "curl"

    def test_more_than_five_window_runs_summarized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: dict[str, Any] = {}

        def fake_fallback(self, alert_class, details):
            recorded["details"] = list(details)
            return 1

        monkeypatch.setattr(GhBackend, "_alert_fallback_issue", fake_fallback)
        runs = [DeployRunRecord(i, NOW, "success") for i in range(1, 9)]
        backend = GhBackend("Etoile04/nucpot", "", "", "")
        backend.alert_master_stall(datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC), timedelta(days=8), runs)
        joined = dict(recorded["details"])
        assert "more" in joined["deploy_runs_in_window"]


# ---------------------------------------------------------------------------
# CLI: --tripwire / --tripwire-max-age-days
# ---------------------------------------------------------------------------


class TestCliTripwire:
    def test_tripwire_dispatches_and_exits_zero_when_silent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        recorded: dict[str, Any] = {}

        def fake_tripwire(backend, *, master_jsonl_path, now=None, max_age_days=7.0):
            recorded["master"] = str(master_jsonl_path)
            recorded["max_age_days"] = max_age_days
            return False

        monkeypatch.setattr(prod_event_collector, "master_stall_tripwire", fake_tripwire)
        master = _write_master(tmp_path, [])
        rc = prod_event_collector.main(
            ["--tripwire", "--master-jsonl", str(master), "--repo", "Etoile04/nucpot"]
        )
        assert rc == 0
        assert recorded["master"] == str(master)
        assert recorded["max_age_days"] == 7.0

    def test_tripwire_exit_nonzero_when_fired(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            prod_event_collector,
            "master_stall_tripwire",
            lambda *a, **k: True,
        )
        master = _write_master(tmp_path, [])
        rc = prod_event_collector.main(
            ["--tripwire", "--master-jsonl", str(master)]
        )
        assert rc == 1

    def test_tripwire_max_age_days_plumbs_through(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        recorded: dict[str, Any] = {}

        def fake_tripwire(backend, *, master_jsonl_path, now=None, max_age_days=7.0):
            recorded["max_age_days"] = max_age_days
            return False

        monkeypatch.setattr(prod_event_collector, "master_stall_tripwire", fake_tripwire)
        master = _write_master(tmp_path, [])
        rc = prod_event_collector.main(
            ["--tripwire", "--master-jsonl", str(master), "--tripwire-max-age-days", "0"]
        )
        assert rc == 0
        assert recorded["max_age_days"] == 0.0

    def test_tripwire_does_not_require_ssh_target_or_sync_state(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Mirror of the P4 self-test contract: the probe runs without any
        # host-bound collector state or SSH target.
        monkeypatch.delenv("NFMD_PROD_SSH_TARGET", raising=False)
        monkeypatch.delenv("NFMD_PROD_EVENTS_SYNC_STATE", raising=False)
        monkeypatch.delenv("NFMD_PROD_EVENTS_PATH", raising=False)
        monkeypatch.setattr(prod_event_collector, "master_stall_tripwire", lambda *a, **k: False)
        master = _write_master(tmp_path, [])
        rc = prod_event_collector.main(
            ["--tripwire", "--master-jsonl", str(master)]
        )
        assert rc == 0

    def test_default_threshold_constant_is_seven_days(self) -> None:
        # Parent NFM-5931 acceptance text: warn when the newest master
        # event age exceeds 7 days.
        assert MASTER_STALL_MAX_AGE_DAYS == 7.0


# ---------------------------------------------------------------------------
# Executor workflow wiring
# ---------------------------------------------------------------------------

EXECUTOR = REPO_ROOT / ".github/workflows/prod-deploy-event-collector-executor.yml"


class TestExecutorTripwireStep:
    @pytest.fixture()
    def executor(self) -> dict[str, Any]:
        import yaml

        return yaml.safe_load(EXECUTOR.read_text(encoding="utf-8"))

    def _tripwire_steps(self, executor: dict[str, Any]) -> list[dict[str, Any]]:
        steps = executor["jobs"]["collect"]["steps"]
        return [
            s for s in steps
            if isinstance(s, dict) and "--tripwire" in str(s.get("run", ""))
        ]

    def test_tripwire_step_exists(self, executor: dict[str, Any]) -> None:
        assert len(self._tripwire_steps(executor)) == 1, (
            "the executor must run the master-stall tripwire as its own step"
        )

    def test_tripwire_gated_on_verified_payload_and_not_self_test(
        self, executor: dict[str, Any]
    ) -> None:
        step = self._tripwire_steps(executor)[0]
        cond = str(step.get("if", ""))
        assert "steps.verify-payload.outcome == 'success'" in cond, (
            "provenance invariant: only verified payload bytes may execute"
        )
        assert "self_test" in cond, (
            "the alert self-test door must not also fire the tripwire"
        )

    def test_tripwire_env_carries_gh_token_and_alert_webhook(
        self, executor: dict[str, Any]
    ) -> None:
        step = self._tripwire_steps(executor)[0]
        env = step.get("env", {})
        assert env.get("GH_TOKEN") == "${{ secrets.GITHUB_TOKEN }}"
        assert env.get("ALERT_WEBHOOK") == "${{ secrets.ALERT_WEBHOOK }}"

    def test_tripwire_reads_master_and_repo_flags(
        self, executor: dict[str, Any]
    ) -> None:
        run = str(self._tripwire_steps(executor)[0].get("run", ""))
        assert "prod_event_collector.py" in run
        assert "--tripwire" in run
        assert "--master-jsonl" in run
        assert "--repo" in run


# ---------------------------------------------------------------------------
# Alert-class family conventions
# ---------------------------------------------------------------------------


class TestAlertClassConventions:
    def test_master_stall_class_is_a_dedup_family_member(self) -> None:
        assert ALERT_CLASS_MASTER_STALL == "master-stall"

    def test_issue_title_prefix_matches_dedup_key_contract(self) -> None:
        assert ALERT_ISSUE_TITLE_PREFIX == "[collector-alert]"
        assert ALERT_ISSUE_LABEL == "collector-alert"
