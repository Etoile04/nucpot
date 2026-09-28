"""Deploy-epoch fencing token — hermetic suite (NFM-5253 / NFM-4848 shadow).

Covers the single primitive end-to-end, all inside sandboxes (no live prod,
no real docker, no real Paperclip):

* D1 ``scripts/deploy_epoch.py`` — mint monotonicity, fcntl-serialized
  concurrent mints (threads AND processes), corruption recovery from
  manifest-epoch + 1, and the NFM_DEPLOY_EPOCH > G2-dir > ~/.nfmd chain.
* D2 the T4 lockfile CAS — legit acquire, shadow refuse (fresh lock + live
  pid >= epoch holder: logged, lock still taken), enforced refuse (rc 80,
  holder's lock untouched), and the overwrite paths (stale lock / dead pid
  / older-epoch holder / no prior lock).
* D3 the T1 health-marker binding — ``deploy_event_marker_attests`` prints
  true ONLY on marker.epoch == run epoch (a stale/poisoned marker can
  structurally never attest), and ``deploy_epoch`` rides the deploy event
  ADDITIVELY (frozen §3.1 field order untouched, collector-validated).
* D2/D3 integration — the REAL deploy_prod.sh runs hermetically: mints +
  logs the epoch, writes the epoch-tagged marker, the manifest carries the
  epoch, the drift checker still accepts the baseline (AC1), a rerun mints
  N+1, and NFM_DEPLOY_LOCK_ENFORCE=1 refuses loudly under conflict.
* D4 ``scripts/record_rollback.sh`` — epoch + manifest + event line (rc/0),
  usage validation, and the fatal mint-failure path (rc 2).
* D5 the drift checker's epoch-aware stand-down — shadow logging only:
  would_stand_down true/false/indeterminate rides the existing cron output
  and the checker's BEHAVIOR (stand down on any fresh lock) is unchanged.
* wiring guards — the workflow surfaces the minted epoch
  (steps.deploy.outputs.epoch), gates FIRST_POLL on the marker predicate,
  emits the epoch additively, and runs this suite pre-deploy; the runbook
  references record_rollback.sh.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from shared_docker_config import snapshot_shared_config as _shared_docker_config_snapshot

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent
EPOCH_SCRIPT = SCRIPTS_DIR / "deploy_epoch.py"
DEPLOY_PROD_SH = SCRIPTS_DIR / "deploy_prod.sh"
RECORD_ROLLBACK_SH = SCRIPTS_DIR / "record_rollback.sh"
DEPLOY_EVENT_SH = SCRIPTS_DIR / "lib" / "deploy_event.sh"
DRIFT_SCRIPT = SCRIPTS_DIR / "check_deploy_drift.py"
COLLECTOR_SCRIPT = SCRIPTS_DIR / "okr" / "prod_event_collector.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production-deployment.yml"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "prod-deploy.md"

COMPOSE_PROJECT = "nucpot-prod"
DEPLOY_SHA = "5253a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9"  # hermetic deploy sha
LOCK_REFUSE_EXIT = 80

# The frozen §3.1 field ORDER (deploy_epoch, when present, appends last).
SCHEMA_FIELD_ORDER = [
    "event_id",
    "ts",
    "environment",
    "triggered_by",
    "commit_sha",
    "first_pass_success",
    "health_gate_first_poll_passed",
    "rollback_triggered",
    "skip_flag_used",
    "duration_ms",
    "health_status",
]

# Deploy-capable fake docker: serves ps/inspect from FAKE_DOCKER_STATE (the
# recorder + drift-checker surface) and accepts every deploy verb by logging
# argv to DEPLOY_DOCKER_CALLS. Mirrors the proven shim in
# test_check_deploy_drift.py (NFM-4273 hermetic deploy run).
DEPLOY_DOCKER = """\
#!/usr/bin/env python3
import json
import os
import sys


def log(line):
    path = os.environ.get("DEPLOY_DOCKER_CALLS")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\\n")


