"""Behavioral guards on the NFM-5898 post-cutover ISR warm-up.

NFM-5897: every prod deploy serves the web image's BUILD-TIME prerender of
'/' and '/browse' for up to ~30 min — the ISR freshness clock is anchored at
`next build` (revalidate = 1800), and the build stage cannot reach the API,
so home-data.ts fail-soft bakes empty sections (the 77150B cold payload SRE
decoded on the NFM-5897 thread). NFM-5898 adds the deploy-side fix:

* a token-guarded POST /api/revalidate-landing — forced on-demand
  revalidation, because a plain GET would cache-HIT the same build-anchored
  entry (this is why the warm-up is a POST + purge, not a GET);
* cold-window capture of the served prerender BEFORE the warm-up, closing
  the NFM-5897 residual (the 77150B body was never content-captured);
* warm-body GETs + a cold→warm byte report in every deploy log.

The tests EXECUTE the real shell block with a stubbed curl and assert
recorded argv, written files and log lines, mirroring
test_deploy_prod_cf_purge.py. Ordering guards pin the block between the web
health gate and the NFM-5420 CF purge (the edge's first refetch after purge
must hit a warm origin), and pin the per-deploy token export before the
`docker compose up` cutover (the container must boot already carrying the
token the warm-up will present).
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_PROD = REPO_ROOT / "scripts" / "deploy_prod.sh"
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"

# Records every invocation to <tmp>/calls (one line, space-joined). POSTs to
# the warm-up endpoint exit NFM5898_POST_RC; every GET with -o writes a
# distinct canned body (stub-body-<n>, incrementing) so the cold/warm pairs
# genuinely differ and the byte-report + diff assertions have teeth.
STUB_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${NFM5898_CALLS}"
case "$*" in
  *revalidate-landing*) exit "${NFM5898_POST_RC:-0}" ;;
esac
prev=""
for a in "$@"; do
  if [ "${prev}" = "-o" ]; then
    n="$(cat "${NFM5898_BODY_SEQ}" 2>/dev/null || echo 0)"
    printf 'stub-body-%s' "${n}" > "${a}"
    printf '%s' "$((n + 1))" > "${NFM5898_BODY_SEQ}"
  fi
  prev="${a}"
done
exit 0
"""


