"""NFM-5556 — race-proof direct checkout: private per-run workdir.

Evidence base (2026-10-16): the NFM-5548 direct-git checkout ran
``git init`` + fetch + checkout IN PLACE in the persisted shared
``$GITHUB_WORKSPACE`` (``_work/nucpot/nucpot``) on the self-hosted
production runner. Multiple autonomous jobs target that one directory —
the two deploy-event collectors (separate concurrency groups, so they
overlap freely) plus push-triggered CI checkouts — so concurrent
init/fetch/checkout sequences race the same ``.git`` and interrupted
runs leave stale refs behind. Symptom: ``git checkout -f FETCH_HEAD``
dies with ``fatal: bad object refs/heads`` (exit 128), intermittent
since 10-15; 3/3 on f5060144 during NFM-5555 merge-CI load, identical
failures earlier on 4d3479300.

Contract pinned here (all three NFM-5548 lanes: both collectors + the
site-monitor watchdog job):

* the checkout step materializes the repo in a PRIVATE per-run workdir
  (``mktemp -d`` under ``$RUNNER_TEMP``) exported as
  ``NFMD_CHECKOUT_DIR`` via ``$GITHUB_ENV`` — it never reads or writes
  ``$GITHUB_WORKSPACE``;
* the shared workspace is left byte-untouched (a concurrent CI job
  holding it is safe);
* two checkouts executing simultaneously against the same RUNNER_TEMP —
  the collector-vs-collector overlap — both succeed and pin
  ``$GITHUB_SHA`` in their own workdirs;
* every later step that references repo files (``scripts/``,
  ``tools/``) runs from ``$NFMD_CHECKOUT_DIR``;
* a ``if: always()`` cleanup step — the LAST step — removes the private
  workdir (the host has a disk-pressure history; per-run dirs under
  ``_work/_temp`` must not accumulate), and is a no-op when the export
  never happened;
* prod-deploy-event-collector no longer maps any env to
  ``github.workspace`` — ``PYTHONPATH`` follows the private checkout
  instead of the stale shared workspace.

Hermetic execution mirrors test_nfm_5548_direct_checkout.py: the real
script runs against a ``file://`` bare fixture through the same
``url.<base>.insteadOf`` seam the host's SSH-over-443 rewrite uses.
"""

from __future__ import annotations

import concurrent.futures
import os
import subprocess

import pytest
import yaml
from test_nfm_5548_direct_checkout import SELF_HOSTED_JOBS, fixture_origin

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

CLEANUP_STEP_NAME = "Clean per-run workdir"
CHECKOUT_EXPORT = "NFMD_CHECKOUT_DIR"


def _load_workflow(rel_path: str) -> dict:
    with open(os.path.join(REPO_ROOT, rel_path)) as fh:
        return yaml.safe_load(fh)


def _checkout_step(workflow: dict, job_name: str) -> dict:
    job = workflow["jobs"][job_name]
    steps = [s for s in job["steps"] if "checkout" in s.get("name", "").lower()]
    assert len(steps) == 1, f"{job_name}: expected exactly one checkout step"
    return steps[0]


def _cleanup_step(workflow: dict, job_name: str) -> dict:
    job = workflow["jobs"][job_name]
    named = [s for s in job["steps"] if s.get("name") == CLEANUP_STEP_NAME]
    assert len(named) == 1, (
        f"{job_name}: expected exactly one '{CLEANUP_STEP_NAME}' step "
        "(NFM-5556: per-run workdirs must not accumulate on the host)"
    )
    return named[0]


