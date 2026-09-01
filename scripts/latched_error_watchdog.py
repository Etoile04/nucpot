"""Latched-error watchdog for Paperclip `claude_local` agents.

NFM-3993 RCA: an agent whose ``status === "error"`` AND whose
``lastHeartbeatAt`` is older than the threshold produces ZERO board signal,
because the heartbeat scheduler at ``server/src/services/heartbeat.ts:12454``
skips ``error``-status agents (``intervalSec <= 0`` is the default for
claude_local adapters). The only signal becomes CEO-manual-observation
during weekly standup synthesis — far too late.

This watchdog closes that gap. On each run it:

1. Lists every agent in the company.
2. Filters to ``claude_local`` adapters whose ``status === "error"`` and whose
   ``lastHeartbeatAt`` is older than ``--threshold-minutes`` (default 10).
3. Probes the agent's on-disk workspace at
   ``~/.paperclip/instances/default/workspaces/{agentId}/`` to distinguish:
   - **transient**: workspace provisioned but missing the ``.git`` anchor
     (NFM-3935 RCA pattern — ``git init -q .`` usually unblocks)
   - **terminal**: workspace provisioned with a healthy ``.git`` but still in
     error (other runtime failure — escalate to CTO Research / Lead Engineer)
   - **never-provisioned**: workspace directory absent (the expected state for
     inactive claude_local agents; NOT a latched error)
4. For each latched agent, either creates a board-level issue tagged
   ``[SRE-LATCHED]`` (Board-action needed: ``POST /api/agents/{id}/clear-error``)
   or appends a timestamp comment to the existing open issue if one is found
   within the dedup window. The fingerprint in the comment dedupes across
   restarts.

AC-3 (false-positive rate ≤ 0 over a 7-day window) is the dominant constraint:
the dedup fingerprint + never-provisioned skip + heartbeat-grace window are
what holds it.

Environment variables
---------------------
PAPERCLIP_API_URL       — Base URL of the Paperclip API
PAPERCLIP_API_KEY       — Bearer token for authentication
PAPERCLIP_COMPANY_ID    — Company UUID
PAPERCLIP_WORKSPACE_ROOT — Override the default workspace root
                           (``~/.paperclip/instances/default/workspaces``)

Exit codes
----------
0 — clean (no latched errors)
1 — configuration / API failure
2 — latched errors detected (dry-run or live)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

__version__ = "1.1.0"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_DRY_RUN_LATCHED = 2

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD_MINUTES = 10
DEFAULT_DEDUP_HOURS = 6
DEFAULT_WORKSPACE_ROOT = os.path.expanduser("~/.paperclip/instances/default/workspaces")
ADAPTER_SCOPE = "claude_local"
SENTINEL_PREFIX = "<!-- latched-watchdog"
SENTINEL_VERSION = "v1"

# ---------------------------------------------------------------------------
# HTTP client (stdlib-only, mirrors stale_checkout_watchdog.py)
# ---------------------------------------------------------------------------


class HttpClient:
    """Thin wrapper around urllib for Paperclip API calls."""

    def __init__(self, base_url: str, api_key: str, timeout: int = 30) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Paperclip-Run-Id": os.environ.get("PAPERCLIP_RUN_ID", ""),
        }

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        if query:
            url += "?" + urlencode(query)
        headers = self._headers()
        data = json.dumps(body).encode("utf-8") if body else None
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(
                f"HTTP {exc.code} {exc.reason} on {method} {path}: {raw[:200]}"
            ) from exc

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, query=query)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, body=body)


# ---------------------------------------------------------------------------
# Pure logic (testable without network)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LatchedAgent:
    """A claude_local agent in `error` past the heartbeat threshold."""

    agent_id: str
    name: str
    url_key: str | None
    status: str
    error_reason: str | None
    last_heartbeat_at: datetime
    classification: str  # "transient" | "terminal"
    workspace_path: str
    workspace_probe: str  # one-line probe description


def parse_agents_response(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the raw list of agents as the API gave it back."""
    return list(data or [])


def is_latched(
    agent: dict[str, Any],
    *,
    threshold_minutes: int,
    now: datetime,
    adapter_scope: str = ADAPTER_SCOPE,
) -> bool:
    """Return True if *agent* matches the latched-error criteria.

    A latched agent is:
      - adapterType == adapter_scope (default "claude_local")
      - status == "error"
      - lastHeartbeatAt older than threshold
    """
    if agent.get("adapterType") != adapter_scope:
        return False
    if agent.get("status") != "error":
        return False
    last_hb = _parse_iso(agent.get("lastHeartbeatAt"))
    if last_hb is None:
        # No heartbeat ever — treat as latched (matches the NFM-3993 first signal)
        return True
    return last_hb < (now - timedelta(minutes=threshold_minutes))


