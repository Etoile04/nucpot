#!/usr/bin/env python3
"""Deploy-drift alarm — ADR-013 §2 G4 part 2 (NFM-4272).

Incident context (NFM-4264, 2026-09-04 00:06 CST): a desktop-agent session
ran host-side ``docker compose --env-file docker/.env.prod up -d --build api
web`` against prod, bypassing every path-based control with zero audit
trail (~6h attribution cost). ADR-013's answer for the residual: ASSUME a
future bypass exists and bound its dwell time. This checker runs on the
Hermes cron (NFM-3195 precedent — same infra as the CI Monitor cron), diffs
live ``docker inspect`` state against the deploy manifest recorded by the
G4a sibling (scripts/record_deploy_manifest.py, NFM-4271), and auto-files
an SRE issue on divergence.

Manifest contract (field names FROZEN by NFM-4271 — consumed verbatim):
    {deploy_sha, image_tags, image_digests, service_containers,
     timestamp, actor}

Digest precedence (must match the recorder exactly): ``RepoDigests[0]``
when non-empty, else the container's image-ID digest (``.Image``). Prod
images are BUILT on the host, so most entries are image-ID digests — a
fresh rebuild of the same tag mints a new one, which is exactly the
NFM-4264 detection signal.

Divergence kinds filed:
  * manifest_missing  — no readable baseline (absent/unreadable/invalid)
  * missing_service   — manifest service with no running container
  * extra_service     — live prod container absent from the manifest
  * digest_mismatch   — live digest != manifest image_digests entry
  * container_mismatch — running container set != manifest mapping (e.g.
                        an unsanctioned scale-up or rename)

Alarm routing (issue spec — no other routing): divergence auto-files an
issue assigned to the SRE Monitor agent, title prefix ``[DEPLOY-DRIFT]``,
body = per-service expected vs actual, first-seen timestamp, manifest
timestamp/actor.

Dedupe: one OPEN issue per divergence signature (sha256 over the sorted
entry fingerprint). Repeat intervals comment-append; a NEW signature —
including a post-resolution regression — files a new issue. The signature
is embedded in the issue body (``signature: <hex>``) so dedupe survives
state-file loss.

In-flight deploy tolerance (ADR-013 §4 "drift alarm noise" row):
  1. deploy_prod.sh holds ``~/.nfmd/prod-deploy.lock`` for the whole
     sanctioned deploy (trap-removed on ANY exit) — a fresh lock suppresses
     filing. A stale lock (crashed deploy) does NOT: a half-deployed prod
     is genuine drift.
  2. Before filing, re-check after ``--recheck-seconds`` (default 300): a
     divergence that converges — or a manifest rewritten by a finishing
     deploy — during the window is tolerated.

Exit codes: 0 = in sync (or tolerated); 1 = divergence (issue filed or
dry-run rendered); 2 = operational error (docker/API unavailable — never
files, so a transient failure can't cry wolf).

``--selftest`` fabricates a divergence against a fixture manifest and
verifies issue-filing end-to-end against an in-process stub Paperclip
server (create + dedupe/comment-append), without touching real prod state
or the real API.

Residual (documented, not detected): a ``--force-recreate`` of the same
image keeps digest AND compose-stable container name — invisible to a
manifest diff. G3 (full command-text logging) is the compensating control
for that vector.

Filing-target safety (2026-09-04 false alarm, NFM-4275): during development
a manual run inherited the ambient ``PAPERCLIP_API_URL``/``KEY`` of a
developer session and filed a fabricated divergence against real Paperclip.
Since then the checker takes its filing target ONLY from
``NFM_DRIFT_PAPERCLIP_URL``/``NFM_DRIFT_PAPERCLIP_KEY`` (set by the cron
wrapper; see docs/runbooks/prod-deploy.md §8) or explicit flags — ambient
session credentials produce an operational error, never a filing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_COMPOSE_PROJECT = "nucpot-prod"
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"

SRE_MONITOR_AGENT_ID = "2ee2415b-e43e-4806-888f-c231e60facaf"
DEFAULT_COMPANY_ID = "ec7c0ded-5688-4002-8d0c-672597244875"
TITLE_PREFIX = "[DEPLOY-DRIFT]"
OPEN_STATUSES = {"todo", "in_progress", "in_review", "blocked"}
DEFAULT_MAX_LOCK_AGE = 7200  # 2h — cold build is ~30 min; crashed locks go stale
DEFAULT_RECHECK_SECONDS = 300


class OpsError(RuntimeError):
    """Operational failure (docker CLI, manifest I/O beyond absence, API).

    Never files an issue: a transient failure must not cry wolf. The cron
    interval retries naturally.
    """


@dataclass(frozen=True)
class Drift:
    kind: str  # manifest_missing | missing_service | extra_service | digest_mismatch | container_mismatch
    service: str
    expected: str
    actual: str


# --------------------------------------------------------------- live state


def _docker(args: list[str]) -> str:
    """Run one docker CLI call; any failure is an operational error."""
    try:
        proc = subprocess.run(["docker", *args], capture_output=True, text=True)
    except OSError as exc:
        raise OpsError(f"cannot execute docker: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[:1]
        raise OpsError(
            f"docker {' '.join(args)} failed (rc={proc.returncode})"
            + (f": {detail[0]}" if detail else "")
        )
    return proc.stdout


def _digest_of(info: dict) -> str:
    """FROZEN digest precedence — identical to record_deploy_manifest.py."""
    repo_digests = info.get("RepoDigests") or []
    return str(repo_digests[0] if repo_digests else (info.get("Image") or ""))


def collect_live_state(project: str) -> dict[str, list[dict]]:
    """Enumerate RUNNING containers of the compose project (by project
    label — the preview overlay shares the name prefix and is excluded) and
    map compose service → [{container, digest, tag}]."""
    listing = _docker(
        ["ps", "--filter", f"label={COMPOSE_PROJECT_LABEL}={project}",
         "--format", "{{.Names}}"]
    )
    names = [name for name in listing.split() if name]
    live: dict[str, list[dict]] = {}
    for name in sorted(names):
        raw = _docker(["inspect", name])
        try:
            info = json.loads(raw)[0]
        except (json.JSONDecodeError, IndexError) as exc:
            raise OpsError(f"docker inspect {name}: unparseable output") from exc
        labels = (info.get("Config") or {}).get("Labels") or {}
        service = str(labels.get(COMPOSE_SERVICE_LABEL) or "")
        if not service:
            raise OpsError(f"container {name}: missing {COMPOSE_SERVICE_LABEL} label")
        container = str(info.get("Name") or "").lstrip("/") or str(info.get("Id") or "")
        digest = _digest_of(info)
        if not digest:
            raise OpsError(f"container {name}: no derivable image digest")
        live.setdefault(service, []).append(
            {"container": container, "digest": digest,
             "tag": str((info.get("Config") or {}).get("Image") or "")}
        )
    return live


# ------------------------------------------------------------------- diff


def load_manifest(path: Path) -> tuple[dict | None, Drift | None]:
    """Load the G4a baseline. Returns (manifest, problem); exactly one is
    None. A baseline that cannot be trusted is itself the divergence."""
    if not path.exists():
        return None, Drift("manifest_missing", "-", str(path), "file does not exist")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, Drift("manifest_missing", "-", str(path), f"unreadable: {exc}")
    required = ("image_digests", "service_containers", "timestamp", "actor")
    missing = [key for key in required if not isinstance(manifest.get(key), (dict, str))]
    if missing:
        return None, Drift(
            "manifest_missing", "-", str(path),
            f"invalid: missing/empty key(s) {', '.join(missing)}",
        )
    return manifest, None


def diff(manifest: dict, live: dict[str, list[dict]]) -> list[Drift]:
    """Diff live state against the manifest baseline."""
    entries: list[Drift] = []
    digests = manifest["image_digests"]
    containers = manifest["service_containers"]
    for service in sorted(digests):
        expected_digest = str(digests[service])
        running = live.get(service, [])
        if not running:
            entries.append(Drift(
                "missing_service", service,
                f"running per manifest (digest {expected_digest}, "
                f"container {containers.get(service, '?')})",
                "no running container",
            ))
            continue
        live_digests = sorted({c["digest"] for c in running})
        if expected_digest not in live_digests:
            entries.append(Drift(
                "digest_mismatch", service, expected_digest, ", ".join(live_digests),
            ))
        expected_name = containers.get(service)
        live_names = sorted(c["container"] for c in running)
        if expected_name is not None and live_names != [expected_name]:
            entries.append(Drift(
                "container_mismatch", service, str(expected_name), ", ".join(live_names),
            ))
    for service in sorted(live):
        if service in digests:
            continue
        running = live[service]
        entries.append(Drift(
            "extra_service", service, "not present in manifest",
            f"{len(running)} running container(s) "
            f"({', '.join(sorted({c['digest'] for c in running}))})",
        ))
    return entries


def signature(entries: list[Drift]) -> str:
    canonical = json.dumps(
        [[e.kind, e.service, e.expected, e.actual]
         for e in sorted(entries, key=lambda e: (e.kind, e.service, e.expected, e.actual))],
        sort_keys=True, ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _diverged_services(entries: list[Drift]) -> str:
    services = sorted({e.service for e in entries if e.service != "-"})
    return ", ".join(services) if services else "manifest"


def render_title(entries: list[Drift], sig: str) -> str:
    return f"{TITLE_PREFIX} {_diverged_services(entries)} diverge from deploy manifest (sig {sig[:8]})"


def render_body(entries: list[Drift], sig: str, manifest: dict | None,
                manifest_path: Path, first_seen: str) -> str:
    lines = [
        f"{TITLE_PREFIX} alarm — live prod state diverges from the sanctioned",
        "deploy manifest (ADR-013 §2 G4b / NFM-4272). Auto-filed by",
        "scripts/check_deploy_drift.py on the Hermes cron.",
        "",
        f"signature: {sig}",
        "",
        "## Divergence(s)",
    ]
    for entry in entries:
        lines.append(
            f"- service {entry.service} — {entry.kind}: "
            f"expected {entry.expected} | actual: {entry.actual}"
        )
    lines += ["", "## Baseline manifest"]
    if manifest is not None:
        lines += [
            f"- deploy_sha: {manifest.get('deploy_sha', '?')}",
            f"- actor: {manifest.get('actor', '?')}",
            f"- timestamp: {manifest.get('timestamp', '?')}",
        ]
    lines += [f"- path: {manifest_path}", "", f"first-seen: {first_seen}", ""]
    lines += [
        "Repeat checks append comments to this issue; a NEW signature files a",
        "new issue. Resolution runbook: docs/runbooks/prod-deploy.md §8.",
    ]
    return "\n".join(lines)


def render_comment(entries: list[Drift], sig: str, manifest: dict | None, now: str) -> str:
    baseline = (
        f"deploy_sha={manifest.get('deploy_sha', '?')}, actor={manifest.get('actor', '?')}"
        if manifest else "no readable manifest"
    )
    return (
        f"still diverged (sig {sig[:8]}) at {now} — "
        f"services: {_diverged_services(entries)}. Baseline: {baseline}. "
        "Auto-appended by the drift checker."
    )


# -------------------------------------------------------------- paperclip


class PaperclipClient:
    """Minimal Paperclip REST client (stdlib only — cron hosts have no uv).

    Connections are always DIRECT: the Paperclip API for this deployment is
    localhost, and dev shells carry proxy env vars that would otherwise
    intercept 127.0.0.1 requests (observed 2026-09-04: ambient proxy turned a
    connection-refused into a misleading HTTP 502).
    """

    def __init__(self, base_url: str, api_key: str, company_id: str, timeout: int = 10):
        root = base_url.rstrip("/")
        self.api = root if root.endswith("/api") else root + "/api"
        self.api_key = api_key
        self.company_id = company_id
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, method: str, path: str, payload: dict | None = None) -> object:
        url = f"{self.api}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.api_key}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode()
        except urllib.error.HTTPError as exc:
            raise OpsError(f"Paperclip {method} {path} -> HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise OpsError(f"Paperclip {method} {path} unreachable: {exc}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpsError(f"Paperclip {method} {path}: unparseable response") from exc

    def create_issue(self, title: str, description: str, assignee_agent_id: str) -> dict:
        result = self._request(
            "POST", f"/companies/{self.company_id}/issues",
            {"title": title, "description": description,
             "assigneeAgentId": assignee_agent_id, "status": "todo",
             "priority": "high"},
        )
        if not isinstance(result, dict) or not result.get("id"):
            raise OpsError("Paperclip create returned no issue id")
        return result

    def get_issue(self, issue_uuid: str) -> dict:
        result = self._request("GET", f"/issues/{issue_uuid}")
        if not isinstance(result, dict):
            raise OpsError(f"Paperclip get {issue_uuid}: unexpected shape")
        return result

    def add_comment(self, issue_uuid: str, body: str) -> None:
        self._request("POST", f"/issues/{issue_uuid}/comments", {"body": body})

    def find_open_drift_issue(self, sig: str) -> dict | None:
        """Dedupe fallback when the state file is lost: search OPEN issues
        with our title prefix whose body carries this exact signature."""
        result = self._request("GET", f"/companies/{self.company_id}/issues?limit=1000")
        issues = result if isinstance(result, list) else (result or {}).get("issues", [])
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if str(issue.get("status")) not in OPEN_STATUSES:
                continue
            if not str(issue.get("title") or "").startswith(TITLE_PREFIX):
                continue
            if f"signature: {sig}" in str(issue.get("description") or ""):
                return issue
        return None


# ------------------------------------------------------------------ state


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"signatures": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OpsError(f"drift state {path} unreadable: {exc}") from exc
    if not isinstance(state.get("signatures"), dict):
        raise OpsError(f"drift state {path}: invalid shape")
    return state


def save_state(path: Path, state: dict) -> None:
    """Atomic write, 0600 — the state maps signatures to live issue uuids."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".drift-state.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


