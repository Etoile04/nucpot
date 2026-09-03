"""Tests for scripts/record_deploy_manifest.py — ADR-013 §2 G4a recorder (NFM-4271).

Incident context (NFM-4264, 2026-09-04): a desktop-agent session ran host-side
``docker compose up -d --build`` against prod, bypassing every control with
zero audit trail (~6h attribution cost). G4a makes each SANCTIONED deploy
record a manifest of what it deployed; the G4b drift alarm (sibling task)
diffs live ``docker inspect`` state against it.

Contract (issue NFM-4271; field names FROZEN for the sibling alarm):
  {deploy_sha, image_tags, image_digests, service_containers, timestamp, actor}

Digest precedence (documented for the G4b sibling): ``RepoDigests[0]`` when
the image was pulled/pushed (true RepoDigest form); otherwise the container's
immutable image-ID digest (``.Image``, ``sha256:...``). Prod images are BUILT
on the host (deploy_prod.sh ``docker build``), so their RepoDigests list is
empty — the image-ID digest is the immutable reference ``docker inspect``
exposes for them (AC-G4a.3).
"""

import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent
SCRIPT = SCRIPTS_DIR / "record_deploy_manifest.py"
DEPLOY_PROD_SH = SCRIPTS_DIR / "deploy_prod.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production-deployment.yml"

COMPOSE_PROJECT = "nucpot-prod"
DEPLOY_SHA = "8e29e906d1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6"

