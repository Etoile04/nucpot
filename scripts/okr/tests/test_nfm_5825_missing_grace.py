"""Race guard: ``missing`` ledger rows are only terminal for concluded runs.

NFM-5825 (RE-FINDING). The 5-minute ``Collect Production Deploy Events``
fire of 2026-10-30T18:11:23Z enumerated production-deployment run
41104046996 mid-deploy (started 18:06:47Z, artifact uploaded 18:57:34Z),
found no ``nfm-deploy-event-*`` artifact, and wrote a terminal ``missing``
ledger row. The orchestrator's ledger skip treats any recorded run as
done ("any status, including missing"), so the run was never re-checked
when it concluded green, and the ship that went to production that day
(``392d52d17``, PR #1511) has no event in ``~/.nfmd/deploy-events.jsonl``.

The fix gates every ``record-missing`` write behind a run-state check:

1. ``status != completed`` → defer (no ledger row). The run stays inside
   the 50-run walk-back window, so a later fire picks it up after the
   producer's ``if: always()`` emit step finally uploads the artifact.
2. ``status == completed`` but concluded less than
   ``NFMD_MISSING_GRACE_SECS`` ago (default 600s) → defer as well. This
   covers artifact-upload + API-index lag right after conclusion.
3. Completed + grace elapsed + still no artifact → record ``missing``
   (terminal, as before). This is now a genuine anomaly: when the
   "Emit Production Deploy Event" job's conclusion is ``success``,
   artifact loss is the only explanation, so the orchestrator raises a
   loud ALERT line and exits non-zero (``NFMD_MISSING_EMIT_ALERT=0``
   disables) — red scheduled fires are the only thing anyone actually
   sees on a 5-minute lane.

The same gate applies to both missing paths: the no-artifact path AND
the artifact-listing API failure path (a transient listing failure on
an in-flight run used to terminally mark it ``missing`` too).

Failure direction is always defer: if the run-state lookup itself
fails, no row is written and the next fire re-checks.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ORCHESTRATOR = REPO_ROOT / "scripts" / "lib" / "collect_prod_run.sh"

RUN_ID = 41104046996
SENTINEL = "0" * 64
DEFAULT_GRACE_SECS = 600
EMIT_JOB_NAME = "Emit Production Deploy Event"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _iso(age_secs: int) -> str:
    """UTC timestamp ``age_secs`` before now, in the GHA wire format."""
    dt = datetime.now(UTC) - timedelta(seconds=age_secs)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def sandbox(tmp_path: Path) -> dict[str, Any]:
    """Fake-gh PATH shim + isolated ledger state for one orchestrator run.

    The shim mirrors the endpoint dispatch of ``test_collect_prod_run_sh.py``
    plus the two endpoints the NFM-5825 gate introduces:

    - ``repos/.../actions/runs/<id>`` → run state from ``run_states.txt``
      (``<run_id>\\t<status>\\t<conclusion>\\t<updated_at>``)
    - ``repos/.../actions/runs/<id>/jobs?...`` → emit-job conclusions from
      ``run_jobs.txt`` (``<run_id>\\t<job_name>\\t<conclusion>``)

    Returns a mutable dict: ``env`` (the subprocess environment), ``jsonl``
    / ``processed`` (paths), and helpers to seed run state.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    jsonl = tmp_path / "events.jsonl"
    processed = tmp_path / "events.jsonl.processed"

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GITHUB_REPOSITORY": "Etoile04/nucpot",
        "NFMD_DEPLOY_EVENTS_PATH": str(jsonl),
        "NFMD_DEPLOY_EVENTS_PROCESSED_PATH": str(processed),
        "FAKE_GH_BIN_DIR": str(bin_dir),
        "RUN_IDS": str(RUN_ID),
        "NFMD_FETCH_MAX_ATTEMPTS": "1",
    }
    env.pop("NFMD_MISSING_GRACE_SECS", None)
    env.pop("NFMD_MISSING_EMIT_ALERT", None)
    env.pop("FAIL_ON", None)
    env.pop("FAIL_ON_SUFFIX", None)

    box: dict[str, Any] = {
        "bin_dir": bin_dir,
        "jsonl": jsonl,
        "processed": processed,
        "env": env,
    }
    return box