def state():
    with open(os.environ["FAKE_DOCKER_STATE"], encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["ps"]:
        label_filter = None
        names_format = False
        rest = iter(args[1:])
        for arg in rest:
            if arg == "--filter":
                label_filter = next(rest, "")
            elif arg.startswith("--filter="):
                label_filter = arg.split("=", 1)[1]
            elif arg == "--format":
                next(rest, None)
                names_format = True
            elif arg.startswith("--format="):
                names_format = True
        if not label_filter or not names_format:
            print(f"deploy-docker: unsupported ps invocation: {args}", file=sys.stderr)
            return 64
        _, _, label_value = label_filter.partition("=")
        _, _, project = label_value.partition("=")
        for name, container in sorted(state()["containers"].items()):
            labels = (container.get("Config") or {}).get("Labels") or {}
            if labels.get("com.docker.compose.project") == project:
                print(name)
        return 0
    if args[:1] == ["inspect"]:
        container = state()["containers"].get(args[1])
        if container is None:
            print(f"Error: No such object: {args[1]}", file=sys.stderr)
            return 1
        print(json.dumps([container]))
        return 0
    log("docker " + " ".join(args))
    if args[:1] == ["compose"] and "up" in args:
        var_dir = os.environ.get("NFM_G2_VAR_DIR", "")
        lock = os.path.join(var_dir, "prod-deploy.lock") if var_dir else ""
        log(f"lock_present={os.path.exists(lock) if lock else 'unknown'}")
    if args[:1] == ["images"]:
        repos = ("nucpot-prod-api", "nucpot-prod-lightrag", "nucpot-prod-web")
        if "{{.Repository}}:{{.Tag}}" in args:
            for repo in repos:
                print(f"{repo}:latest")
        else:
            for repo in repos:
                print(f"{repo}|latest|abc123def456|2026-09-04T00:00:00Z")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def _load_module(path: Path, name: str):
    """Import a script by file path (no side effects at import time)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def epoch_mod():
    return _load_module(EPOCH_SCRIPT, "deploy_epoch_under_test")


@pytest.fixture(scope="module")
def collector_mod():
    return _load_module(COLLECTOR_SCRIPT, "prod_event_collector_under_test")


@pytest.fixture(autouse=True)
def clean_epoch_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Scrub ambient deploy-path overrides so a developer session can never
    point the code under test at real host state, and pin HOME. NFM_G2_VAR_DIR
    is pinned to a NONEXISTENT dir: on the prod host itself the canonical
    /usr/local/var/nfm-g2 exists, and any code path that resolves a default
    epoch path must land in the sandbox HOME fallback, never the live file."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NFM_G2_VAR_DIR", str(tmp_path / "absent-g2"))
    for var in (
        "NFM_DEPLOY_EPOCH",
        "NFM_DEPLOY_LOCK",
        "NFM_DEPLOY_MANIFEST",
        "NFM_DEPLOY_LOCK_ENFORCE",
        "NFM_DEPLOY_MANIFEST_WORLD_READABLE",
        "NFMD_DEPLOY_EVENTS_PATH",
    ):
        monkeypatch.delenv(var, raising=False)


def _subprocess_env(**overrides: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()}
    for var in (
        "NFM_DEPLOY_EPOCH",
        "NFM_G2_VAR_DIR",
        "NFM_DEPLOY_LOCK",
        "NFM_DEPLOY_MANIFEST",
        "NFM_DEPLOY_LOCK_ENFORCE",
        "NFMD_DEPLOY_EVENTS_PATH",
    ):
        env.pop(var, None)
    env.update(overrides)
    return env


def _run_cli(args: list[str], **env_overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(EPOCH_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=_subprocess_env(**env_overrides),
    )


# ===========================================================================
# D1 — the primitive: mint / read / resolution / concurrency / recovery
# ===========================================================================


def test_read_absent_returns_none_and_cli_exits_one(epoch_mod, tmp_path: Path):
    epoch_file = tmp_path / "prod-deploy.epoch"
    assert epoch_mod.read_epoch(epoch_file) is None
    result = _run_cli(["read"], NFM_DEPLOY_EPOCH=str(epoch_file))
    assert result.returncode == 1
    assert result.stdout.strip() == ""


def test_mint_is_monotonic_and_persisted(epoch_mod, tmp_path: Path):
    epoch_file = tmp_path / "prod-deploy.epoch"
    assert [epoch_mod.mint_epoch(epoch_file) for _ in range(3)] == [1, 2, 3]
    assert epoch_file.read_text(encoding="utf-8").strip() == "3"
    assert epoch_mod.read_epoch(epoch_file) == 3


def test_mint_recovers_from_corruption_via_manifest(epoch_mod, tmp_path: Path):
    """A corrupt/unparseable epoch file re-mints from manifest-epoch + 1 —
    recovery must never rewind below the last recorded state transition."""
    epoch_file = tmp_path / "prod-deploy.epoch"
    epoch_file.write_text("not-a-number\n", encoding="utf-8")
    manifest = tmp_path / "prod-deploy-manifest.json"
    manifest.write_text(json.dumps({"deploy_epoch": 7}), encoding="utf-8")
    env = {
        "NFM_DEPLOY_EPOCH": str(epoch_file),
        "NFM_DEPLOY_MANIFEST": str(manifest),
    }
    assert epoch_mod.mint_epoch(epoch_file, env=env) == 8
    # No readable manifest either → restart from 1, never garbage.
    manifest.unlink()
    epoch_file.write_text("]]garbage[[", encoding="utf-8")
    assert epoch_mod.mint_epoch(epoch_file, env=env) == 1


def test_resolution_chain_env_override_then_g2_then_home(epoch_mod, tmp_path: Path):
    override = tmp_path / "override.epoch"
    assert epoch_mod.resolve_epoch_path({"NFM_DEPLOY_EPOCH": str(override)}) == override
    g2 = tmp_path / "g2"
    g2.mkdir()
    assert epoch_mod.resolve_epoch_path({"NFM_G2_VAR_DIR": str(g2)}) == g2 / "prod-deploy.epoch"
    # Missing G2 dir → ~/.nfmd fallback (HOME is pinned per-test).
    assert epoch_mod.resolve_epoch_path({"NFM_G2_VAR_DIR": str(tmp_path / "absent")}) == (
        Path(os.environ["HOME"]) / ".nfmd" / "prod-deploy.epoch"
    )


def test_concurrent_thread_mints_are_distinct(epoch_mod, tmp_path: Path):
    epoch_file = tmp_path / "prod-deploy.epoch"
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: epoch_mod.mint_epoch(epoch_file), range(16)))
    assert sorted(values) == list(range(1, 17)), "concurrent mints must serialize to distinct epochs"


def test_concurrent_process_mints_are_distinct(tmp_path: Path):
    """True multi-process contention through the CLI — the deploy-relevant
    shape (two ssh deploys racing)."""
    epoch_file = tmp_path / "prod-deploy.epoch"
    env = _subprocess_env(NFM_DEPLOY_EPOCH=str(epoch_file))
    procs = [
        subprocess.Popen(
            [sys.executable, str(EPOCH_SCRIPT), "mint"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for _ in range(6)
    ]
    values = []
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err
        values.append(int(out.strip()))
    assert sorted(values) == [1, 2, 3, 4, 5, 6]


# ===========================================================================
# D2 — the T4 lockfile CAS
# ===========================================================================


def _seed(epoch_mod, tmp_path: Path, *, epoch: str, lock: dict | None = None) -> Path:
    epoch_file = tmp_path / "prod-deploy.epoch"
    epoch_file.write_text(epoch + "\n", encoding="utf-8")
    if lock is not None:
        (tmp_path / "prod-deploy.lock").write_text(json.dumps(lock) + "\n", encoding="utf-8")
    return epoch_file


def test_lock_acquire_legit_no_prior_lock(epoch_mod, tmp_path: Path):
    epoch_file = _seed(epoch_mod, tmp_path, epoch="5")
    lock_path = tmp_path / "prod-deploy.lock"
    code, decision, reason, context = epoch_mod.lock_acquire(
        lock_path, DEPLOY_SHA, pid=os.getpid(), epoch_path=epoch_file
    )
    assert (code, decision, reason) == (0, "acquire", "no-prior-lock")
    assert context["epoch_minted"] == 6
    held = json.loads(lock_path.read_text(encoding="utf-8"))
    assert held["epoch"] == 6 and held["deploy_sha"] == DEPLOY_SHA
    assert epoch_mod.read_epoch(epoch_file) == 6


def test_lock_refuse_shadow_still_takes_lock(epoch_mod, tmp_path: Path):
    """Shadow mode (default): a fresh lock with a live pid holding an epoch
    >= ours is REFUSED in the log but the lock is still taken — byte-for-byte
    today's blind-overwrite behavior, now epoch-tagged."""
    epoch_file = _seed(
        epoch_mod,
        tmp_path,
        epoch="5",
        lock={"epoch": 5, "pid": os.getpid(), "deploy_sha": "other", "started": "2026-09-28T00:00:00+00:00"},
    )
    lock_path = tmp_path / "prod-deploy.lock"
    code, decision, reason, _context = epoch_mod.lock_acquire(
        lock_path, DEPLOY_SHA, pid=os.getpid(), epoch_path=epoch_file
    )
    assert code == 0  # shadow: never blocks the deploy
    assert decision == "refuse"
    assert reason == "fresh-lock-live-pid-ge-epoch"
    held = json.loads(lock_path.read_text(encoding="utf-8"))
    assert held["epoch"] == 6 and held["deploy_sha"] == DEPLOY_SHA


def test_lock_refuse_enforced_exits_80_and_preserves_holder(epoch_mod, tmp_path: Path):
    epoch_file = _seed(
        epoch_mod,
        tmp_path,
        epoch="5",
        lock={"epoch": 5, "pid": os.getpid(), "deploy_sha": "holder", "started": "2026-09-28T00:00:00+00:00"},
    )
    lock_path = tmp_path / "prod-deploy.lock"
    before = lock_path.read_bytes()
    code, decision, reason, _context = epoch_mod.lock_acquire(
        lock_path, DEPLOY_SHA, pid=os.getpid(), enforce=True, epoch_path=epoch_file,
    )
    assert (code, decision, reason) == (LOCK_REFUSE_EXIT, "enforced-refuse", "fresh-lock-live-pid-ge-epoch")
    assert lock_path.read_bytes() == before, "an enforced refusal must NOT touch the holder's lock"


def test_lock_cli_enforced_refuse_exit_code(tmp_path: Path):
    epoch_file = tmp_path / "prod-deploy.epoch"
    epoch_file.write_text("5\n", encoding="utf-8")
    lock_path = tmp_path / "prod-deploy.lock"
    lock_path.write_text(
        json.dumps({"epoch": 5, "pid": os.getpid(), "deploy_sha": "holder"}) + "\n",
        encoding="utf-8",
    )
    result = _run_cli(
        ["lock-acquire", "--lock", str(lock_path), "--sha", DEPLOY_SHA, "--enforce"],
        NFM_DEPLOY_EPOCH=str(epoch_file),
    )
    assert result.returncode == LOCK_REFUSE_EXIT
    assert "EPOCH_MINTED=6" in result.stdout
    assert "LOCK_DECISION=enforced-refuse" in result.stdout
    assert "LOCK_REASON=fresh-lock-live-pid-ge-epoch" in result.stdout
    assert "deploy_epoch_lock: " in result.stdout  # AC6 observability line
    assert json.loads(lock_path.read_text(encoding="utf-8"))["epoch"] == 5


def test_lock_overwrite_paths_stale_deadpid_olderholder(epoch_mod, tmp_path: Path):
    # Stale lock (mtime beyond --max-age) → acquire even at equal epoch.
    epoch_file = _seed(
        epoch_mod,
        tmp_path,
        epoch="5",
        lock={"epoch": 9, "pid": os.getpid(), "deploy_sha": "holder", "started": "x"},
    )
    lock_path = tmp_path / "prod-deploy.lock"
    old = time.time() - 9000  # > 7200s default window
    os.utime(lock_path, (old, old))
    code, decision, reason, _ctx = epoch_mod.lock_acquire(lock_path, DEPLOY_SHA, pid=os.getpid(), epoch_path=epoch_file)
    assert (code, decision, reason) == (0, "acquire", "stale-lock")

    # Dead pid → acquire (a crashed deploy's lock is not a fence).
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=30)
    epoch_file = _seed(epoch_mod, tmp_path, epoch="5", lock={"epoch": 99, "pid": dead.pid, "deploy_sha": "h"})
    code, decision, reason, _ctx = epoch_mod.lock_acquire(lock_path, DEPLOY_SHA, pid=os.getpid(), epoch_path=epoch_file)
    assert (code, decision, reason) == (0, "acquire", "dead-pid")

    # Live pid but an OLDER epoch → acquire (pre-fencing leftover holder).
    epoch_file = _seed(
        epoch_mod,
        tmp_path,
        epoch="5",
        lock={"epoch": 1, "pid": os.getpid(), "deploy_sha": "h", "started": "x"},
    )
    code, decision, reason, _ctx = epoch_mod.lock_acquire(lock_path, DEPLOY_SHA, pid=os.getpid(), epoch_path=epoch_file)
    assert (code, decision, reason) == (0, "acquire", "older-epoch-holder")

    # Unparseable prior lock content → treated as no prior lock.
    lock_path.write_text("{{{not json", encoding="utf-8")
    code, decision, reason, _ctx = epoch_mod.lock_acquire(lock_path, DEPLOY_SHA, pid=os.getpid(), epoch_path=epoch_file)
    assert (code, decision, reason) == (0, "acquire", "no-prior-lock")


