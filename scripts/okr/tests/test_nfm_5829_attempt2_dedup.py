"""Per-artifact dedup: attempt-2 (re-run) deploy events must be collected.

NFM-5829 (RE-FINDING, found during the NFM-5825 backfill on 2026-11-02).
The ledger is keyed per **run** but the producer emits one artifact per
**attempt** (``nfm-deploy-event-<run_id>-<attempt>``). The orchestrator's
ledger skip (``grep '^<run_id>\\t'``) fires on the attempt-1 row, so when a
deploy run is re-run the attempt-2 artifact lands *after* the run is
already ledgered and is skipped forever. Two real losses, both recovered
manually on 11-02 via the sha-deduped Python CLI:

- run ``40309185857`` (``d2dd17845``): attempt-2 event 10-27T02:48:23Z
  (artifact ``nfm-deploy-event-40309185857-2``) was missing.
- run ``40400671470`` (``4e4a18ce``): attempt-2 event 10-29T00:53:16Z
  (artifact ``nfm-deploy-event-40400671470-2``) was missing.

The fix (fix sketch option (a), with the API-budget guardrail):

1. The ledger gains an optional 4th "coverage token" field:
   ``<run_id>\\t<sha256>\\t<status>[\\t<coverage>]`` where ``coverage`` is
   the artifact name (``process --artifact-name``) or the bare attempt
   digits (``record-missing --attempt``). ``read_processed`` must accept
   3- OR 4-field rows — dropping 4-field rows would un-ledger their shas
   and double-append to the JSONL.
2. The orchestrator enumerates each run's ``run_attempt`` (free — same
   API response it already fetches) and skips a run only when its ledger
   coverage already reaches that attempt. A covered run costs ZERO
   artifact-listing API calls — the 5-minute fire walks 50 runs and must
   not grow 50 listings per fire (cf. the NFM-5796 rate-budget work).
3. For an uncovered attempt the orchestrator lists artifacts and
   downloads only names not already accounted for in the ledger; the
   sha256-keyed dedup inside ``process`` remains the final backstop.
   Dedup happens BEFORE download, so re-fires cannot bloat the ledger
   with per-fire duplicate rows.

Legacy 3-field rows: a non-``missing`` legacy row was written for
whatever artifact existed at first sight of the run — conservatively
attempt 1. A legacy ``missing`` row covered nothing; the orchestrator
rescans it once and stamps the attempt on a new row (append-only
ledger), after which the run is covered again.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_LIB = REPO_ROOT / "scripts" / "lib"
COLLECTOR = SCRIPTS_LIB / "collect_prod_events.py"
ORCHESTRATOR = SCRIPTS_LIB / "collect_prod_run.sh"

RUN_ID = 40309185857  # the d2dd17845 ship from the issue evidence
ART_1 = f"nfm-deploy-event-{RUN_ID}-1"
ART_2 = f"nfm-deploy-event-{RUN_ID}-2"
SENTINEL = "0" * 64


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _import_collector():
    """Reload the collector module so test isolation is robust."""
    sys.path.insert(0, str(SCRIPTS_LIB))
    import importlib

    if "collect_prod_events" in sys.modules:
        importlib.reload(sys.modules["collect_prod_events"])
    import collect_prod_events  # type: ignore[import-not-found]

    sys.path.pop(0)
    return collect_prod_events


def _canonical_event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "event_id": "11111111-2222-4333-8444-555555555555",
        "ts": "2026-10-26T13:51:00Z",
        "environment": "production",
        "triggered_by": "etoile",
        "commit_sha": "d2dd17845",
        "first_pass_success": True,
        "health_gate_first_poll_passed": True,
        "rollback_triggered": False,
        "skip_flag_used": False,
        "duration_ms": 4321,
    }
    base.update(overrides)
    return base


def _event_text(event: dict[str, Any]) -> str:
    """Serialise the way the producer does (and the way the sha is keyed)."""
    return json.dumps(event, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Part A — ledger contract (collect_prod_events.py)
# ---------------------------------------------------------------------------


class TestLedgerFourFieldCompat:
    """The optional 4th coverage-token field must not break sha indexing."""

    def test_read_processed_indexes_four_field_rows(self, tmp_path: Path) -> None:
        """Regression guard: if ``read_processed`` dropped 4-field rows,
        every post-fix sha would vanish from the ledger and the next fire
        would double-append the event to the JSONL."""
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        sha = "a" * 64
        processed.write_text(f"{RUN_ID}\t{sha}\tprocessed\t{ART_1}\n")
        ledger = mod.read_processed(processed)
        assert ledger == {sha: (str(RUN_ID), "processed")}

    def test_read_processed_still_indexes_legacy_three_field_rows(
        self, tmp_path: Path
    ) -> None:
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        sha = "b" * 64
        processed.write_text(f"{RUN_ID}\t{sha}\tprocessed\n")
        assert mod.read_processed(processed) == {sha: (str(RUN_ID), "processed")}

    def test_read_processed_first_wins_across_mixed_row_shapes(
        self, tmp_path: Path
    ) -> None:
        """The original processed row must win over later duplicate
        annotations regardless of which row shape carries it."""
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        sha = "c" * 64
        processed.write_text(
            f"{RUN_ID}\t{sha}\tprocessed\n"
            f"{RUN_ID}\t{sha}\tduplicate\t{ART_1}\n"
        )
        assert mod.read_processed(processed)[sha] == (str(RUN_ID), "processed")

    def test_malformed_rows_are_still_skipped(self, tmp_path: Path) -> None:
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        processed.write_text("garbage\n\t\t\na\tb\tc\td\te\n")
        assert mod.read_processed(processed) == {}

    def test_process_event_with_artifact_name_writes_four_field_row(
        self, tmp_path: Path
    ) -> None:
        mod = _import_collector()
        jsonl = tmp_path / "events.jsonl"
        processed = tmp_path / "events.jsonl.processed"
        text = _event_text(_canonical_event())
        result = mod.process_event(jsonl, processed, text, str(RUN_ID), artifact_name=ART_1)
        assert result == "processed"
        row = processed.read_text().splitlines()[0].split("\t")
        assert row == [str(RUN_ID), _sha256(text), "processed", ART_1]

    def test_process_event_without_artifact_name_stays_three_field(
        self, tmp_path: Path
    ) -> None:
        """Back-compat: direct callers that don't know the artifact name
        keep producing the legacy 3-field row."""
        mod = _import_collector()
        jsonl = tmp_path / "events.jsonl"
        processed = tmp_path / "events.jsonl.processed"
        text = _event_text(_canonical_event())
        result = mod.process_event(jsonl, processed, text, str(RUN_ID))
        assert result == "processed"
        row = processed.read_text().splitlines()[0].split("\t")
        assert row == [str(RUN_ID), _sha256(text), "processed"]

    def test_duplicate_with_artifact_name_records_coverage(self, tmp_path: Path) -> None:
        """A re-download of an already-seen artifact (legacy run migration)
        must land exactly one duplicate row carrying the artifact name —
        that row is what makes the next fire skip the download."""
        mod = _import_collector()
        jsonl = tmp_path / "events.jsonl"
        processed = tmp_path / "events.jsonl.processed"
        text = _event_text(_canonical_event())
        assert mod.process_event(jsonl, processed, text, str(RUN_ID)) == "processed"
        assert mod.process_event(
            jsonl, processed, text, str(RUN_ID), artifact_name=ART_1
        ) == "duplicate"
        rows = [ln.split("\t") for ln in processed.read_text().splitlines() if ln.strip()]
        assert rows == [
            [str(RUN_ID), _sha256(text), "processed"],
            [str(RUN_ID), _sha256(text), "duplicate", ART_1],
        ]
        assert len(jsonl.read_text().splitlines()) == 1

    def test_quarantine_with_artifact_name_records_coverage(self, tmp_path: Path) -> None:
        mod = _import_collector()
        jsonl = tmp_path / "events.jsonl"
        processed = tmp_path / "events.jsonl.processed"
        result = mod.process_event(
            jsonl, processed, "not json", str(RUN_ID), artifact_name=ART_2
        )
        assert result == "quarantined"
        row = processed.read_text().splitlines()[0].split("\t")
        assert row[2] == "quarantined" and row[3] == ART_2
        assert not jsonl.exists() or jsonl.read_text() == ""

    def test_four_field_ledger_does_not_block_new_events(self, tmp_path: Path) -> None:
        """End-to-end integrity: a ledger that already contains 4-field
        rows must still admit a brand-new event into the JSONL."""
        mod = _import_collector()
        jsonl = tmp_path / "events.jsonl"
        processed = tmp_path / "events.jsonl.processed"
        old = _canonical_event(event_id="11111111-2222-4333-8444-555555555555")
        processed.write_text(f"{RUN_ID}\t{_sha256(_event_text(old))}\tprocessed\t{ART_1}\n")
        new = _canonical_event(
            event_id="22222222-3333-4444-8555-666666666666",
            ts="2026-10-27T02:48:23Z",
        )
        result = mod.process_event(
            jsonl, processed, _event_text(new), str(RUN_ID), artifact_name=ART_2
        )
        assert result == "processed"
        lines = jsonl.read_text().splitlines()
        assert len(lines) == 1 and json.loads(lines[0]) == new

    def test_record_missing_with_attempt_writes_four_field_row(
        self, tmp_path: Path
    ) -> None:
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        mod.record_missing(tmp_path / "events.jsonl", processed, str(RUN_ID), attempt=2)
        row = processed.read_text().splitlines()[0].split("\t")
        assert row == [str(RUN_ID), SENTINEL, "missing", "2"]

    def test_coverage_tokens_are_newline_safe(self, tmp_path: Path) -> None:
        """A token containing a tab or newline would forge extra ledger
        rows — the entry points must strip control characters."""
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        evil = f"{ART_1}\n9999\t{SENTINEL}\tmissing"
        # Seed one legitimate row, then process with a hostile artifact name.
        mod.record_missing(tmp_path / "events.jsonl", processed, str(RUN_ID), attempt=None)
        jsonl = tmp_path / "events.jsonl"
        mod.process_event(
            jsonl,
            processed,
            _event_text(_canonical_event(event_id="33333333-4444-4555-8666-777777777777")),
            str(RUN_ID),
            artifact_name=evil,
        )
        rows = [ln.split("\t") for ln in processed.read_text().splitlines() if ln.strip()]
        assert len(rows) == 2, f"hostile token forged extra rows: {rows!r}"
        assert len(rows[1]) == 4, f"hostile token changed the row shape: {rows!r}"
        assert "\n" not in rows[1][3] and "\t" not in rows[1][3]


class TestRunCoverage:
    """Per-run attempt coverage derived from the ledger."""

    def test_legacy_processed_row_covers_attempt_one(self, tmp_path: Path) -> None:
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        processed.write_text(f"{RUN_ID}\t{'d' * 64}\tprocessed\n")
        assert mod.run_coverage(processed, str(RUN_ID)) == (1, set())

    def test_legacy_quarantined_row_covers_attempt_one(self, tmp_path: Path) -> None:
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        processed.write_text(f"{RUN_ID}\t{'d' * 64}\tquarantined\n")
        assert mod.run_coverage(processed, str(RUN_ID)) == (1, set())

    def test_legacy_missing_row_covers_nothing(self, tmp_path: Path) -> None:
        """A legacy ``missing`` row means "no artifact existed at write
        time" — nothing was collected, so the run must be rescannable."""
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        processed.write_text(f"{RUN_ID}\t{SENTINEL}\tmissing\n")
        assert mod.run_coverage(processed, str(RUN_ID)) == (0, set())

    def test_artifact_name_token_covers_its_attempt(self, tmp_path: Path) -> None:
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        processed.write_text(f"{RUN_ID}\t{'e' * 64}\tprocessed\t{ART_2}\n")
        assert mod.run_coverage(processed, str(RUN_ID)) == (2, {ART_2})

    def test_missing_attempt_token_covers_its_attempt(self, tmp_path: Path) -> None:
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        processed.write_text(f"{RUN_ID}\t{SENTINEL}\tmissing\t1\n")
        assert mod.run_coverage(processed, str(RUN_ID)) == (1, set())

    def test_coverage_takes_max_and_union_across_rows(self, tmp_path: Path) -> None:
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        processed.write_text(
            f"{RUN_ID}\t{'1' * 64}\tprocessed\n"  # legacy → attempt 1
            f"{RUN_ID}\t{'2' * 64}\tduplicate\t{ART_1}\n"  # attempt 1, named
            f"{RUN_ID}\t{'3' * 64}\tprocessed\t{ART_2}\n"  # attempt 2, named
            f"{RUN_ID}\t{SENTINEL}\tmissing\t1\n"  # attempt 1
        )
        attempt, names = mod.run_coverage(processed, str(RUN_ID))
        assert attempt == 2
        assert names == {ART_1, ART_2}

    def test_hostile_or_foreign_tokens_are_ignored(self, tmp_path: Path) -> None:
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        processed.write_text(
            f"{RUN_ID}\t{'4' * 64}\tprocessed\tnfm-deploy-event-999-2\n"
            f"{RUN_ID}\t{'5' * 64}\tprocessed\tnot-a-real-token\n"
        )
        assert mod.run_coverage(processed, str(RUN_ID)) == (0, set())

    def test_unknown_run_has_zero_coverage(self, tmp_path: Path) -> None:
        mod = _import_collector()
        processed = tmp_path / "events.jsonl.processed"
        processed.write_text(f"{RUN_ID}\t{'6' * 64}\tprocessed\n")
        assert mod.run_coverage(processed, "123") == (0, set())

    def test_missing_ledger_file_has_empty_map(self, tmp_path: Path) -> None:
        mod = _import_collector()
        assert mod.coverage_map(tmp_path / "nope.processed") == {}

    def test_coverage_map_cli_emits_tsv(self, tmp_path: Path) -> None:
        processed = tmp_path / "events.jsonl.processed"
        processed.write_text(
            f"{RUN_ID}\t{'1' * 64}\tprocessed\n"
            f"{RUN_ID}\t{'2' * 64}\tprocessed\t{ART_2}\n"
            f"40400671470\t{SENTINEL}\tmissing\t1\n"
        )
        result = subprocess.run(
            [sys.executable, str(COLLECTOR), "coverage-map", "--processed", str(processed)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == (
            f"{RUN_ID}\t2\t{ART_2}\n"
            f"40400671470\t1\t\n"
        )

    def test_process_and_record_missing_via_cli_write_coverage(
        self, tmp_path: Path
    ) -> None:
        jsonl = tmp_path / "events.jsonl"
        processed = tmp_path / "events.jsonl.processed"
        event_file = tmp_path / "event.json"
        text = _event_text(_canonical_event())
        event_file.write_text(text)

        r1 = subprocess.run(
            [
                sys.executable, str(COLLECTOR), "process",
                "--run-id", str(RUN_ID),
                "--event-json", str(event_file),
                "--artifact-name", ART_1,
                "--jsonl", str(jsonl),
                "--processed", str(processed),
            ],
            capture_output=True, text=True,
        )
        r2 = subprocess.run(
            [
                sys.executable, str(COLLECTOR), "record-missing",
                "--run-id", "40400671470",
                "--attempt", "2",
                "--jsonl", str(jsonl),
                "--processed", str(processed),
            ],
            capture_output=True, text=True,
        )
        assert r1.returncode == 0 and r1.stdout.strip() == "processed", r1.stderr
        assert r2.returncode == 0, r2.stderr
        rows = [ln.split("\t") for ln in processed.read_text().splitlines() if ln.strip()]
        assert rows == [
            [str(RUN_ID), _sha256(text), "processed", ART_1],
            ["40400671470", SENTINEL, "missing", "2"],
        ]


# ---------------------------------------------------------------------------
# Part B — orchestrator integration (fake gh PATH shim)
# ---------------------------------------------------------------------------


def _make_event_zip(zip_path: Path, text: str) -> Path:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("event.json", text)
    return zip_path


def _write_shim(bin_dir: Path) -> None:
    """Fake ``gh`` covering every endpoint the NFM-5829 orchestrator uses.

    Sidecar files (all in FAKE_GH_BIN_DIR, bash-3.2 compatible):
      run_attempts.txt  ``<run_id>\\t<attempt>``        (default attempt 1)
      artifacts.txt     ``<run_id>\\t<name>\\t<url>\\t<zip>``
      run_states.txt    ``<run_id>\\t<status>\\t<conclusion>\\t<updated_at>``
      run_jobs.txt      ``<run_id>\\t<job_name>\\t<conclusion>``
      downloads.log     appended with each artifact name actually downloaded
    """
    shim = bin_dir / "gh"
    shim.write_text(
        """#!/usr/bin/env bash
# Fake gh for the NFM-5829 attempt-2 dedup tests.
set -e

if [ "$1" != "api" ]; then
  echo "fake gh: unsupported subcommand $1" >&2
  exit 2
fi
shift  # drop 'api'

endpoint=""
filter=""
saw_jq=0
for arg in "$@"; do
  if [ "$saw_jq" -eq 1 ]; then
    filter="$arg"
    break
  fi
  if [ "$arg" = "--jq" ]; then
    saw_jq=1
    continue
  fi
  endpoint="$arg"
done

if [ -n "${FAIL_ON:-}" ] && [[ "$endpoint" == *"${FAIL_ON}"* ]]; then
  echo "fake gh: simulated API failure on $endpoint" >&2
  exit 22
fi
if [ -n "${FAIL_ON_SUFFIX:-}" ] && [[ "$endpoint" == *"${FAIL_ON_SUFFIX}" ]]; then
  echo "fake gh: simulated API failure on $endpoint" >&2
  exit 22
fi

# Workflow-id lookup.
if [[ "$endpoint" == *"/actions/workflows/"* ]] && [[ "$endpoint" != *"/runs"* ]]; then
  printf '300666980'
  exit 0
fi

# Run enumeration: <run_id>\\t<run_attempt> (the NFM-5829 orchestrator
# needs the attempt from the same response — no extra API call).
if [[ "$endpoint" == *"/runs?per_page=50"* ]]; then
  att_file="${FAKE_GH_BIN_DIR}/run_attempts.txt"
  for rid in ${RUN_IDS}; do
    att=1
    if [ -f "$att_file" ]; then
      while IFS=$'\\t' read -r rid2 att2; do
        if [ "$rid2" = "$rid" ]; then att="$att2"; fi
      done < "$att_file"
    fi
    printf '%s\\t%s\\n' "$rid" "$att"
  done
  exit 0
fi

case "$endpoint" in
  */actions/runs/*)
    tail_path="${endpoint#*/actions/runs/}"
    case "$tail_path" in
      */jobs*)
        run_id="${tail_path%%/jobs*}"
        jobs_file="${FAKE_GH_BIN_DIR}/run_jobs.txt"
        out=""
        first=1
        if [ -f "$jobs_file" ]; then
          while IFS=$'\\t' read -r rid jname jconcl; do
            [ -z "$rid" ] && continue
            [ "$rid" = "$run_id" ] || continue
            if [ "$first" -eq 0 ]; then out="$out,"; fi
            out="$out$jconcl"
            first=0
          done < "$jobs_file"
        fi
        printf '%s' "$out"
        exit 0
        ;;
      */artifacts*)
        run_id="${tail_path%%/artifacts*}"
        art_file="${FAKE_GH_BIN_DIR}/artifacts.txt"
        if [ -f "$art_file" ]; then
          while IFS=$'\\t' read -r rid aname aurl azip; do
            [ -z "$rid" ] && continue
            [ "$rid" = "$run_id" ] || continue
            printf '%s\\t%s\\n' "$aname" "$aurl"
          done < "$art_file"
        fi
        exit 0
        ;;
      *)
        states_file="${FAKE_GH_BIN_DIR}/run_states.txt"
        if [ -f "$states_file" ]; then
          while IFS=$'\\t' read -r rid status conclusion updated_at; do
            if [ "$rid" = "$tail_path" ]; then
              printf '%s\\t%s\\t%s' "$status" "$conclusion" "$updated_at"
              exit 0
            fi
          done < "$states_file"
        fi
        echo "fake gh: no canned state for run $tail_path" >&2
        exit 1
        ;;
    esac
    ;;
esac

# Archive download: cat the zip for the matching URL and log the name.
art_file="${FAKE_GH_BIN_DIR}/artifacts.txt"
if [ -f "$art_file" ]; then
  while IFS=$'\\t' read -r rid aname aurl azip; do
    [ -z "$rid" ] && continue
    if [ "$endpoint" = "$aurl" ]; then
      printf '%s\\n' "$aname" >> "${FAKE_GH_BIN_DIR}/downloads.log"
      cat "$azip"
      exit 0
    fi
  done < "$art_file"
fi

echo "fake gh: unhandled endpoint $endpoint" >&2
exit 1
""",
        encoding="utf-8",
    )
    shim.chmod(0o755)


class OrchestratorSandbox:
    """Fake-gh state + ledger paths for one or more orchestrator fires."""

    def __init__(self, tmp_path: Path) -> None:
        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir()
        self.jsonl = tmp_path / "events.jsonl"
        self.processed = tmp_path / "events.jsonl.processed"
        self.tmp_path = tmp_path

    def seed_ledger(self, rows: list[str]) -> None:
        self.processed.write_text("".join(row + "\n" for row in rows), encoding="utf-8")

    def ledger_rows(self) -> list[list[str]]:
        if not self.processed.exists():
            return []
        return [
            ln.split("\t")
            for ln in self.processed.read_text().splitlines()
            if ln.strip()
        ]

    def jsonl_lines(self) -> list[str]:
        if not self.jsonl.exists():
            return []
        return [ln for ln in self.jsonl.read_text().splitlines() if ln.strip()]

    def set_attempts(self, attempts: dict[int, int]) -> None:
        (self.bin_dir / "run_attempts.txt").write_text(
            "".join(f"{rid}\t{att}\n" for rid, att in attempts.items()),
            encoding="utf-8",
        )

    def set_artifacts(self, artifacts: list[tuple[int, str, str, Path]]) -> None:
        (self.bin_dir / "artifacts.txt").write_text(
            "".join(f"{rid}\t{name}\t{url}\t{zip_path}\n" for rid, name, url, zip_path in artifacts),
            encoding="utf-8",
        )

    def set_run_state(self, *, status: str, conclusion: str, age_secs: int) -> None:
        from datetime import UTC, datetime, timedelta

        updated = datetime.now(UTC) - timedelta(seconds=age_secs)
        (self.bin_dir / "run_states.txt").write_text(
            f"{RUN_ID}\t{status}\t{conclusion}\t{updated.strftime('%Y-%m-%dT%H:%M:%SZ')}\n",
            encoding="utf-8",
        )

    def set_emit_jobs(self, conclusion: str) -> None:
        (self.bin_dir / "run_jobs.txt").write_text(
            f"{RUN_ID}\tEmit Production Deploy Event\t{conclusion}\n",
            encoding="utf-8",
        )

    def downloads(self) -> list[str]:
        log = self.bin_dir / "downloads.log"
        if not log.exists():
            return []
        return [ln for ln in log.read_text().splitlines() if ln.strip()]

    def fire(self, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        _write_shim(self.bin_dir)
        env = {
            **os.environ,
            "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
            "GITHUB_REPOSITORY": "Etoile04/nucpot",
            "NFMD_DEPLOY_EVENTS_PATH": str(self.jsonl),
            "NFMD_DEPLOY_EVENTS_PROCESSED_PATH": str(self.processed),
            "FAKE_GH_BIN_DIR": str(self.bin_dir),
            "RUN_IDS": str(RUN_ID),
            "NFMD_FETCH_MAX_ATTEMPTS": "1",
        }
        env.pop("NFMD_MISSING_GRACE_SECS", None)
        env.pop("NFMD_MISSING_EMIT_ALERT", None)
        env.pop("FAIL_ON", None)
        env.pop("FAIL_ON_SUFFIX", None)
        env.update(extra_env or {})
        return subprocess.run(
            ["bash", str(ORCHESTRATOR)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )


class TestAttempt2EventCollected:
    """The core NFM-5829 regression, end to end against a fake gh."""

    def test_rerun_attempt2_event_collected_then_never_reprocessed(
        self, tmp_path: Path
    ) -> None:
        """Legacy ledger: run 40309185857 has an attempt-1 ``processed``
        row (no artifact name — pre-NFM-5829 shape). The run is re-run:
        enumeration says attempt 2, and both artifacts exist.

        Fire 1 must collect ONLY the attempt-2 event (the attempt-1
        re-download dedups on sha), stamp both artifact names, and gain
        exactly two ledger rows. Fire 2 must be a zero-download,
        zero-write skip.
        """
        event1 = _canonical_event(
            event_id="11111111-2222-4333-8444-555555555555",
            ts="2026-10-26T13:51:00Z",
        )
        event2 = _canonical_event(
            event_id="99999999-8888-4777-8666-555555555555",
            ts="2026-10-27T02:48:23Z",
            duration_ms=9876,
        )
        text1, text2 = _event_text(event1), _event_text(event2)
        sha1, sha2 = _sha256(text1), _sha256(text2)

        zip1 = _make_event_zip(tmp_path / "a1.zip", text1)
        zip2 = _make_event_zip(tmp_path / "a2.zip", text2)
        url1 = f"https://api.github.com/fake/{RUN_ID}/artifact-1"
        url2 = f"https://api.github.com/fake/{RUN_ID}/artifact-2"

        box = OrchestratorSandbox(tmp_path)
        box.seed_ledger([f"{RUN_ID}\t{sha1}\tprocessed"])
        box.set_attempts({RUN_ID: 2})
        box.set_artifacts([
            (RUN_ID, ART_1, url1, zip1),
            (RUN_ID, ART_2, url2, zip2),
        ])

        result1 = box.fire()
        assert result1.returncode == 0, (
            f"fire 1 failed: rc={result1.returncode}\n{result1.stdout}\n{result1.stderr}"
        )

        # Exactly one NEW jsonl line — the attempt-2 event.
        lines = box.jsonl_lines()
        assert len(lines) == 1, f"expected 1 JSONL line, got {len(lines)}: {lines!r}"
        assert json.loads(lines[0]) == event2

        # Ledger: legacy row + attempt-1 duplicate (named) + attempt-2
        # processed (named).
        rows = box.ledger_rows()
        assert rows == [
            [str(RUN_ID), sha1, "processed"],
            [str(RUN_ID), sha1, "duplicate", ART_1],
            [str(RUN_ID), sha2, "processed", ART_2],
        ], rows

        # Both artifacts were downloaded exactly once (migration + collect).
        assert sorted(box.downloads()) == [ART_1, ART_2]

        # ---- Fire 2: identical fake state; the run is now fully covered.
        downloads_before = box.downloads()
        result2 = box.fire()
        assert result2.returncode == 0, (
            f"fire 2 failed: rc={result2.returncode}\n{result2.stdout}\n{result2.stderr}"
        )
        assert "already covered through attempt 2" in result2.stdout
        assert box.downloads() == downloads_before, "fire 2 re-downloaded artifacts"
        assert box.ledger_rows() == rows, "fire 2 grew the ledger"
        assert box.jsonl_lines() == lines, "fire 2 grew the JSONL"

    def test_attempt1_only_run_is_skipped_without_listing(
        self, tmp_path: Path
    ) -> None:
        """A legacy processed run whose current attempt is still 1 is the
        hot path (~50 runs per 5-minute fire): the skip must happen from
        the ledger alone, with ZERO artifact-listing API calls — any
        listing would blow the rate budget the NFM-5796 work protects."""
        box = OrchestratorSandbox(tmp_path)
        box.seed_ledger([f"{RUN_ID}\t{'a' * 64}\tprocessed"])
        box.set_attempts({RUN_ID: 1})

        # Any artifact-listing call would fail the fire loudly.
        result = box.fire(extra_env={"FAIL_ON": "/artifacts"})

        assert result.returncode == 0, (
            f"covered run must skip without listing; rc={result.returncode}\n"
            f"{result.stdout}\n{result.stderr}"
        )
        assert "already covered through attempt 1" in result.stdout
        assert box.ledger_rows() == [[str(RUN_ID), "a" * 64, "processed"]]
        assert box.jsonl_lines() == []
        assert box.downloads() == []


class TestMissingRowsAreAttemptStamped:
    """``missing`` rows must record the attempt they covered, so a legacy
    missing run is rescanned exactly once — not on every fire."""

    def test_legacy_missing_rescanned_once_then_covered(self, tmp_path: Path) -> None:
        box = OrchestratorSandbox(tmp_path)
        box.seed_ledger([f"{RUN_ID}\t{SENTINEL}\tmissing"])
        box.set_attempts({RUN_ID: 1})
        box.set_run_state(status="completed", conclusion="failure", age_secs=3600)
        box.set_emit_jobs("failure")  # quiet path — no artifact expected

        result1 = box.fire()
        assert result1.returncode == 0, (
            f"fire 1 failed: rc={result1.returncode}\n{result1.stdout}\n{result1.stderr}"
        )
        rows1 = box.ledger_rows()
        assert rows1 == [
            [str(RUN_ID), SENTINEL, "missing"],
            [str(RUN_ID), SENTINEL, "missing", "1"],
        ], rows1

        result2 = box.fire()
        assert result2.returncode == 0, result2.stdout + result2.stderr
        assert "already covered through attempt 1" in result2.stdout
        assert box.ledger_rows() == rows1, "fire 2 appended another missing row"

    def test_missing_run_rerun_collects_late_artifact(self, tmp_path: Path) -> None:
        """A run ledgered ``missing@1`` that is later re-run and DOES emit
        its artifact: the attempt-2 event must be collected, overriding
        the missing verdict for the new attempt."""
        event2 = _canonical_event(
            event_id="77777777-6666-4777-8555-444444444444",
            ts="2026-10-29T00:53:16Z",
        )
        text2 = _event_text(event2)
        zip2 = _make_event_zip(tmp_path / "a2.zip", text2)
        url2 = f"https://api.github.com/fake/{RUN_ID}/artifact-2"

        box = OrchestratorSandbox(tmp_path)
        box.seed_ledger([f"{RUN_ID}\t{SENTINEL}\tmissing\t1"])
        box.set_attempts({RUN_ID: 2})
        box.set_artifacts([(RUN_ID, ART_2, url2, zip2)])

        result = box.fire()
        assert result.returncode == 0, (
            f"fire failed: rc={result.returncode}\n{result.stdout}\n{result.stderr}"
        )
        lines = box.jsonl_lines()
        assert len(lines) == 1 and json.loads(lines[0]) == event2
        assert box.ledger_rows() == [
            [str(RUN_ID), SENTINEL, "missing", "1"],
            [str(RUN_ID), _sha256(text2), "processed", ART_2],
        ]

    def test_missing_run_rerun_still_missing_gains_stamped_row_only(
        self, tmp_path: Path
    ) -> None:
        """A re-run that concludes and still has no artifact gains exactly
        one ``missing@2`` row — and a later fire does not append more."""
        box = OrchestratorSandbox(tmp_path)
        box.seed_ledger([f"{RUN_ID}\t{SENTINEL}\tmissing\t1"])
        box.set_attempts({RUN_ID: 2})
        box.set_run_state(status="completed", conclusion="failure", age_secs=3600)
        box.set_emit_jobs("failure")

        result1 = box.fire()
        assert result1.returncode == 0, result1.stdout + result1.stderr
        rows1 = box.ledger_rows()
        assert rows1 == [
            [str(RUN_ID), SENTINEL, "missing", "1"],
            [str(RUN_ID), SENTINEL, "missing", "2"],
        ], rows1

        result2 = box.fire()
        assert result2.returncode == 0, result2.stdout + result2.stderr
        assert box.ledger_rows() == rows1, "a covered attempt must not re-record"

    def test_inflight_rerun_of_missing_run_is_deferred(self, tmp_path: Path) -> None:
        """The NFM-5825 gate must hold for the re-open path too: an
        in-flight re-run with no artifact yet must not gain a row."""
        box = OrchestratorSandbox(tmp_path)
        box.seed_ledger([f"{RUN_ID}\t{SENTINEL}\tmissing\t1"])
        box.set_attempts({RUN_ID: 2})
        box.set_run_state(status="in_progress", conclusion="", age_secs=0)

        result = box.fire()
        assert result.returncode == 0, result.stdout + result.stderr
        assert box.ledger_rows() == [[str(RUN_ID), SENTINEL, "missing", "1"]]
        assert box.jsonl_lines() == []
