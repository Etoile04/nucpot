#!/usr/bin/env python3
"""Record the ADR-013 §2 G4a deploy manifest (NFM-4271).

Incident context (NFM-4264, 2026-09-04): a desktop-agent session ran
host-side ``docker compose up -d --build`` against prod, bypassing every
path-based control with zero audit trail (~6h attribution cost). This
recorder is the observation half of G4: every SANCTIONED deploy writes ONE
JSON artifact describing exactly what it left running, and the G4b drift
alarm (sibling task, Hermes cron) diffs live ``docker inspect`` state
against it — bounding the dwell time of any future bypass.

Called from BOTH sanctioned deploy paths (issue scope):
  * scripts/deploy_prod.sh            (in-script, after cutover + health gates)
  * production-deployment.yml         (outside-script belt-and-braces step,
                                       after the cutover assertion — NFM-3777
                                       lesson: defenses that live only inside
                                       the deploy body die with it)

Manifest contract — field names are FROZEN for the G4b sibling; do not
rename without coordinating in the NFM-4268 thread:

    {
      "deploy_sha":        "<git SHA deployed>",
      "image_tags":        {"<service>": "<tag the container runs>"},
      "image_digests":     {"<service>": "<immutable digest>"},
      "service_containers":{"<service>": "<container name>"},
      "timestamp":         "<UTC ISO-8601>",
      "actor":             "<deploy path + execution identity>"
    }

Digest precedence (the G4b alarm must recompute the same way):
``RepoDigests[0]`` when the image was pulled/pushed (true RepoDigest form,
``repo@sha256:...``); otherwise the container's immutable image-ID digest
(``.Image``, ``sha256:...``). Prod images are BUILT on the host
(deploy_prod.sh ``docker build``), so their RepoDigests list is empty — the
image-ID digest is the immutable reference ``docker inspect`` exposes for
them, and it changes iff the image content changes (a fresh rebuild of the
same tag mints a new one, which is exactly the NFM-4264 detection signal).

Failure semantics (AC-G4a.5): everything is collected BEFORE anything is
written; the write itself is tmp-file + ``os.replace`` (atomic rename) with
fsync. Any collection failure exits non-zero leaving the previous manifest
byte-identical — never a silently-wrong manifest. A deploy that knows it is
incomplete can pass ``--partial "<reason>"`` to record an explicitly-marked
partial state instead.

Tamper resistance (best-effort, ADR-013 §3 "no adversarial-security claim"):
the manifest is 0600 inside a 0700 directory owned by the deploy identity,
so an out-of-band mutator running as a DIFFERENT user cannot refresh it to
cover its tracks. A same-user actor can still defeat this; detecting that
residual is G4b's job (the alarm re-derives digests from live containers).

NFM-4273 (G2 x G4a integration) canonical path: under the host gate the deploy
identity (nfmdeploy) writes ``/usr/local/var/nfm-g2/prod-deploy-manifest.json``
— the ONE location the desktop-user drift cron reads — with the file 0644 so
that cron can diff against it. Deploy-identity-only writability (via the
root-owned sudo entry) replaces 0600-privacy as the tamper resistance there:
set ``NFM_DEPLOY_MANIFEST_WORLD_READABLE=1`` for 0644 files in 0755 dirs.

Default manifest path: ``$HOME/.nfmd/prod-deploy-manifest.json`` (both
pre-gate sanctioned paths execute as the same host user via ssh, so $HOME
resolves identically). Override with --manifest or NFM_DEPLOY_MANIFEST.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
try:  # py3.11+ fast path; fall back for the py3.9 CommandLineTools interpreter
    from datetime import UTC  # noqa: F401
except ImportError:  # pragma: no cover — py<3.11
    from datetime import timezone as _tz
    UTC = _tz.utc
from pathlib import Path

DEFAULT_COMPOSE_PROJECT = "nucpot-prod"
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"


class CollectError(RuntimeError):
    """Raised when live docker state cannot be fully collected.

    The recorder refuses to write a manifest built from partial data —
    a manifest that silently understates prod is worse than no manifest
    (the previous one survives, and the drift alarm stays honest).
    """


def _docker(args: list[str]) -> str:
    """Run one docker CLI call; any failure aborts collection."""
    try:
        proc = subprocess.run(["docker", *args], capture_output=True, text=True)
    except OSError as exc:  # docker binary missing from PATH
        raise CollectError(f"cannot execute docker: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[:1]
        raise CollectError(
            f"docker {' '.join(args)} failed (rc={proc.returncode})"
            + (f": {detail[0]}" if detail else "")
        )
    return proc.stdout


def running_service_containers(project: str) -> list[dict]:
    """Inspect every RUNNING container of the compose project.

    Enumerated by the compose PROJECT label, not the container-name prefix:
    the preview/QA overlay (docker-compose.preview.yml, project
    ``nucpot-prod-preview``) shares the ``nucpot-prod-`` name prefix and must
    not churn the prod manifest.
    """
    listing = _docker(
        [
            "ps",
            "--filter",
            f"label={COMPOSE_PROJECT_LABEL}={project}",
            "--format",
            "{{.Names}}",
        ]
    )
    names = [name for name in listing.split() if name]
    inspected: list[dict] = []
    for name in names:
        raw = _docker(["inspect", name])
        try:
            info = json.loads(raw)[0]
        except (json.JSONDecodeError, IndexError) as exc:
            raise CollectError(f"docker inspect {name}: unparseable output") from exc
        inspected.append(info)
    return inspected


def _service_name(info: dict) -> str:
    # docker inspect nests labels at .Config.Labels (verified against the live
    # prod daemon 2026-09-04); docker ps output puts them top-level — this
    # recorder only ever consumes full `docker inspect` JSON.
    labels = (info.get("Config") or {}).get("Labels") or {}
    return str(labels.get(COMPOSE_SERVICE_LABEL) or "")


def _image_tag(info: dict) -> str:
    return str((info.get("Config") or {}).get("Image") or "")


def _image_digest(info: dict) -> str:
    """RepoDigest when available; else the immutable image-ID digest."""
    repo_digests = info.get("RepoDigests") or []
    return str(repo_digests[0] if repo_digests else (info.get("Image") or ""))


def _container_ref(info: dict) -> str:
    return str(info.get("Name") or "").lstrip("/") or str(info.get("Id") or "")


def build_manifest(
    deploy_sha: str,
    actor: str,
    project: str,
    partial_reason: str | None = None,
) -> dict:
    """Collect live state and assemble the contract-shaped manifest."""
    containers = running_service_containers(project)
    if not containers:
        raise CollectError(
            f"no running containers found for compose project '{project}' — "
            "refusing to record an empty manifest"
        )

    manifest = {
        "deploy_sha": deploy_sha,
        "image_tags": {},
        "image_digests": {},
        "service_containers": {},
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "actor": actor,
    }
    for info in sorted(containers, key=_service_name):
        service = _service_name(info)
        container = _container_ref(info)
        if not service:
            raise CollectError(f"container {container}: missing {COMPOSE_SERVICE_LABEL} label")
        tag = _image_tag(info)
        digest = _image_digest(info)
        if not tag or not digest:
            raise CollectError(
                f"service {service} (container {container}): missing "
                f"image tag ({tag!r}) or digest ({digest!r})"
            )
        manifest["image_tags"][service] = tag
        manifest["image_digests"][service] = digest
        manifest["service_containers"][service] = container

    if partial_reason is not None:
        manifest["partial"] = True
        manifest["partial_reason"] = partial_reason
    return manifest


def _world_readable() -> bool:
    """NFM-4273: canonical gate path is world-readable for the drift cron."""
    return os.environ.get("NFM_DEPLOY_MANIFEST_WORLD_READABLE", "") in ("1", "true", "yes")


def _ensure_private_dir(directory: Path) -> None:
    """Create the manifest directory 0700 (0755 world-readable mode).

    Only tightens dirs we create — host_setup.sh owns the canonical
    /usr/local/var/nfm-g2 dir and its permissions.
    """
    if directory.is_dir():
        return
    directory.mkdir(parents=True, exist_ok=True)
    # best-effort; the file-level mode below is the hard guarantee
    with contextlib.suppress(OSError):
        directory.chmod(0o755 if _world_readable() else 0o700)


def write_atomic(manifest_path: Path, manifest: dict) -> None:
    """Write tmp + fsync + atomic rename; never a torn or absent manifest.

    os.replace is atomic within a filesystem, so a reader (the G4b alarm)
    always sees either the previous or the complete new manifest — including
    across a deploy killed mid-write. The tmp file is removed on any error.
    """
    _ensure_private_dir(manifest_path.parent)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(manifest_path.parent), prefix=".prod-deploy-manifest.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, 0o644 if _world_readable() else 0o600)
        os.replace(tmp_path, manifest_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record the ADR-013 G4a prod deploy manifest (NFM-4271)."
    )
    parser.add_argument(
        "--deploy-sha",
        required=True,
        help="git SHA being deployed (deploy_prod.sh: ${DEPLOY_SHA}).",
    )
    parser.add_argument(
        "--actor",
        required=True,
        help=(
            "deploy path + execution identity, e.g. 'deploy_prod.sh:lwj04' or 'gh-runner:lwj04'."
        ),
    )
    parser.add_argument(
        "--manifest",
        default=os.environ.get("NFM_DEPLOY_MANIFEST")
        or str(Path.home() / ".nfmd" / "prod-deploy-manifest.json"),
        help="manifest path (default: $NFM_DEPLOY_MANIFEST or ~/.nfmd/prod-deploy-manifest.json).",
    )
    parser.add_argument(
        "--compose-project",
        default=os.environ.get("NFM_DEPLOY_COMPOSE_PROJECT") or DEFAULT_COMPOSE_PROJECT,
        help=f"compose project whose running services to record "
        f"(default: {DEFAULT_COMPOSE_PROJECT}).",
    )
    parser.add_argument(
        "--partial",
        metavar="REASON",
        default=None,
        help="mark the manifest as an explicit partial state with this reason "
        "(AC-G4a.5); by default failures leave the previous manifest intact.",
    )
    args = parser.parse_args(argv)
    if not args.deploy_sha.strip():
        parser.error("--deploy-sha must not be empty")
    if not args.actor.strip():
        parser.error("--actor must not be empty")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest_path = Path(args.manifest).expanduser()
    try:
        manifest = build_manifest(
            deploy_sha=args.deploy_sha,
            actor=args.actor,
            project=args.compose_project,
            partial_reason=args.partial,
        )
        write_atomic(manifest_path, manifest)
    except CollectError as exc:
        print(f"record_deploy_manifest: FAILED — {exc}", file=sys.stderr)
        print(
            "record_deploy_manifest: previous manifest (if any) left intact.",
            file=sys.stderr,
        )
        return 1

    services = sorted(manifest["service_containers"])
    print(f"==> Deploy manifest recorded: {manifest_path}")
    print(f"    sha={manifest['deploy_sha']} actor={manifest['actor']}")
    print(f"    services ({len(services)}): {', '.join(services)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