# ----------------------------------------------------------------- filing


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_or_append(
    entries: list[Drift],
    sig: str,
    manifest: dict | None,
    manifest_path: Path,
    state_path: Path,
    client: PaperclipClient | None,
    sre_agent_id: str,
    dry_run: bool,
) -> None:
    state = load_state(state_path)
    entry = state["signatures"].get(sig)
    if entry:
        issue = client.get_issue(entry["issue_uuid"]) if client else None
        if issue is not None and str(issue.get("status")) in OPEN_STATUSES:
            comment = render_comment(entries, sig, manifest, _utc_now())
            if dry_run:
                print(f"[DRY-RUN] would comment-append to "
                      f"{entry.get('identifier', entry['issue_uuid'])}:\n{comment}")
                return
            client.add_comment(entry["issue_uuid"], comment)
            entry["last_append"] = _utc_now()
            save_state(state_path, state)
            print(f"==> Drift persists — comment appended to "
                  f"{entry.get('identifier', entry['issue_uuid'])} (sig {sig[:8]})")
            return
        # Mapped issue is closed and the (same-signature) drift is back:
        # post-resolution regression — fall through and file a NEW issue.

    if not dry_run and client is not None:
        adopted = client.find_open_drift_issue(sig)
        if adopted is not None:
            client.add_comment(
                str(adopted["id"]),
                render_comment(entries, sig, manifest, _utc_now()),
            )
            state["signatures"][sig] = {
                "issue_uuid": str(adopted["id"]),
                "identifier": str(adopted.get("identifier", "?")),
                "first_seen": str((adopted.get("createdAt") or _utc_now())),
                "last_append": _utc_now(),
                "adopted_from_api": True,
            }
            save_state(state_path, state)
            print(f"==> Drift persists — re-adopted OPEN issue "
                  f"{adopted.get('identifier', adopted['id'])} and commented (sig {sig[:8]})")
            return

    title = render_title(entries, sig)
    first_seen = _utc_now()
    body = render_body(entries, sig, manifest, manifest_path, first_seen)
    if dry_run or client is None:
        print(f"[DRY-RUN] would file to SRE Monitor ({sre_agent_id}):\n"
              f"title: {title}\n\n{body}")
        return
    issue = client.create_issue(title, body, sre_agent_id)
    state["signatures"][sig] = {
        "issue_uuid": str(issue["id"]),
        "identifier": str(issue.get("identifier", "?")),
        "first_seen": first_seen,
        "last_append": first_seen,
    }
    save_state(state_path, state)
    print(f"==> DRIFT FILED: {issue.get('identifier', issue['id'])} assigned to SRE "
          f"Monitor — {_diverged_services(entries)} (sig {sig[:8]})")


