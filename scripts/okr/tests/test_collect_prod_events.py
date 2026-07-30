"""Tests for scripts/lib/collect_prod_events.py — the KR-3 production
deploy-event collector (NFM-2111).

The collector is the durable half of ADR-KR3-A1 §C6.1's two-stage design.
A self-hosted workflow (`.github/workflows/collect-prod-deploy-events.yml`)
downloads ``nfm-deploy-event-*.json`` artifacts uploaded by the producer
sibling, validates them against §3.1, and atomically appends one line per
event to ``$NFMD_DEPLOY_EVENTS_PATH``.

Contract under test (acceptance criteria from NFM-2111):
- A valid event appends exactly one JSONL line and writes one
  ``<run_id>\\t<sha256>\\t<processed>`` ledger row.
- A second call with the same ``sha256`` writes ZERO additional JSONL
  lines and a duplicate ledger row (idempotent on sha256, not run_id).
- A schema-invalid event is quarantined to ``<jsonl>.quarantine/<run_id>.json``
  with a ``quarantined`` ledger row, and the JSONL is untouched.
- The ledger format is the exact TSV ``<run_id>\\t<sha256>\\t<status>``.
- Atomic append uses a tempfile + mv (``tmp.jsonl`` write then ``>> jsonl``)
  so a partial write cannot corrupt the JSONL.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_LIB = REPO_ROOT / "scripts" / "lib"
COLLECTOR = SCRIPTS_LIB / "collect_prod_events.py"

SCHEMA_FIELDS = frozenset({
    "event_id",
    "ts",
    "environment",
    "triggered_by",
    "commit_sha",
    "first_pass_success",
    "health_gate_first_poll_passed",
    "rollback_triggered",
    "skip_flag_used",
    "duration_ms",
})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _valid_event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "event_id": "11111111-2222-4333-8444-555555555555",
        "ts": "2026-07-30T10:00:00Z",
        "environment": "production",
        "triggered_by": "alice",
        "commit_sha": "abc1234",
        "first_pass_success": True,
        "health_gate_first_poll_passed": True,
        "rollback_triggered": False,
        "skip_flag_used": False,
        "duration_ms": 4321,
    }
    base.update(overrides)
    return base


def _canonical_json(event: dict[str, Any]) -> str:
    """Serialise the way the producer is expected to: same field order as
    the bash writer, ``separators=(",", ":")``, no spaces, ``ensure_ascii``.
    """
    return json.dumps(event, separators=(",", ":"), ensure_ascii=False)


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    jsonl = tmp_path / "events.jsonl"
    processed = tmp_path / "events.jsonl.processed"
    quarantine = tmp_path / "events.jsonl.quarantine"
    return jsonl, processed, quarantine


# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------


def _import_collector():
    """Reload the collector module on each call so test isolation is robust."""
    sys.path.insert(0, str(SCRIPTS_LIB))
    import importlib
    if "collect_prod_events" in sys.modules:
        importlib.reload(sys.modules["collect_prod_events"])
    import collect_prod_events  # type: ignore[import-not-found]
    sys.path.pop(0)
    return collect_prod_events


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidateEvent:
    """Acceptance criterion: schema validation against §3.1 schema."""

    def test_valid_event_passes(self) -> None:
        mod = _import_collector()
        ok, err = mod.validate_event(_valid_event())
        assert ok is True, f"unexpected error: {err!r}"

    def test_extra_field_fails(self) -> None:
        mod = _import_collector()
        ev = _valid_event(extra_field="oops")
        ok, err = mod.validate_event(ev)
        assert ok is False
        assert err is not None and "extra" in err

    def test_missing_field_fails(self) -> None:
        mod = _import_collector()
        ev = _valid_event()
        del ev["first_pass_success"]
        ok, err = mod.validate_event(ev)
        assert ok is False
        assert err is not None and "first_pass_success" in err

    def test_not_an_object_fails(self) -> None:
        mod = _import_collector()
        ok, err = mod.validate_event([1, 2, 3])
        assert ok is False
        assert err is not None and "object" in err

    @pytest.mark.parametrize("field", [
        "first_pass_success",
        "health_gate_first_poll_passed",
        "rollback_triggered",
        "skip_flag_used",
    ])
    def test_non_bool_boolean_field_fails(self, field: str) -> None:
        mod = _import_collector()
        ev = _valid_event(**{field: "true"})
        ok, err = mod.validate_event(ev)
        assert ok is False
        assert err is not None and field in err

    def test_duration_ms_must_be_non_negative_int(self) -> None:
        mod = _import_collector()
        ok, _ = mod.validate_event(_valid_event(duration_ms=-1))
        assert ok is False
        ok, _ = mod.validate_event(_valid_event(duration_ms="1000"))
        assert ok is False
        ok, _ = mod.validate_event(_valid_event(duration_ms=3.14))
        assert ok is False

    def test_duration_ms_zero_is_allowed(self) -> None:
        mod = _import_collector()
        assert mod.validate_event(_valid_event(duration_ms=0))[0] is True

    def test_bool_is_not_a_valid_duration(self) -> None:
        """`isinstance(True, int)` is True in Python — guard against it."""
        mod = _import_collector()
        ok, _ = mod.validate_event(_valid_event(duration_ms=True))
        assert ok is False

    def test_environment_must_be_production(self) -> None:
        mod = _import_collector()
        ok, err = mod.validate_event(_valid_event(environment="staging"))
        assert ok is False
        assert err is not None and "environment" in err

    def test_event_id_must_be_string(self) -> None:
        mod = _import_collector()
        ok, _ = mod.validate_event(_valid_event(event_id=12345))
        assert ok is False

    def test_empty_string_required_field_fails(self) -> None:
        mod = _import_collector()
        ok, _ = mod.validate_event(_valid_event(triggered_by=""))
        assert ok is False


# ---------------------------------------------------------------------------
# Idempotency / ledger
# ---------------------------------------------------------------------------


class TestProcessEvent:
    """Acceptance: idempotency on sha256, atomic JSONL append, ledger format."""

    def test_valid_event_writes_one_jsonl_line_and_one_ledger_row(self, tmp_path: Path) -> None:
        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        event = _valid_event()
        text = _canonical_json(event)
        status = mod.process_event(jsonl, processed, text, run_id="run-1")
        assert status == "processed"
        assert len(jsonl.read_text().splitlines()) == 1
        assert len(processed.read_text().splitlines()) == 1
        record = processed.read_text().splitlines()[0].split("\t")
        assert record == ["run-1", hashlib.sha256(text.encode()).hexdigest(), "processed"]

    def test_duplicate_sha_does_not_double_append_to_jsonl(self, tmp_path: Path) -> None:
        """Acceptance: re-processing the same sha256 does NOT double-append."""
        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        text = _canonical_json(_valid_event())
        mod.process_event(jsonl, processed, text, run_id="run-1")
        mod.process_event(jsonl, processed, text, run_id="run-1b")
        # Exactly ONE jsonl line — no matter how many times processed
        assert len(jsonl.read_text().splitlines()) == 1
        rows = [ln.split("\t") for ln in processed.read_text().splitlines()]
        assert len(rows) == 2
        assert rows[0][2] == "processed"
        assert rows[1][2] == "duplicate"
        # Idempotency keys on sha, not run_id
        assert rows[0][1] == rows[1][1]

    def test_third_call_with_same_sha_still_skips_jsonl(self, tmp_path: Path) -> None:
        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        text = _canonical_json(_valid_event())
        mod.process_event(jsonl, processed, text, run_id="run-1")
        mod.process_event(jsonl, processed, text, run_id="run-2")
        mod.process_event(jsonl, processed, text, run_id="run-3")
        assert len(jsonl.read_text().splitlines()) == 1
        assert len(processed.read_text().splitlines()) == 3

    def test_distinct_shas_both_append(self, tmp_path: Path) -> None:
        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        e1 = _valid_event(event_id="11111111-2222-4333-8444-555555555551")
        e2 = _valid_event(event_id="11111111-2222-4333-8444-555555555552",
                           first_pass_success=False)
        mod.process_event(jsonl, processed, _canonical_json(e1), run_id="r1")
        mod.process_event(jsonl, processed, _canonical_json(e2), run_id="r2")
        assert len(jsonl.read_text().splitlines()) == 2
        assert len(processed.read_text().splitlines()) == 2

    def test_invalid_json_is_quarantined_and_does_not_touch_jsonl(self, tmp_path: Path) -> None:
        mod = _import_collector()
        jsonl, processed, quarantine = _paths(tmp_path)
        status = mod.process_event(jsonl, processed, "{not json}", run_id="r-bad")
        assert status == "quarantined"
        assert (not jsonl.exists()) or jsonl.read_text() == ""
        files = list(quarantine.glob("*.json"))
        assert len(files) == 1
        assert files[0].name == "r-bad.json"
        assert files[0].read_text() == "{not json}"
        rows = [ln.split("\t") for ln in processed.read_text().splitlines()]
        assert rows[0][2] == "quarantined"

    def test_schema_violation_is_quarantined_with_payload(self, tmp_path: Path) -> None:
        """Acceptance: a missing-field event quarantines."""
        mod = _import_collector()
        jsonl, processed, quarantine = _paths(tmp_path)
        ev = _valid_event()
        del ev["rollback_triggered"]
        text = _canonical_json(ev)
        status = mod.process_event(jsonl, processed, text, run_id="r-bad")
        assert status == "quarantined"
        assert (not jsonl.exists()) or jsonl.read_text() == ""
        files = list(quarantine.glob("*.json"))
        assert len(files) == 1
        assert files[0].name == "r-bad.json"
        assert files[0].read_text() == text

    def test_quarantine_repeated_for_same_run_overwrites(self, tmp_path: Path) -> None:
        """A second bad payload for the same run_id overwrites the quarantine
        file (deterministic naming means we keep the most recent)."""
        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        mod.process_event(jsonl, processed, "garbage-1", run_id="r-bad")
        mod.process_event(jsonl, processed, "garbage-2", run_id="r-bad")
        files = list((jsonl.parent / (jsonl.name + ".quarantine")).glob("*.json"))
        assert len(files) == 1
        assert files[0].read_text() == "garbage-2"

    def test_ledger_format_is_run_id_t_sha_t_status(self, tmp_path: Path) -> None:
        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        text = _canonical_json(_valid_event())
        mod.process_event(jsonl, processed, text, run_id="run-XYZ")
        for line in processed.read_text().splitlines():
            parts = line.split("\t")
            assert len(parts) == 3
            assert parts[0] == "run-XYZ"
            assert _SHA256_RE.match(parts[1]), f"bad sha: {parts[1]!r}"
            assert parts[2] in {"processed", "duplicate", "quarantined", "missing"}

    def test_read_processed_round_trips_first_occurrence(self, tmp_path: Path) -> None:
        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        text = _canonical_json(_valid_event())
        mod.process_event(jsonl, processed, text, run_id="r1")
        mod.process_event(jsonl, processed, text, run_id="r2")
        ledger = mod.read_processed(processed)
        sha = mod.compute_sha256(text)
        assert sha in ledger
        # First entry wins on lookup
        run_id, status = ledger[sha]
        assert run_id == "r1"
        assert status == "processed"

    def test_atomic_append_does_not_break_existing_lines(self, tmp_path: Path) -> None:
        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        jsonl.parent.mkdir(parents=True, exist_ok=True)
        jsonl.write_text('{"a":1}\n{"a":2}\n')
        mod.atomic_append_line(jsonl, '{"a":3}')
        lines = jsonl.read_text().splitlines()
        assert lines == ['{"a":1}', '{"a":2}', '{"a":3}']

    def test_read_processed_skips_malformed_rows(self, tmp_path: Path) -> None:
        """Defensive: ``read_processed`` must not crash on garbage rows
        (blank lines, missing tabs, wrong column count). Covers lines
        182 and 185 of collect_prod_events.py."""
        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        valid_sha = mod.compute_sha256(_canonical_json(_valid_event()))
        processed.write_text(
            # blank line
            "\n"
            # wrong column count (2 fields)
            "run-x\tsha-y\n"
            # valid row — should be returned
            f"run-good\t{valid_sha}\tprocessed\n"
            # wrong column count (4 fields)
            "run-x\tsha-y\tstatus\textra\n"
        )
        ledger = mod.read_processed(processed)
        assert ledger == {valid_sha: ("run-good", "processed")}

    def test_record_missing_writes_ledger_row(self, tmp_path: Path) -> None:
        """``record_missing`` writes a sentinel-sha + 'missing' row for
        runs whose artifact never showed up."""
        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        mod.record_missing(jsonl, processed, "run-no-artifact")
        rows = [ln.split("\t") for ln in processed.read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0][0] == "run-no-artifact"
        assert rows[0][1] == "0" * 64  # MISSING_SHA_SENTINEL
        assert rows[0][2] == "missing"
        # And the JSONL must not have been touched.
        assert not jsonl.exists() or jsonl.read_text() == ""

    def test_process_event_under_lock_acquires_and_releases(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Smoke test for the lock context manager: it must yield at
        least once and exit cleanly. A regression that drops the
        ``with _exclusive_lock(...)`` block would crash here because
        the patched ``fcntl.flock`` would not be called."""
        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)

        calls: list[int] = []
        original_flock = mod.fcntl.flock

        def counting_flock(fd: int, op: int) -> None:
            calls.append(op)
            original_flock(fd, op)

        monkeypatch.setattr(mod.fcntl, "flock", counting_flock)
        mod.process_event(
            jsonl, processed, _canonical_json(_valid_event()), run_id="r-lock"
        )
        # LOCK_EX then LOCK_UN.
        assert mod.fcntl.LOCK_EX in calls
        assert mod.fcntl.LOCK_UN in calls