FAKE_DOCKER = """\
#!/usr/bin/env python3
""" + '"""' + """Fake docker CLI for recorder tests (NFM-4271).

Implements only what scripts/record_deploy_manifest.py calls:
  ps --filter label=com.docker.compose.project=<p> --format {{.Names}}
  inspect <name>
Container state comes from the FAKE_DOCKER_STATE JSON file.
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
        # label=<key>=<value> — two '=' (label=com.docker.compose.project=x).
        _, _, label_value = label_filter.partition("=")
        _, _, project = label_value.partition("=")
        for name, container in sorted(state["containers"].items()):
            # State mirrors real `docker inspect` shape: labels nest under
            # Config.Labels (verified against the live prod daemon 2026-09-04).
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


def prod_containers() -> dict:
    """Canonical running prod state: 6 services of compose project nucpot-prod.

    db/redis are pulled images (RepoDigests populated); api/worker/lightrag/web
    are locally built (RepoDigests empty — the real prod deploy builds these
    with `docker build`). Plus one preview-overlay container that must NOT be
    recorded (different compose project).
    """
    def built(service: str, repo: str, image_id: str) -> dict:
        return {
            "Id": f"cid-{service}",
            "Name": f"/{COMPOSE_PROJECT}-{service}",
            "Config": {
                "Image": f"{repo}:{DEPLOY_SHA[:7]}",
                "Labels": {
                    "com.docker.compose.project": COMPOSE_PROJECT,
                    "com.docker.compose.service": service,
                },
            },
            "Image": f"sha256:{image_id}",
            "RepoDigests": [],
        }

    def pulled(service: str, repo_tag: str, repo_digest: str, image_id: str) -> dict:
        return {
            "Id": f"cid-{service}",
            "Name": f"/{COMPOSE_PROJECT}-{service}",
            "Config": {
                "Image": repo_tag,
                "Labels": {
                    "com.docker.compose.project": COMPOSE_PROJECT,
                    "com.docker.compose.service": service,
                },
            },
            "Image": f"sha256:{image_id}",
            "RepoDigests": [f"{repo_tag.split(':')[0]}@sha256:{repo_digest}"],
        }

    return {
        f"{COMPOSE_PROJECT}-api": built("api", "nucpot-prod-api", "a" * 64),
        f"{COMPOSE_PROJECT}-worker": built("worker", "nucpot-prod-api", "a" * 64),
        f"{COMPOSE_PROJECT}-web": built("web", "nucpot-prod-web", "b" * 64),
        f"{COMPOSE_PROJECT}-lightrag": built("lightrag", "nucpot-prod-lightrag", "c" * 64),
        f"{COMPOSE_PROJECT}-db": pulled("db", "pgvector/pgvector:pg16", "d" * 64, "e" * 64),
        f"{COMPOSE_PROJECT}-redis": pulled("redis", "redis:7-alpine", "f" * 64, "9" * 64),
        f"{COMPOSE_PROJECT}-preview-api": {
            "Id": "cid-preview-api",
            "Name": f"/{COMPOSE_PROJECT}-preview-api",
            "Config": {
                "Image": "nucpot-prod-api:preview-abc",
                "Labels": {
                    "com.docker.compose.project": f"{COMPOSE_PROJECT}-preview",
                    "com.docker.compose.service": "api",
                },
            },
            "Image": f"sha256:{'7' * 64}",
            "RepoDigests": [],
        },
    }


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

    def _load(containers: dict) -> Path:
        state_path.write_text(json.dumps({"containers": containers}), encoding="utf-8")
        monkeypatch.setenv("FAKE_DOCKER_STATE", str(state_path))
        return state_path

    return _load


def run_recorder(
    *args: str,
    manifest: Path,
    deploy_sha: str = DEPLOY_SHA,
    actor: str = "deploy_prod.sh:testuser",
) -> subprocess.CompletedProcess:
    """Invoke the recorder with the fake docker on PATH."""
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--deploy-sha",
            deploy_sha,
            "--actor",
            actor,
            "--manifest",
            str(manifest),
            *args,
        ],
        capture_output=True,
        text=True,
    )


def read_manifest(manifest: Path) -> dict:
    return json.loads(manifest.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# AC-G4a.1 — manifest exists, is valid JSON, matches the schema for every
# running prod service.
# ---------------------------------------------------------------------------

def test_success_records_all_running_services(fake_docker, tmp_path):
    fake_docker(prod_containers())
    manifest = tmp_path / "state" / "prod-deploy-manifest.json"

    proc = run_recorder(manifest=manifest)

    assert proc.returncode == 0, proc.stderr
    assert manifest.is_file(), "manifest must exist after a successful run"
    data = read_manifest(manifest)
    expected_services = {"api", "worker", "web", "lightrag", "db", "redis"}
    for field in ("image_tags", "image_digests", "service_containers"):
        assert set(data[field]) == expected_services, f"{field} must cover every running service"


def test_manifest_schema_core_keys_exact(fake_docker, tmp_path):
    """Field names are a frozen contract for the G4b drift alarm sibling."""
    fake_docker(prod_containers())
    manifest = tmp_path / "m" / "prod-deploy-manifest.json"

    assert run_recorder(manifest=manifest).returncode == 0
    data = read_manifest(manifest)
    assert set(data) == {
        "deploy_sha",
        "image_tags",
        "image_digests",
        "service_containers",
        "timestamp",
        "actor",
    }, "schema drift: the sibling alarm parses these exact keys"


def test_timestamp_is_utc_iso8601(fake_docker, tmp_path):
    fake_docker(prod_containers())
    manifest = tmp_path / "m" / "prod-deploy-manifest.json"

    assert run_recorder(manifest=manifest).returncode == 0
    ts = datetime.fromisoformat(read_manifest(manifest)["timestamp"])
    assert ts.utcoffset() == timedelta(0), "timestamp must carry UTC offset"
    assert abs(datetime.now().astimezone(tz=ts.tzinfo) - ts) < timedelta(minutes=5)


def test_service_containers_use_container_names(fake_docker, tmp_path):
    fake_docker(prod_containers())
    manifest = tmp_path / "m" / "prod-deploy-manifest.json"

    assert run_recorder(manifest=manifest).returncode == 0
    data = read_manifest(manifest)
    assert data["service_containers"]["api"] == f"{COMPOSE_PROJECT}-api"
    assert data["deploy_sha"] == DEPLOY_SHA


def test_preview_overlay_project_excluded(fake_docker, tmp_path):
    """Preview/QA overlay containers share the name prefix but a different
    compose project — they must not churn the prod manifest."""
    fake_docker(prod_containers())
    manifest = tmp_path / "m" / "prod-deploy-manifest.json"

    assert run_recorder(manifest=manifest).returncode == 0
    assert "preview-api" not in read_manifest(manifest)["service_containers"]


# ---------------------------------------------------------------------------
# AC-G4a.2 — both sanctioned paths record; actor distinguishes path+identity.
# ---------------------------------------------------------------------------

def test_actor_recorded_verbatim_gh_runner_shape(fake_docker, tmp_path):
    fake_docker(prod_containers())
    manifest = tmp_path / "m" / "prod-deploy-manifest.json"

    proc = run_recorder(manifest=manifest, actor="gh-runner:lwj04")

    assert proc.returncode == 0, proc.stderr
    assert read_manifest(manifest)["actor"] == "gh-runner:lwj04"


def test_deploy_prod_sh_wires_recorder_with_manual_actor_default():
    """deploy_prod.sh must call the recorder and default the actor to its own
    path (the GH workflow overrides DEPLOY_ACTOR with gh-runner:<actor>)."""
    text = DEPLOY_PROD_SH.read_text(encoding="utf-8")
    assert "record_deploy_manifest.py" in text, "deploy_prod.sh must invoke the recorder"
    assert "deploy_prod.sh:" in text, "manual-run actor must identify the deploy path"
    assert "DEPLOY_ACTOR" in text, "GH-runner deploys must inject their actor"


def test_workflow_wires_recorder_outside_script():
    """production-deployment.yml must pass a gh-runner actor into the deploy
    AND record the manifest from the job context (NFM-3777 lesson: defenses
    that live only inside the deploy body die with it)."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "record_deploy_manifest.py" in text
    assert "gh-runner:" in text
    assert "NFM-4271" in text


