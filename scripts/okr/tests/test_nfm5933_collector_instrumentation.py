"""NFM-5933 P1 — collector instrumentation + pending-run failure tripwire.

Covers the three P1 deliverables:

1. Loud per-iteration stdout: window + listed count, per-run artifact
   counts, download ok/err (incl. gh stderr), validation verdicts,
   append verdicts, and an end-of-iteration summary printed on EVERY
   path — including a fully quiet tick.
2. Pending-run consecutive-failure counter (sidecar next to the
   sync-state): >3 consecutive failures tripwire — the 4th attempt
   fires the alert path and main() exits non-zero.
3. Verify-leg harness shape: the 42473687433 class (artifact present,
   download path exercised, state untouched on failure) driven through
   the same injection seam the live one-shot uses.

Constraint under test: NO behavior change to append semantics — the
non-dry-run sync outcomes (advance-only-on-success, bad-mark + skip,
alert-on-append-failure) are identical to pre-P1.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "okr"))

import prod_event_collector as pec  # noqa: E402
from prod_event_collector import (  # noqa: E402
    MAX_CONSECUTIVE_FAILURES,
    Run,
    SyncState,
    _load_attempts,
    _resolve_attempts_path,
    _save_attempts,
    collect,
    tripwired,
)

_STUCK_RUN_ID = 42473687433  # the NFM-5933 specimen

_FULL_EVENT = {
    "event_id": "01234567-89ab-4cde-9f01-23456789abcd",
    "ts": "2026-11-08T11:48:13Z",
    "environment": "production",
    "triggered_by": "alice",
    "commit_sha": "abc1234",
    "first_pass_success": True,
    "health_gate_first_poll_passed": True,
    "rollback_triggered": False,
    "skip_flag_used": False,
    "duration_ms": 4321,
}


def _make_run(run_id: int = _STUCK_RUN_ID) -> Run:
    return Run(
        run_id=run_id,
        created_at=datetime(2026, 11, 8, 11, 48, 47, tzinfo=UTC),
        artifact_ids=(13616166337,),
    )


@dataclass
class _RecordingBackend:
    """Backend double that records every collector interaction."""

    runs: list
    fragments: dict = field(default_factory=dict)
    download_fail_for: set = field(default_factory=set)
    append_should_raise: Exception | None = None

    def __post_init__(self) -> None:
        self.alerts: list = []
        self.append_calls: list = []

    def list_production_runs(self, since: datetime) -> list:
        return list(self.runs)

    def download_artifact(self, run_id: int, artifact_id: int, dest: Path) -> None:
        if run_id in self.download_fail_for:
            raise RuntimeError(
                f"gh run download failed for run_id={run_id}: "
                f"GraphQL: Could not resolve to an Artifact (exit 1)"
            )
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
        self.alerts.append(("bad_fragment", run_id, reason))

    def alert_append_failure(self, reason: str) -> None:
        self.alerts.append(("append_failure", None, reason))

    def alert_pending_failure(self, run_id: int, attempts: int, reason: str) -> None:
        self.alerts.append(("pending_failure", run_id, reason))


# ---------------------------------------------------------------------------
# Deliverable 1 — loud per-iteration stdout
# ---------------------------------------------------------------------------


class TestInstrumentationStdout:
    def test_happy_path_prints_every_stage(self, capsys: pytest.CaptureFixture) -> None:
        run = _make_run()
        backend = _RecordingBackend(runs=[run], fragments={run.run_id: json.dumps(_FULL_EVENT)})
        collect(SyncState(), backend)
        out = capsys.readouterr().out
        assert "created>=" in out  # window line
        assert "listed 1 Production Deployment run(s)" in out
        assert f"run {_STUCK_RUN_ID}: 1 prod-deploy-event artifact(s)" in out
        assert f"run {_STUCK_RUN_ID}: download OK (artifact 13616166337)" in out
        assert f"run {_STUCK_RUN_ID}: fragment VALID (1 event)" in out
        assert f"append: 1 fragment(s), advancing to run_id {_STUCK_RUN_ID}" in out
        assert "append OK: state advanced" in out

    def test_quiet_tick_still_prints_summary(self, capsys: pytest.CaptureFixture) -> None:
        # The 11-08→11-10 stall class: zero runs in window, zero output.
        # After P1 the tick must say so explicitly.
        backend = _RecordingBackend(runs=[])
        collect(SyncState(), backend)
        out = capsys.readouterr().out
        assert "listed 0 Production Deployment run(s)" in out
        assert "iteration summary: listed=0 pending=0" in out

    def test_window_line_carries_last_synced_at(self, capsys: pytest.CaptureFixture) -> None:
        backend = _RecordingBackend(runs=[])
        state = SyncState(last_synced_run_id=7, last_synced_at="2026-11-08T11:00:00Z")
        collect(state, backend)
        out = capsys.readouterr().out
        assert "last_synced_at='2026-11-08T11:00:00Z'" in out
        assert "last_synced_run_id=7" in out

    def test_download_failure_line_carries_gh_stderr(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        run = _make_run()
        backend = _RecordingBackend(runs=[run])
        backend.download_fail_for = {run.run_id}
        collect(SyncState(), backend)
        out = capsys.readouterr().out
        assert f"run {_STUCK_RUN_ID}: download FAILED (consecutive attempt 1/" in out
        assert "Could not resolve to an Artifact" in out  # the gh stderr tail

    def test_validation_verdict_printed_per_fragment(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        run = _make_run()
        two_line = json.dumps(_FULL_EVENT) + "\n" + json.dumps(_FULL_EVENT)
        backend = _RecordingBackend(runs=[run], fragments={run.run_id: two_line})
        collect(SyncState(), backend)
        out = capsys.readouterr().out
        assert f"run {_STUCK_RUN_ID}: fragment INVALID (fragment has 2 lines, expected 1)" in out
        assert "-> bad_run_ids" in out

    def test_summary_line_present_on_append_failure(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        run = _make_run()
        backend = _RecordingBackend(
            runs=[run],
            fragments={run.run_id: json.dumps(_FULL_EVENT)},
            append_should_raise=RuntimeError("flock: command not found"),
        )
        collect(SyncState(), backend)
        out = capsys.readouterr().out
        assert "append FAILED, state NOT advanced" in out
        assert "iteration summary:" in out


# ---------------------------------------------------------------------------
# Deliverable 2 — pending-run consecutive-failure tripwire
# ---------------------------------------------------------------------------


class TestPendingFailureCounter:
    def test_backward_compatible_no_attempts_arg(self) -> None:
        # Existing callers (and the pre-P1 test suite) call collect with
        # two args; the counter defaults to a throwaway map.
        run = _make_run()
        backend = _RecordingBackend(
            runs=[run], fragments={run.run_id: json.dumps(_FULL_EVENT)}
        )
        result = collect(SyncState(), backend)
        assert result.last_synced_run_id == run.run_id

    def test_download_failure_bumps_counter(self) -> None:
        run = _make_run()
        backend = _RecordingBackend(runs=[run])
        backend.download_fail_for = {run.run_id}
        attempts: dict[int, int] = {}
        result = collect(SyncState(), backend, attempts=attempts)
        assert attempts == {run.run_id: 1}
        # state untouched — same as pre-P1
        assert result.to_json() == SyncState().to_json()

    def test_streak_grows_silently_through_three(self) -> None:
        run = _make_run()
        backend = _RecordingBackend(runs=[run])
        backend.download_fail_for = {run.run_id}
        attempts: dict[int, int] = {}
        for expected in (1, 2, 3):
            collect(SyncState(), backend, attempts=attempts)
            assert attempts[run.run_id] == expected
        assert not any(kind == "pending_failure" for kind, _, _ in backend.alerts)

    def test_fourth_consecutive_failure_fires_alert_once(self) -> None:
        run = _make_run()
        backend = _RecordingBackend(runs=[run])
        backend.download_fail_for = {run.run_id}
        attempts: dict[int, int] = {}
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            collect(SyncState(), backend, attempts=attempts)
        collect(SyncState(), backend, attempts=attempts)  # the >3 crossing
        assert attempts[run.run_id] == MAX_CONSECUTIVE_FAILURES + 1
        crossings = [a for a in backend.alerts if a[0] == "pending_failure"]
        assert len(crossings) == 1
        _, rid, reason = crossings[0]
        assert rid == run.run_id
        assert "download" in reason

    def test_tripwired_predicate(self) -> None:
        assert tripwired({}) == {}
        assert tripwired({1: MAX_CONSECUTIVE_FAILURES, 2: MAX_CONSECUTIVE_FAILURES + 1}) == {
            2: MAX_CONSECUTIVE_FAILURES + 1
        }

    def test_counter_clears_when_run_integrates(self) -> None:
        run = _make_run()
        backend = _RecordingBackend(runs=[run])
        backend.download_fail_for = {run.run_id}
        attempts: dict[int, int] = {}
        collect(SyncState(), backend, attempts=attempts)
        assert attempts == {run.run_id: 1}
        # next iteration the download recovers and the append succeeds
        backend.download_fail_for = set()
        backend.fragments = {run.run_id: json.dumps(_FULL_EVENT)}
        collect(SyncState(), backend, attempts=attempts)
        assert attempts == {}

    def test_counter_clears_when_run_bad_marked(self) -> None:
        run = _make_run()
        two_line = json.dumps(_FULL_EVENT) + "\n" + json.dumps(_FULL_EVENT)
        backend = _RecordingBackend(runs=[run], fragments={run.run_id: two_line})
        attempts: dict[int, int] = {run.run_id: 2}  # prior streak
        collect(SyncState(), backend, attempts=attempts)
        assert run.run_id not in attempts  # left the pending set
        assert attempts == {}

    def test_append_failure_bumps_pending_streaks(self) -> None:
        run = _make_run()
        backend = _RecordingBackend(
            runs=[run],
            fragments={run.run_id: json.dumps(_FULL_EVENT)},
            append_should_raise=RuntimeError(
                "remote append failed (rc=127): bash: flock: command not found"
            ),
        )
        attempts: dict[int, int] = {}
        for expected in (1, 2, 3, 4):
            collect(SyncState(), backend, attempts=attempts)
            assert attempts[run.run_id] == expected
        assert tripwired(attempts) == {run.run_id: 4}
        # append-failure alert still fires per iteration (pre-P1 behavior)
        assert sum(1 for a in backend.alerts if a[0] == "append_failure") == 4
        # and no dedicated pending alert — alert_append_failure covers it
        assert not any(a[0] == "pending_failure" for a in backend.alerts)


# ---------------------------------------------------------------------------
# Deliverable 2 — sidecar persistence
# ---------------------------------------------------------------------------


class TestAttemptsSidecar:
    def test_sidecar_path_derived_from_sync_state(self) -> None:
        p = _resolve_attempts_path(Path("/Users/lwj04/.nfmd/prod-event-sync-state.json"))
        assert p == Path("/Users/lwj04/.nfmd/prod-event-sync-state.attempts.json")
        assert p.parent == Path("/Users/lwj04/.nfmd")

    def test_round_trip(self, tmp_path: Path) -> None:
        p = tmp_path / "state.attempts.json"
        _save_attempts(p, {_STUCK_RUN_ID: 2, 123: 1})
        assert _load_attempts(p) == {_STUCK_RUN_ID: 2, 123: 1}

    def test_garbage_tolerated(self, tmp_path: Path) -> None:
        p = tmp_path / "a.json"
        p.write_text("not json {")
        assert _load_attempts(p) == {}
        p.write_text('["list", "not", "dict"]')
        assert _load_attempts(p) == {}
        p.write_text('{"x": "not-an-int", "5": 0, "6": -1, "7": 2}')
        assert _load_attempts(p) == {7: 2}  # non-int / non-positive dropped

    def test_missing_file_is_zero(self, tmp_path: Path) -> None:
        assert _load_attempts(tmp_path / "absent.json") == {}

    def test_save_is_complete_and_atomic_shape(self, tmp_path: Path) -> None:
        p = tmp_path / "a.json"
        _save_attempts(p, {42: 3})
        text = p.read_text()
        assert text.endswith("\n")
        assert json.loads(text) == {"42": 3}
        assert not list(tmp_path.glob("*.tmp"))  # no torn tempfile left behind


# ---------------------------------------------------------------------------
# Dry-run (execute=False) — validate + plan only, NO write side effects
# ---------------------------------------------------------------------------


class TestDryRunExecuteFalse:
    def test_dry_run_skips_append_alerts_and_counters(self) -> None:
        run = _make_run()
        backend = _RecordingBackend(
            runs=[run],
            fragments={run.run_id: json.dumps(_FULL_EVENT)},
            append_should_raise=RuntimeError("must not be called under dry-run"),
        )
        attempts: dict[int, int] = {}
        result = collect(SyncState(), backend, attempts=attempts, execute=False)
        # the plan materializes…
        assert result.last_synced_run_id == run.run_id
        assert result.last_synced_at != ""
        # …but nothing wrote, alerted, or counted
        assert backend.append_calls == []
        assert backend.alerts == []
        assert attempts == {}

    def test_dry_run_skips_bad_fragment_alert(self) -> None:
        run = _make_run()
        bad = json.dumps(dict(_FULL_EVENT, environment="staging"))
        backend = _RecordingBackend(runs=[run], fragments={run.run_id: bad})
        result = collect(SyncState(), backend, execute=False)
        assert backend.alerts == []
        assert run.run_id in result.bad_run_ids  # plan still records it
        assert result.last_synced_run_id == 0

    def test_dry_run_download_failure_does_not_count(self) -> None:
        run = _make_run()
        backend = _RecordingBackend(runs=[run])
        backend.download_fail_for = {run.run_id}
        attempts: dict[int, int] = {}
        collect(SyncState(), backend, attempts=attempts, execute=False)
        assert attempts == {}  # a validate-only fire is not a real attempt


# ---------------------------------------------------------------------------
# main() integration — the AC: counter advances, 4th attempt exits non-zero
# ---------------------------------------------------------------------------


class TestMainTripwire:
    def _argv(self, tmp_path: Path) -> list:
        return [
            "--sync-state",
            str(tmp_path / "prod-event-sync-state.json"),
            "--master-jsonl",
            str(tmp_path / "master.jsonl"),
            "--ssh-target",
            "lwj04@127.0.0.1",
            "--attempts-path",
            str(tmp_path / "prod-event-sync-state.attempts.json"),
        ]

    def test_fourth_attempt_exits_non_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        run = _make_run()

        def fake_backend_factory(repo, ssh_target, master, state):
            backend = _RecordingBackend(runs=[run])
            backend.download_fail_for = {run.run_id}
            return backend

        monkeypatch.setattr(pec, "GhBackend", fake_backend_factory)
        argv = self._argv(tmp_path)
        rcs = [pec.main(argv) for _ in range(4)]
        assert rcs == [0, 0, 0, 1]
        capsys.readouterr()
        # sidecar survived across runs and carries the advanced streak
        sidecar = json.loads((tmp_path / "prod-event-sync-state.attempts.json").read_text())
        assert sidecar == {str(run.run_id): MAX_CONSECUTIVE_FAILURES + 1}
        # sync-state NOT advanced (download failures leave it untouched)
        state = json.loads((tmp_path / "prod-event-sync-state.json").read_text())
        assert state["last_synced_run_id"] == 0

    def test_tripwire_stderr_names_the_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        run = _make_run()

        def fake_backend_factory(repo, ssh_target, master, state):
            backend = _RecordingBackend(runs=[run])
            backend.download_fail_for = {run.run_id}
            return backend

        monkeypatch.setattr(pec, "GhBackend", fake_backend_factory)
        argv = self._argv(tmp_path)
        for _ in range(MAX_CONSECUTIVE_FAILURES + 1):
            pec.main(argv)
        captured = capsys.readouterr()
        assert "TRIPWIRE" in captured.err
        assert str(run.run_id) in captured.err

    def test_sidecar_defaults_next_to_sync_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _make_run()

        def fake_backend_factory(repo, ssh_target, master, state):
            backend = _RecordingBackend(runs=[run])
            backend.download_fail_for = {run.run_id}
            return backend

        monkeypatch.setattr(pec, "GhBackend", fake_backend_factory)
        argv = ["--sync-state", str(tmp_path / "s.json"), "--master-jsonl", "m", "--ssh-target", "t@h"]
        assert pec.main(argv) == 0
        # derived sidecar appeared even without --attempts-path
        assert (tmp_path / "s.attempts.json").exists()
        assert json.loads((tmp_path / "s.attempts.json").read_text()) == {
            str(run.run_id): 1
        }
