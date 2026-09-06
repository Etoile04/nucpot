"""Guards on the NFM-4385 post-deploy live-E2E wiring.

Incident context (PR #1211 / run 34053445198): the push-only ``E2E Tests
(Live)`` job in ci.yml raced the ``Production Deployment`` workflow triggered
by the same push (~22-43 min serialized rollout vs a ~4 min E2E job), so any
merge whose live specs asserted NEW frontend behavior deterministically
failed its first run against the stale prod bundle (feedback specs read
``feedback_type: undefined`` from the pre-deploy JS; the same job re-ran
green ~20 min later once the deploy had landed).

The fix moves live E2E into e2e-post-deploy.yml behind a ``workflow_run``
trigger so it executes only after the deploy for that same commit succeeds,
and removes the e2e job from ci.yml's triage/recovery ``needs`` graphs.

These are static semantic assertions on the two workflow documents — the
same approach as test_prod_deploy_event_job.py (NFM-2119): template and
trigger-wiring defects are invisible until a runner executes them, and by
then we have already burned a production deploy or silently dropped live
coverage. Parsing the YAML into a normalized model (rather than grepping
prose) means comments explaining the design can never satisfy these guards.

The job-level ``if:`` gate is additionally *evaluated* against simulated
``workflow_run`` payloads via a small GitHub-expression interpreter, so the
run/skip matrix below pins behavior, not syntax.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
E2E_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "e2e-post-deploy.yml"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production-deployment.yml"

LIVE_BASE_URL = "https://nucpot.dpdns.org"
LIVE_API_BASE_URL = "https://verify.nucpot.dpdns.org"


# ---------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def ci_doc() -> dict[str, Any]:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def e2e_doc() -> dict[str, Any]:
    return yaml.safe_load(E2E_WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def e2e_job(e2e_doc: dict[str, Any]) -> dict[str, Any]:
    jobs = e2e_doc["jobs"]
    assert "e2e" in jobs, (
        "the `e2e` job is missing from e2e-post-deploy.yml — live E2E must "
        "run after the deploy completes (NFM-4385), not race it inside ci.yml"
    )
    return jobs["e2e"]


def _on(doc: dict[str, Any]) -> dict[str, Any]:
    """`on:` parses to boolean True under YAML 1.1 — handle both keys."""
    return doc.get(True) or doc.get("on")


def _run_step(job: dict[str, Any]) -> dict[str, Any]:
    for step in job.get("steps", []):
        if "playwright test" in str(step.get("run", "")):
            return step
    raise AssertionError("no `playwright test` step in the post-deploy e2e job")


# ------------------------------------------------- ci.yml: the old job is gone


def test_ci_has_no_e2e_job(ci_doc: dict[str, Any]) -> None:
    """The racing push-only live-E2E job must not live in ci.yml anymore."""
    assert "e2e" not in ci_doc["jobs"], (
        "ci.yml still has a standalone `e2e` job: on `push` it races the "
        "Production Deployment rollout for the same SHA and deterministically "
        "fails first-run specs that assert new frontend behavior (NFM-4385). "
        "Live E2E belongs in e2e-post-deploy.yml behind workflow_run."
    )


def test_ci_needs_graph_references_only_existing_jobs(ci_doc: dict[str, Any]) -> None:
    """Removing the e2e job must not leave dangling `needs:` entries.

    A dangling entry makes the dependent job silently skip with
    "this job was skipped because ... referenced an unavailable job".
    """
    job_ids = set(ci_doc["jobs"])
    for name, job in ci_doc["jobs"].items():
        needs = job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        dangling = [n for n in needs if n not in job_ids]
        assert not dangling, (
            f"ci.yml job {name!r} needs non-existent job(s) {dangling}; "
            "after NFM-4385 the e2e dependency should be fully removed, not "
            "renamed or left behind"
        )


@pytest.mark.parametrize("triage_job", ["ci-failure-triage", "ci-recovery"])
def test_triage_and_recovery_no_longer_track_e2e(ci_doc: dict[str, Any], triage_job: str) -> None:
    """Triage/recovery must neither depend on nor report the removed job.

    `needs.e2e.result` on a job that no longer exists is empty string, so a
    failure would never be classified and an E2E failure alert would be
    silently dropped.
    """
    job_src = str(ci_doc["jobs"][triage_job])
    assert "needs.e2e" not in job_src and "'E2E'" not in job_src, (
        f"{triage_job} still references the removed e2e job; stale references "
        "expand to '' and silently misreport CI failures (NFM-4385)"
    )


# --------------------------- e2e-post-deploy.yml: trigger and gating contracts


def test_workflow_run_targets_the_real_deploy_workflow_name(
    e2e_doc: dict[str, Any],
) -> None:
    """`workflow_run.workflows` matches by workflow *name*, not file name."""
    trigger = _on(e2e_doc)["workflow_run"]
    deploy_name = yaml.safe_load(DEPLOY_WORKFLOW.read_text(encoding="utf-8"))["name"]
    assert deploy_name in trigger["workflows"], (
        f"workflow_run must target the deploy workflow's name {deploy_name!r} "
        f"(got {trigger['workflows']!r}); a typo here means the trigger never "
        "fires and live E2E silently never runs"
    )
    assert "completed" in trigger["types"]


def test_manual_dispatch_available(e2e_doc: dict[str, Any]) -> None:
    """The only path to E2E a hotfix dispatched straight to prod."""
    assert "workflow_dispatch" in _on(e2e_doc)


def test_cancel_in_progress_is_false(e2e_doc: dict[str, Any]) -> None:
    """A fast-failing deploy completion must not cancel in-flight E2E.

    A deploy that fails fast (egress pre-flight probe, yaml-lint) still
    completes with conclusion=failure and triggers a workflow_run that joins
    this concurrency group. With cancel-in-progress it would cancel the
    legitimate E2E for the previously deployed SHA and then skip itself via
    the job-level gate — silently dropping live coverage for the SHA prod is
    actually running.
    """
    conc = e2e_doc.get("concurrency", {})
    assert conc.get("group") == "e2e-post-deploy"
    assert conc.get("cancel-in-progress") is False, (
        "cancel-in-progress must stay false: only a successful newer deploy "
        "invalidates a running E2E, and serialized 22+ min deploys cannot "
        "land inside the ~4 min E2E window anyway"
    )


def test_checkout_pins_the_deployed_sha(e2e_job: dict[str, Any]) -> None:
    """E2E must test the code that was DEPLOYED, not current main."""
    checkout = next(s for s in e2e_job["steps"] if "actions/checkout" in str(s.get("uses", "")))
    ref = checkout.get("with", {}).get("ref", "")
    assert "workflow_run.head_sha" in ref and "github.sha" in ref, (
        "checkout must pin `workflow_run.head_sha || github.sha` — testing "
        "main's head after a deploy would let E2E validate code prod never "
        "received (or fail on code prod hasn't got yet, the exact race "
        "NFM-4385 removes)"
    )


def test_live_payload_matches_the_prod_endpoints(e2e_job: dict[str, Any]) -> None:
    """The live-mode payload carried over from the old ci.yml job verbatim."""
    env = _run_step(e2e_job).get("env", {})
    assert env.get("E2E_TARGET") == "live"
    assert env.get("BASE_URL") == LIVE_BASE_URL
    assert env.get("API_BASE_URL") == LIVE_API_BASE_URL


def test_failure_issues_use_e2e_failure_label_not_ci_failure(
    e2e_job: dict[str, Any],
) -> None:
    """Green push CI must never auto-close a live-prod E2E failure.

    ci.yml's ci-recovery job closes open `ci-failure` issues on the next
    green push, but green push CI does NOT mean the post-deploy live E2E
    passed. Using `e2e-failure` (as e2e-cron.yml does) keeps prod breakage
    visible until a human resolves it.
    """
    job_src = str(e2e_job)
    assert "e2e-failure" in job_src and "ci-failure" not in job_src


# ------------------------------------------------------- the gate's behavior


def _github_eval(expr: str, ctx: dict[str, Any]) -> bool:
    """Evaluate the GitHub-Actions expression subset used by the gate.

    Resolves `github.a.b` identifiers against `ctx`, maps `&&`/`||` to
    Python, and evaluates the result — a small interpreter for the real
    condition text, so the run/skip matrix is exercised as behavior.
    """

    def resolve(match: re.Match[str]) -> str:
        node: Any = ctx
        for part in match.group(0).split("."):
            node = node.get(part, "") if isinstance(node, dict) else ""
        return repr(node)

    py = re.sub(r"\bgithub(?:\.\w+)+", resolve, expr)
    py = py.replace("&&", " and ").replace("||", " or ")
    return bool(eval(py, {"__builtins__": {}}, {}))  # fixed grammar


def _run_matrix(gate: str) -> dict[str, bool]:
    """Simulate the deploy-completion payloads the gate must classify."""

    def payload(
        event_name: str = "workflow_run",
        wr_event: str = "push",
        wr_branch: str = "main",
        wr_conclusion: str = "success",
    ) -> dict[str, Any]:
        return {
            "github": {
                "event_name": event_name,
                "event": {
                    "workflow_run": {
                        "event": wr_event,
                        "head_branch": wr_branch,
                        "conclusion": wr_conclusion,
                    }
                },
            }
        }

    return {
        "push-to-main deploy succeeded": _github_eval(gate, payload()),
        "push-to-main deploy FAILED": _github_eval(gate, payload(wr_conclusion="failure")),
        "push deploy cancelled": _github_eval(gate, payload(wr_conclusion="cancelled")),
        "tag deploy (v1.2.3)": _github_eval(gate, payload(wr_branch="v1.2.3")),
        "staging workflow_dispatch deploy": _github_eval(
            gate, payload(wr_event="workflow_dispatch")
        ),
        "manual E2E workflow_dispatch": _github_eval(gate, payload(event_name="workflow_dispatch")),
    }


def test_gate_runs_only_after_successful_main_push_deploys(
    e2e_job: dict[str, Any],
) -> None:
    """The run/skip matrix that NFM-4385 is supposed to implement.

    Runs: successful main push deploys and the workflow's own manual
    dispatch. Skips: failed/cancelled deploys (the red deploy run is the
    signal; prod unchanged), tag deploys, and staging dispatches (not this
    workflow's product surface).
    """
    matrix = _run_matrix(str(e2e_job["if"]))
    unexpected = [
        f"{name} -> {'RUN' if run else 'SKIP'}"
        for name, run in matrix.items()
        if run != (name in ("push-to-main deploy succeeded", "manual E2E workflow_dispatch"))
    ]
    assert not unexpected, (
        "job-level gate classifies these scenarios wrongly (want RUN only "
        "for successful main push deploys and manual dispatch):\n  " + "\n  ".join(unexpected)
    )
