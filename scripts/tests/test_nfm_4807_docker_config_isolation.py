"""NFM-4807 — deploy-prod DOCKER_CONFIG race isolation.

Incident (2026-09-12, run 34705930216): scripts/tests executing
deploy_prod.sh under a fake HOME re-pointed the SHARED
/tmp/nfm848-no-cred-docker-config/cli-plugins/docker-compose symlink at
a pytest-tmpdir target that garbage-collects. A concurrent prod deploy
then lost `docker compose` mid-flight (exit 125, "unknown shorthand
flag: -f") — the NFM-3777 mutable-shared-/tmp class again.

These tests pin the isolation contract (AC1/AC2/AC3):

  1. an unsandboxed deploy self-isolates into a per-deploy private,
     trap-cleaned DOCKER_CONFIG and never writes the shared literal;
  2. a concurrently running sandboxed session (the NFMD_DOCKER_CONFIG
     override shape test harnesses use) cannot affect a deploy's config;
  3. the NFMD_DOCKER_CONFIG override is honored and caller-owned (the
     script never trap-removes a dir it did not create);
  4. the deploy workflow no longer references the shared literal path.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import test_check_deploy_drift as drift
import yaml
from shared_docker_config import SHARED_DOCKER_CONFIG, snapshot_shared_config

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production-deployment.yml"

# Per-deploy private dirs the fixed script creates (mktemp template).
PRIVATE_CONFIG_GLOB = "nfm-deploy-docker-config.*"


def parse_env_log(env_log: Path) -> set:
    """DOCKER_CONFIG=<value> lines the docker shim recorded per call."""
    values = set()
    if env_log.is_file():
        for line in env_log.read_text(encoding="utf-8").splitlines():
            if line.startswith("DOCKER_CONFIG="):
                values.add(line.partition("=")[2])
    return values


def parse_mode_log(env_log: Path) -> set:
    modes = set()
    if env_log.is_file():
        for line in env_log.read_text(encoding="utf-8").splitlines():
            if line.startswith("DOCKER_CONFIG_MODE="):
                modes.add(line.partition("=")[2])
    return modes


class FakeDeployHost:
    """Drift-harness-shaped fake host (drift test 1187) whose docker
    shim additionally records the DOCKER_CONFIG each call ran under."""

    def __init__(self, root: Path) -> None:
        home = root / "home"
        repo = home / "Projects" / "nucpot"
        (repo / "docker").mkdir(parents=True)
        (repo / "tools" / "post-deploy-cutover-assert").mkdir(parents=True)
        (repo / "tools" / "prod-tag-retention").mkdir(parents=True)
        (repo / "scripts").mkdir()
        shutil.copy(SCRIPTS_DIR / "record_deploy_manifest.py", repo / "scripts")
        shutil.copy(SCRIPTS_DIR / "check_prod_image_tag.py", repo / "scripts")
        stubs = (
            repo / "tools" / "post-deploy-cutover-assert" / "assert.sh",
            repo / "tools" / "prod-tag-retention" / "prune.sh",
            repo / "scripts" / "prod_migrate.sh",
        )
        for stub in stubs:
            stub.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            stub.chmod(0o755)
        (repo / "docker" / ".env.prod").write_text("PROD_IMAGE_TAG=latest\n")
        (repo / "docker-compose.prod.yml").write_text("# hermetic stub\n")

        self.home = home
        self.gate_var = root / "gate-var"
        self.gate_var.mkdir()
        self.calls_log = root / "deploy-docker-calls.log"
        self.env_log = root / "deploy-docker-env.log"

        bin_dir = root / "deploybin"
        bin_dir.mkdir()
        # delegate to the drift module's deploy-capable fake docker, but
        # wrap it so every invocation first records the session's
        # DOCKER_CONFIG (+ its mode) — the observable this file asserts on.
        real_shim = bin_dir / "_deploy_docker.py"
        real_shim.write_text(drift.DEPLOY_DOCKER, encoding="utf-8")
        docker_shim = bin_dir / "docker"
        docker_shim.write_text(
            "#!/bin/bash\n"
            'printf "DOCKER_CONFIG=%s\\n" "${DOCKER_CONFIG:-<unset>}"'
            ' >> "${DEPLOY_DOCKER_ENV_LOG:-/dev/null}"\n'
            'mode="$(stat -f %Lp "${DOCKER_CONFIG:-/nonexistent}" 2>/dev/null'
            ' || stat -c %a "${DOCKER_CONFIG:-/nonexistent}" 2>/dev/null'
            ' || echo unknown)"\n'
            'printf "DOCKER_CONFIG_MODE=%s\\n" "$mode"'
            ' >> "${DEPLOY_DOCKER_ENV_LOG:-/dev/null}"\n'
            'exec python3 "${0%/*}/_deploy_docker.py" "$@"\n',
            encoding="utf-8",
        )
        docker_shim.chmod(0o755)

        def shim(name: str, body: str) -> None:
            target = bin_dir / name
            target.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
            target.chmod(0o755)

        sha = drift.DEPLOY_SHA
        shim(
            "git",
            f'if [ "$1" = "rev-parse" ]; then printf "%s\\n" "{sha}"; exit 0; fi\n'
            "exit 0",
        )
        shim("id", 'printf "nfmdeploy\\n"')
        shim("curl", "exit 0")
        shim("sleep", "exit 0")

        self.state_path = root / "deploy-state.json"
        self.state_path.write_text(
            json.dumps({"containers": drift.prod_containers()}), encoding="utf-8"
        )
        self.bin_dir = bin_dir

    def env(self, extra: dict | None = None) -> dict:
        """Explicit subprocess env (no monkeypatch: the concurrency test
        needs two independent envs at once)."""
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("PROD_IMAGE_TAG", "DOCKER_CONFIG", "NFMD_DOCKER_CONFIG")
        }
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_DOCKER_STATE": str(self.state_path),
                "DEPLOY_DOCKER_CALLS": str(self.calls_log),
                "DEPLOY_DOCKER_ENV_LOG": str(self.env_log),
                "DEPLOY_SHA": drift.DEPLOY_SHA,
                "DEPLOY_ACTOR": "gh-runner:lwj04",
                "NFM_G2_DEPLOY_IDENTITY": "1",
                "NFM_G2_VAR_DIR": str(self.gate_var),
            }
        )
        if extra:
            env.update(extra)
        return env

    def run(self, extra: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(drift.DEPLOY_PROD_SH)],
            capture_output=True,
            text=True,
            timeout=120,
            env=self.env(extra),
        )


def test_unsandboxed_deploy_self_isolates_docker_config(tmp_path: Path):
    """AC1: with no override set, the deploy resolves its OWN private
    DOCKER_CONFIG (per-deploy unique, mode 0700, trap-cleaned) and never
    writes the shared /tmp literal."""
    before = snapshot_shared_config()
    host = FakeDeployHost(tmp_path)
    result = host.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"DEPLOY_SCRIPT_COMPLETED_OK sha={drift.DEPLOY_SHA}" in result.stdout

    # the shared literal is byte-for-byte what it was before the deploy
    assert snapshot_shared_config() == before, (
        "deploy_prod.sh must never mutate /tmp/nfm848-no-cred-docker-config"
    )

    seen = parse_env_log(host.env_log)
    assert seen, "docker shim must record the deploy's DOCKER_CONFIG"
    assert str(SHARED_DOCKER_CONFIG) not in seen
    assert "<unset>" not in seen, "docker calls must run under a DOCKER_CONFIG"

    # private dir: observed values are unique per deploy and trap-removed
    # once the script exits (any exit path — the EXIT trap owns cleanup).
    for value in seen:
        assert Path(value).name.startswith("nfm-deploy-docker-config."), (
            f"DOCKER_CONFIG must be a per-deploy mktemp dir, saw {value}"
        )
        assert not Path(value).exists(), (
            f"per-deploy DOCKER_CONFIG must be trap-removed: {value}"
        )
    # AC1 hard guarantee: the dir is not writable by any other uid/session
    modes = parse_mode_log(host.env_log)
    assert modes <= {"700"}, f"private DOCKER_CONFIG must be mode 0700, saw {modes}"


def test_concurrent_sandboxed_run_cannot_affect_a_deploy(tmp_path: Path):
    """AC1/AC2 behavioral proof: while a deploy is mid-flight, a second
    sandboxed script session (the NFMD_DOCKER_CONFIG shape test harnesses
    use — a pytest stand-in) wires its OWN config; neither run ever
    resolves the other's dir, and the shared literal is untouched."""
    before = snapshot_shared_config()

    deploy_host = FakeDeployHost(tmp_path / "deploy")
    deploy_proc = subprocess.Popen(
        ["bash", str(drift.DEPLOY_PROD_SH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=deploy_host.env(),
    )
    # overlap the deploy's wiring/build window with the sandboxed session
    time.sleep(0.5)
    sandbox_host = FakeDeployHost(tmp_path / "sandbox")
    sandbox_dir = tmp_path / "sandbox" / "dc-sandbox"
    sandbox_proc = subprocess.Popen(
        ["bash", str(drift.DEPLOY_PROD_SH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=sandbox_host.env({"NFMD_DOCKER_CONFIG": str(sandbox_dir)}),
    )

    deploy_out, deploy_err = deploy_proc.communicate(timeout=120)
    sandbox_out, sandbox_err = sandbox_proc.communicate(timeout=120)
    assert deploy_proc.returncode == 0, deploy_out + deploy_err
    assert "DEPLOY_SCRIPT_COMPLETED_OK" in deploy_out
    assert sandbox_proc.returncode == 0, sandbox_out + sandbox_err

    deploy_seen = parse_env_log(deploy_host.env_log)
    sandbox_seen = parse_env_log(sandbox_host.env_log)

    # the sandboxed session wired exactly its override dir
    assert sandbox_seen == {str(sandbox_dir)}
    sandbox_link = sandbox_dir / "cli-plugins" / "docker-compose"
    assert sandbox_link.is_symlink(), "override dir must receive the plugin link"
    assert str(sandbox_host.home) in os.readlink(sandbox_link), (
        "sandbox link must point into the sandbox's own HOME"
    )
    # the deploy never resolved the sandbox's dir (nor the shared literal)
    assert str(sandbox_dir) not in deploy_seen
    assert str(SHARED_DOCKER_CONFIG) not in deploy_seen
    # and the two sessions shared no DOCKER_CONFIG dir at all
    assert deploy_seen.isdisjoint(sandbox_seen)
    # the deploy's own dirs are gone (trap-cleaned); the sandbox dir is
    # caller-owned and intentionally survives
    for value in deploy_seen:
        assert not Path(value).exists()

    assert snapshot_shared_config() == before, (
        "neither the deploy nor the sandboxed session may touch the shared literal"
    )


def test_nfmd_docker_config_override_is_honored_and_caller_owned(tmp_path: Path):
    """AC2 lever + override contract: NFMD_DOCKER_CONFIG redirects every
    docker invocation, receives the plugin symlink + empty config.json,
    and is NOT trap-removed (the caller owns its lifecycle)."""
    before = snapshot_shared_config()
    host = FakeDeployHost(tmp_path)
    override_dir = tmp_path / "dc-override"
    result = host.run({"NFMD_DOCKER_CONFIG": str(override_dir)})

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"DEPLOY_SCRIPT_COMPLETED_OK sha={drift.DEPLOY_SHA}" in result.stdout

    assert parse_env_log(host.env_log) == {str(override_dir)}
    # wired like a real DOCKER_CONFIG: credsStore-free config + plugin link
    # (dangling is fine — the fake HOME ships no plugin binary; what
    # matters is the link was written INSIDE the override, not in /tmp)
    assert (override_dir / "config.json").read_text(encoding="utf-8") == "{}"
    link = override_dir / "cli-plugins" / "docker-compose"
    assert link.is_symlink()
    assert os.readlink(link) == str(host.home / ".nfmd" / "docker-compose")
    # caller-owned: the script must not trap-remove an override dir
    assert override_dir.exists()

    assert snapshot_shared_config() == before


def test_deploy_workflow_has_no_shared_docker_config_literal():
    """AC3: parsed into GitHub's job/step model — no step's executable run
    code may reference the shared literal (comment lines are prose, not
    behavior), and the remote build step's DOCKER_CONFIG must be a per-run
    mktemp dir that the EXIT trap removes."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps") or []]
    assert steps, "workflow must define job steps"

    def code(script: str) -> str:
        return "\n".join(
            line
            for line in script.splitlines()
            if not line.lstrip().startswith("#")
        )

    run_scripts = [code(str(step.get("run") or "")) for step in steps]
    assert all(str(SHARED_DOCKER_CONFIG) not in script for script in run_scripts), (
        "a workflow run script still references the shared literal path"
    )

    build_steps = [
        step for step in steps if step.get("name") == "Build candidate api image"
    ]
    assert build_steps, "workflow must keep the candidate build step"
    run = str(build_steps[0].get("run") or "")
    assert re.search(r'export DOCKER_CONFIG="\\\$\(mktemp -d ', run), (
        "remote DOCKER_CONFIG must be assigned per-run mktemp output"
    )
    assert 'trap \'rm -rf "\\$DOCKER_CONFIG"\' EXIT' in run, (
        "per-run DOCKER_CONFIG must be trap-cleaned on the remote host"
    )
