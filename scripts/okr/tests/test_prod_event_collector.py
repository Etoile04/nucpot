"""Unit tests for ``scripts/okr/prod_event_collector.py`` — NFM-2109.

Covers the four acceptance-criterion-5 cases:
  - flock race (covered by inspecting the SSH command the backend
    builds; the real flock execution is an integration concern on the
    Mac Studio host, not a unit-test concern)
  - partial-fragment rejection
  - sync-state advance-only-on-success
  - schema / environment / UUIDv4 validation
  - coverage_kr3.py --environment filter
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "okr"))

from prod_event_collector import (  # noqa: E402
    FragmentInvalid,
    Run,
    SyncState,
    _resolve_ssh_target,
    collect,
    validate_fragment,
)

_FULL_EVENT = {
    "event_id": "01234567-89ab-4cde-9f01-23456789abcd",
    "ts": "2026-07-30T12:00:00Z",
    "environment": "production",
    "triggered_by": "alice",
    "commit_sha": "abc1234",
    "first_pass_success": True,
    "health_gate_first_poll_passed": True,
    "rollback_triggered": False,
    "skip_flag_used": False,
    "duration_ms": 4321,
}


@dataclass
class _FakeBackend:
    """In-process Backend that records every call the collector makes."""

    runs: list
    fragments: dict
    alerts: list = None
    append_should_raise: Exception | None = None

    def __post_init__(self) -> None:
        if self.alerts is None:
            self.alerts = []
        self.append_calls: list = []
        self.append_failure_alerts: list = []

    def list_production_runs(self, since: datetime) -> list:
        return list(self.runs)

    def download_artifact(self, run_id: int, artifact_id: int, dest: Path) -> None:
        text = self.fragments.get(run_id)
        if text is None:
            raise RuntimeError(f"no fragment registered for run_id={run_id}")
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{run_id}.jsonl").write_text(text)

    def append_and_advance(self, fragments, new_state) -> None:
        self.append_calls.append((list(fragments), new_state))
        if self.append_should_raise is not None:
            raise self.append_should_raise

    def alert_bad_fragment(self, run_id: int, reason: str) -> None:
        self.alerts.append((run_id, reason))

    def alert_append_failure(self, reason: str) -> None:
        self.append_failure_alerts.append(reason)


class TestValidateFragment:
    def test_happy_path(self) -> None:
        out = validate_fragment(42, json.dumps(_FULL_EVENT))
        assert len(out) == 1 and out[0]["environment"] == "production"

    def test_empty_rejected(self) -> None:
        with pytest.raises(FragmentInvalid, match="empty"):
            validate_fragment(1, "")

    def test_whitespace_only_rejected(self) -> None:
        with pytest.raises(FragmentInvalid):
            validate_fragment(1, "   \n\n   ")

    def test_multiple_lines_rejected(self) -> None:
        text = json.dumps(_FULL_EVENT) + "\n" + json.dumps(_FULL_EVENT)
        with pytest.raises(FragmentInvalid, match="2 lines"):
            validate_fragment(1, text)

    def test_malformed_json_rejected(self) -> None:
        with pytest.raises(FragmentInvalid, match="JSON"):
            validate_fragment(1, "{not valid json")

    def test_json_array_rejected(self) -> None:
        with pytest.raises(FragmentInvalid, match="not a JSON object"):
            validate_fragment(1, "[1, 2, 3]")

    def test_missing_field_rejected(self) -> None:
        bad = {k: v for k, v in _FULL_EVENT.items() if k != "commit_sha"}
        with pytest.raises(FragmentInvalid, match="commit_sha"):
            validate_fragment(1, json.dumps(bad))

    def test_wrong_environment_rejected(self) -> None:
        bad = dict(_FULL_EVENT, environment="staging")
        with pytest.raises(FragmentInvalid, match="production"):
            validate_fragment(1, json.dumps(bad))

    def test_non_uuid_event_id_rejected(self) -> None:
        bad = dict(_FULL_EVENT, event_id="not-a-uuid")
        with pytest.raises(FragmentInvalid, match="UUIDv4"):
            validate_fragment(1, json.dumps(bad))

    def test_uuid_v3_rejected(self) -> None:
        # UUIDv3 has version digit "3" in the third group, not "4".
        bad = dict(_FULL_EVENT, event_id="01234567-89ab-3cde-9f01-23456789abcd")
        with pytest.raises(FragmentInvalid):
            validate_fragment(1, json.dumps(bad))

    def test_extra_field_tolerated(self) -> None:
        # Out-of-scope extras land in the event unchanged so a future
        # schema addition cannot break older fragments.
        out = validate_fragment(1, json.dumps(dict(_FULL_EVENT, future_field="ok")))
        assert out[0]["future_field"] == "ok"

    def test_non_string_event_id_rejected(self) -> None:
        bad = dict(_FULL_EVENT, event_id=12345)
        with pytest.raises(FragmentInvalid):
            validate_fragment(1, json.dumps(bad))


class TestSyncState:
    def test_default_zero_state(self) -> None:
        s = SyncState()
        assert (s.last_synced_run_id, s.last_synced_at, s.bad_run_ids) == (0, "", [])

    def test_round_trips(self) -> None:
        s = SyncState(12345, "2026-07-30T12:00:00Z", [101, 102])
        round = SyncState.from_json(s.to_json())
        assert round.last_synced_run_id == 12345
        assert round.last_synced_at == "2026-07-30T12:00:00Z"
        assert round.bad_run_ids == [101, 102]

    def test_to_json_sorts_and_dedups_bad_ids(self) -> None:
        s = SyncState(bad_run_ids=[3, 1, 2, 2, 1])
        assert json.loads(s.to_json())["bad_run_ids"] == [1, 2, 3]

    def test_from_json_tolerates_garbage(self) -> None:
        assert SyncState.from_json("not json {").last_synced_run_id == 0
        assert SyncState.from_json("[1, 2, 3]").last_synced_run_id == 0

    def test_from_json_drops_non_int_bad_ids(self) -> None:
        s = SyncState.from_json('{"bad_run_ids": ["x", 1, null, 2]}')
        assert s.bad_run_ids == [1, 2]


def _make_run(run_id: int) -> Run:
    return Run(
        run_id=run_id,
        created_at=datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC),
        artifact_ids=(999,),
    )


class TestCollectAdvanceOnlyOnSuccess:
    def test_successful_append_advances_state(self) -> None:
        run = _make_run(100)
        backend = _FakeBackend(runs=[run], fragments={100: json.dumps(_FULL_EVENT)})
        result = collect(SyncState(), backend)
        assert result.last_synced_run_id == 100
        assert result.last_synced_at != ""
        assert len(backend.append_calls[0][0]) == 1
        assert backend.alerts == []

    def test_failed_append_leaves_state_unchanged(self) -> None:
        # The most important assertion in this file: a network
        # partition mid-sync must not advance last_synced_run_id.
        run = _make_run(200)
        backend = _FakeBackend(runs=[run], fragments={200: json.dumps(_FULL_EVENT)})
        backend.append_should_raise = RuntimeError("ssh connection reset")
        initial = SyncState()
        result = collect(initial, backend)
        assert result.to_json() == initial.to_json()

    def test_skip_runs_in_bad_run_ids(self) -> None:
        run = _make_run(300)
        backend = _FakeBackend(runs=[run], fragments={300: json.dumps(_FULL_EVENT)})
        initial = SyncState(bad_run_ids=[300])
        result = collect(initial, backend)
        assert result.bad_run_ids == [300]
        assert backend.append_calls == []
        assert backend.alerts == []

    def test_skip_runs_at_or_below_last_synced(self) -> None:
        run_low = _make_run(400)
        run_high = _make_run(501)
        backend = _FakeBackend(
            runs=[run_low, run_high],
            fragments={501: json.dumps(_FULL_EVENT)},
        )
        initial = SyncState(last_synced_run_id=500)
        result = collect(initial, backend)
        assert result.last_synced_run_id == 501
        appended_rids = [rid for rid, _ in backend.append_calls[0][0]]
        assert appended_rids == [501]

    def test_bad_fragment_recorded_and_alerted_not_appended(self) -> None:
        run = _make_run(600)
        bad_text = json.dumps(dict(_FULL_EVENT, environment="staging"))
        backend = _FakeBackend(runs=[run], fragments={600: bad_text})
        result = collect(SyncState(), backend)
        assert 600 in result.bad_run_ids
        assert (600, "environment='staging' is not 'production'") in backend.alerts
        assert backend.append_calls == []
        assert result.last_synced_run_id == 0

    def test_no_artifact_records_bad_run(self) -> None:
        run = Run(
            run_id=700,
            created_at=datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC),
            artifact_ids=(),
        )
        backend = _FakeBackend(runs=[run], fragments={})
        result = collect(SyncState(), backend)
        assert 700 in result.bad_run_ids
        assert any(rid == 700 for rid, _ in backend.alerts)
        assert backend.append_calls == []

    def test_mixed_valid_and_invalid_runs(self) -> None:
        runs = [_make_run(800), _make_run(801)]
        backend = _FakeBackend(
            runs=runs,
            fragments={
                800: json.dumps(_FULL_EVENT),
                801: "{garbage",
            },
        )
        result = collect(SyncState(), backend)
        assert result.last_synced_run_id == 800
        assert 801 in result.bad_run_ids
        assert len(backend.append_calls) == 1


# ---------------------------------------------------------------------------
# coverage_kr3.py — --environment filter (acceptance criterion 5)
# ---------------------------------------------------------------------------

_spec = importlib.util.spec_from_file_location(
    "coverage_kr3", REPO_ROOT / "scripts" / "okr" / "coverage_kr3.py"
)
coverage_kr3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(coverage_kr3)


def _ev(env: str, success: bool = True) -> dict:
    return {
        "event_id": f"00000000-0000-4000-8000-{env:0>12}",
        "ts": "2026-07-30T12:00:00Z",
        "environment": env,
        "triggered_by": "alice",
        "commit_sha": "abc1234",
        "first_pass_success": success,
        "health_gate_first_poll_passed": success,
        "rollback_triggered": not success,
        "skip_flag_used": False,
        "duration_ms": 100,
    }


class _FakePath:
    def __init__(self, events: list) -> None:
        self._events = events

    def exists(self) -> bool:
        return True

    def read_text(self) -> str:
        return "\n".join(json.dumps(e) for e in self._events) + "\n"


class TestEnvironmentFilter:
    def test_filter_keeps_only_matching_environment(self) -> None:
        out = coverage_kr3.filter_environment([_ev("staging"), _ev("production")], "production")
        assert len(out) == 1 and out[0]["environment"] == "production"

    def test_filter_all_is_identity(self) -> None:
        events = [_ev("staging"), _ev("production")]
        out = coverage_kr3.filter_environment(events, "all")
        assert out == events

    def test_filter_drops_unknown_environment(self) -> None:
        events = [_ev("staging"), _ev("canary"), _ev("production")]
        out = coverage_kr3.filter_environment(events, "staging")
        assert [e["environment"] for e in out] == ["staging"]

    def test_filter_drops_missing_environment_field(self) -> None:
        ev = _ev("staging")
        del ev["environment"]
        out = coverage_kr3.filter_environment([ev, _ev("production")], "staging")
        assert out == []

    def test_build_report_uses_environment_arg(self) -> None:
        events = [
            _ev("staging", success=True),
            _ev("production", success=False),
            _ev("staging", success=False),
            _ev("production", success=True),
        ]
        for env, expected_value in [("staging", 0.5), ("production", 0.5)]:
            report = coverage_kr3.build_report(
                _FakePath(events), None, None, environment=env,
            )
            assert report["n"] == 2
            assert report["value"] == expected_value

    def test_default_environment_is_all(self, tmp_path: Path) -> None:
        # ADR-KR3-A2 §Consequences: the default filter is "all
        # environments" so pre-C6.1 callers see the same whole-JSONL
        # behaviour they had before C6.1 began landing. Asserting n==2
        # (both staging and production rows) is the discriminator
        # against the previous "staging" default.
        events = [_ev("staging"), _ev("production")]
        p = tmp_path / "events.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events) + "\n")
        report = coverage_kr3.build_report(p, None, None)
        assert report["n"] == 2
        assert report["environment"] == "all"

    def test_build_report_all_reads_prod_path(self) -> None:
        # When ``--environment`` is ``all`` (the default), the prod path
        # is also read and merged into the report. Until the prod
        # collector starts appending, the prod path is empty — the
        # staging fraction still drives the metric. This is the path
        # the CR's HIGH severity issue unblocks.
        staging_events = [_ev("staging", success=True), _ev("staging", success=False)]
        prod_events = [_ev("production", success=True), _ev("production", success=False)]
        report = coverage_kr3.build_report(
            _FakePath(staging_events),
            None, None,
            environment="all",
            prod_path=_FakePath(prod_events),
        )
        assert report["n"] == 4
        assert report["value"] == 0.5

    def test_build_report_production_only(self) -> None:
        # ``--environment production`` reads only the prod path.
        staging_events = [_ev("staging", success=True)]
        prod_events = [_ev("production", success=False), _ev("production", success=True)]
        report = coverage_kr3.build_report(
            _FakePath(staging_events),
            None, None,
            environment="production",
            prod_path=_FakePath(prod_events),
        )
        assert report["n"] == 2
        assert report["value"] == 0.5

    def test_build_report_staging_skips_prod_path(self) -> None:
        # ``--environment staging`` must not read the prod path; a
        # v1 caller that explicitly opts into the staging-only series
        # is unaffected by future prod appends.
        staging_events = [_ev("staging", success=True)]
        prod_events = [_ev("production", success=False)]
        report = coverage_kr3.build_report(
            _FakePath(staging_events),
            None, None,
            environment="staging",
            prod_path=_FakePath(prod_events),
        )
        assert report["n"] == 1
        assert report["value"] == 1.0

    def test_resolve_prod_path_uses_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NFMD_PROD_EVENTS_PATH", "/tmp/fake-prod.jsonl")
        assert coverage_kr3._resolve_prod_path(None) == Path("/tmp/fake-prod.jsonl")

    def test_resolve_prod_path_explicit_arg_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NFMD_PROD_EVENTS_PATH", "/tmp/env-prod.jsonl")
        assert coverage_kr3._resolve_prod_path("/tmp/explicit-prod.jsonl") == Path(
            "/tmp/explicit-prod.jsonl"
        )

    def test_cli_parses_environment_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["coverage_kr3.py", "--environment", "production"])
        assert coverage_kr3._parse_args().environment == "production"

    def test_cli_default_is_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["coverage_kr3.py"])
        assert coverage_kr3._parse_args().environment == "all"

    def test_cli_accepts_prod_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sys, "argv",
            ["coverage_kr3.py", "--environment", "production", "--prod-path", "/tmp/x.jsonl"],
        )
        assert coverage_kr3._parse_args().prod_path == "/tmp/x.jsonl"

    def test_cli_rejects_unknown_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["coverage_kr3.py", "--environment", "canary"])
        with pytest.raises(SystemExit):
            coverage_kr3._parse_args()


class TestAppendFailureAlert:
    """The except-branch in collect() must call backend.alert_append_failure.

    ADR §Failure-mode 1 talks about a partial-line tail-recovery as
    "logged + alerted"; the same belt-and-braces is required for the
    broader SSH / flock / decode failure. The CR flagged the silent
    stderr-only path as a visibility hole.
    """

    def test_failed_append_fires_alert(self) -> None:
        run = _make_run(900)
        backend = _FakeBackend(runs=[run], fragments={900: json.dumps(_FULL_EVENT)})
        backend.append_should_raise = RuntimeError("ssh connection reset")
        collect(SyncState(), backend)
        assert len(backend.append_failure_alerts) == 1
        assert "ssh connection reset" in backend.append_failure_alerts[0]
        assert "RuntimeError" in backend.append_failure_alerts[0]

    def test_successful_append_does_not_alert(self) -> None:
        run = _make_run(901)
        backend = _FakeBackend(runs=[run], fragments={901: json.dumps(_FULL_EVENT)})
        collect(SyncState(), backend)
        assert backend.append_failure_alerts == []

    def test_no_pending_fragments_skips_alert(self) -> None:
        # All runs are pre-sync — no append attempted, no alert expected.
        run = _make_run(902)
        backend = _FakeBackend(runs=[run], fragments={902: json.dumps(_FULL_EVENT)})
        initial = SyncState(last_synced_run_id=1000)
        collect(initial, backend)
        assert backend.append_failure_alerts == []
        assert backend.append_calls == []


# ── _resolve_ssh_target / _default_ssh_user / _default_ssh_host (ADR-KR3 §C6.3.3) ──


class TestResolveSshTarget:
    """C6.3.3: --ssh-target resolves from CLI arg or NFMD_PROD_SSH_TARGET."""

    def test_explicit_cli_arg_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NFMD_PROD_SSH_TARGET", "env@host")
        assert _resolve_ssh_target("cli@explicit") == "cli@explicit"

    def test_env_var_provides_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NFMD_PROD_SSH_TARGET", "deployer@10.0.0.1")
        assert _resolve_ssh_target(None) == "deployer@10.0.0.1"

    def test_missing_both_exits_with_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC2: omitting flag and env var produces actionable error (SystemExit)."""
        monkeypatch.delenv("NFMD_PROD_SSH_TARGET", raising=False)
        with pytest.raises(SystemExit):
            _resolve_ssh_target(None)

    def test_no_user_specific_literal_in_argparse(self) -> None:
        """AC1: no lwj04@ or other user-specific literal in argparse defaults."""
        import prod_event_collector as _mod

        source = Path(_mod.__file__).read_text()
        for line in source.splitlines():
            if "--ssh-target" in line and "default=" in line:
                assert "lwj04" not in line, (
                    "lwj04 literal found in argparse default for --ssh-target"
                )
                assert "@" not in line or "NFMD" in line, (
                    "user@host literal found in argparse default for --ssh-target"
                )