# ------------------------------------------------------------------ check


def _lock_is_fresh(path: Path, max_lock_age: int) -> bool:
    if not path.exists():
        return False
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age <= max_lock_age


def run_check(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser()
    state_path = Path(args.state).expanduser()
    lock_path = Path(args.lock).expanduser()

    def one_pass() -> tuple[dict | None, dict[str, list[dict]] | None, list[Drift]]:
        manifest, problem = load_manifest(manifest_path)
        if problem is not None:
            return None, None, [problem]
        return manifest, collect_live_state(args.compose_project), None

    try:
        manifest, live, problem = one_pass()
        entries = problem if problem is not None else diff(manifest, live)
    except OpsError as exc:
        print(f"check_deploy_drift: OPERATIONAL ERROR — {exc}", file=sys.stderr)
        print("not filing; next cron interval retries.", file=sys.stderr)
        return 2

    if not entries:
        print(f"==> in sync — live state matches {manifest_path}")
        return 0

    # ADR-013 §4: a sanctioned deploy in progress must not file.
    if _lock_is_fresh(lock_path, args.max_lock_age):
        print(f"==> divergence present but deploy lock is fresh "
              f"({lock_path}) — sanctioned deploy in progress, not filing.")
        return 0

    if args.recheck_seconds > 0:
        print(f"==> divergence detected; re-checking after "
              f"{args.recheck_seconds}s (in-flight deploy tolerance)…")
        time.sleep(args.recheck_seconds)
        if _lock_is_fresh(lock_path, args.max_lock_age):
            print("==> deploy lock appeared during re-check — sanctioned deploy "
                  "in progress, not filing.")
            return 0
        try:
            manifest2, live2, problem2 = one_pass()
            entries2 = problem2 if problem2 is not None else diff(manifest2, live2)
        except OpsError as exc:
            print(f"check_deploy_drift: OPERATIONAL ERROR on re-check — {exc}",
                  file=sys.stderr)
            return 2
        if not entries2:
            print("==> divergence cleared during re-check window — not filing.")
            return 0
        manifest, entries = manifest2, entries2

    sig = signature(entries)
    client = None
    if not args.dry_run:
        missing = [name for name, value in (
            ("--paperclip-url / $NFM_DRIFT_PAPERCLIP_URL", args.paperclip_url),
            ("--paperclip-key / $NFM_DRIFT_PAPERCLIP_KEY", args.paperclip_key),
        ) if not value]
        if missing:
            print(f"check_deploy_drift: OPERATIONAL ERROR — drift detected but "
                  f"{', '.join(missing)} unset; cannot file. The checker "
                  f"deliberately does NOT read ambient PAPERCLIP_API_URL/"
                  f"PAPERCLIP_API_KEY (2026-09-04 false alarm, NFM-4275): the "
                  f"cron wrapper sets the NFM_DRIFT_* variables explicitly.",
                  file=sys.stderr)
            return 2
        client = PaperclipClient(args.paperclip_url, args.paperclip_key, args.company_id)
        print(f"==> filing target: {client.api} (company {args.company_id})")

    try:
        file_or_append(entries, sig, manifest, manifest_path, state_path,
                       client, args.sre_agent_id, args.dry_run)
    except OpsError as exc:
        print(f"check_deploy_drift: OPERATIONAL ERROR while filing — {exc}",
              file=sys.stderr)
        print("not filed; next cron interval retries.", file=sys.stderr)
        return 2
    return 1


# -------------------------------------------------------------------- cli


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ADR-013 G4b deploy-drift alarm: diff live prod digests "
                    "against the G4a deploy manifest, auto-file SRE issues (NFM-4272)."
    )
    parser.add_argument(
        "--manifest",
        default=os.environ.get("NFM_DEPLOY_MANIFEST")
        or str(Path.home() / ".nfmd" / "prod-deploy-manifest.json"),
        help="G4a manifest path (default: $NFM_DEPLOY_MANIFEST or "
             "~/.nfmd/prod-deploy-manifest.json).",
    )
    parser.add_argument(
        "--compose-project",
        default=os.environ.get("NFM_DEPLOY_COMPOSE_PROJECT") or DEFAULT_COMPOSE_PROJECT,
        help=f"compose project to inspect (default: {DEFAULT_COMPOSE_PROJECT}).",
    )
    parser.add_argument(
        "--lock",
        default=os.environ.get("NFM_DEPLOY_LOCK")
        or str(Path.home() / ".nfmd" / "prod-deploy.lock"),
        help="deploy lockfile held by deploy_prod.sh while a sanctioned "
             "deploy runs (default: ~/.nfmd/prod-deploy.lock).",
    )
    parser.add_argument(
        "--max-lock-age", type=int,
        default=int(os.environ.get("NFM_DRIFT_MAX_LOCK_AGE") or DEFAULT_MAX_LOCK_AGE),
        help=f"locks older than this many seconds are stale/ignored "
             f"(default: {DEFAULT_MAX_LOCK_AGE}).",
    )
    parser.add_argument(
        "--recheck-seconds", type=int,
        default=int(os.environ.get("NFM_DRIFT_RECHECK_SECONDS") or DEFAULT_RECHECK_SECONDS),
        help="re-check delay before filing, tolerating in-flight deploys "
             f"(default: {DEFAULT_RECHECK_SECONDS}; 0 disables).",
    )
    parser.add_argument(
        "--state",
        default=os.environ.get("NFM_DRIFT_STATE")
        or str(Path.home() / ".nfmd" / "prod-deploy-drift-state.json"),
        help="checker state mapping divergence signatures to filed issues "
             "(default: ~/.nfmd/prod-deploy-drift-state.json).",
    )
    parser.add_argument("--paperclip-url", default=os.environ.get("NFM_DRIFT_PAPERCLIP_URL"),
                        help="Paperclip API root (default: $NFM_DRIFT_PAPERCLIP_URL — "
                             "NOT the ambient $PAPERCLIP_API_URL; see runbook §8).")
    parser.add_argument("--paperclip-key", default=os.environ.get("NFM_DRIFT_PAPERCLIP_KEY"),
                        help="Paperclip API key (default: $NFM_DRIFT_PAPERCLIP_KEY — "
                             "NOT the ambient $PAPERCLIP_API_KEY; see runbook §8).")
    parser.add_argument("--company-id",
                        default=os.environ.get("NFM_DRIFT_PAPERCLIP_COMPANY_ID")
                        or DEFAULT_COMPANY_ID,
                        help=f"Paperclip company id (default: {DEFAULT_COMPANY_ID}).")
    parser.add_argument("--sre-agent-id", default=SRE_MONITOR_AGENT_ID,
                        help="assignee for drift issues (default: SRE Monitor agent).")
    parser.add_argument("--dry-run", action="store_true",
                        help="render the would-be issue/comment; no API calls, no state writes.")
    parser.add_argument("--selftest", action="store_true",
                        help="fabricate a divergence and verify end-to-end filing "
                             "against an in-process stub target; touches nothing real.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.selftest:
        # Lazy import: the selftest module drives this module's main(), so
        # a module-level import would be circular.
        from deploy_drift_selftest import run_selftest

        return run_selftest()
    return run_check(args)


if __name__ == "__main__":
    sys.exit(main())
