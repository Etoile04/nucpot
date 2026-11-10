"""Tests for scripts/okr/backfill_prod_events_nfm5935.py — the one-shot
reviewed backfill (NFM-5935).

Contract under test (acceptance criteria from NFM-5935):
- Fragment classification dedups by event_id against the master+legacy id set:
  an artifact event already covered by the legacy lane is an
  ``overlap_legacy`` skip, never a duplicate append.
- Runs without artifacts (or with invalid fragments) become the
  documented gap list — genuinely-invalid bad_run_ids only.
- The pending append batch keeps global ``ts`` ascending; a window that
  would violate append order is a fatal, state-preserving abort.
- flush_under_lock re-reads master under the collector's flock and
  re-filters the batch if master drifted, so no duplicate event_id can
  land even when a collector tick appends concurrently.
- check_postconditions: duplicate event_ids fail; state at (or
  legitimately past) the catch-up point passes; the pre-backfill state
  does NOT pass.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

import scripts.okr.backfill_prod_events_nfm5935 as bf
from scripts.okr.prod_event_collector import SyncState


def make_event(ts: str = "2026-10-01T00:00:00Z") -> dict[str, Any]:
    """A §3.1-valid deploy event with a fresh UUIDv4 event_id."""
    return {
        "event_id": str(uuid.uuid4()),
        "ts": ts,
        "environment": "production",
        "triggered_by": "Etoile04",
        "commit_sha": "a" * 40,
        "first_pass_success": True,
        "health_gate_first_poll_passed": True,
        "rollback_triggered": False,
        "skip_flag_used": False,
        "duration_ms": 1_800_000,
    }


class TestClassifyFragments:
    def test_new_event_is_pending(self) -> None:
        ev = make_event()
        pending, status, bad = bf.classify_fragments({42: json.dumps(ev)}, set(), set())
        assert len(pending) == 1
        assert status[42] == bf.STATUS_NEW
        assert bad == {}
        assert json.loads(pending[0][2])["event_id"] == ev["event_id"]

    def test_legacy_overlap_is_skipped_not_appended(self) -> None:
        ev = make_event()
        pending, status, _ = bf.classify_fragments({42: json.dumps(ev)}, set(), {ev["event_id"]})
        assert pending == []
        assert status[42] == bf.STATUS_OVERLAP_LEGACY

    def test_master_overlap_is_skipped(self) -> None:
        ev = make_event()
        pending, status, _ = bf.classify_fragments({42: json.dumps(ev)}, {ev["event_id"]}, set())
        assert pending == []
        assert status[42] == bf.STATUS_OVERLAP_MASTER

    def test_missing_artifact_becomes_no_artifact_bad(self) -> None:
        pending, status, bad = bf.classify_fragments({42: None}, set(), set())
        assert pending == []
        assert status[42] == bf.STATUS_NO_ARTIFACT
        assert bad[bf.STATUS_NO_ARTIFACT] == [42]

    def test_invalid_fragment_is_bad_with_reason(self) -> None:
        pending, status, bad = bf.classify_fragments({42: "not json"}, set(), set())
        assert pending == []
        assert status[42].startswith(bf.STATUS_BAD)
        (bucket,) = bad.values()
        assert bucket == [42]
        assert not any(k == bf.STATUS_NO_ARTIFACT for k in bad)

    def test_pending_sorted_by_ts_then_run_id(self) -> None:
        late = make_event(ts="2026-10-02T00:00:00Z")
        early_a = make_event(ts="2026-10-01T00:00:00Z")
        early_b = make_event(ts="2026-10-01T00:00:00Z")
        pending, _, _ = bf.classify_fragments(
            {900: json.dumps(late), 100: json.dumps(early_a), 200: json.dumps(early_b)},
            set(),
            set(),
        )
        assert [p[0] for p in pending] == [100, 200, 900]

    def test_mixed_window_accounting_balances(self) -> None:
        runs = {i: json.dumps(make_event()) for i in (1, 2, 3)}
        runs[4] = None  # no artifact
        runs[5] = "garbage"  # invalid fragment
        legacy_id = json.loads(runs[2])["event_id"]
        _, status, bad = bf.classify_fragments(runs, set(), {legacy_id})
        integrated = [s for s in status.values() if s == bf.STATUS_NEW]
        assert len(integrated) == 2
        assert status[2] == bf.STATUS_OVERLAP_LEGACY
        assert bad[bf.STATUS_NO_ARTIFACT] == [4]


class TestTsOrdering:
    def test_batch_newer_than_master_passes(self) -> None:
        master = [make_event(ts="2026-09-07T17:11:01Z")]
        batch = [(0, "2026-09-08T00:00:00Z", "{}")]
        bf.validate_ts_ordering(master, batch)

    def test_batch_older_than_master_aborts(self) -> None:
        master = [make_event(ts="2026-10-20T00:00:00Z")]
        batch = [(0, "2026-09-08T00:00:00Z", "{}")]
        with pytest.raises(bf.BackfillError, match="append-order violation"):
            bf.validate_ts_ordering(master, batch)

    def test_empty_master_or_batch_passes(self) -> None:
        bf.validate_ts_ordering([], [(0, "2026-01-01T00:00:00Z", "{}")])
        bf.validate_ts_ordering([make_event()], [])


class TestReadJsonl:
    def test_roundtrip(self, tmp_path: Path) -> None:
        events = [make_event(), make_event(ts="2026-10-02T00:00:00Z")]
        path = tmp_path / "ledger.jsonl"
        path.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in events), encoding="utf-8")
        sha, parsed, ids = bf.read_jsonl(path, "test")
        assert len(parsed) == 2
        assert ids == [e["event_id"] for e in events]
        assert len(sha) == 64

    def test_duplicate_id_within_file_aborts(self, tmp_path: Path) -> None:
        ev = make_event()
        path = tmp_path / "ledger.jsonl"
        path.write_text(json.dumps(ev) + "\n" + json.dumps(ev) + "\n", encoding="utf-8")
        with pytest.raises(bf.BackfillError, match="duplicates event_id"):
            bf.read_jsonl(path, "test")

    def test_unparseable_line_aborts(self, tmp_path: Path) -> None:
        path = tmp_path / "ledger.jsonl"
        path.write_text('{"event_id": "ok"}\n{broken\n', encoding="utf-8")
        with pytest.raises(bf.BackfillError, match="not valid JSON"):
            bf.read_jsonl(path, "test")


class TestCheckPostconditions:
    def _summary(self, lines: int = 122, uniq: int = 122) -> dict[str, Any]:
        return {
            "master_after": {"lines": lines, "uniq_ids": uniq, "sha256": "x"},
            "per_status_counts": {bf.STATUS_NEW: 100, bf.STATUS_OVERLAP_LEGACY: 74, bf.STATUS_NO_ARTIFACT: 90},
            "bad_reasons": {"invalid fragment: x": {"count": 0, "run_ids": []}},
            "window_runs": 264,
        }

    def test_passes_at_target_state(self) -> None:
        state = SyncState(last_synced_run_id=42_550_621_741, last_synced_at="2026-11-10T09:00:00Z")
        assert bf.check_postconditions(self._summary(), state, 42_550_621_741) == []

    def test_passes_on_legitimate_forward_advance(self) -> None:
        state = SyncState(last_synced_run_id=42_550_621_800, last_synced_at="2026-11-10T09:05:00Z")
        assert bf.check_postconditions(self._summary(), state, 42_550_621_741) == []

    def test_pre_backfill_state_fails(self) -> None:
        state = SyncState(last_synced_run_id=0, last_synced_at="")
        failures = bf.check_postconditions(self._summary(), state, 42_550_621_741)
        assert any("state not at catch-up point" in f for f in failures)

    def test_duplicate_master_ids_fail(self) -> None:
        state = SyncState(last_synced_run_id=42_550_621_741, last_synced_at="2026-11-10T09:00:00Z")
        failures = bf.check_postconditions(self._summary(lines=130, uniq=122), state, 42_550_621_741)
        assert any("duplicate event_ids" in f for f in failures)

    def test_none_state_skips_state_checks(self) -> None:
        assert bf.check_postconditions(self._summary(), None, 42_550_621_741) == []

    def test_unbalanced_accounting_fails(self) -> None:
        state = SyncState(last_synced_run_id=42_550_621_741, last_synced_at="2026-11-10T09:00:00Z")
        summary = self._summary()
        summary["window_runs"] = 999
        failures = bf.check_postconditions(summary, state, 42_550_621_741)
        assert any("does not balance" in f for f in failures)


class TestVerifyBackups:
    def test_matching_backups_pass(self, tmp_path: Path) -> None:
        live_master = tmp_path / "master-deploy-events.jsonl"
        live_state = tmp_path / "prod-event-sync-state.json"
        bdir = tmp_path / "backup"
        bdir.mkdir()
        for live in (live_master, live_state):
            live.write_text("payload\n", encoding="utf-8")
            (bdir / live.name).write_text("payload\n", encoding="utf-8")
        assert bf.verify_backups(str(bdir), live_master, live_state) == str(bdir)

    def test_missing_backup_aborts(self, tmp_path: Path) -> None:
        live = tmp_path / "master-deploy-events.jsonl"
        live.write_text("payload\n", encoding="utf-8")
        bdir = tmp_path / "backup"
        bdir.mkdir()
        with pytest.raises(bf.BackfillError, match="backup missing"):
            bf.verify_backups(str(bdir), live, tmp_path / "state.json")

    def test_drifted_backup_aborts(self, tmp_path: Path) -> None:
        live = tmp_path / "master-deploy-events.jsonl"
        bdir = tmp_path / "backup"
        bdir.mkdir()
        live.write_text("live\n", encoding="utf-8")
        (bdir / live.name).write_text("stale\n", encoding="utf-8")
        with pytest.raises(bf.BackfillError, match="drift before write"):
            bf.verify_backups(str(bdir), live, tmp_path / "state.json")


class TestFlushUnderLock:
    def test_append_and_state_advance(self, tmp_path: Path) -> None:
        master = tmp_path / "master-deploy-events.jsonl"
        state = tmp_path / "state.json"
        ev_old = make_event(ts="2026-09-07T17:11:01Z")
        ev_new = make_event(ts="2026-09-08T00:00:00Z")
        master.write_text(json.dumps(ev_old, separators=(",", ":")) + "\n", encoding="utf-8")
        sha = bf.sha256_text(master.read_text(encoding="utf-8"))
        batch = [(42, "2026-09-08T00:00:00Z", bf.compact_event(ev_new))]
        target = SyncState(last_synced_run_id=42, last_synced_at="2026-11-10T09:00:00Z", bad_run_ids=[])
        result = bf.flush_under_lock(master, state, batch, target, sha)
        assert result["appended"] == 1
        lines = master.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["event_id"] == ev_new["event_id"]
        persisted = SyncState.from_json(state.read_text(encoding="utf-8"))
        assert persisted.last_synced_run_id == 42
        # existing line preserved byte-for-byte
        assert lines[0] == bf.compact_event(ev_old)

    def test_master_drift_under_lock_refilters_batch(self, tmp_path: Path) -> None:
        master = tmp_path / "master-deploy-events.jsonl"
        state = tmp_path / "state.json"
        ev_old = make_event(ts="2026-09-07T17:11:01Z")
        ev_collector = make_event(ts="2026-09-07T18:00:00Z")  # lands between load and flush
        ev_batch = make_event(ts="2026-09-08T00:00:00Z")
        master.write_text(bf.compact_event(ev_old) + "\n", encoding="utf-8")
        stale_sha = bf.sha256_text(master.read_text(encoding="utf-8"))
        # simulate the concurrent collector append
        master.write_text(bf.compact_event(ev_old) + "\n" + bf.compact_event(ev_collector) + "\n", encoding="utf-8")
        batch = [(42, "2026-09-08T00:00:00Z", bf.compact_event(ev_batch))]
        target = SyncState(last_synced_run_id=42, last_synced_at="2026-11-10T09:00:00Z", bad_run_ids=[])
        bf.flush_under_lock(master, state, batch, target, stale_sha)
        lines = master.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3  # old + collector's + ours, no duplicates
        ids = [json.loads(line)["event_id"] for line in lines]
        assert len(ids) == len(set(ids))

    def test_batch_line_already_present_is_dropped(self, tmp_path: Path) -> None:
        master = tmp_path / "master-deploy-events.jsonl"
        state = tmp_path / "state.json"
        ev = make_event(ts="2026-09-08T00:00:00Z")
        master.write_text(bf.compact_event(ev) + "\n", encoding="utf-8")
        stale_sha = "0" * 64  # guarantees the drift path triggers
        batch = [(42, "2026-09-08T00:00:00Z", bf.compact_event(ev))]
        target = SyncState(last_synced_run_id=42, last_synced_at="2026-11-10T09:00:00Z", bad_run_ids=[])
        result = bf.flush_under_lock(master, state, batch, target, stale_sha)
        assert result["appended"] == 0
        assert len(master.read_text(encoding="utf-8").splitlines()) == 1


class TestWatchAndRepair:
    def _fast(self, monkeypatch: pytest.MonkeyPatch, polls: int = 2) -> None:
        monkeypatch.setattr(bf, "CLOBBER_WATCH_INTERVAL_S", 0)
        monkeypatch.setattr(bf, "CLOBBER_WATCH_POLLS", polls)

    def test_reverting_write_is_repaired(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._fast(monkeypatch)
        state = tmp_path / "state.json"
        master = tmp_path / "master-deploy-events.jsonl"
        master_lock = bf.master_lock_path(master)
        target = SyncState(last_synced_run_id=42, last_synced_at="2026-11-10T09:00:00Z")
        expected = (target.to_json() + "\n").encode("utf-8")
        state.write_text(expected.decode("utf-8"), encoding="utf-8")
        # a reverting tick writes the stale pre-backfill state after the first poll
        original_sleep = bf.time.sleep

        def clobber_once(_seconds: float) -> None:
            clobber_once.calls = getattr(clobber_once, "calls", 0) + 1
            original_sleep(0)
            if clobber_once.calls == 1:
                state.write_text('{"last_synced_run_id": 0, "last_synced_at": "", "bad_run_ids": []}\n', encoding="utf-8")

        monkeypatch.setattr(bf.time, "sleep", clobber_once)
        repairs = bf.watch_and_repair(state, master_lock, expected, target)
        assert repairs == 1
        assert SyncState.from_json(state.read_text(encoding="utf-8")).last_synced_run_id == 42

    def test_legitimate_advance_stops_watch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._fast(monkeypatch)
        state = tmp_path / "state.json"
        master = tmp_path / "master-deploy-events.jsonl"
        target = SyncState(last_synced_run_id=42, last_synced_at="2026-11-10T09:00:00Z")
        expected = (target.to_json() + "\n").encode("utf-8")
        state.write_text(expected.decode("utf-8"), encoding="utf-8")
        original_sleep = bf.time.sleep

        def advance_once(_seconds: float) -> None:
            advance_once.calls = getattr(advance_once, "calls", 0) + 1
            original_sleep(0)
            if advance_once.calls == 1:
                advanced = SyncState(last_synced_run_id=50, last_synced_at="2026-11-10T09:01:00Z")
                state.write_text(advanced.to_json() + "\n", encoding="utf-8")

        monkeypatch.setattr(bf.time, "sleep", advance_once)
        assert bf.watch_and_repair(state, bf.master_lock_path(master), expected, target) == 0
        assert SyncState.from_json(state.read_text(encoding="utf-8")).last_synced_run_id == 50


class TestRepairStateOnly:
    def test_rewrites_from_summary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(bf, "CLOBBER_WATCH_INTERVAL_S", 0)
        monkeypatch.setattr(bf, "CLOBBER_WATCH_POLLS", 1)
        state = tmp_path / "prod-event-sync-state.json"
        master = tmp_path / "master-deploy-events.jsonl"
        master.write_text("", encoding="utf-8")
        state.write_text('{"last_synced_run_id": 0, "last_synced_at": "", "bad_run_ids": []}\n', encoding="utf-8")
        summary = tmp_path / "summary.json"
        summary.write_text(
            json.dumps({"state_after": {"last_synced_run_id": 42, "last_synced_at": "2026-11-10T09:00:00Z", "bad_run_ids": []}}),
            encoding="utf-8",
        )
        args = bf.parse_args(
            ["--sync-state", str(state), "--master-jsonl", str(master), "--repair-state-only", str(summary)]
        )
        result = bf.repair_state_only(args)
        assert result["state_after"]["last_synced_run_id"] == 42
        assert SyncState.from_json(state.read_text(encoding="utf-8")).last_synced_run_id == 42