class TestCliSubprocess:
    """The workflow calls the collector via subprocess — exercise the CLI."""

    def _run_cli(self, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(COLLECTOR), *args],
            capture_output=True,
            text=True,
            env={**os.environ, **env},
        )

    def test_process_valid_event_via_cli(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "events.jsonl"
        processed = jsonl.with_suffix(jsonl.suffix + ".processed")
        event_file = tmp_path / "event.json"
        text = _canonical_json(_valid_event())
        event_file.write_text(text)
        result = self._run_cli(
            ["process", "--run-id", "r-cli-1",
             "--event-json", str(event_file),
             "--jsonl", str(jsonl),
             "--processed", str(processed)],
            env={},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "processed"
        assert len(jsonl.read_text().splitlines()) == 1
        assert len(processed.read_text().splitlines()) == 1

    def test_process_invalid_event_via_cli(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "events.jsonl"
        processed = jsonl.with_suffix(jsonl.suffix + ".processed")
        event_file = tmp_path / "event.json"
        event_file.write_text("not valid json")
        result = self._run_cli(
            ["process", "--run-id", "r-bad-cli",
             "--event-json", str(event_file),
             "--jsonl", str(jsonl),
             "--processed", str(processed)],
            env={},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "quarantined"

    def test_cli_idempotency(self, tmp_path: Path) -> None:
        """Same event twice via CLI → exactly one jsonl line + two ledger rows."""
        jsonl = tmp_path / "events.jsonl"
        processed = jsonl.with_suffix(jsonl.suffix + ".processed")
        event_file = tmp_path / "event.json"
        text = _canonical_json(_valid_event())
        event_file.write_text(text)
        env: dict[str, str] = {}
        r1 = self._run_cli(
            ["process", "--run-id", "r-cli-a",
             "--event-json", str(event_file),
             "--jsonl", str(jsonl),
             "--processed", str(processed)],
            env=env,
        )
        r2 = self._run_cli(
            ["process", "--run-id", "r-cli-b",
             "--event-json", str(event_file),
             "--jsonl", str(jsonl),
             "--processed", str(processed)],
            env=env,
        )
        assert r1.stdout.strip() == "processed"
        assert r2.stdout.strip() == "duplicate"
        assert len(jsonl.read_text().splitlines()) == 1
        assert len(processed.read_text().splitlines()) == 2

    def test_cli_default_paths_use_nfmd_env(self, tmp_path: Path) -> None:
        """When env points JSONL & processed, the CLI uses them without flags."""
        jsonl = tmp_path / "events.jsonl"
        processed = jsonl.with_suffix(jsonl.suffix + ".processed")
        event_file = tmp_path / "event.json"
        event_file.write_text(_canonical_json(_valid_event()))
        result = self._run_cli(
            ["process", "--run-id", "r-env",
             "--event-json", str(event_file)],
            env={
                "NFMD_DEPLOY_EVENTS_PATH": str(jsonl),
                "NFMD_DEPLOY_EVENTS_PROCESSED_PATH": str(processed),
            },
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "processed"
        assert len(jsonl.read_text().splitlines()) == 1


class TestPathResolution:
    """Both NFMD_DEPLOY_EVENTS_PATH and NFMD_DEPLOY_EVENTS_PROCESSED_PATH
    must have sane local fallbacks (collector works in dev without the repo
    variable)."""

    def test_default_processed_path_is_jsonl_with_processed_suffix(self) -> None:
        mod = _import_collector()
        sentinel = "/tmp/x/.deploy-events.jsonl"
        assert str(mod.resolve_processed_path(sentinel)) == sentinel + ".processed"

    def test_default_jsonl_when_env_unset(self) -> None:
        mod = _import_collector()
        env = {k: v for k, v in os.environ.items() if k != "NFMD_DEPLOY_EVENTS_PATH"}
        path = mod.resolve_jsonl_path(env=env)
        assert str(path).endswith("/docker/.deploy-events.jsonl")

    def test_env_override_wins_for_jsonl(self) -> None:
        mod = _import_collector()
        result = mod.resolve_jsonl_path(env={"NFMD_DEPLOY_EVENTS_PATH": "/custom/path.jsonl"})
        assert result == Path("/custom/path.jsonl")


class TestConcurrencySafety:
    """ADR §C6.1.6 / code review 2026-07-30:

    The read-process-write critical section must hold an exclusive
    ``fcntl.flock``, and the ledger row must hit disk BEFORE the JSONL
    line. These tests are the regression net for the two HIGH findings
    the reviewer flagged.
    """

    def test_lockfile_is_created_alongside_processed_ledger(
        self, tmp_path: Path
    ) -> None:
        """``process_event`` opens a lockfile co-located with the ledger.
        Used purely as an ``fcntl.flock`` handle; its contents are
        irrelevant."""
        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        mod.process_event(jsonl, processed, _canonical_json(_valid_event()), run_id="r1")
        lock_path = mod._lock_path_for(processed)
        assert lock_path.exists()
        assert lock_path.parent == processed.parent
        assert lock_path.name == processed.name + mod._LOCK_SUFFIX

    def test_concurrent_threads_with_same_sha_produce_one_jsonl_line(
        self, tmp_path: Path
    ) -> None:
        """Spawn N threads all calling ``process_event`` with the SAME
        sha. The ``fcntl.flock`` must serialise them so exactly one
        JSONL line is appended, regardless of how many threads race.

        This is the regression test for the HIGH #3 check-then-act race.
        """
        import threading

        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        text = _canonical_json(_valid_event())
        n = 16
        barrier = threading.Barrier(n)
        results: list[str] = []
        results_lock = threading.Lock()

        def worker(run_id: str) -> None:
            # All threads block on the barrier until everyone is ready,
            # then release simultaneously to maximise the race window.
            barrier.wait()
            status = mod.process_event(jsonl, processed, text, run_id=run_id)
            with results_lock:
                results.append(status)

        threads = [
            threading.Thread(target=worker, args=(f"r{i}",))
            for i in range(n)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one writer claimed the sha; everyone else recorded
        # "duplicate". Order between writers is non-deterministic but
        # the count must be exactly one.
        assert results.count("processed") == 1, results
        assert results.count("duplicate") == n - 1, results
        assert len(jsonl.read_text().splitlines()) == 1
        rows = [ln.split("\t") for ln in processed.read_text().splitlines()]
        assert len(rows) == n
        assert sum(1 for r in rows if r[2] == "processed") == 1
        assert sum(1 for r in rows if r[2] == "duplicate") == n - 1

    def test_concurrent_distinct_shas_all_persist(
        self, tmp_path: Path
    ) -> None:
        """Sanity: the lock serialises correctly but does NOT block
        independent events. N distinct shas must each append exactly
        one JSONL line under contention."""
        import threading

        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        n = 8
        barrier = threading.Barrier(n)

        def worker(idx: int) -> None:
            ev = _valid_event(
                event_id=f"11111111-2222-4333-8444-5555555555{idx:02d}",
                duration_ms=1000 + idx,
            )
            barrier.wait()
            mod.process_event(jsonl, processed, _canonical_json(ev), run_id=f"r{idx}")

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(n)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(jsonl.read_text().splitlines()) == n
        assert len(processed.read_text().splitlines()) == n

    def test_ledger_row_is_written_before_jsonl_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression for HIGH #4 crash window.

        Inside the lock, the ledger row must hit disk FIRST, then the
        JSONL line. We assert this by replacing ``atomic_append_line``
        with a probe that reads the ledger just BEFORE the would-be
        JSONL append — the row must already be there.
        """
        mod = _import_collector()
        jsonl, processed, _ = _paths(tmp_path)
        text = _canonical_json(_valid_event())
        sha = mod.compute_sha256(text)

        # Snapshot the probe state when the test fires.
        probe: dict[str, object] = {"ledger_had_row": False, "jsonl_had_line": False}

        original_atomic = mod.atomic_append_line

        def probe_atomic(jp: Path, line: str) -> None:
            # The ledger row MUST already be on disk by this point.
            ledger = mod.read_processed(processed)
            probe["ledger_had_row"] = sha in ledger
            probe["jsonl_had_line"] = jp.exists() and bool(jp.read_text())
            # Defer to the real implementation.
            original_atomic(jp, line)

        monkeypatch.setattr(mod, "atomic_append_line", probe_atomic)

        status = mod.process_event(jsonl, processed, text, run_id="run-1")
        assert status == "processed"
        assert probe["ledger_had_row"] is True
        assert probe["jsonl_had_line"] is False  # not yet appended
        # And of course after the probe returns the JSONL does get it.
        assert len(jsonl.read_text().splitlines()) == 1