def classify_workspace(workspace_path: str) -> tuple[str, str]:
    """Return (classification, probe_description) for an agent's workspace.

    Three outcomes:
      - "never_provisioned" — directory does not exist (expected for inactive
        claude_local agents; the watchdog SKIPS these)
      - "transient" — directory exists but `.git` is missing (NFM-3935 RCA)
      - "terminal" — directory exists and has `.git` (different failure mode)
    """
    if not os.path.isdir(workspace_path):
        return "never_provisioned", f"workspace missing at {workspace_path}"
    git_anchor = os.path.join(workspace_path, ".git")
    if os.path.isdir(git_anchor) or os.path.isfile(git_anchor):
        return "terminal", f"workspace OK ({workspace_path}); .git anchor present"
    return "transient", (f"workspace present at {workspace_path} but missing .git anchor")


def build_fingerprint(agent_id: str, error_reason: str | None) -> str:
    """Stable hash of the (agent, errorReason) pair — used for dedup."""
    reason_part = error_reason if error_reason is not None else "<none>"
    blob = f"{agent_id}|{reason_part.strip()}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build_issue_title(agent: dict[str, Any]) -> str:
    name = (agent.get("name") or "Unknown").strip()
    return f"[SRE-LATCHED] {name} stuck in error — Board clear-error needed"


def build_issue_body(
    agent: dict[str, Any],
    *,
    fingerprint: str,
    classification: str,
    workspace_probe: str,
    threshold_minutes: int,
    now: datetime,
) -> str:
    """Compose the description for a new latched-error board issue."""
    agent_id = agent.get("id", "")
    name = agent.get("name", "Unknown")
    url_key = agent.get("urlKey") or ""
    error_reason = agent.get("errorReason") or "(none recorded)"
    last_hb = agent.get("lastHeartbeatAt") or "(never)"
    age_min = _age_minutes(last_hb, now=now)
    age_label = (
        f"~{age_min // 60}h {age_min % 60}m ago"
        if age_min is not None and age_min >= 60
        else f"~{age_min}m ago"
        if age_min is not None
        else "unknown"
    )

    classification_label = {
        "transient": "TRANSIENT — workspace `.git` anchor missing (NFM-3935 RCA)",
        "terminal": "TERMINAL — workspace `.git` present; error not the anchor pattern",
    }.get(classification, classification)

    clear_error_url = (
        f"{os.environ.get('PAPERCLIP_API_URL', 'PAPERCLIP_API_URL')}"
        f"/api/agents/{agent_id}/clear-error"
    )
    board_link = (
        f"{os.environ.get('PAPERCLIP_API_URL', 'PAPERCLIP_API_URL')}/agents/{url_key}"
        if url_key
        else ""
    )

    sentinel = (
        f"{SENTINEL_PREFIX}: agentId={agent_id} "
        f"fingerprint={fingerprint} version={SENTINEL_VERSION} -->"
    )

    return (
        f"## Latched error detected\n\n"
        f"- **Agent**: `{name}` (`{agent_id}`)\n"
        f"- **Adapter**: `{agent.get('adapterType', '?')}`\n"
        f"- **Status**: `{agent.get('status', '?')}`\n"
        f"- **Last heartbeat**: `{last_hb}` ({age_label})\n"
        f"- **Threshold**: `{threshold_minutes} min`\n"
        f"- **Classification**: {classification_label}\n"
        f"- **Workspace probe**: {workspace_probe}\n"
        f"- **errorReason**: `{error_reason}`\n\n"
        f"## Board action required\n\n"
        f"The agent has been in `error` for longer than the heartbeat grace "
        f"window. The heartbeat scheduler skips `error`-status agents, so this "
        f"state produces no automatic recovery signal.\n\n"
        f"**One-click recovery** (Board access only):\n\n"
        f"```\nPOST {clear_error_url}\n```\n\n"
        f"Agent page: {board_link or '(no urlKey)'}\n\n"
        f"## Runbook\n\n"
        f"See `docs/runbooks/latched-error-recovery.md`. "
        + (
            f"For TRANSIENT cases (`.git` missing), the operational fix is one line:\n\n"
            f"```bash\n"
            f"cd ~/.paperclip/instances/default/workspaces/{agent_id} && git init -q .\n"
            f"```\n\n"
            f"Then re-run the watchdog or invoke the clear-error route above.\n\n"
            if classification == "transient"
            else f"For TERMINAL cases the workspace `.git` anchor is present, so the "
            f"`git init` fix does not apply. Escalate to CTO Research / Lead Engineer.\n\n"
        )
        + f"---\n\n"
        f"{sentinel}\n"
    )


