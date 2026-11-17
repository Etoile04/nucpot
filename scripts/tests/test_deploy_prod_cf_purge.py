"""Behavioral guards on the post-cutover Cloudflare edge purge (NFM-5426).

NFM-5418: before NFM-5419, static pages pinned HTML at the CF edge for a
year (s-maxage=31536000). Every deploy that changed chunk hashes left warm
colos serving HTML whose /_next/static refs 404'd at origin — dead pages
for cache-cold visitors while HTML-200 monitors stayed green. NFM-5426
added the deploy-side purge and found the failure mode these tests pin:

* the purge must be ``purge_everything`` — a canonical-route purge list
  misses ALIAS paths (the /browse → /potentials rewrite), which is exactly
  how NFM-5418's remnant survived the first fix;
* credentials reach the GH-runner deploy path via docker/.env.prod (the
  sudo env_keep on run-deploy.sh passes only DEPLOY_SHA/PROXY_PORT, so
  the operator-managed env file compose already reads is the sanctioned
  channel), with exported env winning for manual runs;
* unprovisioned and failed purges are advisory-only: by purge time the
  origin cutover is complete and verified, and a purge problem must never
  retro-red a healthy deploy (NFM-5149 precedent);
* the block runs AFTER the web health gate and BEFORE the manifest
  recording, i.e. only once the new origin is verified live.

The tests EXECUTE the real shell block with a stubbed curl and assert
recorded argv and log lines, mirroring test_deploy_prod_throttle_gate.py.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_PROD = REPO_ROOT / "scripts" / "deploy_prod.sh"

# Records every invocation to <tmp>/calls (one line, space-joined) and exits
# NFM5426_CURL_RC (default 0).
STUB_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${NFM5426_CALLS}"
exit "${NFM5426_CURL_RC:-0}"
"""


def _write_stub(bin_dir: Path, name: str, text: str) -> None:
    stub = bin_dir / name
    stub.write_text(text, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _extract_purge_block() -> str:
    text = DEPLOY_PROD.read_text(encoding="utf-8")
    match = re.search(
        r'^echo "==> Cloudflare edge purge.*?(?=^# NFM-4271 / ADR-013)',
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, (
        "the NFM-5426 CF purge block is missing from scripts/deploy_prod.sh — "
        "it cannot have been deleted or renamed without updating these guards."
    )
    return match.group(0)


def _run_block(
    tmp_path: Path, env_overrides: dict[str, str], curl_rc: int
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_stub(bin_dir, "curl", STUB_CURL)
    calls = tmp_path / "calls"
    calls.write_text("", encoding="utf-8")
    block_file = tmp_path / "purge_block.sh"
    block_file.write_text(_extract_purge_block(), encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "NFM5426_CALLS": str(calls),
        "NFM5426_CURL_RC": str(curl_rc),
        **env_overrides,
    }
    for cf_var in ("CF_ZONE_PURGE_TOKEN", "CF_ZONE_ID"):
        if cf_var not in env_overrides:
            env.pop(cf_var, None)
    result = subprocess.run(
        ["/bin/bash", str(block_file)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    recorded = [ln for ln in calls.read_text(encoding="utf-8").splitlines() if ln]
    return result, recorded


def test_unprovisioned_skip_is_loud_and_nonfatal(tmp_path: Path) -> None:
    result, calls = _run_block(tmp_path, env_overrides={}, curl_rc=0)
    assert result.returncode == 0
    assert calls == []
    assert "SKIPPED" in result.stderr
    assert "docker/.env.prod" in result.stderr


def test_exported_creds_issue_purge_everything(tmp_path: Path) -> None:
    result, calls = _run_block(
        tmp_path,
        env_overrides={"CF_ZONE_PURGE_TOKEN": "tok-123", "CF_ZONE_ID": "zone-abc"},
        curl_rc=0,
    )
    assert result.returncode == 0
    assert len(calls) == 1
    argv = calls[0]
    assert "-X POST" in argv
    assert "Bearer tok-123" in argv
    assert "purge_everything" in argv
    assert "/zones/zone-abc/purge_cache" in argv
    assert "issued" in result.stdout


def test_env_file_creds_flow_from_docker_env_prod(tmp_path: Path) -> None:
    env_dir = tmp_path / "docker"
    env_dir.mkdir()
    (env_dir / ".env.prod").write_text(
        "CF_ZONE_PURGE_TOKEN=file-tok-9\nCF_ZONE_ID=file-zone-7\n",
        encoding="utf-8",
    )
    result, calls = _run_block(tmp_path, env_overrides={}, curl_rc=0)
    assert result.returncode == 0
    assert len(calls) == 1
    assert "Bearer file-tok-9" in calls[0]
    assert "/zones/file-zone-7/purge_cache" in calls[0]


def test_purge_api_failure_is_advisory_not_fatal(tmp_path: Path) -> None:
    result, calls = _run_block(
        tmp_path,
        env_overrides={"CF_ZONE_PURGE_TOKEN": "tok-123", "CF_ZONE_ID": "zone-abc"},
        curl_rc=22,
    )
    assert result.returncode == 0
    assert len(calls) == 1
    assert "purge FAILED" in result.stderr
    assert "curl exit 22" in result.stderr


def test_block_is_placed_after_web_health_before_manifest() -> None:
    text = DEPLOY_PROD.read_text(encoding="utf-8")
    web_health = text.index("health_first_poll http://localhost:3000/")
    purge = text.index('echo "==> Cloudflare edge purge')
    manifest = text.index("# NFM-4271 / ADR-013 §2 G4a — record the deploy manifest")
    assert web_health < purge < manifest, (
        "the NFM-5426 purge must run after the web health gate (origin "
        "cutover verified) and before the manifest recording"
    )