# ===========================================================================
# D3 — the T1 marker predicate + additive event emission
# ===========================================================================


def _attests(marker: str, run_epoch: str) -> subprocess.CompletedProcess[str]:
    script = f'. "{DEPLOY_EVENT_SH}"; deploy_event_marker_attests "{marker}" "{run_epoch}"'
    return subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True, timeout=30)


def test_marker_attests_true_only_on_exact_epoch_match(tmp_path: Path):
    marker = tmp_path / "nfmd_prod_health_passed"
    marker.write_text('{"epoch": 42, "sha": "abc1234"}\n', encoding="utf-8")
    ok = _attests(str(marker), "42")
    assert ok.stdout.strip() == "true"
    stale = _attests(str(marker), "43")
    assert stale.stdout.strip() == "false"
    assert "STALE MARKER" in stale.stderr


def test_marker_attests_false_on_missing_legacy_or_garbage(tmp_path: Path):
    marker = tmp_path / "nfmd_prod_health_passed"
    # Missing marker.
    assert _attests(str(marker), "7").stdout.strip() == "false"
    # Pre-fencing marker (empty, from `touch`) — no epoch member.
    marker.touch()
    result = _attests(str(marker), "7")
    assert result.stdout.strip() == "false"
    assert "no parseable epoch" in result.stderr
    # Garbage content.
    marker.write_text("torn write \x00\x01", encoding="utf-8", errors="surrogateescape")
    assert _attests(str(marker), "7").stdout.strip() == "false"


