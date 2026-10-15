"""NFM-5548 — direct git fetch replaces actions/checkout on self-hosted lanes.

Evidence base (2026-10-15): codeload.github.com started returning 429 to the
self-hosted collector host's direct egress, so ``uses: actions/checkout`` died
during "Set up job" whenever the runner's action archive cache was empty
(3 download attempts exhausted; collectors red since 10:16Z). The three
workflows that run on that host — collect-prod-deploy-events,
prod-deploy-event-collector, and the site-monitor watchdog job — now check
out with a plain ``git fetch`` + ``git checkout FETCH_HEAD``, which removes
the action-archive dependency entirely.

These tests pin that contract behaviorally rather than by string-matching:

* the checkout step of every self-hosted job is a ``run:`` step (no
  ``actions/checkout`` ``uses:`` left on those lanes) whose script, when
  actually EXECUTED in a fresh workspace with the standard GITHUB_* env,
  fetches exactly ``$GITHUB_SHA`` and leaves HEAD at that commit;
* the executed script also mirrors actions/checkout's clean semantics
  against a persisted self-hosted workspace: leftover untracked files from
  a previous run are removed by the ``git clean -ffdx`` before checkout;
* prod-deploy-event-collector declares ``contents: read`` so the token can
  actually authenticate the fetch regardless of the org default flipping.

Hermetic execution: the script hardcodes
``https://github.com/${GITHUB_REPOSITORY}.git``, the same URL the host's
``url.git@github.com:.insteadOf`` rewrite redirects to the SSH-over-443
lane. The tests inject the same mechanism (via git's ``GIT_CONFIG_*`` env
seams) pointing at a local ``file://`` bare fixture repo, so the real
script runs against a real git server with no network. The
``http.https://github.com/.extraheader`` token is inert off-HTTP, so a
dummy GITHUB_TOKEN exercises the SSH-lane auth path exactly as production
does on this host.
"""

from __future__ import annotations

import os
import subprocess

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# workflow file -> list of self-hosted job names whose checkout must be direct
SELF_HOSTED_JOBS = {
    ".github/workflows/collect-prod-deploy-events.yml": ["collect-prod-events"],
    ".github/workflows/prod-deploy-event-collector.yml": ["collect"],
    ".github/workflows/site-monitor.yml": ["watchdog"],
}


def _load_workflow(rel_path: str) -> dict:
    with open(os.path.join(REPO_ROOT, rel_path)) as fh:
        return yaml.safe_load(fh)


def _checkout_step(workflow: dict, job_name: str) -> dict:
    job = workflow["jobs"][job_name]
    steps = [s for s in job["steps"] if "checkout" in s.get("name", "").lower()]
    assert len(steps) == 1, f"{job_name}: expected exactly one checkout step"
    return steps[0]


def _assert_self_hosted(workflow: dict, job_name: str) -> None:
    runs_on = workflow["jobs"][job_name]["runs-on"]
    if isinstance(runs_on, str):
        runs_on = [runs_on]
    assert "self-hosted" in runs_on, f"{job_name} expected on self-hosted runners"


