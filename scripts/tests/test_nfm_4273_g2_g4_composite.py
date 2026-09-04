"""NFM-4273 integration composite — ADR-013 §5 incident-replay slice (G2+G4).

Proves the G2+G4 branches work as ONE unit against the composite scenario,
not just per-sibling:

  1. G4a record: a sanctioned deploy records its manifest at the canonical
     gate path shape (/usr/local/var/nfm-g2/prod-deploy-manifest.json,
     world-readable 0644 — the NFM-4273 coherence contract: the desktop-user
     drift cron must be able to read the copy the deploy identity wrote).
  2. G4b detect: an unsanctioned mutation (fresh rebuild of a prod service —
     the NFM-4264 incident signal) diverges live state from that manifest;
     the checker files an [DEPLOY-DRIFT] SRE issue naming the service with
     expected vs actual digest, in ONE interval.
  3. G4b quiet: the SAME checker run issues only read-only docker verbs
     (ps --filter / inspect) — the G2 read-only regression at the layer the
     alarm actually touches.

Sibling modules are imported for their harnesses (fake docker in the real
inspect shape, stub Paperclip over real HTTP) — the flows under test are
the real scripts/check_deploy_drift.py + scripts/record_deploy_manifest.py.
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import test_check_deploy_drift as drift
import test_record_deploy_manifest as recorder

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent
GATE_PACKAGE = SCRIPTS_DIR / "host-prod-gate"

DEPLOY_SHA = recorder.DEPLOY_SHA
COMPOSE_PROJECT = drift.COMPOSE_PROJECT


# Fixtures don't cross module boundaries — reuse the sibling module's
# harness OBJECTS (shim source, stub server class) in local fixtures.
@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Same contract as test_check_deploy_drift.fake_docker."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    shim = bin_dir / "docker"
    shim.write_text(drift.FAKE_DOCKER, encoding="utf-8")
    shim.chmod(0o755)
    state_path = tmp_path / "docker-state.json"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    def _load(containers: dict, **extra: object) -> Path:
        state_path.write_text(
            json.dumps({"containers": containers, **extra}), encoding="utf-8"
        )
        monkeypatch.setenv("FAKE_DOCKER_STATE", str(state_path))
        return state_path

    return _load


@pytest.fixture
def stub_paperclip():
    stub = drift.StubPaperclip()
    yield stub
    stub.close()


# ------------------------------------------------------------- the composite


def test_g4_record_then_drift_detect_at_canonical_path(
    fake_docker, stub_paperclip, tmp_path: Path
):
    """The full G4a→G4b loop at the canonical gate-path shape (NFM-4273).

    Recorder output (not a handcrafted fixture) is the alarm's baseline:
    whatever the gated deploy identity wrote is exactly what the desktop
    cron diffs against — one copy, one location, world-readable.
    """
    # -- 1. sanctioned deploy state: all six prod services in sync ---------
    fake_docker(drift.prod_containers())
    gate_var = tmp_path / "usr-local-var-nfm-g2"
    manifest = gate_var / "prod-deploy-manifest.json"

    # The gate entry's env contract (run-record-manifest.sh): canonical
    # path + world-readable. Arg validation + deploy-identity enforcement
    # are covered by test_nfm_docker_gate_hostsetup.py; this proves the
    # RECORDER honors the contract the entry sets.
    recorded = subprocess.run(
        [
            sys.executable, str(recorder.SCRIPT),
            "--deploy-sha", DEPLOY_SHA,
            "--actor", "gh-runner:lwj04",
        ],
        capture_output=True, text=True, timeout=60,
        env={
            **os.environ,
            "NFM_DEPLOY_MANIFEST": str(manifest),
            "NFM_DEPLOY_MANIFEST_WORLD_READABLE": "1",
        },
    )
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr

    # world-readable at the canonical path — the desktop cron can read it
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o644
    assert stat.S_IMODE(gate_var.stat().st_mode) == 0o755

    # -- 2. unsanctioned mutation: api rebuilt out-of-band (NFM-4264) ------
    diverged = drift.prod_containers()
    diverged[f"{COMPOSE_PROJECT}-api"] = drift._container("api", "5" * 64)
    # same tag shape (nucpot-prod-api:<short>), different image-id digest —
    # a fresh rebuild mints a new one; this is the detection signal.
    assert (
        diverged[f"{COMPOSE_PROJECT}-api"]["Config"]["Image"]
        != drift.prod_containers()[f"{COMPOSE_PROJECT}-api"]["Config"]["Image"]
        or drift.expected_digest(diverged[f"{COMPOSE_PROJECT}-api"])
        != drift.expected_digest(
            drift.prod_containers()[f"{COMPOSE_PROJECT}-api"]
        )
    )
    fake_docker(diverged)

    # -- 3. the drift cron fires once against the canonical-shaped paths ----
    checker = subprocess.run(
        [
            sys.executable, str(drift.SCRIPT),
            "--manifest", str(manifest),
            "--lock", str(gate_var / "prod-deploy.lock"),
            "--state", str(tmp_path / "drift-state.json"),
            "--recheck-seconds", "0",
            "--paperclip-url", stub_paperclip.url,
            "--paperclip-key", "test-key",
            "--company-id", drift.COMPANY_ID,
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert checker.returncode == 1, checker.stdout + checker.stderr

    creates = stub_paperclip.creates()
    assert len(creates) == 1, f"expected exactly one filed issue, got {len(creates)}"
    issue = creates[0]["body"]
    assert issue["title"].startswith("[DEPLOY-DRIFT]")
    assert issue["assigneeAgentId"] == drift.SRE_AGENT_ID
    body = issue["description"]
    # names the service with expected vs actual digest (AC: composite test)
    assert "api" in body
    assert drift.expected_digest(
        drift.prod_containers()[f"{COMPOSE_PROJECT}-api"]
    ) in body, "filed body must cite the manifest (expected) digest"
    assert drift.expected_digest(diverged[f"{COMPOSE_PROJECT}-api"]) in body, (
        "filed body must cite the live (actual) digest"
    )
    # provenance rides through: the alarm cites who recorded the baseline
    assert "gh-runner:lwj04" in body


def test_converged_state_stays_quiet_at_canonical_path(
    fake_docker, stub_paperclip, tmp_path: Path
):
    """No divergence ⇒ no issue — the composite must not cry wolf on the
    coherent path either (read-only regression for the filing side)."""
    fake_docker(drift.prod_containers())
    gate_var = tmp_path / "usr-local-var-nfm-g2"
    manifest = gate_var / "prod-deploy-manifest.json"
    assert subprocess.run(
        [
            sys.executable, str(recorder.SCRIPT),
            "--deploy-sha", DEPLOY_SHA,
            "--actor", "deploy_prod.sh:testuser",
        ],
        capture_output=True, text=True, timeout=60,
        env={
            **os.environ,
            "NFM_DEPLOY_MANIFEST": str(manifest),
            "NFM_DEPLOY_MANIFEST_WORLD_READABLE": "1",
        },
    ).returncode == 0

    checker = subprocess.run(
        [
            sys.executable, str(drift.SCRIPT),
            "--manifest", str(manifest),
            "--lock", str(gate_var / "prod-deploy.lock"),
            "--state", str(tmp_path / "drift-state.json"),
            "--recheck-seconds", "0",
            "--paperclip-url", stub_paperclip.url,
            "--paperclip-key", "test-key",
            "--company-id", drift.COMPANY_ID,
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert checker.returncode == 0, checker.stdout + checker.stderr
    assert stub_paperclip.creates() == []


# --------------------------------------------- G2 read-only regression layer


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/containers/json"),                    # docker ps
        ("GET", "/containers/{id}/json"),               # docker inspect
        ("GET", "/containers/{id}/logs"),               # docker logs
        ("GET", "/containers/{id}/stats"),              # docker stats
        ("GET", "/images/json"),                        # docker images
        ("GET", "/info"),                               # docker info / compose config ctx
    ],
)
def test_g2_policy_read_only_endpoints_stay_frictionless(method: str, path: str):
    """The drift cron + daily docker workflow only ever issues read-only
    verbs; the G2 wall must pass them WITHOUT audit friction (AC-G2.2).
    Direct policy classification — the same decision the proxy enforces."""
    sys.path.insert(0, str(GATE_PACKAGE))
    try:
        import importlib
        policy = importlib.import_module("nfm_docker_gate.policy")
    finally:
        sys.path.pop(0)

    decision = policy.classify(
        method=method,
        raw_path=path,
        query="",
        body_json=None,
        resolver=None,
        cfg=policy.ScopeConfig(),
    )
    assert decision.allowed, f"{method} {path} must stay allowed (read-only)"
    assert decision.audit is False, f"{method} {path} must not audit-spam (AC-G2.2)"


def test_drift_cron_only_issues_read_only_docker_verbs(
    fake_docker, stub_paperclip, tmp_path: Path
):
    """End-to-end: the checker's whole docker conversation is ps --filter +
    inspect — the exact verb set G2 passes frictionless. Proves the G4 alarm
    composes with the G2 wall rather than fighting it."""
    calls_log = tmp_path / "docker-calls.log"
    # wrapper fake docker: records argv, delegates to the state shim
    bin_dir = tmp_path / "wrapbin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(
        "#!/bin/bash\n"
        f'printf \'%s\\n\' "$*" >> {calls_log}\n'
        f'exec "{tmp_path}/bin/docker" "$@"\n',
        encoding="utf-8",
    )
    (bin_dir / "docker").chmod(0o755)
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    fake_docker(drift.prod_containers())
    manifest = tmp_path / "m" / "prod-deploy-manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(drift.manifest_from(drift.prod_containers())),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable, str(drift.SCRIPT),
            "--manifest", str(manifest),
            "--lock", str(tmp_path / "absent.lock"),
            "--state", str(tmp_path / "state.json"),
            "--recheck-seconds", "0",
            "--paperclip-url", stub_paperclip.url,
            "--paperclip-key", "test-key",
            "--company-id", drift.COMPANY_ID,
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    argv_lines = [ln for ln in calls_log.read_text().splitlines() if ln.strip()]
    assert argv_lines, "checker must have talked to docker"
    for line in argv_lines:
        argv = line.split()
        assert argv[0] in {"ps", "inspect"}, f"non-read-only verb issued: {line}"
        if argv[0] == "inspect":
            assert "--format" in argv or len(argv) == 2, line