def test_marker_attests_false_when_run_epoch_unknown(tmp_path: Path):
    """The conservative root: no run epoch (deploy died before minting /
    anchor lost) → the marker can never attest, whatever it holds."""
    marker = tmp_path / "nfmd_prod_health_passed"
    marker.write_text('{"epoch": 42, "sha": "abc1234"}\n', encoding="utf-8")
    result = _attests(str(marker), "")
    assert result.stdout.strip() == "false"
    assert "run epoch unknown" in result.stderr


def _fake_docker_bin(tmp_path: Path) -> str:
    """Install the deploy-capable fake docker on a private PATH; returns the
    bin dir. Every subprocess that may reach `docker` (recorder, drift
    checker) MUST run with this first on PATH — the real daemon is never
    touched from this suite."""
    bin_dir = tmp_path / "docker-bin"
    bin_dir.mkdir(exist_ok=True)
    docker_shim = bin_dir / "docker"
    docker_shim.write_text(DEPLOY_DOCKER, encoding="utf-8")
    docker_shim.chmod(0o755)
    return str(bin_dir)


def _emit(tmp_path: Path, *extra_args: str) -> dict:
    events = tmp_path / f"events-{os.urandom(4).hex()}.jsonl"
    args = " ".join(
        [
            "--environment production",
            "--triggered-by tester",
            "--commit-sha abc1234def5678",
            "--first-pass-success true",
            "--health-gate-first-poll-passed true",
            "--rollback-triggered false",
            "--skip-flag-used false",
            "--duration-ms 42",
            "--health-status ok",
            *extra_args,
        ]
    )
    script = (
        f'. "{DEPLOY_EVENT_SH}"; export NFMD_DEPLOY_EVENTS_PATH="{events}"; '
        f'export NFMD_SYNC_BIN=/bin/true; deploy_event_emit {args}'
    )
    proc = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    lines = events.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    return lines[0], json.loads(lines[0])