def build_dedup_comment(
    agent: dict[str, Any],
    *,
    fingerprint: str,
    threshold_minutes: int,
    now: datetime,
) -> str:
    """Short timestamp comment for an already-open latched issue."""
    last_hb = agent.get("lastHeartbeatAt") or "(never)"
    age_min = _age_minutes(last_hb, now=now)
    age_label = f"{age_min}m" if age_min is not None else "unknown"
    sentinel = (
        f"{SENTINEL_PREFIX}: agentId={agent.get('id', '')} "
        f"fingerprint={fingerprint} version={SENTINEL_VERSION} -->"
    )
    return (
        f"[SRE-LATCHED-CHECK] Still latched — heartbeat age {age_label} "
        f"(threshold {threshold_minutes}m). No new issue created.\n\n"
        f"{sentinel}\n"
    )


def extract_sentinel(body: str) -> dict[str, str] | None:
    """Parse a sentinel line out of a description/comment body."""
    if not body:
        return None
    match = re.search(
        rf"{re.escape(SENTINEL_PREFIX)}:\s*(?P<rest>.+?)\s*-->",
        body,
        flags=re.DOTALL,
    )
    if not match:
        return None
    rest = match.group("rest")
    fields: dict[str, str] = {}
    for key, value in re.findall(r"(\w+)=([^\s]+)", rest):
        fields[key] = value
    return fields


# ---------------------------------------------------------------------------
# API interactions
# ---------------------------------------------------------------------------


def fetch_agents(api: HttpClient, company_id: str) -> list[dict[str, Any]]:
    """List every agent in the company. The list endpoint rejects query params."""
    return parse_agents_response(api.get(f"/api/companies/{company_id}/agents"))