def _run_script(script: str, env: dict, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _assert_rc0(proc: subprocess.CompletedProcess, context: str) -> None:
    assert proc.returncode == 0, (
        f"{context} script failed (rc={proc.returncode}):\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def _checkout_env(
    fixture_origin: tuple[str, str],
    workspace: str,
    runner_temp: str,
    github_env_file: str,
) -> dict:
    origin_url, _ = fixture_origin
    return dict(
        os.environ,
        GITHUB_WORKSPACE=workspace,
        GITHUB_REPOSITORY="fixture/repo",
        GITHUB_SHA=fixture_origin[1],
        GITHUB_TOKEN="dummy-off-http-token",
        GITHUB_ENV=github_env_file,
        RUNNER_TEMP=runner_temp,
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0=f"url.{origin_url}.insteadOf",
        GIT_CONFIG_VALUE_0="https://github.com/",
    )


def _read_export(path: str, key: str) -> str:
    with open(path) as fh:
        for line in fh:
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    raise AssertionError(f"{key} not exported to GITHUB_ENV ({path})")


# ---------------------------------------------------------------------------
# 1. Structure: private workdir, no shared-workspace access
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_path", sorted(SELF_HOSTED_JOBS))
def test_checkout_step_uses_private_workdir(workflow_path):
    """The direct checkout must operate in a per-run dir, not the shared workspace."""
    workflow = _load_workflow(workflow_path)
    for job_name in SELF_HOSTED_JOBS[workflow_path]:
        script = _checkout_step(workflow, job_name)["run"]
        assert "mktemp -d" in script, (
            f"{workflow_path}/{job_name}: checkout must create a private "
            "per-run workdir (NFM-5556 race)"
        )
        assert "GITHUB_WORKSPACE" not in script, (
            f"{workflow_path}/{job_name}: checkout still touches the shared "
            "$GITHUB_WORKSPACE — concurrent jobs race the same .git "
            "(fatal: bad object refs/heads, NFM-5556)"
        )
        assert f'echo "{CHECKOUT_EXPORT}=' in script and '"$GITHUB_ENV"' in script, (
            f"{workflow_path}/{job_name}: checkout must export "
            f"{CHECKOUT_EXPORT} via $GITHUB_ENV for subsequent steps"
        )
        assert "$GITHUB_SHA" in script and "FETCH_HEAD" in script, (
            f"{workflow_path}/{job_name}: NFM-5548 direct-fetch contract regressed"
        )


def test_collector_checkout_exports_pythonpath(fixture_origin, tmp_path):
    """Collector #2's PYTHONPATH must follow the private checkout, not
    github.workspace — asserted on the GITHUB_ENV the executed checkout
    step actually writes, so a behavior-preserving rewrite of the step
    cannot break the contract silently."""
    workflow = _load_workflow(".github/workflows/prod-deploy-event-collector.yml")
    script = _checkout_step(workflow, "collect")["run"]

    workspace = tmp_path / "ws"
    workspace.mkdir()
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    github_env = tmp_path / "env"

    proc = _run_script(
        script,
        _checkout_env(fixture_origin, str(workspace), str(runner_temp), str(github_env)),
        str(workspace),
    )
    _assert_rc0(proc, "collector-2 checkout")

    checkout_dir = _read_export(str(github_env), CHECKOUT_EXPORT)
    pythonpath = _read_export(str(github_env), "PYTHONPATH")
    assert pythonpath == checkout_dir, (
        "PYTHONPATH must be exported to $GITHUB_ENV pointing at the private "
        f"per-run checkout: PYTHONPATH={pythonpath!r} vs {CHECKOUT_EXPORT}={checkout_dir!r}"
    )


# ---------------------------------------------------------------------------
# 2. Behavior: real execution against the file:// fixture
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_path", sorted(SELF_HOSTED_JOBS))
def test_checkout_executes_in_private_dir_and_leaves_workspace_untouched(
    workflow_path, fixture_origin, tmp_path
):
    """Verbatim execution: checkout lands in the exported private dir; the
    shared workspace (held by a concurrent CI job) is byte-untouched."""
    workflow = _load_workflow(workflow_path)
    for job_name in SELF_HOSTED_JOBS[workflow_path]:
        workspace = tmp_path / f"ws-{job_name}"
        workspace.mkdir()
        (workspace / "ci-job-marker.txt").write_text("held by a concurrent job\n")
        stale = workspace / ".git"
        stale.mkdir()
        (stale / "HEAD").write_text("ref: refs/heads/main\n")

        runner_temp = tmp_path / "runner-temp"
        runner_temp.mkdir()
        github_env = tmp_path / f"env-{job_name}"

        script = _checkout_step(workflow, job_name)["run"]
        proc = _run_script(
            script,
            _checkout_env(fixture_origin, str(workspace), str(runner_temp), str(github_env)),
            str(workspace),
        )
        _assert_rc0(proc, f"{workflow_path}/{job_name} checkout")

        checkout_dir = _read_export(str(github_env), CHECKOUT_EXPORT)
        assert os.path.dirname(os.path.realpath(checkout_dir)) == os.path.realpath(
            str(runner_temp)
        ), f"private workdir {checkout_dir} must live under RUNNER_TEMP"

        head = subprocess.run(
            ["git", "-C", checkout_dir, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert head == fixture_origin[1]
        with open(os.path.join(checkout_dir, "sentinel.txt")) as fh:
            assert fh.read() == "nfm-5548 fixture\n"

        # The shared workspace is untouched — marker, junk, and stale .git
        # survive exactly as the concurrent CI job left them.
        assert (workspace / "ci-job-marker.txt").read_text() == "held by a concurrent job\n"
        assert (stale / "HEAD").exists(), "checkout must not mutate the shared workspace .git"


def test_concurrent_checkouts_are_isolated(fixture_origin, tmp_path):
    """Two checkouts racing the same RUNNER_TEMP (collector #1 vs #2 overlap)
    both succeed — the NFM-5556 failure mode cannot recur."""
    workflow = _load_workflow(".github/workflows/collect-prod-deploy-events.yml")
    script_a = _checkout_step(workflow, "collect-prod-events")["run"]
    workflow_b = _load_workflow(".github/workflows/prod-deploy-event-collector.yml")
    script_b = _checkout_step(workflow_b, "collect")["run"]

    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()

    def launch(script: str, ident: str) -> subprocess.CompletedProcess:
        workspace = tmp_path / f"ws-{ident}"
        workspace.mkdir()
        return _run_script(
            script,
            _checkout_env(
                fixture_origin, str(workspace), str(runner_temp), str(tmp_path / f"env-{ident}")
            ),
            str(workspace),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        procs = [
            pool.submit(launch, script_a, "collector-1"),
            pool.submit(launch, script_b, "collector-2"),
        ]
        results = [f.result() for f in procs]

    for proc, ident in zip(results, ("collector-1", "collector-2")):
        _assert_rc0(proc, f"concurrent {ident}")

    dirs = [
        _read_export(str(tmp_path / f"env-{ident}"), CHECKOUT_EXPORT)
        for ident in ("collector-1", "collector-2")
    ]
    assert dirs[0] != dirs[1], "the two overlapping runs must not share a workdir"
    for d in dirs:
        head = subprocess.run(
            ["git", "-C", d, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert head == fixture_origin[1]


# ---------------------------------------------------------------------------
# 3. Downstream steps + cleanup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_path", sorted(SELF_HOSTED_JOBS))
def test_repo_steps_run_in_private_checkout(workflow_path):
    """Steps referencing repo files (scripts/, tools/) must cd into the
    private checkout — running them from the stale shared workspace reads
    whatever the last CI job left there."""
    workflow = _load_workflow(workflow_path)
    for job_name in SELF_HOSTED_JOBS[workflow_path]:
        steps = workflow["jobs"][job_name]["steps"]
        checkout_idx = next(
            i for i, s in enumerate(steps) if "checkout" in s.get("name", "").lower()
        )
        for step in steps[checkout_idx + 1 :]:
            if step.get("name") == CLEANUP_STEP_NAME:
                continue
            body = step.get("run", "")
            if "scripts/" in body or "tools/" in body:
                assert CHECKOUT_EXPORT in body, (
                    f"{workflow_path}/{job_name}/{step.get('name')}: runs repo "
                    f"files but never references ${CHECKOUT_EXPORT} — it would "
                    "execute against the stale shared workspace"
                )


@pytest.mark.parametrize("workflow_path", sorted(SELF_HOSTED_JOBS))
def test_cleanup_step_is_last_always_and_hermetic(workflow_path, tmp_path):
    """An if:always() LAST step removes the per-run workdir; without the
    export it is a clean no-op (no rm -rf of an empty string)."""
    workflow = _load_workflow(workflow_path)
    for job_name in SELF_HOSTED_JOBS[workflow_path]:
        step = _cleanup_step(workflow, job_name)
        steps = workflow["jobs"][job_name]["steps"]
        assert steps[-1] is not None and steps[-1].get("name") == CLEANUP_STEP_NAME, (
            f"{workflow_path}/{job_name}: '{CLEANUP_STEP_NAME}' must be the last "
            "step — cleaning earlier deletes the checkout mid-job"
        )
        assert step.get("if") == "always()", (
            f"{workflow_path}/{job_name}: cleanup must run if: always() so "
            "failed runs do not leak workdirs onto the disk-pressured host"
        )

        script = step["run"]
        seeded = tmp_path / "seeded-workdir"
        (seeded / "nested").mkdir(parents=True)
        (seeded / "nested" / "f.txt").write_text("x\n")
        proc = _run_script(
            script, dict(os.environ, **{CHECKOUT_EXPORT: str(seeded)}), str(tmp_path)
        )
        _assert_rc0(proc, f"{workflow_path}/{job_name} cleanup (set)")
        assert not seeded.exists(), "cleanup did not remove the private workdir"

        hermetic = dict(os.environ)
        hermetic.pop(CHECKOUT_EXPORT, None)
        proc = _run_script(script, hermetic, str(tmp_path))
        _assert_rc0(proc, f"{workflow_path}/{job_name} cleanup (unset)")
