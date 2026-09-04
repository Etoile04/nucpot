"""Tests for scripts/check_deploy_drift.py — ADR-013 §2 G4b drift alarm (NFM-4272).

Incident context (NFM-4264, 2026-09-04): a desktop-agent session ran
host-side ``docker compose up -d --build`` against prod, bypassing every
path-based control with zero audit trail (~6h attribution cost). G4a
(NFM-4271) makes each SANCTIONED deploy record a manifest:
``{deploy_sha, image_tags, image_digests, service_containers, timestamp,
actor}`` (field names FROZEN). This checker (G4b, Hermes cron) diffs live
``docker inspect`` state against that manifest and auto-files an SRE issue
titled ``[DEPLOY-DRIFT] …`` on divergence.

Digest precedence contract (must match the recorder exactly):
``RepoDigests[0]`` when non-empty, else the container's image-ID digest
(``.Image``). Prod images are built on the host, so most entries are
image-ID digests — a fresh rebuild of the same tag mints a new one, which is
exactly the NFM-4264 detection signal.

Paperclip filing is exercised END-TO-END against an in-process stub HTTP
server (the "test target" of the --selftest acceptance criterion): the same
code path, real HTTP, canned responses.
"""

import json
import os
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent
SCRIPT = SCRIPTS_DIR / "check_deploy_drift.py"
DEPLOY_PROD_SH = SCRIPTS_DIR / "deploy_prod.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production-deployment.yml"

COMPOSE_PROJECT = "nucpot-prod"
SRE_AGENT_ID = "2ee2415b-e43e-4806-888f-c231e60facaf"
COMPANY_ID = "ec7c0ded-0000-4002-8d0c-672597244875"

# ---------------------------------------------------------------- fixtures


FAKE_DOCKER = """\
#!/usr/bin/env python3
""" + '"""' + """Fake docker CLI for drift-checker tests (NFM-4272).

Implements only what scripts/check_deploy_drift.py calls (identical CLI
surface to the G4a recorder's shim):
  ps --filter label=com.docker.compose.project=<p> --format {{.Names}}
  inspect <name>
Container state comes from the FAKE_DOCKER_STATE JSON file; set
"ps_fail": true or a container's "inspect_fail" to simulate CLI errors.
""" + '"""' + """

import json
import os
import sys


def main() -> int:
    state_path = os.environ.get("FAKE_DOCKER_STATE")
    if not state_path:
        print("fake-docker: FAKE_DOCKER_STATE unset", file=sys.stderr)
        return 64
    with open(state_path, encoding="utf-8") as fh:
        state = json.load(fh)

    args = sys.argv[1:]

    if args[:1] == ["ps"]:
        if state.get("ps_fail"):
            print("Error response from daemon:", file=sys.stderr)
            return 1
        label_filter = None
        has_names_format = False
        rest = iter(args[1:])
        for arg in rest:
            if arg == "--filter":
                label_filter = next(rest, "")
            elif arg.startswith("--filter="):
                label_filter = arg.split("=", 1)[1]
            elif arg == "--format":
                next(rest, None)
                has_names_format = True
            elif arg.startswith("--format="):
                has_names_format = True
        if not label_filter or not label_filter.startswith("label=") or not has_names_format:
            print(f"fake-docker: unsupported ps invocation: {args}", file=sys.stderr)
            return 64
        _, _, label_value = label_filter.partition("=")
        _, _, project = label_value.partition("=")
        for name, container in sorted(state["containers"].items()):
            labels = (container.get("Config") or {}).get("Labels") or {}
            if labels.get("com.docker.compose.project") == project:
                print(name)
        return 0

    if args[:1] == ["inspect"]:
        name = args[1]
        container = state["containers"].get(name)
        if container is None or container.get("inspect_fail"):
            print(f"Error: No such object: {name}", file=sys.stderr)
            return 1
        print(json.dumps([container]))
        return 0

    print(f"fake-docker: unsupported invocation: {args}", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
"""


def _container(service: str, image_id: str, *, repo_digests: list[str] | None = None,
               repo_tag: str | None = None, name: str | None = None,
               project: str = COMPOSE_PROJECT) -> dict:
    """One container in the real `docker inspect` shape (labels nested under
    Config.Labels — verified against the live prod daemon 2026-09-04)."""
    tag = repo_tag or f"nucpot-prod-{service}:{image_id[:7]}"
    return {
        "Id": f"cid-{service}-{image_id[:6]}",
        "Name": f"/{name or f'{project}-{service}'}",
        "Config": {
            "Image": tag,
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": service,
            },
        },
        "Image": f"sha256:{image_id}",
        "RepoDigests": repo_digests or [],
    }