@pytest.fixture()
def fixture_origin(tmp_path):
    """A bare repo serving one commit through a real (file://) git transport.

    file:// is required rather than a plain path because the workflow script
    fetches with ``--depth=1``; only the smart transport supports shallowing.
    """
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    (src / "sentinel.txt").write_text("nfm-5548 fixture\n")
    subprocess.run(["git", "-C", str(src), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(src),
            "-c",
            "user.name=test",
            "-c",
            "user.email=t@e.st",
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        check=True,
    )
    sha = subprocess.run(
        ["git", "-C", str(src), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Layout mirrors the rewritten URL shape: insteadOf replaces the
    # "https://github.com/" prefix, so the base must be a directory under
    # which "<owner>/<repo>.git" resolves — exactly like a real forge.
    bare = tmp_path / "remote" / "fixture" / "repo.git"
    bare.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    return f"file://{tmp_path / 'remote'}/", sha


def _run_checkout_script(script: str, fixture_origin: tuple[str, str], workspace: str) -> None:
    origin_url, sha = fixture_origin
    env = dict(
        os.environ,
        GITHUB_WORKSPACE=workspace,
        GITHUB_REPOSITORY="fixture/repo",
        GITHUB_SHA=sha,
        # Dummy token: off-HTTP the extraheader is inert, which is exactly
        # the SSH-lane auth path production uses on this host.
        GITHUB_TOKEN="dummy-off-http-token",
        # Redirect https://github.com/ to the fixture origin the same way
        # the host's insteadOf rewrite redirects it to the SSH-over-443
        # lane — the script itself stays byte-identical to the workflow.
        # Nulling the global/system configs keeps a host-level insteadOf
        # (like this runner's own SSH rewrite) from shadowing the seam.
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0=f"url.{origin_url}.insteadOf",
        GIT_CONFIG_VALUE_0="https://github.com/",
    )
    proc = subprocess.run(
        ["bash", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        cwd=workspace,
    )
    assert proc.returncode == 0, (
        f"checkout script failed (rc={proc.returncode}):\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


@pytest.mark.parametrize("workflow_path", sorted(SELF_HOSTED_JOBS))
def test_self_hosted_checkout_step_is_direct_fetch(workflow_path):
    """Self-hosted jobs must not depend on the codeload action archive."""
    workflow = _load_workflow(workflow_path)
    for job_name in SELF_HOSTED_JOBS[workflow_path]:
        _assert_self_hosted(workflow, job_name)
        step = _checkout_step(workflow, job_name)
        assert "uses" not in step, (
            f"{workflow_path}/{job_name}: checkout still delegates to an "
            "action (actions/checkout) — codeload 429 kills it on the "
            "self-hosted host when the action cache is empty (NFM-5548)"
        )
        script = step.get("run", "")
        # The script must fetch the commit the job was dispatched for and
        # check it out — semantic anchors, not exhaustive text matching.
        assert "$GITHUB_SHA" in script and "FETCH_HEAD" in script
        assert "checkout -f" in script


@pytest.mark.parametrize("workflow_path", sorted(SELF_HOSTED_JOBS))
def test_self_hosted_checkout_script_executes_and_pins_sha(workflow_path, fixture_origin, tmp_path):
    """Executing the real checkout script yields exactly GITHUB_SHA on disk."""
    workflow = _load_workflow(workflow_path)
    for job_name in SELF_HOSTED_JOBS[workflow_path]:
        workspace = tmp_path / f"ws-{job_name}"
        workspace.mkdir()
        script = _checkout_step(workflow, job_name)["run"]
        _run_checkout_script(script, fixture_origin, str(workspace))

        head = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert head == fixture_origin[1], (
            f"{workflow_path}/{job_name}: direct fetch left HEAD at {head}, "
            f"expected {fixture_origin[1]}"
        )
        assert (workspace / "sentinel.txt").read_text() == "nfm-5548 fixture\n"


def test_direct_checkout_cleans_persisted_workspace(fixture_origin, tmp_path):
    """The script mirrors actions/checkout clean:true on a reused workspace.

    A self-hosted runner workspace persists between runs; leftover untracked
    files must be gone after the direct checkout, and a stale .git must not
    leak the previous run's history into the new commit.
    """
    workflow = _load_workflow(".github/workflows/prod-deploy-event-collector.yml")
    workspace = tmp_path / "ws-reused"
    workspace.mkdir()
    stale_git = workspace / ".git"
    stale_git.mkdir()
    (stale_git / "stale").write_text("from a previous run\n")
    leftover_dir = workspace / "leftover-dir"
    leftover_dir.mkdir()
    (leftover_dir / "junk.txt").write_text("stale\n")
    (workspace / "untracked-root.txt").write_text("stale\n")

    script = _checkout_step(workflow, "collect")["run"]
    _run_checkout_script(script, fixture_origin, str(workspace))

    assert not leftover_dir.exists(), "git clean -ffdx did not remove leftover dirs"
    assert not (workspace / "untracked-root.txt").exists()

    # Stale .git content (git never cleans inside .git) must be inert: the
    # forced checkout of FETCH_HEAD pins HEAD to the dispatched SHA and the
    # working tree must be exactly the fetched commit, with nothing stale
    # leaking into it.
    head = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == fixture_origin[1]
    status = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert status == "", f"working tree not clean after forced checkout:\n{status}"
    assert (workspace / "sentinel.txt").read_text() == "nfm-5548 fixture\n"


def test_collector_declares_fetch_permissions():
    """prod-deploy-event-collector pins contents:read for the direct fetch."""
    workflow = _load_workflow(".github/workflows/prod-deploy-event-collector.yml")
    perms = workflow.get("permissions")
    assert perms is not None, "expected an explicit permissions block (NFM-5548)"
    assert perms.get("contents") == "read", f"direct git fetch needs contents:read, got {perms}"
