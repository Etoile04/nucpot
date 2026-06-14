#!/usr/bin/env python3
"""Staging smoke tests for the NFM-DB platform (NFM-111).

Run after a staging deploy to confirm the stack is up and serving:
    python3 scripts/staging_smoke_test.py

Defaults are derived from the staging host contract (NFM-112) and the same
STAGING_*_HOST_PORT / STAGING_HEALTH_PATH variables the deploy writes into
docker/.env.staging (see scripts/staging_deploy.sh). When that file is present
it is auto-loaded so the defaults match the ports the stack actually maps to on
this host (e.g. STAGING_WEB_HOST_PORT may be 3001 to dodge a host conflict);
otherwise the documented defaults (API 8001, WEB 3000) are used.
    API:  http://127.0.0.1:${STAGING_API_HOST_PORT:-8001}${STAGING_HEALTH_PATH:-/api/v1/health}
    WEB:  http://127.0.0.1:${STAGING_WEB_HOST_PORT:-3000}/

Any default can still be overridden with --api-url / --web-url.

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
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


# --------------------------------------------------------------------------- #
# Staging env discovery (mirrors scripts/staging_deploy.sh)
# --------------------------------------------------------------------------- #
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STAGING_ENV_FILE = _PROJECT_ROOT / "docker" / ".env.staging"


def _load_staging_env_file() -> None:
    """Load docker/.env.staging so smoke defaults match the live deploy.

    The staging deploy writes STAGING_API_HOST_PORT / STAGING_WEB_HOST_PORT /
    STAGING_HEALTH_PATH into docker/.env.staging (see scripts/staging_deploy.sh).
    On the staging host that file reflects the actual host ports the stack maps
    to (e.g. STAGING_WEB_HOST_PORT may be 3001 to dodge a host conflict), which
    the previous hardcoded defaults (3000/8001) could never match. Values already
    present in os.environ win; a missing or malformed file is ignored so this
    never breaks a smoke run in a checkout without the env file.
    """
    try:
        text = _STAGING_ENV_FILE.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_staging_env_file()


def _default_api_url() -> str:
    port = os.environ.get("STAGING_API_HOST_PORT", "8001")
    path = os.environ.get("STAGING_HEALTH_PATH", "/api/v1/health")
    if not path.startswith("/"):
        path = "/" + path
    return f"http://127.0.0.1:{port}{path}"


def _default_web_url() -> str:
    port = os.environ.get("STAGING_WEB_HOST_PORT", "3000")
    return f"http://127.0.0.1:{port}/"


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
def parse_args(argv: Optional[list[str]] = None) -> SmokeConfig:
    p = argparse.ArgumentParser(
        description="NFM-DB staging smoke tests (NFM-111). Exits 0 iff all checks pass."
    )
    p.add_argument("--api-url", default=_default_api_url(),
                   help="staging API health URL (default: %(default)s)")
    p.add_argument("--web-url", default=_default_web_url(),
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