def expected_digest(container: dict) -> str:
    """The FROZEN digest-precedence rule — mirrors the G4a recorder."""
    repo_digests = container.get("RepoDigests") or []
    return str(repo_digests[0] if repo_digests else (container.get("Image") or ""))


def prod_containers() -> dict:
    """Canonical running prod state: 6 services of compose project nucpot-prod.

    db/redis are pulled images (RepoDigests populated); api/worker share one
    locally built image; lightrag/web are locally built (RepoDigests empty —
    the real prod deploy builds these). Plus one preview-overlay container
    that the label filter must exclude (different compose project).
    """
    return {
        f"{COMPOSE_PROJECT}-api": _container("api", "a" * 64),
        f"{COMPOSE_PROJECT}-worker": _container("worker", "a" * 64),
        f"{COMPOSE_PROJECT}-web": _container("web", "b" * 64),
        f"{COMPOSE_PROJECT}-lightrag": _container("lightrag", "c" * 64),
        f"{COMPOSE_PROJECT}-db": _container(
            "db", "e" * 64,
            repo_digests=["pgvector/pgvector@sha256:" + "d" * 64],
            repo_tag="pgvector/pgvector:pg16",
        ),
        f"{COMPOSE_PROJECT}-redis": _container(
            "redis", "9" * 64,
            repo_digests=["redis@sha256:" + "f" * 64],
            repo_tag="redis:7-alpine",
        ),
        f"{COMPOSE_PROJECT}-preview-api": _container(
            "api", "7" * 64, project=f"{COMPOSE_PROJECT}-preview",
            name=f"{COMPOSE_PROJECT}-preview-api",
        ),
    }


def manifest_from(containers: dict) -> dict:
    """Build a G4a-contract manifest from untampered container state."""
    manifest = {
        "deploy_sha": "8e29e906d1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6",
        "image_tags": {},
        "image_digests": {},
        "service_containers": {},
        "timestamp": "2026-09-04T01:02:03+00:00",
        "actor": "deploy_prod.sh:testuser",
    }
    for name, container in sorted(containers.items()):
        service = container["Config"]["Labels"]["com.docker.compose.service"]
        if container["Config"]["Labels"]["com.docker.compose.project"] != COMPOSE_PROJECT:
            continue
        manifest["image_tags"][service] = container["Config"]["Image"]
        manifest["image_digests"][service] = expected_digest(container)
        manifest["service_containers"][service] = container["Name"].lstrip("/")
    return manifest