# ---------------------------------------------------------------------------
# AC-G4a.3 — digests match `docker inspect` of the deployed containers.
# ---------------------------------------------------------------------------

def test_digests_match_docker_inspect(fake_docker, tmp_path):
    """Pulled services record RepoDigests[0]; locally built services (empty
    RepoDigests — prod images are built on the host) record the immutable
    image-ID digest (.Image). Both are recoverable from docker inspect."""
    containers = prod_containers()
    fake_docker(containers)
    manifest = tmp_path / "m" / "prod-deploy-manifest.json"

    assert run_recorder(manifest=manifest).returncode == 0
    data = read_manifest(manifest)

    for name, info in containers.items():
        if info["Config"]["Labels"]["com.docker.compose.project"] != COMPOSE_PROJECT:
            continue
        service = info["Config"]["Labels"]["com.docker.compose.service"]
        expected = (info["RepoDigests"] or [info["Image"]])[0]
        assert data["image_digests"][service] == expected, f"digest mismatch for {service}"
        assert data["image_tags"][service] == info["Config"]["Image"]
    # The shared api image: worker and api record the same built digest.
    assert data["image_digests"]["worker"] == data["image_digests"]["api"]


# ---------------------------------------------------------------------------
# AC-G4a.4 — overwritten per deploy, no history accumulation.
# ---------------------------------------------------------------------------

def test_manifest_overwritten_per_deploy(fake_docker, tmp_path):
    state = fake_docker(prod_containers())
    manifest = tmp_path / "m" / "prod-deploy-manifest.json"

    assert run_recorder(manifest=manifest, deploy_sha="f" * 40, actor="deploy_prod.sh:one").returncode == 0
    new_sha = "0123456789abcdef0123456789abcdef01234567"
    assert run_recorder(manifest=manifest, deploy_sha=new_sha, actor="gh-runner:two").returncode == 0

    raw = manifest.read_text(encoding="utf-8")
    assert raw.count('"deploy_sha"') == 1, "no history accumulation in the artifact"
    data = json.loads(raw)  # exactly ONE JSON object
    assert data["deploy_sha"] == new_sha
    assert data["actor"] == "gh-runner:two"


