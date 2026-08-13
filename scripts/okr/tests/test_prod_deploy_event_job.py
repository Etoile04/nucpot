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

These are static assertions rather than a live run, because the failure mode
is a *template* bug: by the time a runner could tell us, we have already
burned a production deploy.

The contract each test below pins one clause of:

    A metric must never be able to suppress the event it annotates.

``duration_ms`` is observability. If it cannot be computed, the event must
still be emitted and the artifact must still upload, carrying sentinel ``0``.
"""

from __future__ import annotations

import re
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