class StubPaperclip:
    """In-process Paperclip stand-in: records every request, serves the four
    endpoints the checker uses, backs issues in memory. The 'test target'."""

    def __init__(self):
        self.journal: list[dict] = []
        self.issues: dict[str, dict] = {}
        self.seq = 9000
        handler = self._make_handler()

        class Server(ThreadingHTTPServer):
            pass

        self.server = Server(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence test output
                pass

            def _send(self, payload, code=200):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode() if length else "{}"
                path = urlparse(self.path).path
                outer.journal.append(
                    {"method": "POST", "path": path, "body": json.loads(raw)}
                )
                if path == f"/api/companies/{COMPANY_ID}/issues":
                    outer.seq += 1
                    issue = {
                        "id": f"uuid-{outer.seq}",
                        "identifier": f"NFM-{outer.seq}",
                        "status": "todo",
                        **json.loads(raw),
                    }
                    outer.issues[issue["id"]] = issue
                    self._send(issue, 201)
                    return
                if path.endswith("/comments"):
                    uuid = path.split("/")[-2]
                    if uuid in outer.issues:
                        self._send({"ok": True})
                    else:
                        self._send({"error": "not found"}, 404)
                    return
                self._send({"error": "unsupported"}, 404)

            def do_GET(self):
                path = urlparse(self.path).path
                outer.journal.append({"method": "GET", "path": path, "body": None})
                if path == f"/api/companies/{COMPANY_ID}/issues":
                    self._send(list(outer.issues.values()))
                    return
                parts = path.strip("/").split("/")
                if len(parts) == 3 and parts[0] == "api" and parts[1] == "issues":
                    issue = outer.issues.get(parts[2])
                    self._send(issue if issue else {"error": "not found"},
                               200 if issue else 404)
                    return
                self._send({"error": "unsupported"}, 404)

        return Handler

    def set_status(self, uuid: str, status: str) -> None:
        self.issues[uuid]["status"] = status

    def creates(self) -> list[dict]:
        return [r for r in self.journal
                if r["method"] == "POST" and r["path"].endswith("/issues")]

    def comments(self) -> list[dict]:
        return [r for r in self.journal
                if r["method"] == "POST" and r["path"].endswith("/comments")]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def stub_paperclip():
    stub = StubPaperclip()
    yield stub
    stub.close()


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Install a state-driven fake `docker` on PATH; return a state writer."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_shim = bin_dir / "docker"
    docker_shim.write_text(FAKE_DOCKER, encoding="utf-8")
    docker_shim.chmod(0o755)
    state_path = tmp_path / "docker-state.json"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    def _load(containers: dict, **extra: object) -> Path:
        state_path.write_text(
            json.dumps({"containers": containers, **extra}), encoding="utf-8"
        )
        monkeypatch.setenv("FAKE_DOCKER_STATE", str(state_path))
        return state_path

    return _load


class DriftEnv:
    """Bundles the checker's tmp paths + stub API into one handle."""

    def __init__(self, tmp_path: Path, stub: StubPaperclip):
        self.tmp = tmp_path
        self.stub = stub
        self.manifest = tmp_path / "manifest.json"
        self.state = tmp_path / "drift-state.json"
        self.lock = tmp_path / "prod-deploy.lock"

    def write_manifest(self, manifest: dict) -> Path:
        self.manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return self.manifest

    def run(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--manifest", str(self.manifest),
                "--state", str(self.state),
                "--lock", str(self.lock),
                "--recheck-seconds", "0",
                "--paperclip-url", self.stub.url,
                "--paperclip-key", "test-key",
                "--company-id", COMPANY_ID,
                *extra,
            ],
            capture_output=True, text=True, timeout=60,
        )


@pytest.fixture
def env(tmp_path: Path, stub_paperclip: StubPaperclip) -> DriftEnv:
    handle = DriftEnv(tmp_path, stub_paperclip)
    handle.write_manifest(manifest_from(prod_containers()))
    return handle


# ------------------------------------------------------------ no-drift path

def test_in_sync_exits_zero_without_filing(fake_docker, env: DriftEnv):
    fake_docker(prod_containers())
    result = env.run()
    assert result.returncode == 0, result.stderr
    assert env.stub.creates() == []
    assert not env.state.exists()


def test_preview_overlay_containers_are_ignored(fake_docker, env: DriftEnv):
    fake_docker(prod_containers())
    result = env.run()
    assert result.returncode == 0
    assert env.stub.creates() == []


# ------------------------------------------------------- divergence filings

def test_digest_mismatch_files_sre_issue(fake_docker, env: DriftEnv):
    # NFM-4264 replay: out-of-band `up -d --build api` mints a fresh image-ID
    # digest for api under the same tag.
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-api"] = _container("api", "f" * 64)
    fake_docker(containers)

    result = env.run()
    assert result.returncode == 1, result.stderr

    creates = env.stub.creates()
    assert len(creates) == 1
    payload = creates[0]["body"]
    assert payload["assigneeAgentId"] == SRE_AGENT_ID
    assert payload["title"].startswith("[DEPLOY-DRIFT]")
    assert "api" in payload["title"]
    body = payload["description"]
    assert "sha256:" + "a" * 64 in body          # expected digest
    assert "sha256:" + "f" * 64 in body          # actual digest
    assert "deploy_prod.sh:testuser" in body     # manifest actor
    assert "2026-09-04T01:02:03" in body         # manifest timestamp
    assert "first-seen:" in body
    assert "signature: " in body                 # full sig for dedupe fallback


