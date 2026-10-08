"""Behavioral guards on the ci-throttle build gate (NFM-5389).

2026-10-07 ~15:30Z: Docker Desktop stopped injecting proxy env into
legacy-build RUN containers (manual-proxy clearance + daemon restart,
NFM-5346 window). Every deploy since then printed "ci-throttle healthy —
build downloads capped" while the builds downloaded UNCAPPED: the gate
only probed the host loopback, where the proxy still answers, and the
env-prefix form ``HTTP_PROXY=… docker build`` never reached legacy RUN
containers on this docker CLI anyway — the capped traffic had been
riding Docker Desktop's daemon-side proxy injection, which the same
clearance killed. (Correction 2026-10-08: the host.docker.internal:7899
alias path itself kept working — a container HTTPS relay through it
moves capped bytes; early "alias dead" reads were artifacts of
requesting host.docker.internal as an upstream URL, which the host-side
proxy cannot resolve.)

These tests pin the honest gate (``nfmd_ci_throttle_path_ready`` +
three-branch gate in ``scripts/deploy_prod.sh`` and the candidate step in
``.github/workflows/production-deployment.yml``) by EXECUTING the real
shell blocks with stubbed curl/docker and asserting recorded argv and
log output:

* the path probe must run the health check FROM a container through the
  build URL (proxy-form GET, Host pinned to 127.0.0.1 so the proxy
  answers health instead of relaying), not from the host shell;
* the capped branch requires host probe AND path probe — and passes the
  cap as explicit predefined ``--build-arg``s (the only form verified to
  reach legacy RUN containers on this CLI), not env prefixes alone;
* host-OK + path-BROKEN must NOT print the old "capped" line: it prints
  the NFM-5389 uncapped-with-reason line so a future alias death is
  readable in the deploy log instead of false-green;
* the path probe must not run when the host probe already failed (no
  useless container spin);
* all three ``docker build`` sites in deploy_prod.sh execute under
  ``set -u`` in every branch and receive the expanded
  ``THROTTLE_BUILD_ARGS`` proxy build args only in the capped branch —
  an unsafe empty-array expansion aborts /bin/bash 3.2 before any build
  is recorded;
* the workflow candidate step's shell block runs the same dual probe
  and passes the explicit ``--build-arg`` lines only when both probes
  pass.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_PROD = REPO_ROOT / "scripts" / "deploy_prod.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production-deployment.yml"

HOST_URL = "http://127.0.0.1:7899"
BUILD_URL = "http://host.docker.internal:7899"
PROBE_IMAGE = "ghcr.io/etoile04/nucpot-build-base:stable"
PROXY_BUILD_ARGS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
)

# Records every invocation to <tmp>/calls (one line, space-joined); probe
# runs exit with NFM5389_DOCKER_RUN_RC, every other subcommand exits 0.
STUB_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${NFM5389_CALLS}"
if [ "$1" = "run" ]; then
  exit "${NFM5389_DOCKER_RUN_RC:-0}"
fi
exit 0
"""

# Health probe plan: "rc" per curl call; calls past the last line repeat it.
STUB_CURL = """#!/usr/bin/env bash
n="$(cat "${NFM5389_CURL_COUNT}" 2>/dev/null || printf 0)"
printf '%s' "$((n + 1))" > "${NFM5389_CURL_COUNT}"
plan="$(sed -n "$((n + 1))p" "${NFM5389_CURL_PLAN}")"
[ -z "$plan" ] && plan="$(tail -n 1 "${NFM5389_CURL_PLAN}")"
exit "${plan%%|*}"
"""