def _write_shim(bin_dir: Path) -> None:
    """Write the fake ``gh`` binary. Bash 3.2 compatible (no assoc arrays)."""
    shim = bin_dir / "gh"
    shim.write_text(
        """#!/usr/bin/env bash
# Fake gh for the NFM-5825 missing-grace tests.
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

# Workflow-id lookup. The enumeration endpoint is
# /actions/workflows/<id>/runs?per_page=50 — it contains both markers, so
# the workflow branch must exclude /runs (same as test_collect_prod_run_sh.py).
if [[ "$endpoint" == *"/actions/workflows/"* ]] && [[ "$endpoint" != *"/runs"* ]]; then
  printf '300666980'
  exit 0
fi

# Run enumeration (newest first). NFM-5829: the orchestrator asks for
# ``<id>\t<run_attempt>`` pairs; every canned run is at attempt 1.
if [[ "$endpoint" == *"/runs?per_page=50"* ]]; then
  for rid in ${RUN_IDS}; do
    printf '%s\\t1\\n' "$rid"
  done
  exit 0
fi

# Run state lookup: repos/.../actions/runs/<id> (bare, no suffix).
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
        if [[ "$filter" == *".archive_download_url"* ]]; then
          # No artifacts in this suite's scenarios — the jq filter that the
          # orchestrator uses yields nothing (empty ARCHIVE_URLS).
          exit 0
        fi
        printf '{"artifacts":[]}'
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

echo "fake gh: unhandled endpoint $endpoint" >&2
exit 1
""",
        encoding="utf-8",
    )
    shim.chmod(0o755)


def _set_run_state(box: dict[str, Any], *, status: str, conclusion: str, age_secs: int) -> None:
    states = box["bin_dir"] / "run_states.txt"
    states.write_text(f"{RUN_ID}\t{status}\t{conclusion}\t{_iso(age_secs)}\n", encoding="utf-8")


def _set_emit_jobs(box: dict[str, Any], conclusion: str) -> None:
    jobs = box["bin_dir"] / "run_jobs.txt"
    jobs.write_text(f"{RUN_ID}\t{EMIT_JOB_NAME}\t{conclusion}\n", encoding="utf-8")