def test_digest_precedence_matches_recorder_rule(fake_docker, env: DriftEnv):
    # Discriminating probe for the FROZEN precedence (RepoDigests[0] else
    # .Image): db is a PULLED image whose .Image differs from the manifest
    # but whose RepoDigest is unchanged — correct precedence must NOT flag
    # db, naive .Image precedence would. api is BUILT (RepoDigests empty)
    # with a changed image id — must flag via .Image.
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-api"] = _container("api", "0" * 64)
    containers[f"{COMPOSE_PROJECT}-db"] = _container(
        "db", "z" * 64,
        repo_digests=["pgvector/pgvector@sha256:" + "d" * 64],
        repo_tag="pgvector/pgvector:pg16",
    )
    fake_docker(containers)

    result = env.run()
    assert result.returncode == 1
    body = env.stub.creates()[0]["body"]["description"]
    assert "service api" in body          # built image: .Image digest diverged
    assert "service db" not in body       # pulled image: RepoDigest unchanged → in sync


def test_manifest_service_missing_from_live_state(fake_docker, env: DriftEnv):
    containers = prod_containers()
    del containers[f"{COMPOSE_PROJECT}-web"]
    fake_docker(containers)

    result = env.run()
    assert result.returncode == 1
    body = env.stub.creates()[0]["body"]["description"]
    assert "web" in body
    assert "missing" in body.lower()


def test_live_container_absent_from_manifest(fake_docker, env: DriftEnv):
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-extra"] = _container("extra", "5" * 64)
    fake_docker(containers)

    result = env.run()
    assert result.returncode == 1
    body = env.stub.creates()[0]["body"]["description"]
    assert "extra" in body


def test_manifest_missing_files_baseline_alarm(fake_docker, env: DriftEnv, tmp_path):
    fake_docker(prod_containers())
    env.manifest = tmp_path / "does-not-exist.json"
    result = env.run()
    assert result.returncode == 1
    body = env.stub.creates()[0]["body"]["description"]
    assert "manifest" in body.lower()
    assert "missing" in body.lower()


# ------------------------------------------------------------------- dedupe

def test_repeat_divergence_comment_appends_not_refiles(fake_docker, env: DriftEnv):
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-api"] = _container("api", "f" * 64)
    fake_docker(containers)

    assert env.run().returncode == 1
    assert env.run().returncode == 1

    assert len(env.stub.creates()) == 1
    assert len(env.stub.comments()) == 1
    comment_body = env.stub.comments()[0]["body"]["body"]
    assert "still diverged" in comment_body

    state = json.loads(env.state.read_text())
    assert len(state["signatures"]) == 1


def test_new_signature_files_new_issue(fake_docker, env: DriftEnv):
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-api"] = _container("api", "f" * 64)
    fake_docker(containers)
    assert env.run().returncode == 1

    # Divergence WIDENS (web rebuilt too) → new fingerprint → new issue.
    containers[f"{COMPOSE_PROJECT}-web"] = _container("web", "6" * 64)
    fake_docker(containers)
    assert env.run().returncode == 1

    assert len(env.stub.creates()) == 2
    assert "web" in env.stub.creates()[1]["body"]["title"]
    assert len(env.stub.comments()) == 0


def test_state_loss_reconstructs_dedupe_from_api(fake_docker, env: DriftEnv):
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-api"] = _container("api", "f" * 64)
    fake_docker(containers)
    assert env.run().returncode == 1
    first_uuid = f"uuid-{env.stub.seq}"

    env.state.unlink()  # state file lost (host wipe, cron user change, …)
    assert env.run().returncode == 1

    # Dedupe fell back to searching OPEN [DEPLOY-DRIFT] issues by signature.
    assert len(env.stub.creates()) == 1
    assert len(env.stub.comments()) == 1
    commented = env.stub.comments()[0]["path"].split("/")[-2]
    assert commented == first_uuid


def test_resolved_issue_regression_files_new_issue(fake_docker, env: DriftEnv):
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-api"] = _container("api", "f" * 64)
    fake_docker(containers)
    assert env.run().returncode == 1
    env.stub.set_status(f"uuid-{env.stub.seq}", "done")  # SRE resolved it

    assert env.run().returncode == 1  # same drift persists post-resolution
    assert len(env.stub.creates()) == 2  # → NEW issue, per spec


# ------------------------------------------------ deploy-in-flight tolerance

def test_fresh_deploy_lock_suppresses_filing(fake_docker, env: DriftEnv):
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-api"] = _container("api", "f" * 64)
    fake_docker(containers)
    env.lock.write_text('{"pid": 1}\n')
    # mtime is NOW → fresh lock → sanctioned deploy in progress.

    result = env.run()
    assert result.returncode == 0, result.stderr
    assert env.stub.creates() == []
    assert not env.state.exists()


