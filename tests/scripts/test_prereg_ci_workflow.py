"""Offline shape tests for .github/workflows/prereg-gate.yml (NFM-5320 —
NFM-5312 Phase 2 CI wiring).

The workflow is the enforcement surface this issue adds, so its contract is
pinned here the same way Phase 1 pinned the script's behavior
(tests/scripts/test_check_prereg.py). These tests are the CI-side half of
"exit-code contract preserved": a regression that re-scopes the path
filters, swallows the script's exit code, or drags a local-only flag
(--verify / --strict) into a CI run step cannot land silently.

Pure file-shape assertions — no network, no git, no Paperclip access.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "prereg-gate.yml"

# The five NFM-5320 scan surfaces — must equal SCAN_GLOBS in
# scripts/check_prereg.py (drift between the two is a re-scope and must be
# a deliberate, reviewed change).
EXPECTED_PATH_FILTERS = {
    "apps/api/src/nfm_db/ml/**",
    "apps/api/tests/**",
    "experiments/**",
    "docs/analysis/**",
    "docs/reports/**",
}

JOB_ID = "prereg-scope-guard"


def load() -> dict:
    with WORKFLOW.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def run_steps() -> list[str]:
    """All `run:` bodies of the job, in order."""
    steps = load()["jobs"][JOB_ID]["steps"]
    return [step["run"] for step in steps if "run" in step]


def invoke_step() -> str:
    """The single step that invokes check_prereg.py."""
    candidates = [run for run in run_steps() if "check_prereg.py" in run]
    assert len(candidates) == 1, "exactly one step must invoke check_prereg.py"
    return candidates[0]


def test_workflow_parses_and_job_exists() -> None:
    doc = load()
    assert JOB_ID in doc["jobs"], doc["jobs"].keys()


def test_pull_request_trigger_matches_issue_path_filters() -> None:
    pr = load()[True]["pull_request"]  # yaml loads the `on` key as True
    assert pr["branches"] == ["main"]
    assert set(pr["paths"]) == EXPECTED_PATH_FILTERS


def test_push_trigger_covers_same_paths() -> None:
    """push shares the path filters (NFM-2191 bypass closure + NFM-5320
    demonstrating runs); drift between the two triggers would re-scope one
    of them silently."""
    push = load()[True]["push"]
    assert "**" in push["branches"]
    assert set(push["paths"]) == EXPECTED_PATH_FILTERS


def test_invoke_uses_merge_base_not_a_snapshot() -> None:
    invoke = invoke_step()
    assert '--base "${MERGE_BASE}"' in invoke
    assert "git merge-base" in "".join(run_steps()), (
        "merge-base must be resolved in-workflow (commit-ref-gate recipe), "
        "not assumed from the event payload"
    )


def test_script_exit_code_is_the_job_verdict() -> None:
    """The check_prereg call must be the last command of its step, with no
    `|| true` / `exit 0` override — otherwise exit 1 (hard-fail) could stop
    failing CI."""
    invoke = invoke_step()
    last = [line for line in invoke.strip().splitlines() if line.strip()][-1]
    assert "check_prereg.py --base" in last, last
    assert "||" not in last, last


def test_local_only_flags_never_reach_a_run_step() -> None:
    """--verify (needs Paperclip credentials) and --strict (NDE burn-in
    mode) are local-only by design; comments may mention them, run steps
    may not."""
    for run in run_steps():
        assert "--verify" not in run, run
        assert "--strict" not in run, run


def test_permissions_are_read_only() -> None:
    assert load()["permissions"] == {"contents": "read"}