def _run_orchestrator(box: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    _write_shim(box["bin_dir"])
    return subprocess.run(
        ["bash", str(ORCHESTRATOR)],
        capture_output=True,
        text=True,
        env=box["env"],
        check=False,
    )


def _ledger_rows(processed: Path) -> list[list[str]]:
    if not processed.exists():
        return []
    return [ln.split("\t") for ln in processed.read_text().splitlines() if ln.strip()]


def _jsonl_lines(jsonl: Path) -> list[str]:
    if not jsonl.exists():
        return []
    return [ln for ln in jsonl.read_text().splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# Defer cases — an un-concluded run must never gain a ledger row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["queued", "in_progress", "pending"])
def test_unconcluded_run_is_deferred_without_ledger_row(
    sandbox: dict[str, Any], status: str
) -> None:
    """The exact NFM-5825 race: a fire lands mid-deploy, sees no artifact.

    Pre-fix this wrote a terminal ``missing`` row; post-fix the run is
    deferred and the next fire re-walks it. No ledger row, no JSONL line,
    orchestrator still exits 0 (deferral is a normal, healthy outcome).
    """
    _set_run_state(sandbox, status=status, conclusion="", age_secs=0)

    result = _run_orchestrator(sandbox)

    assert result.returncode == 0, (
        f"deferral must not red the fire; rc={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "deferring" in result.stdout.lower() or "deferring" in result.stderr.lower(), (
        "the defer decision should be visible in the log for triage"
    )
    rows = _ledger_rows(sandbox["processed"])
    assert rows == [], f"no ledger row may be written for an unconcluded run: {rows!r}"
    assert _jsonl_lines(sandbox["jsonl"]) == []


def test_completed_within_grace_window_is_deferred(sandbox: dict[str, Any]) -> None:
    """Concluded seconds ago: artifact upload + API index may still be
    settling, and the producer's fragment upload runs inside the same
    conclusion window. Defer until ``NFMD_MISSING_GRACE_SECS`` has passed."""
    _set_run_state(sandbox, status="completed", conclusion="success", age_secs=60)

    result = _run_orchestrator(sandbox)

    assert result.returncode == 0
    assert _ledger_rows(sandbox["processed"]) == []
    assert _jsonl_lines(sandbox["jsonl"]) == []


def test_completed_past_grace_is_recorded_missing(sandbox: dict[str, Any]) -> None:
    """Completed, grace elapsed, still no artifact: the terminal row is
    now trustworthy — and legitimately so (e.g. a run whose emit step
    never existed). This preserves the pre-NFM-5825 behavior for
    genuinely concluded runs."""
    _set_run_state(sandbox, status="completed", conclusion="success", age_secs=3600)

    result = _run_orchestrator(sandbox)

    assert result.returncode == 0, result.stdout + result.stderr
    rows = _ledger_rows(sandbox["processed"])
    assert rows == [[str(RUN_ID), SENTINEL, "missing", "1"]], rows
    assert _jsonl_lines(sandbox["jsonl"]) == []


def test_grace_window_is_env_tunable(sandbox: dict[str, Any]) -> None:
    """Triage knob, mirroring the NFM-5796 env-tunable convention: a small
    grace records quickly; a large grace keeps deferring."""
    sandbox["env"]["NFMD_MISSING_GRACE_SECS"] = "120"

    _set_run_state(sandbox, status="completed", conclusion="success", age_secs=300)
    result = _run_orchestrator(sandbox)
    assert result.returncode == 0
    assert _ledger_rows(sandbox["processed"]) == [[str(RUN_ID), SENTINEL, "missing", "1"]]

    # Fresh ledger: same conclusion but inside the shrunk grace window.
    sandbox["processed"].unlink()
    _set_run_state(sandbox, status="completed", conclusion="success", age_secs=30)
    result = _run_orchestrator(sandbox)
    assert result.returncode == 0
    assert _ledger_rows(sandbox["processed"]) == []


# ---------------------------------------------------------------------------
# Fail-safe direction — a failed state lookup must defer, not record
# ---------------------------------------------------------------------------


def test_state_lookup_failure_defers(sandbox: dict[str, Any]) -> None:
    """If the run-state API call fails, the orchestrator must NOT fall
    back to writing a terminal row — that would reintroduce the race
    through a different door. The next fire retries."""
    sandbox["env"]["FAIL_ON_SUFFIX"] = f"actions/runs/{RUN_ID}"

    result = _run_orchestrator(sandbox)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _ledger_rows(sandbox["processed"]) == []
    assert _jsonl_lines(sandbox["jsonl"]) == []


def test_listing_failure_on_unconcluded_run_defers(sandbox: dict[str, Any]) -> None:
    """The artifact-listing failure path had the same hole: a transient
    listing error mid-deploy used to write the terminal row. An
    unconcluded run must defer here too."""
    _set_run_state(sandbox, status="in_progress", conclusion="", age_secs=0)
    sandbox["env"]["FAIL_ON"] = "/artifacts"

    result = _run_orchestrator(sandbox)

    assert result.returncode == 0
    assert _ledger_rows(sandbox["processed"]) == []


def test_listing_failure_on_concluded_run_records_missing(sandbox: dict[str, Any]) -> None:
    """Concluded + grace elapsed + listing failure → the pre-existing
    'record missing and move on' resilience for bad runs is preserved."""
    _set_run_state(sandbox, status="completed", conclusion="success", age_secs=3600)
    sandbox["env"]["FAIL_ON"] = "/artifacts"

    result = _run_orchestrator(sandbox)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _ledger_rows(sandbox["processed"]) == [[str(RUN_ID), SENTINEL, "missing", "1"]]


# ---------------------------------------------------------------------------
# Optional alert — emit-step success + missing artifact = genuine loss
# ---------------------------------------------------------------------------


def test_emit_success_but_missing_raises_alert_and_fails_fire(
    sandbox: dict[str, Any],
) -> None:
    """When 'Emit Production Deploy Event' succeeded and the artifact is
    still absent after grace, the event is genuinely lost. The ledger row
    is recorded (audit truth) AND the fire goes red so someone looks."""
    _set_run_state(sandbox, status="completed", conclusion="success", age_secs=3600)
    _set_emit_jobs(sandbox, "success")

    result = _run_orchestrator(sandbox)

    assert result.returncode != 0, (
        f"an emit-success/missing row must red the fire; rc={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "ALERT" in combined
    assert _ledger_rows(sandbox["processed"]) == [[str(RUN_ID), SENTINEL, "missing", "1"]]
    assert _jsonl_lines(sandbox["jsonl"]) == []


def test_emit_success_alert_is_env_disableable(sandbox: dict[str, Any]) -> None:
    sandbox["env"]["NFMD_MISSING_EMIT_ALERT"] = "0"
    _set_run_state(sandbox, status="completed", conclusion="success", age_secs=3600)
    _set_emit_jobs(sandbox, "success")

    result = _run_orchestrator(sandbox)

    assert result.returncode == 0
    assert _ledger_rows(sandbox["processed"]) == [[str(RUN_ID), SENTINEL, "missing", "1"]]


def test_emit_failure_but_missing_records_quietly(sandbox: dict[str, Any]) -> None:
    """Emit job failed → no artifact is expected; recording missing is
    correct and must NOT red the fire."""
    _set_run_state(sandbox, status="completed", conclusion="failure", age_secs=3600)
    _set_emit_jobs(sandbox, "failure")

    result = _run_orchestrator(sandbox)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _ledger_rows(sandbox["processed"]) == [[str(RUN_ID), SENTINEL, "missing", "1"]]


def test_emit_jobs_lookup_failure_still_records_missing(sandbox: dict[str, Any]) -> None:
    """The alert's jobs query is best-effort: if it fails, the ledger row
    still lands and the fire stays green (alert loss is acceptable;
    ledger corruption is not)."""
    _set_run_state(sandbox, status="completed", conclusion="success", age_secs=3600)
    sandbox["env"]["FAIL_ON"] = "/jobs"

    result = _run_orchestrator(sandbox)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _ledger_rows(sandbox["processed"]) == [[str(RUN_ID), SENTINEL, "missing", "1"]]


# ---------------------------------------------------------------------------
# Ledger-skip contract (static): terminal-missing documentation must match
# the new semantics so a future refactor doesn't silently reopen the race.
# ---------------------------------------------------------------------------


def test_ledger_skip_comment_documents_post_conclusion_semantics() -> None:
    body = ORCHESTRATOR.read_text(encoding="utf-8")
    assert "NFM-5825" in body, (
        "the ledger-skip site must carry the NFM-5825 rationale: missing rows "
        "are only written post-conclusion + grace, which is what keeps them terminal"
    )


def test_grace_defaults_are_sane() -> None:
    body = ORCHESTRATOR.read_text(encoding="utf-8")
    match = re.search(r"NFMD_MISSING_GRACE_SECS:-(\d+)", body)
    assert match is not None, "missing grace must default via NFMD_MISSING_GRACE_SECS"
    grace = int(match.group(1))
    assert 60 <= grace <= 1800, (
        f"grace {grace}s outside [60, 1800]: below 60s re-races the index lag, "
        "above 30min turns every legitimately-skipped run into a long"
        "-deferred ledger hole"
    )
