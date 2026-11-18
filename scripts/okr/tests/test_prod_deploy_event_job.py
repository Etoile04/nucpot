"""Static guards on the ``emit-prod-event`` job in the production workflow.

NFM-2119 (SRE-CRITICAL). The C6.1.1 durable producer (NFM-2110) shipped to
``main`` and then failed its canary on every prod run: the job's first step
crashed with ``ValueError: Invalid isoformat string: ''`` and, because the
downstream emit step had no ``if:`` guard, the whole point of the job — the
``nfm-deploy-event-<run_id>-<attempt>.json`` artifact — never uploaded.

The bug is that ``github.run_started_at`` is **not a property of the
``github`` context**. It exists only on the ``workflow_run`` *event payload*.
Actions expands an unknown context property to the empty string rather than
erroring, so the defect is invisible in review and only appears at runtime.

The ``github.*`` guards are static assertions rather than a live run, because
that failure mode is a *template* bug: by the time a runner could tell us, we
have already burned a production deploy. Plain-shell computation in the emit
step (duration/health, NFM-5984) has no such excuse and is executed
end-to-end below.

The contract each test below pins one clause of:

    A metric must never be able to suppress the event it annotates.

``duration_ms`` is observability. If it cannot be computed, the event must
still be emitted and the artifact must still upload, carrying sentinel ``0``.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production-deployment.yml"

JOB_NAME = "emit-prod-event"


@pytest.fixture(scope="module")
def raw() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def emit_job(raw: str) -> dict[str, Any]:
    jobs = yaml.safe_load(raw)["jobs"]
    assert JOB_NAME in jobs, (
        f"the {JOB_NAME!r} job is missing from the production workflow. "
        "ADR-KR3-deploy-events.md §C6.2 rules that 'C6.1 stands as-is', so "
        "this job is not removable: deleting it silently reverts NFM-2110 "
        "and KR-COMPANY-3 loses its production numerator."
    )
    return jobs[JOB_NAME]


def _step(job: dict[str, Any], needle: str) -> dict[str, Any]:
    for step in job.get("steps", []):
        if needle.lower() in str(step.get("name", "")).lower():
            return step
    raise AssertionError(
        f"no step matching {needle!r} in job {JOB_NAME!r}; present: "
        f"{[s.get('name') or s.get('uses') for s in job.get('steps', [])]}"
    )


# --------------------------------------------------------------------------
# Root cause: an invalid context property that expands to ""
# --------------------------------------------------------------------------


def test_does_not_reference_invalid_github_run_started_at(raw: str) -> None:
    """``github.run_started_at`` is not a real ``github`` context property.

    Matches only inside a ``${{ }}`` expression — prose mentioning the property
    (such as the comment explaining why it is banned) is not the defect.
    """
    hits = re.findall(r"\$\{\{[^}]*github\.run_started_at[^}]*\}\}", raw)
    assert not hits, (
        f"found {len(hits)} expression(s) using `github.run_started_at`, which "
        "always expands to the empty string and crashes fromisoformat(). Read "
        "the run start from `gh api repos/$REPO/actions/runs/$RUN_ID "
        f"--jq .run_started_at` instead (NFM-2119).\n{hits}"
    )


def test_duration_step_tolerates_an_unparseable_run_start(emit_job: dict[str, Any]) -> None:
    """The duration step must degrade to a sentinel, never raise."""
    body = _step(emit_job, "duration").get("run", "")
    assert "except" in body and "ValueError" in body, (
        "the duration step must catch ValueError from timestamp parsing and fall "
        "back to duration_ms=0; a metric may not fail the job carrying the "
        f"deploy event.\nstep body was:\n{body}"
    )


# --------------------------------------------------------------------------
# The artifact must survive a metric failure
# --------------------------------------------------------------------------


def test_emit_step_runs_even_when_duration_step_fails(emit_job: dict[str, Any]) -> None:
    """Guards the observed symptom: a missing artifact.

    On runs 30516215050 and 30516354836 the duration step failed, the emit step
    was skipped for want of an ``if:``, ``deploy-event.json`` was never written,
    and the upload produced nothing.
    """
    step = _step(emit_job, "Emit production deploy event")
    assert str(step.get("if", "")).strip() == "always()", (
        "the emit step needs `if: always()`; without it a failure in the "
        "preceding duration step skips event emission entirely and the "
        "nfm-deploy-event artifact is never produced (NFM-2119)."
    )


def test_artifact_upload_is_unconditional_and_retained_90_days(
    emit_job: dict[str, Any],
) -> None:
    """C6.1.1 acceptance criterion: the artifact is the producer's only output."""
    step = _step(emit_job, "artifact")
    assert str(step.get("if", "")).strip() == "always()"
    assert step["with"]["retention-days"] == 90
    assert "nfm-deploy-event-" in step["with"]["name"]


