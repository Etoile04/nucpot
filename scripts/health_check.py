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
from urllib.request import ProxyHandler, Request, build_opener, urlopen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TIMEOUT_SECONDS = 10
MAX_RESPONSE_TIME_MS = 5000
RETRY_COUNT = 2
RETRY_DELAY_SECONDS = 5

# NFM-5279: opener with proxies explicitly disabled. The prod sentinel host's
# macOS system proxy (v2cloud 127.0.0.1:7892) applies to bare urlopen() via
# getproxies(), so every check measures host → proxy → upstream → CF edge.
# P0 connection-class failures get ONE confirmation attempt through this
# direct-egress opener: direct egress from the host reaches the real CF edges
# (calibration 2026-10-01). No-op where no system proxy exists (GHA runners)
# — the confirmation only runs on a P0 failure path.
NO_PROXY_OPENER = build_opener(ProxyHandler({}))


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


def _confirm_p0_direct_egress(target: CheckTarget, failure: CheckResult) -> CheckResult:
    """NFM-5279 — one direct-egress confirmation for a P0 connection-class failure.

    Called only when a P0 target failed without the edge demonstrably
    answering wrong content (connection errors, timeouts incl.
    retry-exhausted, latency over budget) — the failure pattern of a local
    proxy-vantage flap (NFM-5277). Re-probes the same URL through
    ``NO_PROXY_OPENER`` with status + ``expected_contains`` only (no latency
    budget on the confirmation). A correct direct answer means the edge is
    healthy: return success annotated as a vantage flap so the P0
    streak/latch never sees the failure. Anything else — exception, wrong
    status, wrong body — returns the original failure unchanged (page path).
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


def check_url(target: CheckTarget) -> CheckResult:
    """Perform a single health check with retry logic."""
    last_error: str | None = None
    # NFM-5279: True when the latest failure is connection-class (the edge
    # never answered, or answered too slowly) — eligible for one direct-egress
    # confirmation on P0 targets once retries are exhausted.
    vantage_suspect = False

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            start = time.monotonic()
            req = Request(target.url, method="GET")
            req.add_header("User-Agent", "NucPot-HealthCheck/1.0")

            with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
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
                        return _confirm_p0_direct_egress(target, latency_failure)
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
    if vantage_suspect and target.severity == "P0":
        return _confirm_p0_direct_egress(target, retry_failure)
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
        with urlopen(req, timeout=10):
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