def _write_stub(bin_dir: Path, name: str, text: str) -> None:
    stub = bin_dir / name
    stub.write_text(text, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _extract(name: str, source: Path, pattern: str) -> str:
    text = source.read_text(encoding="utf-8")
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    assert match, (
        f"{name} is missing from {source.name} — the NFM-5389 throttle gate "
        "cannot have been deleted or renamed without updating these guards."
    )
    return match.group(0)


def _run_script(
    tmp_path: Path, script: str, curl_plan: list[str], docker_run_rc: int
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_stub(bin_dir, "docker", STUB_DOCKER)
    _write_stub(bin_dir, "curl", STUB_CURL)

    plan = tmp_path / "curl_plan"
    plan.write_text("\n".join(curl_plan) + "\n", encoding="utf-8")
    count = tmp_path / "curl_count"
    count.write_text("0", encoding="utf-8")
    calls = tmp_path / "calls"
    calls.write_text("", encoding="utf-8")

    script_file = tmp_path / "gate.sh"
    script_file.write_text(script, encoding="utf-8")

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "NFM5389_CURL_PLAN": str(plan),
        "NFM5389_CURL_COUNT": str(count),
        "NFM5389_CALLS": str(calls),
        "NFM5389_DOCKER_RUN_RC": str(docker_run_rc),
    }
    result = subprocess.run(
        ["/bin/bash", "-c", f'. "{script_file}"'],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    result.calls = calls.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    return result


def _run_deploy_gate(
    tmp_path: Path, curl_plan: list[str], docker_run_rc: int
) -> subprocess.CompletedProcess[str]:
    """Execute deploy_prod.sh's throttle vars, both probe functions, the
    three-branch gate, and all three ``docker build`` sites with stubbed
    curl/docker (builds are stubbed to no-ops; every docker invocation's
    argv is recorded to <tmp>/calls)."""
    script = (
        "set -uo pipefail\n"
        "DEPLOY_SHA=0123456789abcdef\n"
        "PROD_IMAGE_TAG=nfm5389test\n"
        + _extract(
            "ci-throttle gate and build sites",
            DEPLOY_PROD,
            r"^NFMD_CI_THROTTLE_HOST_URL=.*?^\)\n",
        )
    )
    return _run_script(tmp_path, script, curl_plan, docker_run_rc)


def _run_workflow_candidate_gate(
    tmp_path: Path, curl_plan: list[str], docker_run_rc: int
) -> subprocess.CompletedProcess[str]:
    """Execute the workflow's candidate-gate shell block (extracted from the
    YAML run block verbatim) with the same stubbed curl/docker."""
    gate = _extract(
        "candidate gate",
        WORKFLOW,
        r"if curl -fsS --max-time 4 -x http://127\.0\.0\.1:7899.*?\n[ ]*fi\n\n",
    )
    script = "set -u\nCANDIDATE_TAG=nfm5389-cand\n" + gate
    return _run_script(tmp_path, script, curl_plan, docker_run_rc)


def _build_calls(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [l for l in result.calls.splitlines() if l.startswith("build ")]  # type: ignore[attr-defined]


def _assert_no_proxy_build_args(builds: list[str]) -> None:
    for call in builds:
        for var in PROXY_BUILD_ARGS:
            assert f"--build-arg {var}=" not in call, (
                f"uncapped branch must not pass {var} — pip would burn "
                f"retries against the dead alias: {call}"
            )


# --------------------------------------------------------------------------
# Path probe shape
# --------------------------------------------------------------------------


def test_path_probe_runs_health_check_from_a_container(tmp_path: Path) -> None:
    result = _run_deploy_gate(tmp_path, curl_plan=["0|"], docker_run_rc=0)
    probe_lines = [l for l in result.calls.splitlines() if l.startswith("run --rm")]
    assert probe_lines, (
        "gate must probe the throttle through `docker run` — a host-shell "
        "curl cannot see the host.docker.internal alias breakage (NFM-5389)"
    )
    probe = probe_lines[0]
    assert PROBE_IMAGE in probe, f"probe must use the local probe image, got: {probe}"
    assert f"-x {BUILD_URL}" in probe, (
        f"probe must dial through the build URL ({BUILD_URL}), got: {probe}"
    )
    assert f"{HOST_URL}/__nfmd_ci_throttle_health" in probe, (
        "probe must use the proxy-form health URL with Host pinned to "
        f"{HOST_URL} so the proxy answers health instead of relaying, got: {probe}"
    )


def test_path_probe_skipped_when_host_probe_fails(tmp_path: Path) -> None:
    result = _run_deploy_gate(tmp_path, curl_plan=["7|"], docker_run_rc=0)
    assert "run --rm" not in result.calls, (
        "path probe must not spin a container when the host probe already "
        "failed — the proxy is down and the uncapped branch is correct"
    )


# --------------------------------------------------------------------------
# Gate branches
# --------------------------------------------------------------------------


def test_both_probes_ok_caps_with_explicit_build_args(tmp_path: Path) -> None:
    result = _run_deploy_gate(tmp_path, curl_plan=["0|"], docker_run_rc=0)
    assert result.returncode == 0, result.stderr
    assert "capped" in result.stdout
    assert "NFM-5389" not in result.stdout or "host + container path" in result.stdout
    builds = _build_calls(result)
    assert len(builds) == 3, f"expected the 3 deploy build sites, got: {result.calls}"
    for call in builds:
        for var in PROXY_BUILD_ARGS:
            assert f"--build-arg {var}=" in call, (
                f"capped branch must pass {var} as an explicit predefined build "
                f"arg (env prefixes do not reach legacy RUN containers), got: {call}"
            )
    assert f"--build-arg HTTP_PROXY={BUILD_URL}" in builds[0]


def test_host_ok_path_broken_is_loud_and_uncapped(tmp_path: Path) -> None:
    result = _run_deploy_gate(tmp_path, curl_plan=["0|"], docker_run_rc=1)
    assert result.returncode == 0, result.stderr
    assert "NFM-5389" in result.stdout, (
        "host-OK + container-path-BROKEN must name NFM-5389 in the deploy "
        "log — the old 'healthy — capped' line was false-green for 4 deploys"
    )
    assert "UNCAPPED" in result.stdout.upper()
    assert "capped via" not in result.stdout, (
        "must not claim the cap is active when the container path is broken"
    )
    builds = _build_calls(result)
    assert len(builds) == 3
    _assert_no_proxy_build_args(builds)


def test_host_down_is_plain_uncapped(tmp_path: Path) -> None:
    result = _run_deploy_gate(tmp_path, curl_plan=["7|"], docker_run_rc=1)
    assert result.returncode == 0, result.stderr
    assert "NOT reachable" in result.stdout
    assert "uncapped" in result.stdout.lower()
    builds = _build_calls(result)
    assert len(builds) == 3
    _assert_no_proxy_build_args(builds)


# --------------------------------------------------------------------------
# Build sites and workflow candidate step
# --------------------------------------------------------------------------


def test_all_build_sites_expand_throttle_args_bash32_safe(tmp_path: Path) -> None:
    capped = _run_deploy_gate(tmp_path / "capped", curl_plan=["0|"], docker_run_rc=0)
    assert capped.returncode == 0, capped.stderr
    uncapped = _run_deploy_gate(tmp_path / "uncapped", curl_plan=["7|"], docker_run_rc=1)
    assert uncapped.returncode == 0, (
        "every build site must expand THROTTLE_BUILD_ARGS with the bash-3.2 "
        "set-u-safe idiom (empty-array expansion under set -u is an "
        "unbound-variable error on /bin/bash 3.2) — the block aborted:\n"
        + uncapped.stderr
    )

    dockerfiles = (
        "docker/prod-api.Dockerfile",
        "docker/lightrag.Dockerfile",
        "docker/web.Dockerfile",
    )
    capped_builds = _build_calls(capped)
    uncapped_builds = _build_calls(uncapped)
    for dockerfile in dockerfiles:
        capped_site = [c for c in capped_builds if dockerfile in c]
        uncapped_site = [c for c in uncapped_builds if dockerfile in c]
        assert capped_site, f"no docker build executed for {dockerfile} in the capped run"
        assert uncapped_site, (
            f"no docker build executed for {dockerfile} in the uncapped run — "
            "the site was skipped or aborted"
        )
        for var in PROXY_BUILD_ARGS:
            assert f"--build-arg {var}=" in capped_site[0], (
                f"capped build for {dockerfile} is missing {var}: {capped_site[0]}"
            )
        _assert_no_proxy_build_args(uncapped_site)


def test_workflow_candidate_step_dual_probes_and_explicit_args(tmp_path: Path) -> None:
    result = _run_workflow_candidate_gate(tmp_path, curl_plan=["0|"], docker_run_rc=0)
    assert result.returncode == 0, result.stderr
    assert "capped" in result.stdout
    probe_lines = [l for l in result.calls.splitlines() if l.startswith("run --rm")]
    assert probe_lines, (
        "candidate gate must probe the throttle through `docker run` — a "
        "host-shell curl cannot see the alias breakage (NFM-5389)"
    )
    assert PROBE_IMAGE in probe_lines[0]
    assert f"-x {BUILD_URL}" in probe_lines[0]
    builds = _build_calls(result)
    assert len(builds) == 1, f"expected one candidate build, got: {result.calls}"
    for var in PROXY_BUILD_ARGS:
        assert f"--build-arg {var}=" in builds[0], (
            f"candidate capped branch must pass {var} as an explicit predefined "
            f"build arg, got: {builds[0]}"
        )
    assert f"--build-arg HTTP_PROXY={BUILD_URL}" in builds[0]


def test_workflow_candidate_step_fallbacks_are_uncapped(tmp_path: Path) -> None:
    broken = _run_workflow_candidate_gate(
        tmp_path / "broken", curl_plan=["0|"], docker_run_rc=1
    )
    assert broken.returncode == 0, broken.stderr
    assert "NFM-5389" in broken.stdout, (
        "host-OK + path-BROKEN must name NFM-5389 in the candidate log "
        "instead of the old false-green cap line"
    )
    assert "UNCAPPED" in broken.stdout.upper()
    builds = _build_calls(broken)
    assert len(builds) == 1
    _assert_no_proxy_build_args(builds)

    down = _run_workflow_candidate_gate(tmp_path / "down", curl_plan=["7|"], docker_run_rc=1)
    assert down.returncode == 0, down.stderr
    assert "NOT reachable" in down.stdout
    assert "run --rm" not in down.calls, (
        "candidate path probe must not spin a container when the host probe "
        "already failed"
    )
    builds = _build_calls(down)
    assert len(builds) == 1
    _assert_no_proxy_build_args(builds)