# ---------------------------------------------------------------------------
# AC-G4a.5 — failure leaves the previous manifest intact (or an explicitly
# marked partial); never a silently-wrong manifest.
# ---------------------------------------------------------------------------

def test_collect_failure_leaves_previous_manifest_intact(fake_docker, tmp_path):
    state = fake_docker(prod_containers())
    manifest = tmp_path / "m" / "prod-deploy-manifest.json"
    assert run_recorder(manifest=manifest, deploy_sha="a" * 40).returncode == 0
    before = manifest.read_bytes()

    # web's inspect now fails mid-collection (daemon hiccup / dying container).
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-web"]["inspect_fail"] = True
    fake_docker(containers)
    proc = run_recorder(manifest=manifest, deploy_sha="b" * 40)

    assert proc.returncode != 0, "a failed collection must not exit 0"
    assert manifest.read_bytes() == before, "previous manifest must survive intact"


def test_no_running_services_refuses_to_write(fake_docker, tmp_path):
    """Zero running prod services is a silently-wrong manifest — refuse."""
    fake_docker({"unrelated-container": {"Labels": {}}})
    manifest = tmp_path / "m" / "prod-deploy-manifest.json"

    proc = run_recorder(manifest=manifest)

    assert proc.returncode != 0
    assert not manifest.exists()


def test_partial_state_explicitly_marked(fake_docker, tmp_path):
    fake_docker(prod_containers())
    manifest = tmp_path / "m" / "prod-deploy-manifest.json"

    proc = run_recorder("--partial", "deploy aborted mid-cutover", manifest=manifest)

    assert proc.returncode == 0, proc.stderr
    data = read_manifest(manifest)
    assert data["partial"] is True
    assert data["partial_reason"] == "deploy aborted mid-cutover"
    # Schema contract still holds for the core fields.
    assert data["deploy_sha"] == DEPLOY_SHA


# ---------------------------------------------------------------------------
# Scope: atomic write + deploy-identity-only permissions (tamper resistance).
# ---------------------------------------------------------------------------

def test_manifest_permissions_are_deploy_identity_only(fake_docker, tmp_path):
    fake_docker(prod_containers())
    manifest = tmp_path / "nfmd" / "prod-deploy-manifest.json"

    assert run_recorder(manifest=manifest).returncode == 0
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600, "manifest must be 0600"
    assert stat.S_IMODE(manifest.parent.stat().st_mode) == 0o700, "manifest dir must be 0700"


def test_no_tmp_residue_success_and_failure(fake_docker, tmp_path):
    state = fake_docker(prod_containers())
    manifest = tmp_path / "nfmd" / "prod-deploy-manifest.json"
    manifest.parent.mkdir(parents=True)

    assert run_recorder(manifest=manifest).returncode == 0
    containers = prod_containers()
    containers[f"{COMPOSE_PROJECT}-db"]["inspect_fail"] = True
    fake_docker(containers)
    assert run_recorder(manifest=manifest).returncode != 0

    leftovers = [p.name for p in manifest.parent.iterdir() if p.name != manifest.name]
    assert leftovers == [], f"atomic-write tmp files leaked: {leftovers}"


def test_missing_required_args_fail_loudly(fake_docker, tmp_path):
    fake_docker(prod_containers())
    manifest = tmp_path / "m" / "manifest.json"
    for dropped in ("--deploy-sha", "--actor"):
        base = [
            sys.executable, str(SCRIPT), "--deploy-sha", DEPLOY_SHA,
            "--actor", "deploy_prod.sh:t", "--manifest", str(manifest),
        ]
        idx = base.index(dropped)
        proc = subprocess.run(base[:idx] + base[idx + 2:], capture_output=True, text=True)
        assert proc.returncode != 0, f"{dropped} must be required"
        assert not manifest.exists()
