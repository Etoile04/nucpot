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
  * container_anomaly — a prod-project container that cannot be verified
                        (missing service label / underivable or malformed
                        digest). NFM-4297 CR F6: one anomalous container
                        used to raise OpsError → exit 2 → NOTHING filed,
                        forever — a single anomaly blinded the whole
                        alarm. Anomalous containers now degrade to their
                        own drift entry while every other service stays
                        checked. CLI/daemon failures (docker down,
                        unparseable output) remain operational errors —
                        the transient-retry semantics are unchanged.

Alarm routing (issue spec — no other routing): divergence auto-files an
issue assigned to the SRE Monitor agent, title prefix ``[DEPLOY-DRIFT]``,
body = per-service expected vs actual, first-seen timestamp, manifest
timestamp/actor.

Dedupe: one OPEN issue per divergence signature (sha256 over the sorted
entry fingerprint). Repeat intervals comment-append; a post-resolution
regression files a new issue. The signature is embedded in the issue body
(``signature: <hex>``) so dedupe survives state-file loss.

Family dedupe (NFM-4297 CR F6): an EVOLVING incident — a partial rollback
that widens or narrows the affected service set — changes the exact
signature but is the same incident. On a signature miss, an OPEN tracked
issue whose affected-service FAMILY overlaps the new one is comment-
appended with a ``+added/-removed`` delta and the tracked signature is
re-pointed, instead of double-filing one incident as two open issues.
Disjoint families file separately; closed issues never family-adopt (a
regression still files new). The family is embedded in filed bodies
(``family: a,b,c``) so the API fallback family-matches after state loss.
Pre-family state entries/bodies simply do not family-match.

In-flight deploy tolerance (ADR-013 §4 "drift alarm noise" row):
  1. deploy_prod.sh holds a deploy lock for the whole sanctioned deploy
     (trap-removed on ANY exit) — a fresh lock suppresses filing. A stale
     lock (crashed deploy) does NOT: a half-deployed prod is genuine drift.
     NFM-4273: under the host gate the lock is
     ``/usr/local/var/nfm-g2/prod-deploy.lock`` (canonical shared G4 dir);
     pre-gate hosts keep ``~/.nfmd/prod-deploy.lock`` — the default
     resolution here mirrors deploy_prod.sh exactly.
  2. Before filing, re-check after ``--recheck-seconds`` (default 300): a
     divergence that converges — or a manifest rewritten by a finishing
     deploy — during the window is tolerated.