# --------------------------------------------------------------------------
# Latent defect: `needs.deploy-prod` was unreachable from this job
# --------------------------------------------------------------------------


def test_job_if_only_reads_jobs_declared_in_needs(emit_job: dict[str, Any]) -> None:
    """``needs.<job>`` resolves only for *direct* dependencies.

    The shipped guard was ``if: always() && needs.deploy-prod.result !=
    'skipped'`` while ``needs: [smoke-test]``. ``needs.deploy-prod`` is
    undefined there, expands to ``""``, and ``"" != 'skipped'`` is always true —
    the guard never fired. It passed the canary by accident, not by design.
    """
    declared = set(emit_job.get("needs", []) or [])
    referenced = set(re.findall(r"needs\.([A-Za-z0-9_-]+)", str(emit_job.get("if", ""))))
    missing = referenced - declared
    assert not missing, (
        f"job `if:` reads needs.{sorted(missing)} but `needs:` declares "
        f"{sorted(declared)}. Undeclared jobs expand to the empty string, so the "
        "condition silently does nothing. Add them to `needs:`."
    )


# --------------------------------------------------------------------------
# ADR §C6.1.5 / §C6.3.2 — no developer home directories
# --------------------------------------------------------------------------


def test_no_hardcoded_developer_home_paths(raw: str) -> None:
    """§C6.1.5: "The path is no longer a developer's home directory."

    §C6.3.2 rejected a proposal to reintroduce one and called it
    "disqualifying". A superseded branch commit re-added
    ``/Users/<dev>/Projects/nucpot/docker/.deploy-events.jsonl`` during a
    rebase, so this guard is load-bearing rather than theoretical.
    """
    offenders = re.findall(r"^.*(?:/Users/|/home/)[A-Za-z0-9._-]+/.*$", raw, flags=re.M)
    assert not offenders, (
        "hardcoded developer home directory path(s) in the workflow — ADR "
        "§C6.1.5 forbids these and §C6.3.2 calls them disqualifying:\n"
        + "\n".join(o.strip() for o in offenders)
    )


# --------------------------------------------------------------------------
# NFM-5934: the fragment must be emitted exactly ONCE per run
# --------------------------------------------------------------------------


def test_fragment_emit_is_emit_once(emit_step_run: str) -> None:
    """The direct ``_prod_emit`` call plus the EXIT trap used to append
    TWO lines per fragment (deploy_event.sh appends unconditionally), and
    ``validate_fragment`` rejects >1 line — every Era-B fragment was born
    invalid (runs 42473687433 / 42477796832 both carry 2 lines). The
    ``[ -s "$FRAGMENT_PATH" ]`` guard makes the second (trap) invocation
    a no-op while keeping the early-emit + trap redundancy.
    """
    guard = 'if [ -s "$FRAGMENT_PATH" ]; then'
    assert guard in emit_step_run, (
        "the deploy-prod fragment emit step lost the emit-once guard; the "
        "direct _prod_emit call plus the EXIT trap would double-append "
        "again and every fragment would be rejected as 2-line"
    )
    assert "trap '_prod_emit" in emit_step_run, "EXIT-trap emission must stay armed"
    assert '_prod_emit "$SUCCESS" "$FIRST_POLL"' in emit_step_run
    # The guard must sit INSIDE _prod_emit, i.e. before the trap arming.
    assert emit_step_run.index(guard) < emit_step_run.index("trap '_prod_emit")