class TestGhApiArgContract:
    """Regression: NFM-2754 prod-deploy-event-collector 5+ failures since 2026-08-10T05:55Z.

    The collector invokes ``gh api`` to discover workflow runs and download
    artifacts. The implementation builds the subprocess argv by spreading
    ``*args`` into the command list, which produces TWO positional arguments
    after ``gh api``. ``gh api`` only accepts ONE endpoint positional — the
    second arg is rejected with ``accepts 1 arg(s), received 2`` and the
    collector iteration aborts. These tests pin the contract: the argv
    passed to ``subprocess.run`` must contain exactly one endpoint string
    per ``gh api`` call (concatenation is the fix).
    """

    def _capture(self, monkeypatch, stdout: str = "{}") -> list:
        """Patch subprocess.run so _gh_api records the argv instead of
        spawning gh. Returns the captured list (one entry per call)."""
        captured: list = []
        import subprocess as _sp

        import prod_event_collector as _mod

        def fake_run(cmd, *args, **kwargs):
            captured.append(list(cmd))
            return _sp.CompletedProcess(cmd, 0, stdout, "")

        # _gh_api looks up ``subprocess`` via the module globals of
        # prod_event_collector (it imported ``import subprocess`` at module
        # top). Patch the same name the function resolves at call time.
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        return captured

    def test_single_endpoint_argv_for_actions_runs(self, monkeypatch) -> None:
        captured = self._capture(monkeypatch, stdout='{"workflow_runs": []}')
        from prod_event_collector import _gh_api
        _gh_api("Etoile04/nucpot", "actions/runs?created=>=2026-08-10T00:00:00Z&per_page=100")
        assert len(captured) == 1
        cmd = captured[0]
        assert cmd[0] == "gh"
        assert cmd[1] == "api"
        # Exactly one endpoint positional after the gh/api prefix, and it
        # MUST be a single concatenated path, not two separate args.
        assert len(cmd) == 3, (
            f"expected gh api to receive a single endpoint arg, got cmd={cmd!r}"
        )
        assert cmd[2] == "repos/Etoile04/nucpot/actions/runs?created=>=2026-08-10T00:00:00Z&per_page=100"

    def test_single_endpoint_argv_for_artifacts(self, monkeypatch) -> None:
        captured = self._capture(monkeypatch, stdout='{"artifacts": []}')
        from prod_event_collector import _gh_api
        _gh_api("Etoile04/nucpot", "actions/runs/12345/artifacts")
        assert len(captured) == 1
        cmd = captured[0]
        assert cmd[:2] == ["gh", "api"]
        assert len(cmd) == 3, (
            f"expected gh api to receive a single endpoint arg, got cmd={cmd!r}"
        )
        assert cmd[2] == "repos/Etoile04/nucpot/actions/runs/12345/artifacts"
