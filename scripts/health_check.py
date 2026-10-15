#!/usr/bin/env python3
"""NucPot site health check monitor.

Checks all critical NucPot URLs and reports failures.
Designed to run as a GitHub Actions cron workflow or standalone script.

Usage:
    python scripts/health_check.py                  # check all targets
    ALERT_WEBHOOK=https://... python scripts/health_check.py  # with alerts
"""

# ruff: noqa: F841

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

try:  # py3.11+; fallback for the py3.9 CommandLineTools interpreter on the runner
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone as _tz

    UTC = _tz.utc
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TIMEOUT_SECONDS = 10
MAX_RESPONSE_TIME_MS = 5000
RETRY_COUNT = 2
RETRY_DELAY_SECONDS = 5

# NFM-5279/NFM-5525/ADR-023: opener with proxies explicitly disabled. The
# prod sentinel host's macOS system proxy (v2cloud 127.0.0.1:7892) applies
# to bare urlopen() via getproxies(), so a proxied fetch measures
# host → proxy → upstream → CF edge and a user VPN flap reads as site
# trouble. ADR-023 (2026-10-15, NFM-5540 → NFM-5541, implemented NFM-5542)
# makes DIRECT THE PRIMARY lane for every sentinel python egress: this
# opener now carries check_url()'s primary fetch (and send_alert()'s
# webhook POST; the chunk-scan heredoc in nfm-site-monitor.sh got the same
# opener). The NFM-5279/NFM-5525 two-stage confirmation ladder stays —
# UNCHANGED — as the failure-path cross-check (one direct re-probe + one
# curl-direct probe), covering python-lane transients like the
# 2026-10-14T09:34Z gaierror. Records correction (NFM-5540): the 10-14
# site-monitor blind windows first attributed to CN-side ClientHello DPI
# were LOCAL proxy-vantage defects — superseded (ADR-013's broker/GitHub
# DPI rulings are a different subsystem and remain valid). No-op where no
# system proxy exists (GHA runners).
NO_PROXY_OPENER = build_opener(ProxyHandler({}))

# NFM-5525: curl binary for the second confirmation stage. Absolute path
# because GUI-launched LaunchAgents have a sparse PATH (NFM-4579); env-seam
# overridable so the test suite can substitute a hermetic stub.
CURL_BIN = "/usr/bin/curl"
CURL_ENV_SEAM = "NFM_HEALTH_CURL"


@dataclass(frozen=True)
class CheckTarget:
    """A single URL to monitor."""

    name: str
    url: str
    expected_status: int = 200
    expected_contains: str | None = None
    max_response_ms: int = MAX_RESPONSE_TIME_MS
    severity: str = "P1"  # P0 or P1


TARGETS: list[CheckTarget] = [
    CheckTarget(
        name="Frontend Homepage",
        url="https://nucpot.dpdns.org",
        expected_status=200,
        severity="P1",
    ),
    CheckTarget(
        name="Frontend Browse Page",
        url="https://nucpot.dpdns.org/browse",
        expected_status=200,
        severity="P1",
    ),
    CheckTarget(
        name="Backend API Health",
        url="https://nucpot.dpdns.org/api/v1/health",
        expected_status=200,
        expected_contains='"ok"',
        # P0 signals availability only: budget == hard socket timeout, so the
        # chronic CN↔edge RTT tail (1-3.3s baseline) cannot escalate slowness
        # to CRITICAL while the origin is healthy (NFM-5263 flap cluster).
        # Latency regression stays visible via the 5s-default P1 probes.
        max_response_ms=TIMEOUT_SECONDS * 1000,
        severity="P0",
    ),
    CheckTarget(
        name="Backend API Potentials",
        url="https://nucpot.dpdns.org/api/v1/potentials",
        expected_status=200,
        max_response_ms=10000,
        severity="P1",
    ),
    CheckTarget(
        name="Backend API Reference Values",
        url="https://nucpot.dpdns.org/api/v1/reference-values/pending-review",
        expected_status=200,
        max_response_ms=10000,
        severity="P1",
    ),
    CheckTarget(
        name="AutoVC API Health",
        url="https://verify.nucpot.dpdns.org/api/health",
        expected_status=200,
        expected_contains='"ok"',
        # AutoVC serves a headless API: root `/` is an origin-normal 404 —
        # monitor /api/health only (NFM-5269). P0 with the same availability
        # budget as Backend API Health (NFM-5263): budget == hard socket
        # timeout so the CN↔edge RTT tail (observed 2.4s on this route)
        # cannot escalate slowness to CRITICAL while the origin is healthy.
        max_response_ms=TIMEOUT_SECONDS * 1000,
        severity="P0",
    ),
    CheckTarget(
        name="Supabase Health",
        url="https://gzhiqyopzlmnkdzammhx.supabase.co/rest/v1/",
        expected_status=401,  # 401 = service is up, requires auth
        severity="P2",
    ),
]