def fetch_issues(
    api: HttpClient,
    company_id: str,
    *,
    page_size: int = 100,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """Paginate through all issues for the company (collection endpoint)."""
    all_issues: list[dict[str, Any]] = []
    offset = 0
    while True:
        data = api.get(
            f"/api/companies/{company_id}/issues",
            query={"limit": page_size, "offset": offset},
        )
        page = list(data or [])
        if not page:
            break
        all_issues.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
        if offset // page_size >= max_pages:
            break
    return all_issues


def find_existing_issue(
    issues: list[dict[str, Any]],
    *,
    agent_id: str,
    fingerprint: str,
    dedup_hours: int,
    now: datetime,
) -> dict[str, Any] | None:
    """Return the most-recent open issue tagged with this agent + fingerprint.

    Matches by parsing the sentinel from the description. Skips closed issues
    and issues older than ``dedup_hours``.
    """
    cutoff = now - timedelta(hours=dedup_hours)
    for issue in sorted(
        issues,
        key=lambda i: i.get("updatedAt") or i.get("createdAt") or "",
        reverse=True,
    ):
        if (issue.get("status") or "").lower() in {"done", "cancelled", "closed"}:
            continue
        updated = _parse_iso(issue.get("updatedAt") or issue.get("createdAt"))
        if updated and updated < cutoff:
            continue
        title = issue.get("title") or ""
        if "[SRE-LATCHED]" not in title:
            continue
        sentinel = extract_sentinel(issue.get("description") or "")
        if not sentinel:
            continue
        if sentinel.get("agentId") == agent_id and sentinel.get("fingerprint") == fingerprint:
            return issue
    return None


def create_latched_issue(
    api: HttpClient,
    company_id: str,
    *,
    title: str,
    body: str,
) -> dict[str, Any]:
    """POST a new board-level issue tagged [SRE-LATCHED]."""
    return cast(
        dict[str, Any],
        api.post(
            f"/api/companies/{company_id}/issues",
            body={
                "title": title,
                "description": body,
                "status": "todo",
                "priority": "critical",
                "workMode": "standard",
            },
        ),
    )


def post_comment(api: HttpClient, issue_id: str, body: str) -> dict[str, Any]:
    """POST a comment to an existing issue."""
    return cast(
        dict[str, Any],
        api.post(f"/api/issues/{issue_id}/comments", body={"body": body}),
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_watchdog(
    api: HttpClient,
    company_id: str,
    *,
    threshold_minutes: int,
    dedup_hours: int,
    dry_run: bool,
    workspace_root: str,
    now: datetime | None = None,
) -> tuple[int, list[LatchedAgent]]:
    """Single watchdog pass. Returns (exit_code, latched_agents)."""
    when = now or datetime.now(UTC)

    try:
        agents = fetch_agents(api, company_id)
    except Exception as exc:
        logger.error("Failed to fetch agents: %s", exc)
        return EXIT_ERROR, []

    logger.info(
        "Scanned %d total agents (threshold=%dm, adapter=%s)",
        len(agents),
        threshold_minutes,
        ADAPTER_SCOPE,
    )

    latched: list[LatchedAgent] = []
    for agent in agents:
        if not is_latched(
            agent,
            threshold_minutes=threshold_minutes,
            now=when,
        ):
            continue
        workspace_path = os.path.join(workspace_root, agent["id"])
        classification, probe = classify_workspace(workspace_path)
        if classification == "never_provisioned":
            logger.info(
                "[SKIP] %s — never provisioned (workspace absent)",
                agent.get("name"),
            )
            continue
        last_hb = _parse_iso(agent.get("lastHeartbeatAt")) or when
        latched.append(
            LatchedAgent(
                agent_id=agent["id"],
                name=agent.get("name", "Unknown"),
                url_key=agent.get("urlKey"),
                status=agent.get("status", "error"),
                error_reason=agent.get("errorReason"),
                last_heartbeat_at=last_hb,
                classification=classification,
                workspace_path=workspace_path,
                workspace_probe=probe,
            )
        )

    if not latched:
        logger.info("No latched claude_local errors detected.")
        return EXIT_SUCCESS, []

    logger.warning("Detected %d latched claude_local agent(s).", len(latched))
    for la in latched:
        logger.warning(
            "  - %s (%s) classification=%s lastHB=%s",
            la.name,
            la.agent_id[:8],
            la.classification,
            la.last_heartbeat_at.isoformat(),
        )

    # Dedup against existing open issues
    try:
        all_issues = fetch_issues(api, company_id)
    except Exception as exc:
        logger.error("Failed to fetch issues for dedup: %s", exc)
        return EXIT_ERROR, latched

    # Index existing [SRE-LATCHED] issues by agentId for O(1) match
    open_latched = [
        i
        for i in all_issues
        if "[SRE-LATCHED]" in (i.get("title") or "")
        and (i.get("status") or "").lower() not in {"done", "cancelled", "closed"}
    ]

    fingerprint_by_agent = {}
    for la in latched:
        # Re-fetch the agent dict to access errorReason; cheapest: build from LatchedAgent
        fingerprint_by_agent[la.agent_id] = build_fingerprint(la.agent_id, la.error_reason)

    actions_created = 0
    actions_updated = 0
    for la in latched:
        agent_dict = {
            "id": la.agent_id,
            "name": la.name,
            "urlKey": la.url_key,
            "status": la.status,
            "errorReason": la.error_reason,
            "lastHeartbeatAt": la.last_heartbeat_at.isoformat(),
            "adapterType": ADAPTER_SCOPE,
        }
        fingerprint = fingerprint_by_agent[la.agent_id]
        existing = find_existing_issue(
            open_latched,
            agent_id=la.agent_id,
            fingerprint=fingerprint,
            dedup_hours=dedup_hours,
            now=when,
        )
        if existing is None:
            title = build_issue_title(agent_dict)
            body = build_issue_body(
                agent_dict,
                fingerprint=fingerprint,
                classification=la.classification,
                workspace_probe=la.workspace_probe,
                threshold_minutes=threshold_minutes,
                now=when,
            )
            if dry_run:
                logger.info(
                    "[DRY-RUN] Would create issue for %s (fp=%s)",
                    la.name,
                    fingerprint,
                )
                actions_created += 1
                continue
            try:
                created = create_latched_issue(api, company_id, title=title, body=body)
            except Exception as exc:
                logger.error("Failed to create issue for %s: %s", la.name, exc)
                continue
            actions_created += 1
            logger.warning(
                "[CREATED] %s issue=%s fingerprint=%s",
                la.name,
                created.get("identifier") or created.get("id"),
                fingerprint,
            )
        else:
            comment_body = build_dedup_comment(
                agent_dict,
                fingerprint=fingerprint,
                threshold_minutes=threshold_minutes,
                now=when,
            )
            if dry_run:
                logger.info(
                    "[DRY-RUN] Would post dedup comment on %s for %s",
                    existing.get("identifier"),
                    la.name,
                )
                actions_updated += 1
                continue
            try:
                post_comment(api, existing["id"], comment_body)
            except Exception as exc:
                logger.error(
                    "Failed to post dedup comment on %s: %s",
                    existing.get("identifier"),
                    exc,
                )
                continue
            actions_updated += 1
            logger.warning(
                "[UPDATED] %s issue=%s fingerprint=%s",
                la.name,
                existing.get("identifier") or existing.get("id"),
                fingerprint,
            )

    if dry_run:
        logger.info(
            "[DRY-RUN] Would create %d issue(s), update %d.",
            actions_created,
            actions_updated,
        )
        return EXIT_DRY_RUN_LATCHED, latched
    logger.info("Created %d issue(s), updated %d.", actions_created, actions_updated)
    return EXIT_SUCCESS, latched


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def _age_minutes(last_hb_iso: Any, *, now: datetime) -> int | None:
    ts = _parse_iso(last_hb_iso)
    if ts is None:
        return None
    delta = now - ts
    return max(0, int(delta.total_seconds() // 60))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watchdog: emit board-level issues for latched claude_local agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables:\n"
            "  PAPERCLIP_API_URL        Base URL of the Paperclip API\n"
            "  PAPERCLIP_API_KEY        Bearer token for authentication\n"
            "  PAPERCLIP_COMPANY_ID     Company UUID\n"
            "  PAPERCLIP_WORKSPACE_ROOT Override the workspace root\n"
            "\n"
            "Exit codes:\n"
            "  0  Clean (no latched errors detected, or all alerts handled)\n"
            "  1  Configuration / API failure\n"
            "  2  Latched errors detected (dry-run mode only)\n"
        ),
    )
    parser.add_argument(
        "--threshold-minutes",
        type=int,
        default=DEFAULT_THRESHOLD_MINUTES,
        help="Heartbeat age (in minutes) after which an error-state agent is "
        "considered latched (default: 10)",
    )
    parser.add_argument(
        "--dedup-hours",
        type=int,
        default=DEFAULT_DEDUP_HOURS,
        help="Window during which a duplicate fingerprint is folded into a "
        "timestamp comment instead of a new issue (default: 6)",
    )
    parser.add_argument(
        "--workspace-root",
        type=str,
        default=os.environ.get("PAPERCLIP_WORKSPACE_ROOT", DEFAULT_WORKSPACE_ROOT),
        help="Root directory holding per-agent workspaces (default: "
        "~/.paperclip/instances/default/workspaces)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Detect latched agents and log actions, but never POST.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=True,
        help="Run a single pass and exit (default).",
    )
    parser.add_argument(
        "--loop-seconds",
        type=int,
        default=0,
        help="If > 0, loop the watchdog every N seconds (default: 0 = single pass).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable DEBUG-level logging.",
    )
    return parser.parse_args(argv)


def main(args: argparse.Namespace | None = None) -> int:
    if args is None:
        args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    base_url = os.environ.get("PAPERCLIP_API_URL", "").rstrip("/")
    api_key = os.environ.get("PAPERCLIP_API_KEY", "")
    company_id = os.environ.get("PAPERCLIP_COMPANY_ID", "")
    if not base_url or not api_key or not company_id:
        logger.error(
            "Missing required env vars: PAPERCLIP_API_URL, PAPERCLIP_API_KEY, PAPERCLIP_COMPANY_ID"
        )
        return EXIT_ERROR

    api = HttpClient(base_url, api_key)

    if args.loop_seconds > 0:
        logger.info(
            "Looping every %ds (threshold=%dm, dedup=%dh)",
            args.loop_seconds,
            args.threshold_minutes,
            args.dedup_hours,
        )
        import time

        while True:
            rc, _ = run_watchdog(
                api,
                company_id,
                threshold_minutes=args.threshold_minutes,
                dedup_hours=args.dedup_hours,
                dry_run=args.dry_run,
                workspace_root=args.workspace_root,
            )
            # Only loop on 0 or 2 — not on 1 (configuration / API failure)
            if rc == EXIT_ERROR:
                logger.error("API error — exiting loop. Fix the upstream and rerun.")
                return rc
            time.sleep(args.loop_seconds)

    rc, _ = run_watchdog(
        api,
        company_id,
        threshold_minutes=args.threshold_minutes,
        dedup_hours=args.dedup_hours,
        dry_run=args.dry_run,
        workspace_root=args.workspace_root,
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