def test_deploy_event_epoch_is_additive_and_last(tmp_path: Path):
    raw, obj = _emit(tmp_path, "--deploy-epoch", "7")
    assert obj["deploy_epoch"] == 7
    # The frozen §3.1 order is untouched; deploy_epoch appends LAST.
    assert list(obj)[:-1] == SCHEMA_FIELD_ORDER
    assert list(obj)[-1] == "deploy_epoch"
    assert raw.rstrip("\n").endswith(',"health_status":"ok","deploy_epoch":7}')


def test_deploy_event_without_or_malformed_epoch_keeps_legacy_shape(tmp_path: Path):
    for extra in ([], ["--deploy-epoch", ""], ["--deploy-epoch", "seven"]):
        _, obj = _emit(tmp_path, *extra)
        assert list(obj) == SCHEMA_FIELD_ORDER, f"legacy shape must not gain fields for {extra}"
        assert "deploy_epoch" not in obj


def test_epoch_event_line_passes_prod_collector_validation(collector_mod, tmp_path: Path):
    """The REAL collector's fragment gate accepts the epoch-augmented line —
    additive fields must not break §3.1 validation (missing-fields-only)."""
    text, _ = _emit(tmp_path, "--deploy-epoch", "9")
    validated = collector_mod.validate_fragment(12345, text)
    assert validated[0]["deploy_epoch"] == 9


# ===========================================================================
# D2/D3 integration — the REAL deploy_prod.sh, hermetically
# ===========================================================================


def _container(service: str, image_id: str, *, project: str = COMPOSE_PROJECT) -> dict:
    return {
        "Id": f"cid-{service}-{image_id[:6]}",
        "Name": f"/{project}-{service}",
        "Config": {
            "Image": f"nucpot-prod-{service}:{image_id[:7]}",
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": service,
            },
        },
        "Image": f"sha256:{image_id}",
        "RepoDigests": [],
    }


def _prod_state() -> dict:
    return {
        "containers": {
            f"{COMPOSE_PROJECT}-api": _container("api", "a" * 64),
            f"{COMPOSE_PROJECT}-web": _container("web", "b" * 64),
            f"{COMPOSE_PROJECT}-db": _container("db", "e" * 64),
        }
    }