# ---------------------------------------------------------------------------
# Check logic
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """Result of a single health check."""

    target: CheckTarget
    success: bool
    status_code: int | None = None
    response_time_ms: int | None = None
    error: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def _confirm_direct_egress(target: CheckTarget, failure: CheckResult) -> CheckResult:
    """NFM-5279 — one direct-egress python confirmation for a connection-class failure.

    Called only when a target failed without the edge demonstrably answering
    wrong content (connection errors, timeouts incl. retry-exhausted,
    latency over budget) — the failure pattern of a local proxy-vantage flap
    (NFM-5277). Re-probes the same URL through ``NO_PROXY_OPENER`` with
    status + ``expected_contains`` only (no latency budget on the
    confirmation). A correct direct answer means the edge is healthy: return
    success annotated as a vantage flap so the streak/latch never sees the
    failure. Anything else — exception, wrong status, wrong body — returns
    the original failure unchanged (page path). Stage 1 of the NFM-5525
    confirmation ladder (see ``_confirm_connection_class``).
    """
    try:
        start = time.monotonic()
        req = Request(target.url, method="GET")
        req.add_header("User-Agent", "NucPot-HealthCheck/1.0")

        with NO_PROXY_OPENER.open(req, timeout=TIMEOUT_SECONDS) as resp:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            body = resp.read().decode("utf-8", errors="replace")

            if resp.status != target.expected_status:
                return failure
            if target.expected_contains and target.expected_contains not in body:
                return failure

            return CheckResult(
                target=target,
                success=True,
                status_code=resp.status,
                response_time_ms=elapsed_ms,
                error=(
                    f"proxy-vantage failure ({failure.error}), "
                    f"direct-egress confirm OK {elapsed_ms / 1000:.1f}s — vantage flap, not edge"
                ),
            )
    except Exception:
        return failure