def _write_stub(bin_dir: Path, name: str, text: str) -> None:
    stub = bin_dir / name
    stub.write_text(text, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _extract_warmup_block() -> str:
    text = DEPLOY_PROD.read_text(encoding="utf-8")
    match = re.search(
        r'^echo "==> NFM-5898 ISR warm-up.*?(?=^# NFM-5420 task 3)',
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, (
        "the NFM-5898 ISR warm-up block is missing from scripts/deploy_prod.sh — "
        "it cannot have been deleted or renamed without updating these guards."
    )
    return match.group(0)


def _run_block(
    tmp_path: Path,
    env_overrides: dict[str, str],
    post_rc: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_stub(bin_dir, "curl", STUB_CURL)
    calls = tmp_path / "calls"
    calls.write_text("", encoding="utf-8")
    body_seq = tmp_path / "body-seq"
    body_seq.write_text("0", encoding="utf-8")
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    block_file = tmp_path / "warmup_block.sh"
    # The real deploy script runs `set -euo pipefail` (deploy_prod.sh, near
    # the top). Execute the fragment under the SAME shell options, otherwise
    # errexit-dependent behavior — the advisory-only failure policy — is not
    # actually exercised: without set -e a nonzero `sleep`/`mkdir` mid-block
    # is silently swallowed and the post-health-gate tail-skip class (abort
    # before the CF purge) cannot be caught by any guard here.
    block_file.write_text(
        "set -euo pipefail\n" + _extract_warmup_block(), encoding="utf-8"
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "NFM5898_CALLS": str(calls),
        "NFM5898_BODY_SEQ": str(body_seq),
        "NFM5898_POST_RC": str(post_rc),
        # The real script always runs with a minted snapshot dir, a validated
        # DEPLOY_SHA and the retry delay default; pin them so the fragment is
        # executable standalone and no test pays the real 5s retry sleep.
        "NFM_CUTOVER_SNAPSHOT_DIR": str(snapshot_dir),
        "DEPLOY_SHA": "testsha00000000000000000000000000000000",
        "NFM5898_RETRY_DELAY_S": "0",
        **env_overrides,
    }
    env.pop("ISR_WARMUP_TOKEN", None)
    env.update(env_overrides)
    # A BASH_ENV inherited from the invoking session (CI build agents and
    # local runtimes alike can set one) is sourced by EVERY non-interactive
    # bash below this point — the block shell and the stub's
    # `#!/usr/bin/env bash` shebang both — and a rc file that manages PATH
    # would silently route `curl` back to the real binary, defeating the
    # stub. Strip it: the deploy path itself runs under its own documented
    # ssh non-login zsh contract and never reads a BASH_ENV.
    env.pop("BASH_ENV", None)
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


def test_block_sits_between_web_health_gate_and_cf_purge() -> None:
    text = DEPLOY_PROD.read_text(encoding="utf-8")
    i_health = text.index('echo "Checking Web health..."')
    i_warmup = text.index('echo "==> NFM-5898 ISR warm-up')
    i_purge = text.index('echo "==> Cloudflare edge purge')
    assert i_health < i_warmup < i_purge, (
        "the ISR warm-up must run AFTER the web health gate (origin verified "
        "live) and BEFORE the NFM-5420 CF purge (the edge's first refetch "
        "after purge must hit a warm origin)"
    )


def test_token_exported_before_compose_cutover() -> None:
    text = DEPLOY_PROD.read_text(encoding="utf-8")
    i_export = text.index("export ISR_WARMUP_TOKEN=")
    i_compose_up = text.index("docker compose -f docker-compose.prod.yml")
    assert i_export < i_compose_up, (
        "the per-deploy ISR_WARMUP_TOKEN must be exported BEFORE the "
        "`docker compose up` cutover so the web container boots already "
        "carrying the token the post-health-gate warm-up presents"
    )


def test_compose_declares_warmup_token_passthrough() -> None:
    compose = PROD_COMPOSE.read_text(encoding="utf-8")
    web_start = compose.index("  web:")
    web_section = compose[web_start:]
    assert 'ISR_WARMUP_TOKEN: "${ISR_WARMUP_TOKEN:-}"' in web_section, (
        "docker-compose.prod.yml web service must pass ISR_WARMUP_TOKEN "
        "through to the container (empty default keeps the endpoint "
        "fail-closed for ad-hoc compose invocations)"
    )


def test_warmup_success_posts_token_and_fetches_warm_bodies(tmp_path: Path) -> None:
    result, calls = _run_block(
        tmp_path, env_overrides={"ISR_WARMUP_TOKEN": "testtok"}, post_rc=0
    )
    assert result.returncode == 0

    posts = [c for c in calls if "revalidate-landing" in c]
    assert len(posts) == 1
    assert "x-nfm-warmup-token: testtok" in posts[0]
    assert "-X POST" in posts[0]

    gets = [c for c in calls if "-X POST" not in c]
    assert [c for c in gets if "127.0.0.1:3000/" in c and "browse" not in c]
    assert [c for c in gets if "127.0.0.1:3000/browse" in c]
    # cold capture precedes the warm-up POST
    assert calls.index([c for c in gets if "browse" not in c][0]) < calls.index(posts[0])

    cap_dir = tmp_path / "snapshot" / "coldwindow-nfm-5898"
    for name in ("cold-index.html", "cold-browse.html", "warm-index.html", "warm-browse.html"):
        assert (cap_dir / name).is_file(), f"missing capture artifact {name}"
    assert (cap_dir / "cold-index.html").read_text(encoding="utf-8") != (
        cap_dir / "warm-index.html"
    ).read_text(encoding="utf-8")
    assert (cap_dir / "index.diff").is_file()

    assert "ISR_WARMUP_COMPLETED_OK" in result.stdout
    assert "cold prerender" in result.stdout
    assert "warm regen" in result.stdout


def test_warmup_post_failure_is_loud_and_nonfatal(tmp_path: Path) -> None:
    result, calls = _run_block(
        tmp_path, env_overrides={"ISR_WARMUP_TOKEN": "testtok"}, post_rc=7
    )
    # Advisory-only (NFM-5149 / NFM-5426 precedent): a failed warm-up must
    # never retro-red a healthy deploy.
    assert result.returncode == 0
    assert "ISR_WARMUP_FAILED" in result.stderr
    assert "ISR_WARMUP_COMPLETED_OK" not in result.stdout
    # both retry attempts happened
    posts = [c for c in calls if "revalidate-landing" in c]
    assert len(posts) == 2
    # and no warm-body GETs were attempted after the failed POST
    gets = [c for c in calls if "-X POST" not in c and "revalidate-landing" not in c]
    warm_gets = [c for c in gets if "warm-" in c]
    assert warm_gets == []


def test_warmup_post_failure_survives_unusable_retry_delay(tmp_path: Path) -> None:
    result, calls = _run_block(
        tmp_path,
        env_overrides={
            "ISR_WARMUP_TOKEN": "testtok",
            "NFM5898_RETRY_DELAY_S": "not-a-number",
        },
        post_rc=7,
    )
    # CR (NFM-5898 review): NFM5898_RETRY_DELAY_S is an env knob and may hold
    # a value `sleep` rejects (non-numeric, or a GNU-style "5s" suffix on the
    # BSD deploy host). Under set -e an unguarded sleep on the retry path
    # would errexit the deploy AFTER the web health gate and BEFORE the
    # NFM-5420 CF purge — the tail-skip class the advisory-only failure
    # policy exists to prevent. The retry delay must never be fatal: both
    # POST attempts still run, the loud anchor still fires, exit stays 0.
    assert result.returncode == 0
    posts = [c for c in calls if "revalidate-landing" in c]
    assert len(posts) == 2
    assert "ISR_WARMUP_FAILED" in result.stderr
    assert "ISR_WARMUP_COMPLETED_OK" not in result.stdout


def test_missing_token_skips_post_but_still_captures_cold(tmp_path: Path) -> None:
    result, calls = _run_block(tmp_path, env_overrides={}, post_rc=0)
    assert result.returncode == 0
    assert "ISR_WARMUP_SKIPPED" in result.stderr
    assert not [c for c in calls if "revalidate-landing" in c]
    # the cold-window capture (NFM-5897 residual) still runs — it is
    # independent of the warm-up itself
    cold_gets = [c for c in calls if "-o" in c and "cold-" in c]
    assert len(cold_gets) == 2
    cap_dir = tmp_path / "snapshot" / "coldwindow-nfm-5898"
    assert (cap_dir / "cold-index.html").is_file()
    assert (cap_dir / "cold-browse.html").is_file()