class DeployHost:
    """Fake host + gate dir + PATH shims for a full deploy_prod.sh run."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.repo = self.home / "Projects" / "nucpot"
        (self.repo / "docker").mkdir(parents=True)
        (self.repo / "tools" / "post-deploy-cutover-assert").mkdir(parents=True)
        (self.repo / "tools" / "prod-tag-retention").mkdir(parents=True)
        (self.repo / "scripts").mkdir()
        shutil.copy(SCRIPTS_DIR / "record_deploy_manifest.py", self.repo / "scripts")
        shutil.copy(SCRIPTS_DIR / "check_prod_image_tag.py", self.repo / "scripts")
        for script_name in ("prod_migrate.sh",):
            stub = self.repo / "scripts" / script_name
            stub.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            stub.chmod(0o755)
        for stub_path in (
            self.repo / "tools" / "post-deploy-cutover-assert" / "assert.sh",
            self.repo / "tools" / "prod-tag-retention" / "prune.sh",
        ):
            stub_path.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            stub_path.chmod(0o755)
        (self.repo / "docker" / ".env.prod").write_text("PROD_IMAGE_TAG=latest\n", encoding="utf-8")
        (self.repo / "docker-compose.prod.yml").write_text("# hermetic stub\n", encoding="utf-8")

        self.gate_var = tmp_path / "gate-var"
        self.gate_var.mkdir()
        self.calls_log = tmp_path / "deploy-docker-calls.log"
        self.state_path = tmp_path / "deploy-state.json"
        self.state_path.write_text(json.dumps(_prod_state()), encoding="utf-8")

        bin_dir = tmp_path / "deploybin"
        bin_dir.mkdir()
        docker_shim = bin_dir / "docker"
        docker_shim.write_text(DEPLOY_DOCKER, encoding="utf-8")
        docker_shim.chmod(0o755)

        def shim(name: str, body: str) -> None:
            target = bin_dir / name
            target.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
            target.chmod(0o755)

        shim("git", f'if [ "$1" = "rev-parse" ]; then printf "%s\\n" "{DEPLOY_SHA}"; exit 0; fi\nexit 1')
        shim("id", 'printf "nfmdeploy\\n"')  # the gated deploy-identity branch
        shim("curl", "exit 0")
        shim("sleep", "exit 0")

        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FAKE_DOCKER_STATE", str(self.state_path))
        monkeypatch.setenv("DEPLOY_DOCKER_CALLS", str(self.calls_log))
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("DEPLOY_SHA", DEPLOY_SHA)
        monkeypatch.setenv("PROXY_PORT", "7897")
        monkeypatch.setenv("DEPLOY_ACTOR", "gh-runner:lwj04")
        monkeypatch.setenv("NFM_G2_DEPLOY_IDENTITY", "1")
        monkeypatch.setenv("NFM_G2_VAR_DIR", str(self.gate_var))
        # NFM-4807: sandbox the DOCKER_CONFIG wiring away from the shared /tmp literal.
        monkeypatch.setenv("NFMD_DOCKER_CONFIG", str(tmp_path / "dc-config"))

    @property
    def epoch_file(self) -> Path:
        return self.gate_var / "prod-deploy.epoch"

    @property
    def lock_file(self) -> Path:
        return self.gate_var / "prod-deploy.lock"

    @property
    def manifest_file(self) -> Path:
        return self.gate_var / "prod-deploy-manifest.json"

    @property
    def marker_file(self) -> Path:
        return self.home / ".nfmd" / "nfmd_prod_health_passed"

    def run(self, *, enforce: bool = False, actor: str | None = "gh-runner:lwj04") -> subprocess.CompletedProcess[str]:
        env = _subprocess_env(
            HOME=str(self.home),
            FAKE_DOCKER_STATE=str(self.state_path),
            DEPLOY_DOCKER_CALLS=str(self.calls_log),
            DEPLOY_SHA=DEPLOY_SHA,
            PROXY_PORT="7897",
            NFM_G2_DEPLOY_IDENTITY="1",
            NFM_G2_VAR_DIR=str(self.gate_var),
            NFMD_DOCKER_CONFIG=str(self.tmp / "dc-config"),
        )
        if actor is not None:
            env["DEPLOY_ACTOR"] = actor
        if enforce:
            env["NFM_DEPLOY_LOCK_ENFORCE"] = "1"
        return subprocess.run(
            ["bash", str(DEPLOY_PROD_SH)],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )


def test_full_deploy_mints_epoch_marks_marker_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = DeployHost(tmp_path, monkeypatch)
    shared_dc_before = _shared_docker_config_snapshot()

    # Poison the marker with a STALE epoch first: the run must scrub it and
    # write its own epoch — and the predicate must never attest the stale
    # one against a different run's epoch (AC3).
    host.marker_file.parent.mkdir(parents=True, exist_ok=True)
    host.marker_file.write_text('{"epoch": 0, "sha": "stale"}\n', encoding="utf-8")

    result = host.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"DEPLOY_SCRIPT_COMPLETED_OK sha={DEPLOY_SHA}" in result.stdout

    # D2: minted + logged (AC6), epoch file persisted at the canonical dir.
    assert "DEPLOY_EPOCH_MINTED=1" in result.stdout
    assert "==> deploy epoch 1 minted; lock decision: acquire" in result.stdout
    assert host.epoch_file.read_text(encoding="utf-8").strip() == "1"
    assert not host.lock_file.exists(), "lock must be trap-removed"

    # The lock was held across the compose cutover.
    assert "lock_present=True" in host.calls_log.read_text(encoding="utf-8")

    # D3: the marker carries THIS run's epoch + sha — scrubbed, not appended.
    assert host.marker_file.read_text(encoding="utf-8") == (
        json.dumps({"epoch": 1, "sha": DEPLOY_SHA}, separators=(", ", ": ")) + "\n"
    )
    assert _attests(str(host.marker_file), "1").stdout.strip() == "true"
    # AC3: the stale epoch can never attest a (hypothetical) newer run.
    assert _attests(str(host.marker_file), "2").stdout.strip() == "false"

    # The manifest carries the epoch (recorder read the epoch FILE — the
    # outside-script re-record path with fixed argv relies on this).
    manifest = json.loads(host.manifest_file.read_text(encoding="utf-8"))
    assert manifest["deploy_sha"] == DEPLOY_SHA
    assert manifest["deploy_epoch"] == 1
    assert set(manifest["service_containers"]) == {"api", "web", "db"}

    # NFM-4807: the shared /tmp DOCKER_CONFIG literal is untouched.
    assert _shared_docker_config_snapshot() == shared_dc_before

    # AC1: zero drift-checker regressions — the epoch-carrying manifest is a
    # valid in-sync baseline for the REAL checker.
    checker = subprocess.run(
        [
            sys.executable,
            str(DRIFT_SCRIPT),
            "--manifest",
            str(host.manifest_file),
            "--lock",
            str(host.lock_file),
            "--state",
            str(tmp_path / "drift-state.json"),
            "--recheck-seconds",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=_subprocess_env(
            PATH=os.environ["PATH"],
            FAKE_DOCKER_STATE=str(host.state_path),
        ),
    )
    assert checker.returncode == 0, checker.stdout + checker.stderr

    # A second deploy mints N+1 and re-binds the marker (monotonicity live).
    rerun = host.run(actor=None)  # manual run: deploy_prod.sh:<user> default
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert "DEPLOY_EPOCH_MINTED=2" in rerun.stdout
    assert host.epoch_file.read_text(encoding="utf-8").strip() == "2"
    assert json.loads(host.marker_file.read_text(encoding="utf-8"))["epoch"] == 2
    assert json.loads(host.manifest_file.read_text(encoding="utf-8"))["deploy_epoch"] == 2


def test_full_deploy_enforced_refusal_exits_80_before_cutover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = DeployHost(tmp_path, monkeypatch)
    # A fresh lock, live pid (this pytest process), epoch >= the baseline the
    # next mint would compare against — the conflict the CAS exists for.
    host.epoch_file.write_text("5\n", encoding="utf-8")
    host.lock_file.write_text(
        json.dumps({"epoch": 5, "pid": os.getpid(), "deploy_sha": "concurrent-run"}) + "\n",
        encoding="utf-8",
    )
    result = host.run(enforce=True)
    assert result.returncode == LOCK_REFUSE_EXIT
    assert "FATAL (NFM-5253): deploy REFUSED" in result.stdout + result.stderr
    # The holder's lock survives byte-for-byte; the epoch still advanced
    # (the refused run's mint is its message).
    assert json.loads(host.lock_file.read_text(encoding="utf-8"))["deploy_sha"] == "concurrent-run"
    assert host.epoch_file.read_text(encoding="utf-8").strip() == "6"
    if host.calls_log.exists():
        assert "docker compose" not in host.calls_log.read_text(encoding="utf-8"), (
            "an enforced refusal happens at the lock, BEFORE any compose mutation"
        )


def test_full_deploy_shadow_conflict_still_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Shadow mode default: same fresh-lock/live-pid conflict, no enforce
    flag → the deploy logs the refusal warning and completes as today."""
    host = DeployHost(tmp_path, monkeypatch)
    host.epoch_file.write_text("5\n", encoding="utf-8")
    host.lock_file.write_text(
        json.dumps({"epoch": 5, "pid": os.getpid(), "deploy_sha": "concurrent-run"}) + "\n",
        encoding="utf-8",
    )
    result = host.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEPLOY_EPOCH_MINTED=6" in result.stdout
    assert "lock decision: refuse" in result.stdout
    assert "WARNING (NFM-5253 shadow)" in result.stdout
    assert f"DEPLOY_SCRIPT_COMPLETED_OK sha={DEPLOY_SHA}" in result.stdout