def _confirm_via_curl(target: CheckTarget, failure: CheckResult) -> CheckResult:
    """NFM-5525 — curl-backed second confirmation stage (direct egress).

    Covers the complementary failure class to stage 1: the python direct
    path itself reset or DNS-blipped (a transient ``gaierror`` was observed
    mid-episode 2026-10-14T09:34Z while curl direct kept serving 200s).
    Runs the curl binary via the ``NFM_HEALTH_CURL`` env seam (absolute
    path — GUI-launched agents have a sparse PATH, NFM-4579), direct
    (``--noproxy '*'`` — the system proxy is the flapping vantage), same
    UA, hard socket budget. Verifies status + ``expected_contains`` only —
    no latency budget on a confirmation (NFM-5279 semantics). Any error,
    wrong status, or wrong body returns the original failure unchanged:
    this stage hardens verification, it never manufactures a false green.
    """
    curl_bin = os.environ.get(CURL_ENV_SEAM, CURL_BIN)
    try:
        start = time.monotonic()
        proc = subprocess.run(
            [
                curl_bin,
                "-sS",
                "-L",
                "--noproxy",
                "*",
                "--max-time",
                str(TIMEOUT_SECONDS),
                "-A",
                "NucPot-HealthCheck/1.0",
                "-w",
                "\n%{http_code}",
                target.url,
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS + 5,
            check=False,
        )
        if proc.returncode != 0:
            return failure

        body, _, code_str = proc.stdout.rpartition("\n")
        status_code = int(code_str)
        if status_code != target.expected_status:
            return failure
        if target.expected_contains and target.expected_contains not in body:
            return failure

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return CheckResult(
            target=target,
            success=True,
            status_code=status_code,
            response_time_ms=elapsed_ms,
            error=(
                f"python-lane failure ({failure.error}), "
                f"curl direct-egress confirm OK {elapsed_ms / 1000:.1f}s — "
                f"vantage flap, not edge (NFM-5525)"
            ),
        )
    except Exception:
        return failure


def _confirm_connection_class(target: CheckTarget, failure: CheckResult) -> CheckResult:
    """NFM-5279 + NFM-5525 confirmation ladder for connection-class failures.

    Stage 1 — one direct-egress python re-probe (NFM-5279): survives local
    proxy-vantage flaps; a correct answer means the edge is healthy and the
    failure was a vantage artifact.
    Stage 2 — one curl-backed direct probe (NFM-5525): survives the
    complementary class where the python direct path is itself reset or
    DNS-blipped.
    A pass at either stage is annotated with the vantage evidence so the
    flap stays observable; anything else returns the original failure
    unchanged (page path).
    """
    confirmed = _confirm_direct_egress(target, failure)
    if confirmed.success:
        return confirmed
    return _confirm_via_curl(target, failure)


def check_url(target: CheckTarget) -> CheckResult:
    """Perform a single health check with retry logic."""
    last_error: str | None = None
    # NFM-5279: True when the latest failure is connection-class (the edge
    # never answered, or answered too slowly) — eligible for the direct-egress
    # + curl confirmation ladder (NFM-5525, all severities) once retries are
    # exhausted. Post-ADR-023 the primary itself is already direct, so this
    # marks DIRECT-lane transients (DNS blips, local resets) for the ladder's
    # curl cross-check.
    vantage_suspect = False

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            start = time.monotonic()
            req = Request(target.url, method="GET")
            req.add_header("User-Agent", "NucPot-HealthCheck/1.0")

            # ADR-023 (NFM-5542): primary fetch goes DIRECT through the
            # no-proxy opener — a probe must never measure the user VPN
            # proxy, so a proxy-vantage flap cannot blind this lane. The
            # NFM-5279/NFM-5525 ladder below remains as the failure-path
            # cross-check for direct-lane transients.
            with NO_PROXY_OPENER.open(req, timeout=TIMEOUT_SECONDS) as resp:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                body = resp.read().decode("utf-8", errors="replace")

                if resp.status != target.expected_status:
                    return CheckResult(
                        target=target,
                        success=False,
                        status_code=resp.status,
                        response_time_ms=elapsed_ms,
                        error=(f"Expected status {target.expected_status}, got {resp.status}"),
                    )

                if target.expected_contains and target.expected_contains not in body:
                    return CheckResult(
                        target=target,
                        success=False,
                        status_code=resp.status,
                        response_time_ms=elapsed_ms,
                        error=(f"Response body does not contain '{target.expected_contains}'"),
                    )

                if elapsed_ms > target.max_response_ms:
                    latency_failure = CheckResult(
                        target=target,
                        success=False,
                        status_code=resp.status,
                        response_time_ms=elapsed_ms,
                        error=(
                            f"Response time {elapsed_ms}ms exceeds limit {target.max_response_ms}ms"
                        ),
                    )
                    if target.severity == "P0":
                        return _confirm_connection_class(target, latency_failure)
                    return latency_failure

                return CheckResult(
                    target=target,
                    success=True,
                    status_code=resp.status,
                    response_time_ms=elapsed_ms,
                )

        except HTTPError as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if exc.code == target.expected_status:
                # Non-2xx but expected (e.g. 401 for auth-required endpoints)
                return CheckResult(
                    target=target,
                    success=True,
                    status_code=exc.code,
                    response_time_ms=elapsed_ms,
                )
            last_error = f"HTTP {exc.code}: {exc.reason}"
            vantage_suspect = False  # the edge answered, just wrongly — not a vantage artifact
        except URLError as exc:
            last_error = f"Connection error: {exc.reason}"
            vantage_suspect = True
        except TimeoutError:
            last_error = f"Timeout after {TIMEOUT_SECONDS}s"
            vantage_suspect = True
        except Exception as exc:
            last_error = str(exc)
            vantage_suspect = False

        if attempt < RETRY_COUNT:
            time.sleep(RETRY_DELAY_SECONDS)

    retry_failure = CheckResult(
        target=target,
        success=False,
        error=last_error or "Unknown error after retries",
    )
    # NFM-5525: the confirmation ladder is no longer P0-only. A connection-
    # class failure on ANY named check (P1/P2 included) means the edge never
    # answered from this vantage — the proxy-vantage flap signature — so it
    # gets the same two-stage confirmation before being allowed to fail.
    if vantage_suspect:
        return _confirm_connection_class(target, retry_failure)
    return retry_failure


def run_all_checks() -> list[CheckResult]:
    """Run health checks on all targets."""
    return [check_url(t) for t in TARGETS]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_results(results: list[CheckResult]) -> str:
    """Format check results as a human-readable summary."""
    lines = ["## NucPot Health Check Report", ""]

    failures = [r for r in results if not r.success]
    successes = [r for r in results if r.success]

    if not failures:
        lines.append(f"**All {len(results)} checks passed.** ✅")
    else:
        lines.append(f"**{len(failures)}/{len(results)} checks FAILED** ❌")

    lines.append("")
    lines.append("| Target | Status | Response | Details |")
    lines.append("|--------|--------|----------|---------|")

    for r in results:
        icon = "✅" if r.success else "❌"
        ms = f"{r.response_time_ms}ms" if r.response_time_ms is not None else "—"
        detail = r.error or "OK"
        lines.append(f"| {icon} {r.target.name} | {r.target.severity} | {ms} | {detail} |")

    return "\n".join(lines)


def send_alert(results: list[CheckResult], webhook_url: str) -> None:
    """Send alert to Feishu/Lark webhook for failed checks."""
    failures = [r for r in results if not r.success]
    if not failures:
        return

    p0_failures = [r for r in failures if r.target.severity == "P0"]
    p1_failures = [r for r in failures if r.target.severity == "P1"]

    alert_level = "🔴 P0 CRITICAL" if p0_failures else "🟡 P1 WARNING"

    failure_lines = []
    for r in failures:
        ms = f"{r.response_time_ms}ms" if r.response_time_ms is not None else "N/A"
        failure_lines.append(
            f"**{r.target.name}** ({r.target.severity}): {r.error or 'Failed'} | Response: {ms}"
        )

    # Format for Feishu/Lark webhook
    content_text = (
        f"{alert_level}\n\n"
        f"**NucPot Site Monitor Alert**\n"
        f"Time: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Failed: {len(failures)}/{len(results)} checks\n\n" + "\n".join(failure_lines)
    )

    payload: dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"NucPot Monitor: {len(failures)} check(s) failed",
                },
                "template": "red" if p0_failures else "orange",
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": content_text,
                }
            ],
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = Request(
        webhook_url,
        data=data,
        method="POST",
    )
    req.add_header("Content-Type", "application/json")

    try:
        # ADR-023 (NFM-5542): webhook POST goes through the no-proxy opener —
        # no sentinel egress may transit the user VPN proxy. Unused on the
        # prod host today (no ALERT_WEBHOOK), fixed in passing with the probe
        # lanes so the posture cannot regress if a webhook is added later.
        with NO_PROXY_OPENER.open(req, timeout=10):
            pass
    except Exception as exc:
        print(f"Failed to send alert: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# GitHub Actions output
# ---------------------------------------------------------------------------


def write_github_output(results: list[CheckResult]) -> None:
    """Write GitHub Actions output variables."""
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if not gh_output:
        return

    failures = [r for r in results if not r.success]
    all_passed = len(failures) == 0

    with open(gh_output, "a", encoding="utf-8") as fh:
        fh.write(f"all_passed={str(all_passed).lower()}\n")
        fh.write(f"total_checks={len(results)}\n")
        fh.write(f"failed_checks={len(failures)}\n")
        fh.write(f"p0_failures={len([r for r in failures if r.target.severity == 'P0'])}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Run all health checks and report results."""
    print("Starting NucPot health checks...")
    print(f"Targets: {len(TARGETS)}")
    print("")

    results = run_all_checks()

    # Print report
    report = format_results(results)
    print(report)

    # NFM-5525: keep vantage flaps OBSERVABLE. The sentinel consumes only the
    # exit code + counts, so without this line a proxy-vantage flap confirmed
    # green would leave no stdout trace — the 2026-10-14 2.5h episode would
    # vanish from the evidence feed (NFM-5496 classifier relies on it).
    flap_confirms = [r for r in results if r.success and r.error]
    if flap_confirms:
        names = "; ".join(r.target.name for r in flap_confirms)
        print(
            f"\nNOTE: {len(flap_confirms)} check(s) passed via vantage confirmation "
            f"(flap class, not site): {names}"
        )

    # Send alerts if configured
    webhook_url = os.environ.get("ALERT_WEBHOOK", "")
    if webhook_url:
        send_alert(results, webhook_url)
        print("\nAlert sent to webhook.")

    # GitHub Actions output
    write_github_output(results)

    # Exit code
    failures = [r for r in results if not r.success]
    if failures:
        p0 = [r for r in failures if r.target.severity == "P0"]
        print(f"\n⚠️  {len(failures)} check(s) failed ({len(p0)} P0 critical)")
        return 1

    print("\n✅ All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