def _run_fragment_emit_step(
    emit_step_run: str,
    tmp_path: Path,
    *,
    start_ms: str,
    run_epoch: str,
    marker_epoch: str | None,
) -> list[dict[str, Any]]:
    """Execute the REAL deploy-prod fragment-emit run body (parsed from the
    workflow, GH expressions and the runner-host marker path substituted
    with fakes) against a fake RUNNER_TEMP; returns the parsed JSONL
    fragment lines. The duration/health computation under test is plain
    shell arithmetic and comparison, so unlike the ``github.*`` template
    guards above it is exercised here end-to-end through deploy_event.sh."""
    marker = tmp_path / "nfmd_prod_health_passed"
    if marker_epoch is not None:
        marker.write_text(f'{{"epoch": {marker_epoch}, "sha": "abc1234"}}\n', encoding="utf-8")

    run_id = "42473687433"
    for expr, value in (
        ("${{ github.run_id }}", run_id),
        ("${{ steps.deploy.outcome }}", "success"),
        ("${{ steps.external-smoke-tests.outcome }}", "success"),
        ("${{ steps.deploy.outputs.epoch }}", run_epoch),
        ("${{ steps.deploy.outputs.start_ms }}", start_ms),
        ("/var/lib/nfmdeploy/.nfmd/nfmd_prod_health_passed", str(marker)),
    ):
        emit_step_run = emit_step_run.replace(expr, value)
    assert "${{" not in emit_step_run, "unsubstituted GH expression would not survive bash"

    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    proc = subprocess.run(
        ["bash", "-c", emit_step_run],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin",
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_WORKSPACE": str(REPO_ROOT),
            "GITHUB_ACTOR_VAL": "tester",
            "GITHUB_SHA_VAL": "deadbee",
        },
    )
    assert proc.returncode == 0, (
        f"the emit step must never fail the deploy-prod job (set +e + "
        f"never-fail lib), but exited {proc.returncode}:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    fragment = runner_temp / "prod-deploy-events" / f"{run_id}.jsonl"
    lines = fragment.read_text(encoding="utf-8").splitlines() if fragment.exists() else []
    return [json.loads(line) for line in lines]


def test_fragment_emits_computed_duration_and_gate_derived_health(
    emit_step_run: str, tmp_path: Path
) -> None:
    """NFM-5984: the fragment step hardcoded ``--duration-ms 0`` and passed no
    ``--health-status`` at all, so deploy_event.sh's defaults leaked into
    every master event — even green deploys with
    ``health_gate_first_poll_passed=true`` recorded ``duration_ms=0`` and
    ``health_status="error"`` (epochs 71-72, e.g. event 7f3b7e8d… @
    2026-11-15T13:53:20Z).

    Contract, executed against the real step body: duration is computed from
    the deploy step's ``start_ms`` output — a start-anchored wall clock like
    staging's exit-trap figure, but measured through post-deploy verification
    (cutover, external smoke, Celery verify all run between deploy and emit),
    so prod durations run systematically larger — and health_status is
    derived from the SAME FIRST_POLL boolean the event emits, so the fields
    cannot contradict each other. This fails on the pre-fix body, which
    emitted ``duration_ms=0`` for a green attested deploy.
    """
    # Wiring check: the step must consume the deploy step's start_ms output.
    assert "steps.deploy.outputs.start_ms" in emit_step_run, (
        "the emit step must read the deploy step's start_ms output to compute "
        "duration_ms (NFM-5984)"
    )

    start_ms = str(int(time.time() * 1000) - 5000)
    events = _run_fragment_emit_step(
        emit_step_run, tmp_path, start_ms=start_ms, run_epoch="7", marker_epoch="7"
    )

    assert len(events) == 1, f"expected exactly one fragment line, got: {events!r}"
    event = events[0]
    assert event["health_gate_first_poll_passed"] is True
    assert event["first_pass_success"] is True
    assert event["health_status"] == "ok", (
        "a green deploy with an attested health marker must record "
        "health_status=ok, not the lib's conservative default (NFM-5984)"
    )
    assert isinstance(event["duration_ms"], int)
    assert 4000 <= event["duration_ms"] < 60_000, (
        f"duration_ms must reflect the ~5s start→emit span from start_ms, "
        f"got {event['duration_ms']} — a hardcoded 0 or wrong anchor fails here"
    )


def test_fragment_falls_back_to_sentinel_duration_and_error_health(
    emit_step_run: str, tmp_path: Path
) -> None:
    """When the deploy died before minting a start time and nothing attests
    the health gate, the fragment still emits (a metric may not fail the
    event it annotates — NFM-2119 doctrine) carrying the sentinel
    ``duration_ms=0`` and ``health_status="error"`` consistent with
    ``health_gate_first_poll_passed=false``.
    """
    events = _run_fragment_emit_step(
        emit_step_run, tmp_path, start_ms="", run_epoch="", marker_epoch=None
    )

    assert len(events) == 1, f"expected exactly one fragment line, got: {events!r}"
    event = events[0]
    assert event["health_gate_first_poll_passed"] is False
    assert event["health_status"] == "error"
    assert event["duration_ms"] == 0


@pytest.fixture(scope="module")
def emit_step_run(raw: str) -> str:
    """The ``run:`` body of the deploy-prod job's fragment emit step."""
    import yaml

    steps = yaml.safe_load(raw)["jobs"]["deploy-prod"]["steps"]
    for step in steps:
        if step.get("name") == "Emit prod deploy-event fragment":
            return str(step.get("run", ""))
    raise AssertionError(
        "the deploy-prod job's 'Emit prod deploy-event fragment' step is "
        "missing — the ADR-KR3-A2 producer lane cannot emit fragments"
    )