# ===========================================================================
# D4 — record_rollback.sh
# ===========================================================================


def _run_rollback(tmp_path: Path, *args: str, epoch_override: str | None = None) -> subprocess.CompletedProcess[str]:
    gate_var = tmp_path / "gate-var"
    gate_var.mkdir(exist_ok=True)
    env = _subprocess_env(
        HOME=str(tmp_path / "home"),
        PATH=f"{_fake_docker_bin(tmp_path)}{os.pathsep}{os.environ.get('PATH', '')}",
        FAKE_DOCKER_STATE=str(tmp_path / "docker-state.json"),
        NFM_G2_VAR_DIR=str(gate_var),
    )
    if epoch_override is not None:
        env["NFM_DEPLOY_EPOCH"] = epoch_override
    return subprocess.run(
        ["bash", str(RECORD_ROLLBACK_SH), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_record_rollback_mints_records_and_emits(tmp_path: Path):
    state_path = tmp_path / "docker-state.json"
    state_path.write_text(json.dumps(_prod_state()), encoding="utf-8")
    tag = "4273a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9"

    result = _run_rollback(tmp_path, "--tag", tag, "--reason", "canary regression")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"ROLLBACK_RECORDED_OK tag={tag} epoch=1" in result.stdout
    assert "DEPLOY_EPOCH_MINTED=1" in result.stdout

    gate_var = tmp_path / "gate-var"
    assert (gate_var / "prod-deploy.epoch").read_text(encoding="utf-8").strip() == "1"
    manifest = json.loads((gate_var / "prod-deploy-manifest.json").read_text(encoding="utf-8"))
    assert manifest["deploy_sha"] == tag
    assert manifest["deploy_epoch"] == 1
    assert manifest["actor"].startswith("record_rollback.sh:")

    events = tmp_path / "home" / ".nfmd" / "master-deploy-events.jsonl"
    line = events.read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["rollback_triggered"] is True
    assert event["deploy_epoch"] == 1
    assert event["commit_sha"] == tag
    assert event["environment"] == "production"


def test_record_rollback_event_line_passes_collector_validation(
    collector_mod, tmp_path: Path
):
    state_path = tmp_path / "docker-state.json"
    state_path.write_text(json.dumps(_prod_state()), encoding="utf-8")
    assert _run_rollback(tmp_path, "--tag", "abc1234def5678").returncode == 0
    events = tmp_path / "home" / ".nfmd" / "master-deploy-events.jsonl"
    validated = collector_mod.validate_fragment(999, events.read_text(encoding="utf-8"))
    assert validated[0]["rollback_triggered"] is True


def test_record_rollback_usage_validation(tmp_path: Path):
    state_path = tmp_path / "docker-state.json"
    state_path.write_text(json.dumps(_prod_state()), encoding="utf-8")
    assert _run_rollback(tmp_path).returncode == 1  # --tag required
    assert _run_rollback(tmp_path, "--tag", "zzznot-hex").returncode == 1
    assert _run_rollback(tmp_path, "--tag", "abc123").returncode == 1  # < 7 chars


def test_record_rollback_mint_failure_is_fatal(tmp_path: Path):
    """Epoch/manifest failure → rc 2, loud: the drift alarm firing on the
    unrecorded rollback is the documented backstop."""
    state_path = tmp_path / "docker-state.json"
    state_path.write_text(json.dumps(_prod_state()), encoding="utf-8")
    result = _run_rollback(
        tmp_path, "--tag", "abc1234def5678", epoch_override=str(tmp_path)  # a DIRECTORY
    )
    assert result.returncode == 2
    assert "epoch mint failed" in result.stderr


# ===========================================================================
# D5 — drift checker epoch-aware stand-down (shadow logging only)
# ===========================================================================


def _drift_env(tmp_path: Path, *, manifest_epoch: int | None, lock: dict | None) -> None:
    state = _prod_state()
    # Diverge live state from the manifest: rebuild api's image id.
    state["containers"][f"{COMPOSE_PROJECT}-api"] = _container("api", "f" * 64)
    state_path = tmp_path / "docker-state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    manifest = {
        "deploy_sha": "8e29e906d1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6",
        "image_tags": {},
        "image_digests": {},
        "service_containers": {},
        "timestamp": "2026-09-28T01:02:03+00:00",
        "actor": "deploy_prod.sh:testuser",
    }
    for _name, container in sorted(_prod_state()["containers"].items()):
        service = container["Config"]["Labels"]["com.docker.compose.service"]
        manifest["image_tags"][service] = container["Config"]["Image"]
        manifest["image_digests"][service] = container["Image"]
        manifest["service_containers"][service] = container["Name"].lstrip("/")
    if manifest_epoch is not None:
        manifest["deploy_epoch"] = manifest_epoch
    (tmp_path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    lock_path = tmp_path / "prod-deploy.lock"
    if lock is not None:
        lock_path.write_text(json.dumps(lock) + "\n", encoding="utf-8")


def _run_drift(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(DRIFT_SCRIPT),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--lock",
            str(tmp_path / "prod-deploy.lock"),
            "--state",
            str(tmp_path / "drift-state.json"),
            "--recheck-seconds",
            "0",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=_subprocess_env(
            PATH=f"{_fake_docker_bin(tmp_path)}{os.pathsep}{os.environ.get('PATH', '')}",
            FAKE_DOCKER_STATE=str(tmp_path / "docker-state.json"),
        ),
    )


def test_drift_stand_down_logs_would_stand_down_true(tmp_path: Path):
    """lock.epoch > manifest.epoch → the FUTURE rule agrees with today's
    fresh-lock stand-down; shadow log says true."""
    _drift_env(tmp_path, manifest_epoch=7, lock={"epoch": 9, "pid": 1})
    result = _run_drift(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "==> epoch fencing (NFM-5253 shadow): lock.epoch=9 manifest.epoch=7" in result.stdout
    assert "would_stand_down=true (lock epoch 9 is ahead of manifest epoch 7)" in result.stdout
    assert "NOT enforced — standing down per the fresh-lock rule" in result.stdout


def test_drift_stand_down_logs_would_stand_down_false_but_behavior_unchanged(tmp_path: Path):
    """A lock at-or-behind the manifest epoch is leftover/stale — the future
    rule would NOT stand down. Shadow mode: logged, and the checker still
    stands down (current behavior) until the enforcement flip."""
    _drift_env(tmp_path, manifest_epoch=7, lock={"epoch": 7, "pid": 1})
    result = _run_drift(tmp_path)
    assert result.returncode == 0, "shadow mode must not change checker behavior"
    assert "would_stand_down=false" in result.stdout
    assert "leftover lock" in result.stdout
    assert "NOT enforced — standing down per the fresh-lock rule" in result.stdout


def test_drift_stand_down_indeterminate_on_pre_fencing_artifacts(tmp_path: Path):
    # Pre-fencing lock (no epoch member) + epoch-carrying manifest.
    _drift_env(tmp_path, manifest_epoch=7, lock={"pid": 1, "deploy_sha": "old"})
    assert "would_stand_down=indeterminate" in _run_drift(tmp_path).stdout
    # Epoch-carrying lock + pre-fencing manifest (no deploy_epoch key).
    _drift_env(tmp_path, manifest_epoch=None, lock={"epoch": 9, "pid": 1})
    assert "would_stand_down=indeterminate" in _run_drift(tmp_path).stdout


# ===========================================================================
# wiring guards — workflow, deploy_prod.sh, runbook
# ===========================================================================


def test_deploy_prod_sh_lock_lifecycle_preserved():
    text = DEPLOY_PROD_SH.read_text(encoding="utf-8")
    assert 'trap \'rm -f "$NFM_DEPLOY_LOCK"\' EXIT' in text, (
        "the CAS must not drop the trap-removal lifecycle (a crashed deploy must alarm)"
    )
    assert subprocess.run(["bash", "-n", str(DEPLOY_PROD_SH)], capture_output=True).returncode == 0


def test_workflow_wires_epoch_anchor_predicate_and_tests():
    import yaml

    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = [step for job in doc["jobs"].values() for step in job.get("steps", [])]
    runs = [step.get("run") or "" for step in steps]

    # The deploy step greps the ssh-stdout anchor into GITHUB_OUTPUT.
    deploy_steps = [s for s in steps if s.get("id") == "deploy"]
    assert deploy_steps, "the deploy step must keep id: deploy (the emit step reads its output)"
    deploy_run = deploy_steps[0].get("run") or ""
    assert "DEPLOY_EPOCH_MINTED=" in deploy_run and "GITHUB_OUTPUT" in deploy_run
    assert "epoch=" in deploy_run

    # The emit step gates FIRST_POLL on the marker predicate + passes the
    # epoch additively.
    emit_runs = [r for r in runs if "deploy_event_marker_attests" in r]
    assert emit_runs, "the emit step must call deploy_event_marker_attests"
    assert any("steps.deploy.outputs.epoch" in r for r in emit_runs)
    assert any("--deploy-epoch" in r for r in runs)

    # This suite runs pre-deploy.
    assert any("pytest" in r and "scripts/tests/test_deploy_epoch.py" in r for r in runs), (
        "the pre-deploy pytest gate must execute the deploy-epoch suite"
    )


def test_runbook_references_record_rollback():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "record_rollback.sh" in text, (
        "the rollback runbook must reference record_rollback.sh (D4 acceptance)"
    )
    assert "NFM-5253" in text