def test_stale_deploy_lock_does_not_suppress(fake_docker, env: DriftEnv):
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-api"] = _container("api", "f" * 64)
    fake_docker(containers)
    env.lock.write_text('{"pid": 1}\n')
    three_hours = 3 * 60 * 60
    os.utime(env.lock, (time.time() - three_hours, time.time() - three_hours))

    result = env.run()
    assert result.returncode == 1
    assert len(env.stub.creates()) == 1


def test_diverged_then_converged_during_recheck_window(fake_docker, env: DriftEnv):
    # Live state starts TAMPERED (old digest mid-deploy); converges (deploy
    # finished, manifest-matching container running) while the checker waits
    # out its 1s re-check window → tolerated, no issue filed (AC-G4b.2).
    state_path = fake_docker(prod_containers())
    converged = prod_containers()

    holder: dict = {}

    def run_checker() -> None:
        holder["proc"] = env.run("--recheck-seconds", "1")

    # Start from tampered live state so pass 1 genuinely diverges.
    tampered = prod_containers()
    tampered[f"{COMPOSE_PROJECT}-api"] = _container("api", "f" * 64)
    state_path.write_text(json.dumps({"containers": tampered}), encoding="utf-8")

    thread = threading.Thread(target=run_checker)
    thread.start()
    time.sleep(0.3)
    state_path.write_text(json.dumps({"containers": converged}), encoding="utf-8")
    thread.join(timeout=30)

    proc = holder["proc"]
    assert proc.returncode == 0, proc.stderr
    assert env.stub.creates() == []
    assert not env.state.exists()


# ------------------------------------------------------------ failure modes

def test_docker_ps_failure_is_operational_error(fake_docker, env: DriftEnv, tmp_path):
    state_path = fake_docker(prod_containers())
    state = json.loads(state_path.read_text())
    state["ps_fail"] = True
    state_path.write_text(json.dumps(state))

    result = env.run()
    assert result.returncode == 2
    assert env.stub.creates() == []


def test_docker_inspect_failure_is_operational_error(fake_docker, env: DriftEnv):
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-web"]["inspect_fail"] = True
    fake_docker(containers)

    result = env.run()
    assert result.returncode == 2
    assert env.stub.creates() == []


def test_paperclip_unreachable_is_operational_error(fake_docker, env: DriftEnv):
    fake_docker(prod_containers())
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-api"] = _container("api", "f" * 64)
    fake_docker(containers)

    result = env.run("--paperclip-url", "http://127.0.0.1:1")
    assert result.returncode == 2
    assert result.stderr != ""


def test_ambient_session_paperclip_env_is_refused(
        fake_docker, env: DriftEnv, monkeypatch: pytest.MonkeyPatch):
    # Regression for the 2026-09-04 false alarm: a manual run in a developer
    # shell inherited ambient PAPERCLIP_API_URL/KEY and filed a fabricated
    # divergence against REAL Paperclip (NFM-4275). The checker must take its
    # filing target ONLY from checker-specific config — ambient session creds
    # must produce an operational error, never a filing.
    monkeypatch.setenv("PAPERCLIP_API_URL", env.stub.url)
    monkeypatch.setenv("PAPERCLIP_API_KEY", "ambient-key")
    monkeypatch.delenv("NFM_DRIFT_PAPERCLIP_URL", raising=False)
    monkeypatch.delenv("NFM_DRIFT_PAPERCLIP_KEY", raising=False)
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-api"] = _container("api", "f" * 64)
    fake_docker(containers)

    result = env.run("--paperclip-url", "", "--paperclip-key", "")
    assert result.returncode == 2
    assert "NFM_DRIFT_PAPERCLIP_URL" in result.stderr
    assert env.stub.journal == []


def test_dry_run_renders_without_filing(fake_docker, env: DriftEnv):
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-api"] = _container("api", "f" * 64)
    fake_docker(containers)

    result = env.run("--dry-run")
    assert result.returncode == 1
    assert "[DEPLOY-DRIFT]" in result.stdout
    assert "sha256:" + "a" * 64 in result.stdout
    assert env.stub.journal == []
    assert not env.state.exists()


# ------------------------------------------------------------------ selftest

