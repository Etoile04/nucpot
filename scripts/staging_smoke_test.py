#!/usr/bin/env python3
"""NFM / NucPot staging smoke test.

Hits the public staging endpoints after a deploy and fails (exit 1) if any
critical check does not pass. Uses only the standard library so it runs in the
GitHub Actions runner, on the host, or inside the CI image without extra deps.

Configuration (env, lowest-wins with CLI flags):
    STAGING_WEB_URL  default https://staging.nucpot.dpdns.org
    STAGING_API_URL  default https://staging-api.nucpot.dpdns.org
    SMOKE_TIMEOUT    per-request timeout seconds        (default 10)
    SMOKE_RETRIES    retry attempts per check            (default 2)
    SMOKE_RETRY_DELAY retry delay seconds                (default 5)
    GITHUB_OUTPUT    when set, writes machine-readable summary for Actions

Exit codes:
    0  all checks passed
    1  one or more checks failed
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USER_AGENT = "NucPot-StagingSmokeTest/1.0"


@dataclass(frozen=True)
class Check:
    """A single staging endpoint assertion."""

    name: str
    url: str
    expected_status: int = 200
    expected_contains: Optional[str] = None
    max_response_ms: int = 5000
    severity: str = "P1"  # P0 = blocks rollback-on-success; P1 = warning


def build_checks(web_url: str, api_url: str) -> list[Check]:
    """Build the staging check list from the configured base URLs."""
    web_url = web_url.rstrip("/")
    api_url = api_url.rstrip("/")
    return [
        Check(
            name="API health",
            url=f"{api_url}/api/health",
            expected_status=200,
            expected_contains="ok",
            severity="P0",
        ),
        Check(
            name="API potentials",
            url=f"{api_url}/api/potentials",
            expected_status=200,
            max_response_ms=10_000,
            severity="P1",
        ),
        Check(
            name="Web homepage",
            url=f"{web_url}/",
            expected_status=200,
            severity="P0",
        ),
        Check(
            name="Web browse page",
            url=f"{web_url}/browse",
            expected_status=200,
            severity="P1",
        ),
    ]


def run_check(check: Check, timeout: int, retries: int, retry_delay: int) -> tuple[bool, str]:
    """Run one check with retries. Returns (success, detail)."""
    last_detail = "no attempt made"
    for attempt in range(1, retries + 2):
        start = time.monotonic()
        try:
            req = Request(check.url, method="GET")
            req.add_header("User-Agent", USER_AGENT)
            with urlopen(req, timeout=timeout) as resp:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                body = resp.read().decode("utf-8", errors="replace")

                if resp.status != check.expected_status:
                    last_detail = f"expected {check.expected_status}, got {resp.status}"
                elif check.expected_contains and check.expected_contains not in body:
                    last_detail = f"body missing '{check.expected_contains}'"
                elif elapsed_ms > check.max_response_ms:
                    last_detail = f"{elapsed_ms}ms > {check.max_response_ms}ms budget"
                else:
                    return True, f"ok ({elapsed_ms}ms)"

        except HTTPError as exc:
            last_detail = f"HTTP {exc.code}: {exc.reason}"
        except (URLError, TimeoutError, OSError) as exc:
            last_detail = f"connection error: {exc}"
        except Exception as exc:  # noqa: BLE001 — last-resort, never crash the runner
            last_detail = f"unexpected error: {exc}"

        if attempt <= retries:
            time.sleep(retry_delay)

    return False, last_detail


def parse_args(argv: list[str]) -> argparse.Namespace:
    env = os.environ
    parser = argparse.ArgumentParser(description="NFM/NucPot staging smoke test")
    parser.add_argument("--web-url", default=env.get("STAGING_WEB_URL", "https://staging.nucpot.dpdns.org"))
    parser.add_argument("--api-url", default=env.get("STAGING_API_URL", "https://staging-api.nucpot.dpdns.org"))
    parser.add_argument("--timeout", type=int, default=int(env.get("SMOKE_TIMEOUT", "10")))
    parser.add_argument("--retries", type=int, default=int(env.get("SMOKE_RETRIES", "2")))
    parser.add_argument("--retry-delay", type=int, default=int(env.get("SMOKE_RETRY_DELAY", "5")))
    return parser.parse_args(argv)


def write_github_output(results: list[tuple[Check, bool, str]]) -> None:
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if not gh_output:
        return
    failures = [r for r in results if not r[1]]
    with open(gh_output, "a", encoding="utf-8") as fh:
        fh.write(f"smoke_passed={str(not failures).lower()}\n")
        fh.write(f"smoke_total={len(results)}\n")
        fh.write(f"smoke_failed={len(failures)}\n")


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    checks = build_checks(args.web_url, args.api_url)

    print(f"Staging smoke test — {len(checks)} checks")
    print(f"  web: {args.web_url}")
    print(f"  api: {args.api_url}")
    print(f"  timeout={args.timeout}s retries={args.retries} retry_delay={args.retry_delay}s")
    print()

    results: list[tuple[Check, bool, str]] = []
    for check in checks:
        ok, detail = run_check(check, args.timeout, args.retries, args.retry_delay)
        icon = "PASS" if ok else "FAIL"
        print(f"  [{check.severity}] {icon} {check.name} ({check.url}) — {detail}")
        results.append((check, ok, detail))

    write_github_output(results)

    failures = [r for r in results if not r[1]]
    p0_failures = [r for r in failures if r[0].severity == "P0"]
    if failures:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"\n{len(failures)}/{len(results)} checks FAILED at {stamp} ({len(p0_failures)} P0).")
        return 1

    print(f"\nAll {len(results)} staging checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