Exit codes: 0 = in sync (or tolerated); 1 = divergence (issue filed or
dry-run rendered); 2 = operational error (docker/API unavailable — never
files, so a transient failure can't cry wolf).

Operational-failure escalation + overlap exclusion (NFM-4297 CR F8): a
persistent OpsError used to exit 2 silently forever — a dead alarm nobody
can see. Consecutive failures are counted in the state file; after
``--escalate-after-failures`` (default 24 ≈ 6h at the runbook's
registered 15-min cron interval) an
``[DEPLOY-DRIFT-OPS]`` alarm is filed/adopted to SRE and the counter
resets. A healthy pass resets the counter. Overlapping cron runs
(possible at the 5m incident-response interval vs a 300s recheck sleep)
used to double-file; an exclusive
flock on ``<state>.runlock`` makes the overlapping instance stand down
(rc 0, no filing). If the runlock itself cannot be opened the check
proceeds WITHOUT it — lock-infra failure must never kill the alarm.

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
import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
try:  # py3.11+; fallback for the py3.9 CommandLineTools interpreter on the runner
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone as _tz
    UTC = _tz.utc
from pathlib import Path

DEFAULT_COMPOSE_PROJECT = "nucpot-prod"
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"

SRE_MONITOR_AGENT_ID = "2ee2415b-e43e-4806-888f-c231e60facaf"
DEFAULT_COMPANY_ID = "ec7c0ded-5688-4002-8d0c-672597244875"
TITLE_PREFIX = "[DEPLOY-DRIFT]"
OPS_TITLE_PREFIX = "[DEPLOY-DRIFT-OPS]"
OPEN_STATUSES = {"todo", "in_progress", "in_review", "blocked"}
DEFAULT_MAX_LOCK_AGE = 7200  # 2h — cold build is ~30 min; crashed locks go stale
DEFAULT_RECHECK_SECONDS = 300
DEFAULT_ESCALATE_AFTER_FAILURES = 24  # ≈6h of consecutive exit-2 at the registered 15-min cron
RUNLOCK_SUFFIX = ".runlock"
_FAMILY_LINE = re.compile(r"^family: (.+)$", re.MULTILINE)

# NFM-4273 (G2 x G4 coherence): under the host gate the deploy body runs as
# nfmdeploy, whose $HOME is not the desktop user's. When the gate's
# canonical G4 state dir exists, BOTH the manifest and the deploy lock live
# there — deploy-identity-writable, world-readable — so this desktop-user
# cron and the gated deploy body always agree on ONE copy per artifact.
# Pre-gate hosts (and NFM_DEPLOY_MANIFEST / --manifest overrides) keep the
# historical ~/.nfmd layout. Mirrors deploy_prod.sh's NFM_DEPLOY_LOCK logic.
CANONICAL_G4_DIR = Path("/usr/local/var/nfm-g2")


def _default_g4_path(env_var: str, filename: str) -> str:
    """Resolve a G4 artifact path: $env > canonical gate dir > ~/.nfmd."""
    override = os.environ.get(env_var)
    if override:
        return override
    if CANONICAL_G4_DIR.is_dir():
        # Prefer canonical even before the first manifest exists: a stale
        # ~/.nfmd copy would mask exactly the fork this resolution prevents.
        return str(CANONICAL_G4_DIR / filename)
    return str(Path.home() / ".nfmd" / filename)


class OpsError(RuntimeError):
    """Operational failure (docker CLI, manifest I/O beyond absence, API).

    Never files an issue: a transient failure must not cry wolf. The cron
    interval retries naturally.
    """


class NotFoundError(OpsError):
    """The API answered 404 for a specific object (NFM-4297 CR F8). Raised
    (and caught) by get_issue so a server-side hard-deleted mapped issue is
    distinguishable from an unreachable API: the former must re-file, the
    latter must retry."""


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


def _digest_of(info: dict) -> str | None:
    """FROZEN digest precedence — identical to record_deploy_manifest.py —
    but shape-validated (NFM-4297 CR F8): a RepoDigests shape change (a
    string, a dict, a non-str element) is UNDERIVABLE, never indexed into
    garbage — ``"sha256:x"[0]`` yields ``"s"``, a dict raises KeyError
    straight past every handler. Returns None when no digest can be
    trusted; the caller degrades that container per CR F6."""
    repo_digests = info.get("RepoDigests") or []
    if repo_digests:
        if not isinstance(repo_digests, list):
            return None
        first = repo_digests[0]
        if not isinstance(first, str) or not first:
            return None
        return first
    image = info.get("Image")
    if isinstance(image, str) and image:
        return image
    return None


def collect_live_state(project: str) -> tuple[dict[str, list[dict]], list[Drift]]:
    """Enumerate RUNNING containers of the compose project (by project
    label — the preview overlay shares the name prefix and is excluded) and
    map compose service → [{container, digest, tag}].

    NFM-4297 CR F6: a container that cannot be VERIFIED (missing service
    label, underivable digest) no longer aborts the whole check — it is
    returned as its own ``container_anomaly`` Drift while every other
    container is still enumerated. Daemon-level failures (ps/inspect CLI
    errors, unparseable output) still raise OpsError: those are transient
    by hypothesis and the cron retries them."""
    listing = _docker(
        ["ps", "--filter", f"label={COMPOSE_PROJECT_LABEL}={project}", "--format", "{{.Names}}"]
    )
    names = [name for name in listing.split() if name]
    live: dict[str, list[dict]] = {}
    anomalies: list[Drift] = []
    for name in sorted(names):
        raw = _docker(["inspect", name])
        try:
            info = json.loads(raw)[0]
        except (json.JSONDecodeError, IndexError) as exc:
            raise OpsError(f"docker inspect {name}: unparseable output") from exc
        labels = (info.get("Config") or {}).get("Labels") or {}
        service = str(labels.get(COMPOSE_SERVICE_LABEL) or "")
        digest = _digest_of(info)
        problem = ""
        if not service:
            problem = f"missing {COMPOSE_SERVICE_LABEL} label — cannot map to a manifest service"
        elif digest is None:
            problem = "no derivable image digest (RepoDigests/Image absent or malformed shape)"
        if problem:
            anomalies.append(
                Drift(
                    "container_anomaly",
                    service or name,
                    "prod-project container verifiable against the manifest",
                    f"{name}: {problem}",
                )
            )
            continue
        container = str(info.get("Name") or "").lstrip("/") or str(info.get("Id") or "")
        live.setdefault(service, []).append(
            {
                "container": container,
                "digest": digest,
                "tag": str((info.get("Config") or {}).get("Image") or ""),
            }
        )
    return live, anomalies


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
            "manifest_missing",
            "-",
            str(path),
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
            entries.append(
                Drift(
                    "missing_service",
                    service,
                    f"running per manifest (digest {expected_digest}, "
                    f"container {containers.get(service, '?')})",
                    "no running container",
                )
            )
            continue
        live_digests = sorted({c["digest"] for c in running})
        if expected_digest not in live_digests:
            entries.append(
                Drift(
                    "digest_mismatch",
                    service,
                    expected_digest,
                    ", ".join(live_digests),
                )
            )
        expected_name = containers.get(service)
        live_names = sorted(c["container"] for c in running)
        if expected_name is not None and live_names != [expected_name]:
            entries.append(
                Drift(
                    "container_mismatch",
                    service,
                    str(expected_name),
                    ", ".join(live_names),
                )
            )
    for service in sorted(live):
        if service in digests:
            continue
        running = live[service]
        entries.append(
            Drift(
                "extra_service",
                service,
                "not present in manifest",
                f"{len(running)} running container(s) "
                f"({', '.join(sorted({c['digest'] for c in running}))})",
            )
        )
    return entries


def signature(entries: list[Drift]) -> str:
    canonical = json.dumps(
        [
            [e.kind, e.service, e.expected, e.actual]
            for e in sorted(entries, key=lambda e: (e.kind, e.service, e.expected, e.actual))
        ],
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def family_of(entries: list[Drift]) -> list[str]:
    """Coarse incident identity (NFM-4297 CR F6): the sorted set of affected
    services — ['-'] entries (manifest_missing) collapse to ['manifest'].
    Two drifts whose families overlap are the same EVOLVING incident (a
    partial rollback widening/narrowing the affected set); disjoint
    families are separate incidents."""
    services = {e.service for e in entries if e.service != "-"}
    return sorted(services) if services else ["manifest"]


def _diverged_services(entries: list[Drift]) -> str:
    services = sorted({e.service for e in entries if e.service != "-"})
    return ", ".join(services) if services else "manifest"


def render_title(entries: list[Drift], sig: str) -> str:
    return (
        f"{TITLE_PREFIX} {_diverged_services(entries)} diverge from deploy manifest (sig {sig[:8]})"
    )


def render_body(
    entries: list[Drift], sig: str, manifest: dict | None, manifest_path: Path, first_seen: str
) -> str:
    lines = [
        f"{TITLE_PREFIX} alarm — live prod state diverges from the sanctioned",
        "deploy manifest (ADR-013 §2 G4b / NFM-4272). Auto-filed by",
        "scripts/check_deploy_drift.py on the Hermes cron.",
        "",
        f"signature: {sig}",
        f"family: {','.join(family_of(entries))}",
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
        if manifest
        else "no readable manifest"
    )
    return (
        f"still diverged (sig {sig[:8]}) at {now} — "
        f"services: {_diverged_services(entries)}. Baseline: {baseline}. "
        "Auto-appended by the drift checker."
    )


def render_family_comment(
    entries: list[Drift], sig: str, old_family: list[str], manifest: dict | None, now: str
) -> str:
    """Append text for an EVOLVED incident (NFM-4297 CR F6): same family,
    new exact signature — a partial rollback widened or narrowed the
    affected service set. Names the delta and re-points the tracked
    signature instead of double-filing the incident."""
    new_family = family_of(entries)
    delta = []
    added = sorted(set(new_family) - set(old_family))
    removed = sorted(set(old_family) - set(new_family))
    if added:
        delta.append("+" + ",".join(added))
    if removed:
        delta.append("-" + ",".join(removed))
    baseline = (
        f"deploy_sha={manifest.get('deploy_sha', '?')}" if manifest else "no readable manifest"
    )
    return (
        f"drift evolved (new sig {sig[:8]}) at {now} — "
        f"{' '.join(delta) if delta else 'scope unchanged'}; "
        f"affected now: {', '.join(new_family)}. Baseline: {baseline}. "
        "Auto-appended by the drift checker (family dedupe, NFM-4297)."
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
            if exc.code == 404:
                raise NotFoundError(f"Paperclip {method} {path} -> HTTP 404") from exc
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
            "POST",
            f"/companies/{self.company_id}/issues",
            {
                "title": title,
                "description": description,
                "assigneeAgentId": assignee_agent_id,
                "status": "todo",
                "priority": "high",
            },
        )
        if not isinstance(result, dict) or not result.get("id"):
            raise OpsError("Paperclip create returned no issue id")
        return result

    def get_issue(self, issue_uuid: str) -> dict | None:
        """None when the API answers 404 (hard-deleted server-side) so the
        caller re-files instead of retrying a dead mapping forever (CR F8).
        Unreachable-API and other HTTP failures still raise OpsError."""
        try:
            result = self._request("GET", f"/issues/{issue_uuid}")
        except NotFoundError:
            return None
        if not isinstance(result, dict):
            raise OpsError(f"Paperclip get {issue_uuid}: unexpected shape")
        return result

    def add_comment(self, issue_uuid: str, body: str) -> None:
        self._request("POST", f"/issues/{issue_uuid}/comments", {"body": body})

    def _list_company_issues(self, max_pages: int = 50) -> list[dict]:
        """All company issues, walking the API's silent 1000-row page cap
        (NFM-4297 CR F8: a single ?limit=1000 read truncates and made drift
        issues beyond page one invisible to the dedupe fallback)."""
        rows: list[dict] = []
        offset = 0
        for _ in range(max_pages):
            result = self._request(
                "GET", f"/companies/{self.company_id}/issues?limit=1000&offset={offset}"
            )
            issues = result if isinstance(result, list) else (result or {}).get("issues", [])
            rows.extend(issue for issue in issues if isinstance(issue, dict))
            if len(issues) < 1000:
                break
            offset += 1000
        return rows

    def find_open_drift_issue(self, sig: str) -> dict | None:
        """Dedupe fallback when the state file is lost: search OPEN issues
        with our title prefix whose body carries this exact signature."""
        for issue in self._list_company_issues():
            if str(issue.get("status")) not in OPEN_STATUSES:
                continue
            if not str(issue.get("title") or "").startswith(TITLE_PREFIX):
                continue
            if f"signature: {sig}" in str(issue.get("description") or ""):
                return issue
        return None

    def find_open_family_issue(self, family: list[str]) -> dict | None:
        """Family fallback when the state file is lost AND the signature
        evolved (NFM-4297 CR F6): an OPEN drift issue whose embedded
        ``family:`` line overlaps the new family. OPS alarms never match —
        they carry no family line and a distinct prefix."""
        wanted = set(family)
        for issue in self._list_company_issues():
            if str(issue.get("status")) not in OPEN_STATUSES:
                continue
            title = str(issue.get("title") or "")
            if not title.startswith(TITLE_PREFIX) or title.startswith(OPS_TITLE_PREFIX):
                continue
            match = _FAMILY_LINE.search(str(issue.get("description") or ""))
            if match and wanted & {part.strip() for part in match.group(1).split(",")}:
                return issue
        return None

    def find_open_ops_issue(self) -> dict | None:
        """The OPEN [DEPLOY-DRIFT-OPS] alarm, if any (CR F8 escalation
        re-escalations adopt it instead of stacking)."""
        for issue in self._list_company_issues():
            if str(issue.get("status")) not in OPEN_STATUSES:
                continue
            if str(issue.get("title") or "").startswith(OPS_TITLE_PREFIX):
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
    return datetime.now(UTC).isoformat(timespec="seconds")


def _open_family_match(
    state: dict, sig: str, family: list[str], client: PaperclipClient
) -> tuple[str, dict] | None:
    """The newest OPEN state-tracked issue whose family overlaps `family`
    (NFM-4297 CR F6). Closed/deleted tracked issues never match — a
    post-resolution regression must file new. Pre-family state entries
    (no "family" key) never match either."""
    wanted = set(family)
    best: tuple[str, dict] | None = None
    for other_sig, other in state["signatures"].items():
        if other_sig == sig:
            continue
        old_family = other.get("family") or []
        if not old_family or not (set(old_family) & wanted):
            continue
        issue = client.get_issue(str(other["issue_uuid"]))
        if issue is None or str(issue.get("status")) not in OPEN_STATUSES:
            continue
        if best is None or str(other.get("last_append", "")) > str(best[1].get("last_append", "")):
            best = (other_sig, other)
    return best


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
    family = family_of(entries)
    entry = state["signatures"].get(sig)
    if entry:
        issue = client.get_issue(entry["issue_uuid"]) if client else None
        if issue is not None and str(issue.get("status")) in OPEN_STATUSES:
            comment = render_comment(entries, sig, manifest, _utc_now())
            if dry_run:
                print(
                    f"[DRY-RUN] would comment-append to "
                    f"{entry.get('identifier', entry['issue_uuid'])}:\n{comment}"
                )
                return
            client.add_comment(entry["issue_uuid"], comment)
            entry["last_append"] = _utc_now()
            save_state(state_path, state)
            print(
                f"==> Drift persists — comment appended to "
                f"{entry.get('identifier', entry['issue_uuid'])} (sig {sig[:8]})"
            )
            return
        # Mapped issue is closed (or deleted server-side) and the drift is
        # back: post-resolution regression — fall through. Family adopt is
        # skipped here on purpose: closed issues never re-adopt.

    if not dry_run and client is not None:
        # CR F6 family dedupe: an OPEN tracked issue for an overlapping
        # service family is the SAME evolving incident (partial rollback
        # widened/narrowed the set) — append with the delta and re-point.
        best = _open_family_match(state, sig, family, client)
        if best is not None:
            old_sig, old = best
            client.add_comment(
                str(old["issue_uuid"]),
                render_family_comment(
                    entries, sig, sorted(old.get("family") or []), manifest, _utc_now()
                ),
            )
            moved = {
                **old,
                "family": family,
                "last_append": _utc_now(),
                "superseded_signature": old_sig,
            }
            state["signatures"] = {k: v for k, v in state["signatures"].items() if k != old_sig}
            state["signatures"][sig] = moved
            save_state(state_path, state)
            print(
                f"==> Drift family evolved — comment appended to "
                f"{old.get('identifier', old['issue_uuid'])} and signature re-pointed "
                f"(sig {sig[:8]}, family {','.join(family)})"
            )
            return

        adopted = client.find_open_drift_issue(sig)
        if adopted is not None:
            client.add_comment(
                str(adopted["id"]),
                render_comment(entries, sig, manifest, _utc_now()),
            )
            state["signatures"][sig] = {
                "issue_uuid": str(adopted["id"]),
                "identifier": str(adopted.get("identifier", "?")),
                "family": family,
                "first_seen": str(adopted.get("createdAt") or _utc_now()),
                "last_append": _utc_now(),
                "adopted_from_api": True,
            }
            save_state(state_path, state)
            print(
                f"==> Drift persists — re-adopted OPEN issue "
                f"{adopted.get('identifier', adopted['id'])} and commented (sig {sig[:8]})"
            )
            return

        adopted_family = client.find_open_family_issue(family)
        if adopted_family is not None:
            old_family_line = _FAMILY_LINE.search(str(adopted_family.get("description") or ""))
            old_family = (
                [part.strip() for part in old_family_line.group(1).split(",")]
                if old_family_line
                else family
            )
            client.add_comment(
                str(adopted_family["id"]),
                render_family_comment(entries, sig, sorted(old_family), manifest, _utc_now()),
            )
            state["signatures"][sig] = {
                "issue_uuid": str(adopted_family["id"]),
                "identifier": str(adopted_family.get("identifier", "?")),
                "family": family,
                "first_seen": str(adopted_family.get("createdAt") or _utc_now()),
                "last_append": _utc_now(),
                "adopted_from_api": True,
            }
            save_state(state_path, state)
            print(
                f"==> Drift family evolved — re-adopted OPEN issue "
                f"{adopted_family.get('identifier', adopted_family['id'])} and commented "
                f"(sig {sig[:8]}, family {','.join(family)})"
            )
            return

    title = render_title(entries, sig)
    first_seen = _utc_now()
    body = render_body(entries, sig, manifest, manifest_path, first_seen)
    if dry_run or client is None:
        print(f"[DRY-RUN] would file to SRE Monitor ({sre_agent_id}):\ntitle: {title}\n\n{body}")
        return
    issue = client.create_issue(title, body, sre_agent_id)
    state["signatures"][sig] = {
        "issue_uuid": str(issue["id"]),
        "identifier": str(issue.get("identifier", "?")),
        "family": family,
        "first_seen": first_seen,
        "last_append": first_seen,
    }
    save_state(state_path, state)
    print(
        f"==> DRIFT FILED: {issue.get('identifier', issue['id'])} assigned to SRE "
        f"Monitor — {_diverged_services(entries)} (sig {sig[:8]})"
    )


# ------------------------------------------------------------------ check


def _lock_is_fresh(path: Path, max_lock_age: int) -> bool:
    if not path.exists():
        return False
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age <= max_lock_age


def _acquire_runlock(state_path: Path) -> tuple[object | None, Path | None]:
    """Exclusive per-run lock against overlapping cron instances (CR F8).

    Returns (fd, path): fd open + flocked when acquired (caller closes to
    release); (None, path) when another instance holds it; (None, None)
    when the lock infrastructure itself fails (open error, or a flock
    error other than contention) — in which case the caller proceeds
    WITHOUT the lock: lock-infra failure must never kill the alarm
    (fail-open on the exclusion mechanism only)."""
    path = Path(str(state_path) + RUNLOCK_SUFFIX)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # fd must OUTLIVE this function — the flock holds until run_check's
        # finally closes it (a context manager here would release instantly).
        fd = open(path, "a+")  # noqa: SIM115
    except OSError:
        return None, None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fd.close()
        return None, path
    except OSError:
        fd.close()
        return None, None
    return fd, path


def _record_ops_failure(state_path: Path) -> tuple[int, str]:
    """Count one consecutive operational failure. Best-effort: an
    unreadable state file returns (0, '') — counting must never turn an
    OpsError into a crash."""
    try:
        state = load_state(state_path)
    except OpsError:
        return 0, ""
    arm = dict(state.get("ops_failures") or {})
    arm["count"] = int(arm.get("count", 0)) + 1
    arm["first_at"] = str(arm.get("first_at") or "") or _utc_now()
    state = {**state, "ops_failures": arm}
    with contextlib.suppress(OpsError):
        save_state(state_path, state)
    return int(arm["count"]), str(arm["first_at"])


def _clear_ops_failures(state_path: Path) -> None:
    try:
        state = load_state(state_path)
        if (state.get("ops_failures") or {}).get("count"):
            state = {**state, "ops_failures": {"count": 0, "first_at": ""}}
            save_state(state_path, state)
    except OpsError:
        pass


def _maybe_escalate_ops(
    count: int, first_at: str, args: argparse.Namespace, state_path: Path
) -> None:
    """After N consecutive failures, file/adopt an [DEPLOY-DRIFT-OPS] alarm
    (CR F8: a dead alarm must be visible). Counter resets only on a
    SUCCESSFUL escalation or a healthy pass — a failing API keeps the count
    so the alarm fires as soon as filing is possible again."""
    if count < args.escalate_after_failures:
        return
    if not (args.paperclip_url and args.paperclip_key):
        print(
            "check_deploy_drift: cannot escalate OPS alarm — no filing target "
            "(NFM_DRIFT_PAPERCLIP_URL/KEY unset); failure count retained.",
            file=sys.stderr,
        )
        return
    try:
        client = PaperclipClient(args.paperclip_url, args.paperclip_key, args.company_id)
        existing = client.find_open_ops_issue()
        if existing is not None:
            client.add_comment(
                str(existing["id"]),
                f"still failing — {count} consecutive operational failures "
                f"(first at {first_at or '?'}). Auto-appended by the drift checker.",
            )
        else:
            client.create_issue(
                f"{OPS_TITLE_PREFIX} drift checker down — {count} consecutive operational failures",
                (
                    f"The ADR-013 G4b drift alarm has failed {count} consecutive "
                    f"times since {first_at or '?'} — it is currently NOT watching "
                    "prod. Each failure exits 2 (operational error: docker CLI or "
                    "filing API unavailable). Run the checker by hand to see the "
                    "live error; resolution runbook: docs/runbooks/prod-deploy.md §8.\n"
                    f"consecutive-failures: {count}\nfirst-at: {first_at or '?'}"
                ),
                args.sre_agent_id,
            )
        _clear_ops_failures(state_path)
        print(
            f"==> OPS ALARM: {count} consecutive failures escalated to SRE Monitor.",
        )
    except OpsError as exc:
        print(f"check_deploy_drift: OPS escalation itself failed — {exc}", file=sys.stderr)


def _ops_failed(state_path: Path, args: argparse.Namespace, detail: str) -> int:
    """The shared exit-2 tail: report, count, maybe escalate."""
    print(f"check_deploy_drift: OPERATIONAL ERROR — {detail}", file=sys.stderr)
    print("not filing; next cron interval retries.", file=sys.stderr)
    count, first_at = _record_ops_failure(state_path)
    _maybe_escalate_ops(count, first_at, args, state_path)
    return 2


def run_check(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser()
    state_path = Path(args.state).expanduser()
    lock_path = Path(args.lock).expanduser()

    runlock_fd, runlock_path = _acquire_runlock(state_path)
    if runlock_fd is None and runlock_path is not None:
        print(
            f"==> another checker instance is in flight "
            f"({runlock_path} held) — standing down, not filing."
        )
        return 0
    if runlock_fd is None:
        print("==> runlock unavailable — proceeding WITHOUT overlap exclusion.")

    try:
        return _run_check_locked(args, manifest_path, state_path, lock_path)
    finally:
        if runlock_fd is not None:
            runlock_fd.close()  # type: ignore[attr-defined]


def _run_check_locked(
    args: argparse.Namespace, manifest_path: Path, state_path: Path, lock_path: Path
) -> int:
    def one_pass() -> tuple[dict | None, list[Drift]]:
        manifest, problem = load_manifest(manifest_path)
        if problem is not None:
            return None, [problem]
        live, anomalies = collect_live_state(args.compose_project)
        return manifest, [*anomalies, *diff(manifest, live)]

    try:
        manifest, entries = one_pass()
    except OpsError as exc:
        return _ops_failed(state_path, args, str(exc))

    if not entries:
        print(f"==> in sync — live state matches {manifest_path}")
        _clear_ops_failures(state_path)
        return 0

    # ADR-013 §4: a sanctioned deploy in progress must not file.
    if _lock_is_fresh(lock_path, args.max_lock_age):
        print(
            f"==> divergence present but deploy lock is fresh "
            f"({lock_path}) — sanctioned deploy in progress, not filing."
        )
        _clear_ops_failures(state_path)
        return 0

    if args.recheck_seconds > 0:
        print(
            f"==> divergence detected; re-checking after "
            f"{args.recheck_seconds}s (in-flight deploy tolerance)…"
        )
        time.sleep(args.recheck_seconds)
        if _lock_is_fresh(lock_path, args.max_lock_age):
            print(
                "==> deploy lock appeared during re-check — sanctioned deploy "
                "in progress, not filing."
            )
            _clear_ops_failures(state_path)
            return 0
        try:
            manifest2, entries2 = one_pass()
        except OpsError as exc:
            return _ops_failed(state_path, args, f"on re-check — {exc}")
        if not entries2:
            print("==> divergence cleared during re-check window — not filing.")
            _clear_ops_failures(state_path)
            return 0
        manifest, entries = manifest2, entries2

    sig = signature(entries)
    client = None
    if not args.dry_run:
        missing = [
            name
            for name, value in (
                ("--paperclip-url / $NFM_DRIFT_PAPERCLIP_URL", args.paperclip_url),
                ("--paperclip-key / $NFM_DRIFT_PAPERCLIP_KEY", args.paperclip_key),
            )
            if not value
        ]
        if missing:
            return _ops_failed(
                state_path,
                args,
                f"drift detected but {', '.join(missing)} unset; cannot file. The "
                "checker deliberately does NOT read ambient PAPERCLIP_API_URL/"
                "PAPERCLIP_API_KEY (2026-09-04 false alarm, NFM-4275): the cron "
                "wrapper sets the NFM_DRIFT_* variables explicitly.",
            )
        client = PaperclipClient(args.paperclip_url, args.paperclip_key, args.company_id)
        print(f"==> filing target: {client.api} (company {args.company_id})")

    try:
        file_or_append(
            entries,
            sig,
            manifest,
            manifest_path,
            state_path,
            client,
            args.sre_agent_id,
            args.dry_run,
        )
    except OpsError as exc:
        return _ops_failed(state_path, args, f"while filing — {exc}")
    _clear_ops_failures(state_path)
    return 1


# -------------------------------------------------------------------- cli


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ADR-013 G4b deploy-drift alarm: diff live prod digests "
        "against the G4a deploy manifest, auto-file SRE issues (NFM-4272)."
    )
    parser.add_argument(
        "--manifest",
        default=_default_g4_path("NFM_DEPLOY_MANIFEST", "prod-deploy-manifest.json"),
        help="G4a manifest path (default: $NFM_DEPLOY_MANIFEST, else "
        "/usr/local/var/nfm-g2/ when the gate is installed, else "
        "~/.nfmd/prod-deploy-manifest.json — NFM-4273).",
    )
    parser.add_argument(
        "--compose-project",
        default=os.environ.get("NFM_DEPLOY_COMPOSE_PROJECT") or DEFAULT_COMPOSE_PROJECT,
        help=f"compose project to inspect (default: {DEFAULT_COMPOSE_PROJECT}).",
    )
    parser.add_argument(
        "--lock",
        default=_default_g4_path("NFM_DEPLOY_LOCK", "prod-deploy.lock"),
        help="deploy lockfile held by deploy_prod.sh while a sanctioned "
        "deploy runs (default: $NFM_DEPLOY_LOCK, else "
        "/usr/local/var/nfm-g2/ when the gate is installed, else "
        "~/.nfmd/prod-deploy.lock — NFM-4273).",
    )
    parser.add_argument(
        "--max-lock-age",
        type=int,
        default=int(os.environ.get("NFM_DRIFT_MAX_LOCK_AGE") or DEFAULT_MAX_LOCK_AGE),
        help=f"locks older than this many seconds are stale/ignored "
        f"(default: {DEFAULT_MAX_LOCK_AGE}).",
    )
    parser.add_argument(
        "--recheck-seconds",
        type=int,
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
    parser.add_argument(
        "--paperclip-url",
        default=os.environ.get("NFM_DRIFT_PAPERCLIP_URL"),
        help="Paperclip API root (default: $NFM_DRIFT_PAPERCLIP_URL — "
        "NOT the ambient $PAPERCLIP_API_URL; see runbook §8).",
    )
    parser.add_argument(
        "--paperclip-key",
        default=os.environ.get("NFM_DRIFT_PAPERCLIP_KEY"),
        help="Paperclip API key (default: $NFM_DRIFT_PAPERCLIP_KEY — "
        "NOT the ambient $PAPERCLIP_API_KEY; see runbook §8).",
    )
    parser.add_argument(
        "--company-id",
        default=os.environ.get("NFM_DRIFT_PAPERCLIP_COMPANY_ID") or DEFAULT_COMPANY_ID,
        help=f"Paperclip company id (default: {DEFAULT_COMPANY_ID}).",
    )
    parser.add_argument(
        "--sre-agent-id",
        default=SRE_MONITOR_AGENT_ID,
        help="assignee for drift issues (default: SRE Monitor agent).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="render the would-be issue/comment; no API calls, no state writes.",
    )
    parser.add_argument(
        "--escalate-after-failures",
        type=int,
        default=int(os.environ.get("NFM_DRIFT_ESCALATE_AFTER") or DEFAULT_ESCALATE_AFTER_FAILURES),
        help=f"file an [DEPLOY-DRIFT-OPS] alarm to SRE after this many "
        f"CONSECUTIVE operational failures (default: {DEFAULT_ESCALATE_AFTER_FAILURES} "
        f"≈6h at the runbook's registered 15-min cron; a healthy pass resets the count).",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="fabricate a divergence and verify end-to-end filing "
        "against an in-process stub target; touches nothing real.",
    )
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