def test_selftest_passes(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--selftest"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELFTEST PASS" in result.stdout


# ------------------------------------------------------------------- wiring

def test_deploy_prod_sh_writes_and_clears_deploy_lock():
    source = DEPLOY_PROD_SH.read_text(encoding="utf-8")
    assert "prod-deploy.lock" in source
    assert 'trap' in source and 'rm -f' in source
    # Syntax-check the modified script.
    proc = subprocess.run(["bash", "-n", str(DEPLOY_PROD_SH)], capture_output=True)
    assert proc.returncode == 0, proc.stderr


# ------------------------------------------------- NFM-4273 canonical G4 dir


def _load_checker():
    """Import scripts/check_deploy_drift.py as a module (no side effects at
    import time — everything lives under functions / the main guard)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_deploy_drift_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # dataclass @Drift resolves cls.__module__ through sys.modules — the
    # module must be registered before exec_module runs the decorators.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_default_paths_prefer_canonical_gate_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """NFM-4273: when the host gate's canonical G4 state dir exists, BOTH the
    manifest and the lock resolve there — even before the first manifest
    exists (a stale ~/.nfmd copy would mask exactly the fork this prevents)."""
    checker = _load_checker()
    gate_dir = tmp_path / "gate-var"
    gate_dir.mkdir()
    monkeypatch.setattr(checker, "CANONICAL_G4_DIR", gate_dir)
    monkeypatch.delenv("NFM_DEPLOY_MANIFEST", raising=False)
    monkeypatch.delenv("NFM_DEPLOY_LOCK", raising=False)

    args = checker.parse_args([])
    assert str(args.manifest) == str(gate_dir / "prod-deploy-manifest.json")
    assert str(args.lock) == str(gate_dir / "prod-deploy.lock")


def test_default_paths_fall_back_to_nfmd_without_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Pre-gate hosts keep the historical ~/.nfmd layout for both paths."""
    checker = _load_checker()
    monkeypatch.setattr(checker, "CANONICAL_G4_DIR", tmp_path / "absent-gate-var")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("NFM_DEPLOY_MANIFEST", raising=False)
    monkeypatch.delenv("NFM_DEPLOY_LOCK", raising=False)

    args = checker.parse_args([])
    assert str(args.manifest) == str(tmp_path / "home" / ".nfmd" / "prod-deploy-manifest.json")
    assert str(args.lock) == str(tmp_path / "home" / ".nfmd" / "prod-deploy.lock")


def test_env_override_beats_canonical_and_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """$NFM_DEPLOY_MANIFEST / $NFM_DEPLOY_LOCK win over both default layers
    (the cron's explicit config stays authoritative)."""
    checker = _load_checker()
    gate_dir = tmp_path / "gate-var"
    gate_dir.mkdir()
    monkeypatch.setattr(checker, "CANONICAL_G4_DIR", gate_dir)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NFM_DEPLOY_MANIFEST", str(tmp_path / "explicit-manifest.json"))
    monkeypatch.setenv("NFM_DEPLOY_LOCK", str(tmp_path / "explicit.lock"))

    args = checker.parse_args([])
    assert str(args.manifest) == str(tmp_path / "explicit-manifest.json")
    assert str(args.lock) == str(tmp_path / "explicit.lock")


def test_deploy_prod_sh_and_checker_agree_on_g4_paths():
    """Coherence contract (the NFM-4273 integration bug this fixes): the
    writer (deploy_prod.sh) and the reader (this checker) must resolve the
    lock AND manifest through the SAME canonical-dir preference."""
    deploy_sh = DEPLOY_PROD_SH.read_text(encoding="utf-8")
    assert "/usr/local/var/nfm-g2" in deploy_sh, "deploy lock must prefer the canonical G4 dir"
    assert "run-record-manifest.sh" in deploy_sh, "gated manifest record must go through the entry"
    assert "NFM_DEPLOY_MANIFEST_WORLD_READABLE" in deploy_sh
    # under the gate the deploy body itself writes the canonical path
    assert "NFM_DEPLOY_MANIFEST=/usr/local/var/nfm-g2/prod-deploy-manifest.json" in deploy_sh


def test_workflow_runs_checker_tests_before_deploy():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/tests/test_check_deploy_drift.py" in source


def test_state_file_written_0600(fake_docker, env: DriftEnv):
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-api"] = _container("api", "f" * 64)
    fake_docker(containers)
    assert env.run().returncode == 1
    mode = stat.S_IMODE(env.state.stat().st_mode)
    assert mode == 0o600

