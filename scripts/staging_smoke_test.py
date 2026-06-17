#!/usr/bin/env python3
"""Staging smoke tests for the NFM-DB platform (NFM-111).

Run after a staging deploy to confirm the stack is up and serving:
    python3 scripts/staging_smoke_test.py

Defaults target the staging host contract (NFM-112):
    API:  http://127.0.0.1:8001/api/v1/health
    WEB:  http://127.0.0.1:3000/

Exits 0 only when every check passes; non-zero otherwise. Uses only the
standard library so it runs in any python3 on the staging host. Pair with
scripts/staging_deploy.sh (health gate + auto-rollback). See
docs/deployment/staging-pipeline.md.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional


# --------------------------------------------------------------------------- #
# Result model (immutable)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class SmokeConfig:
    api_url: str
    web_url: str
    timeout: float
    container_prefix: str
    expected_containers: tuple[str, ...]
    skip_docker: bool


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def fetch_json(url: str, timeout: float) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", errors="replace")
        try:
            return resp.status, json.loads(body)
        except json.JSONDecodeError:
            return resp.status, body


def fetch_status(url: str, timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read(200).decode("utf-8", errors="replace").splitlines()
            return resp.status, body[0] if body else ""
    except urllib.error.HTTPError as exc:
        return exc.code, f"http error: {exc.reason}"


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_api_health(cfg: SmokeConfig) -> CheckResult:
    name = "api-health"
    try:
        status, payload = fetch_json(cfg.api_url, cfg.timeout)
    except (urllib.error.URLError, OSError) as exc:
        return CheckResult(name, False, f"unreachable: {exc}")
    if status != 200:
        return CheckResult(name, False, f"HTTP {status}")
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return CheckResult(name, False, f"unexpected body: {payload!r}")
    return CheckResult(name, True, f"ok (status={payload.get('status')})")


def check_web_reachable(cfg: SmokeConfig) -> CheckResult:
    name = "web-reachable"
    try:
        status, _line = fetch_status(cfg.web_url, cfg.timeout)
    except (urllib.error.URLError, OSError) as exc:
        return CheckResult(name, False, f"unreachable: {exc}")
    if status >= 500:
        return CheckResult(name, False, f"HTTP {status} (server error)")
    return CheckResult(name, True, f"reachable (HTTP {status})")


def _docker_ps_names(prefix: str) -> list[str]:
    try:
        proc = subprocess.run(
            ["docker", "ps", "--filter", f"name={prefix}", "--format", "{{.Names}}"],
            check=True, capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def check_container_set(cfg: SmokeConfig) -> CheckResult:
    name = "container-set"
    if cfg.skip_docker:
        return CheckResult(name, True, "skipped (--skip-docker)")
    running = _docker_ps_names(cfg.container_prefix)
    if not running:
        return CheckResult(name, False, f"no '{cfg.container_prefix}*' containers found (is docker reachable?)")
    missing = [c for c in cfg.expected_containers if c not in running]
    if missing:
        return CheckResult(name, False, f"missing containers: {missing} (running: {running})")
    return CheckResult(name, True, f"all expected containers running: {sorted(running)}")


CHECKS: tuple[Callable[[SmokeConfig], CheckResult], ...] = (
    check_api_health,
    check_web_reachable,
    check_container_set,
)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _load_staging_env_contract() -> None:
    """Load docker/.env.staging (the staging port/secrets contract) so the
    URL defaults below match the host ports the deploy actually bound — even
    when the caller didn't export them (the staging-deploy smoke SSH step only
    forwards STAGING_REPO_DIR). Only sets vars absent from the environment;
    an explicit --api-url/--web-url or a pre-exported var always wins. Looks
    in CWD first (the workflow runs from the repo root), then relative to
    this script, so it also works when invoked from elsewhere.
    """
    candidates = [
        Path("docker") / ".env.staging",
        Path(__file__).resolve().parent.parent / "docker" / ".env.staging",
    ]
    env_file = next((p for p in candidates if p.is_file()), None)
    if env_file is None:
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = val.strip().strip('"').strip("'")


def parse_args(argv: Optional[list[str]] = None) -> SmokeConfig:
    p = argparse.ArgumentParser(
        description="NFM-DB staging smoke tests (NFM-111). Exits 0 iff all checks pass."
    )
    # Honor the staging port contract (docker/.env.staging) so the smoke test
    # targets the same host ports the deploy actually bound. The host override
    # STAGING_WEB_HOST_PORT=3001 previously made web-reachable curl :3000 and
    # fail with Connection refused even though web was healthy on :3001
    # (surfaced once NFM-167 let the deploy reach the smoke step).
    _load_staging_env_contract()
    api_port = os.environ.get("STAGING_API_HOST_PORT", "8001")
    web_port = os.environ.get("STAGING_WEB_HOST_PORT", "3000")
    p.add_argument("--api-url", default=f"http://127.0.0.1:{api_port}/api/v1/health",
                   help="staging API health URL (default: %(default)s)")
    p.add_argument("--web-url", default=f"http://127.0.0.1:{web_port}/",
                   help="staging web URL (default: %(default)s)")
    p.add_argument("--timeout", type=float, default=5.0,
                   help="per-request timeout in seconds (default: %(default)s)")
    p.add_argument("--container-prefix", default="nucpot-staging-",
                   help="docker container name prefix (default: %(default)s)")
    p.add_argument("--skip-docker", action="store_true",
                   help="skip the docker container-set check")
    args = p.parse_args(argv)

    expected = (
        f"{args.container_prefix}db",
        f"{args.container_prefix}api",
        f"{args.container_prefix}web",
    )
    return SmokeConfig(
        api_url=args.api_url,
        web_url=args.web_url,
        timeout=args.timeout,
        container_prefix=args.container_prefix,
        expected_containers=expected,
        skip_docker=args.skip_docker,
    )


def run(cfg: SmokeConfig) -> Report:
    report = Report()
    for check in CHECKS:
        result = check(cfg)
        report.add(result)
        marker = "PASS" if result.passed else "FAIL"
        print(f"[{marker}] {result.name}: {result.detail}", file=sys.stderr)
    return report


def main(argv: Optional[list[str]] = None) -> int:
    cfg = parse_args(argv)
    start = time.monotonic()
    report = run(cfg)
    elapsed = time.monotonic() - start
    print(f"\n{'ALL CHECKS PASSED' if report.all_passed else 'SMOKE TESTS FAILED'} "
          f"({len([r for r in report.results if r.passed])}/{len(report.results)} ok, "
          f"{elapsed:.1f}s)", file=sys.stderr)
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
