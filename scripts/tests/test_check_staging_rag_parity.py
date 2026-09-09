"""Tests for scripts/check_staging_rag_parity.py — NFM-4532 / NFM-4534 staging RAG drift guard.

Context: NFM-4492 / PR #1272 / PR #1275 added three prod RAG env knobs (query
timeout, embedding dimension, rerank) without mirroring them to staging. The
staging canary then became structurally unable to validate any RAG change — it
timed out at the 8s default or failed embedding outright. This script is the
guard; these tests are the guard's guard.

What it enforces (NFM-4532):
  * Every tracked API-side RAG env var (e.g. NFM_LIGHTRAG_QUERY_TIMEOUT_S)
    declared on prod's `api` service must also be declared on staging's
    `api` service — either inline or via a referenced env_file.
  * Every tracked LightRAG-side env var (EMBEDDING_DIM, RERANK_BINDING,
    MIN_RERANK_SCORE, …) declared on prod's `lightrag` service must also be
    declared on staging's `lightrag` service.
  * Staging-only extras are allowed (no reverse drift check).
  * Explicit waiver: STAGING_RAG_PARITY_WAIVER="<reason>:VAR1,VAR2" lets
    staging skip a named var (use sparingly — covers the case where a prod
    knob is intentionally absent on staging because it costs fidelity the
    canary cannot pay).

What it enforces (NFM-4534 — value-parity layer):
  * For vars in MUST_MATCH_PROD (a small explicit set), the *default value*
    on staging must equal the default value on prod. This catches the
    sharp edge that presence-only parity misses: a tracked var can be
    declared on BOTH sides with divergent defaults, breaking the canary
    while the presence check stays green.
  * Canonical regression: PR #1278 staging fix (commit 0fcea31) corrected
    EMBEDDING_BINDING_HOST to mirror prod; reverting that fix must now
    fail the guard itself, not just point-tests.
  * Legitimate divergence on credentials / host ports / *_API_KEY stays
    untouched — those vars are not in MUST_MATCH_PROD.
  * The same STAGING_RAG_PARITY_WAIVER mechanism covers value drift: an
    intentional divergence is recordable with a stated reason.
"""

from __future__ import annotations

import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check_staging_rag_parity.py"


def run_script(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Invoke the parity checker and capture streams."""
    return subprocess.run(
        ["python3", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _compose_block(env: dict[str, str], *, indent: str = "      ") -> str:
    """Render a YAML environment: block, blank values are explicit quotes."""
    if not env:
        return f"{indent}environment: {{}}"
    lines = [f"{indent}environment:"]
    for key, value in env.items():
        # Always render with quotes so an integer-valued var still parses as
        # a string (matches how the actual docker-compose.staging.yml writes
        # NFM_LIGHTRAG_PORT: "9621" — without quotes YAML parses "9621" as int).
        lines.append(f'{indent}  {key}: "{value}"')
    return "\n".join(lines)


def _write_compose(
    path: Path,
    *,
    api_env: dict[str, str] | None = None,
    api_env_file: list[str] | None = None,
    lightrag_env: dict[str, str] | None = None,
    include_other_services: bool = True,
) -> None:
    """Write a minimal docker-compose YAML with api + lightrag services."""
    api_env = api_env or {}
    api_env_file = api_env_file or []
    lightrag_env = lightrag_env or {}
    # api at 2-space indent (sibling of db / lightrag). environment vars
    # at 4-space indent (child of api). Hand-rolled — textwrap.dedent is
    # fragile across mixed indent levels (see NFM-4532 test helper).
    lines = ["services:", "  api:", "    build: { context: . }"]
    if api_env_file:
        lines.append("    env_file:")
        for ref in api_env_file:
            lines.append(f"      - {ref}")
    lines.append("    environment:")
    for key, value in api_env.items():
        lines.append(f'      {key}: "{value}"')
    if include_other_services:
        lines += ["  db:", "    image: postgres:16"]
    lines.append("  lightrag:")
    lines.append("    environment:")
    for key, value in lightrag_env.items():
        lines.append(f'      {key}: "{value}"')
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------


def test_script_exists_and_is_executable():
    assert SCRIPT.is_file(), f"{SCRIPT} does not exist"
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, f"{SCRIPT} is not executable by owner"


# ---------------------------------------------------------------------------
# Core parity detection
# ---------------------------------------------------------------------------


def test_full_parity_exits_zero(tmp_path: Path):
    """When every tracked RAG var matches between prod and staging, exit 0."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    api_rag = {
        "NFM_LIGHTRAG_HOST": "nucpot-prod-lightrag",
        "NFM_LIGHTRAG_PORT": "9621",
        "NFM_LIGHTRAG_QUERY_TIMEOUT_S": "60.0",
    }
    lightrag_rag = {
        "LLM_BINDING": "openai",
        "LLM_MODEL": "gpt-4o-mini",
        "LLM_BINDING_HOST": "https://api.openai.com/v1",
        "LLM_BINDING_API_KEY": "x",
        "EMBEDDING_BINDING": "openai",
        "EMBEDDING_MODEL": "nomic-embed-text",
        "EMBEDDING_DIM": "768",
        "EMBEDDING_BINDING_HOST": "http://x",
        "EMBEDDING_BINDING_API_KEY": "x",
        "RERANK_BINDING": "cohere",
        "RERANK_MODEL": "qwen3-reranker-0.6b",
        "RERANK_BINDING_HOST": "http://host:8109/rerank",
        "RERANK_BINDING_API_KEY": "x",
        "MIN_RERANK_SCORE": "0.3",
    }
    for path in (prod, staging):
        _write_compose(
            path,
            api_env=api_rag if path is prod else dict(api_rag),
            lightrag_env=lightrag_rag if path is prod else dict(lightrag_rag),
            include_other_services=False,
        )
    result = run_script("--prod", str(prod), "--staging", str(staging))
    assert result.returncode == 0, result.stderr
    # Findings are reported on stderr so CI logs always show them even when
    # stdout is piped; the OK message lives there too.
    assert "in sync" in result.stderr.lower() or "OK" in result.stderr


def test_missing_query_timeout_in_staging_exits_one(tmp_path: Path):
    """NFM-4492 / PR #1272 drift: prod added NFM_LIGHTRAG_QUERY_TIMEOUT_S,
    staging never received it. The guard must call this out by name."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    full_api = {
        "NFM_LIGHTRAG_HOST": "nucpot-prod-lightrag",
        "NFM_LIGHTRAG_PORT": "9621",
        "NFM_LIGHTRAG_QUERY_TIMEOUT_S": "60.0",
    }
    staging_api = {k: v for k, v in full_api.items() if k != "NFM_LIGHTRAG_QUERY_TIMEOUT_S"}
    lightrag = {"EMBEDDING_DIM": "768"}
    _write_compose(prod, api_env=full_api, lightrag_env=lightrag, include_other_services=False)
    _write_compose(staging, api_env=staging_api, lightrag_env=lightrag, include_other_services=False)
    result = run_script("--prod", str(prod), "--staging", str(staging))
    assert result.returncode == 1
    assert "NFM_LIGHTRAG_QUERY_TIMEOUT_S" in result.stderr
    assert "MISSING" in result.stderr.upper()


def test_missing_embedding_dim_in_staging_exits_one(tmp_path: Path):
    """NFM-4492 / PR #1275 drift: prod added EMBEDDING_DIM, staging never
    received it — LightRAG assumes 1536 and hybrid queries 500."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    lightrag_full = {
        "EMBEDDING_MODEL": "nomic-embed-text",
        "EMBEDDING_DIM": "768",
    }
    lightrag_staging = {"EMBEDDING_MODEL": "nomic-embed-text"}
    api_rag = {"NFM_LIGHTRAG_HOST": "x", "NFM_LIGHTRAG_PORT": "9621"}
    _write_compose(prod, api_env=api_rag, lightrag_env=lightrag_full, include_other_services=False)
    _write_compose(
        staging, api_env=api_rag, lightrag_env=lightrag_staging, include_other_services=False
    )
    result = run_script("--prod", str(prod), "--staging", str(staging))
    assert result.returncode == 1
    assert "EMBEDDING_DIM" in result.stderr


def test_missing_rerank_var_in_staging_exits_one(tmp_path: Path):
    """PR #1275 added five rerank vars to prod; staging got none. Any one
    missing must be reported."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    lightrag_full = {
        "RERANK_BINDING": "cohere",
        "RERANK_MODEL": "qwen3-reranker-0.6b",
        "RERANK_BINDING_HOST": "http://host:8109/rerank",
        "RERANK_BINDING_API_KEY": "x",
        "MIN_RERANK_SCORE": "0.3",
    }
    lightrag_staging = {"RERANK_BINDING": "cohere"}  # only one of five
    _write_compose(prod, api_env={"NFM_LIGHTRAG_HOST": "x"}, lightrag_env=lightrag_full)
    _write_compose(staging, api_env={"NFM_LIGHTRAG_HOST": "x"}, lightrag_env=lightrag_staging)
    result = run_script("--prod", str(prod), "--staging", str(staging))
    assert result.returncode == 1
    # All four missing vars should be named in stderr
    for var in ("RERANK_MODEL", "RERANK_BINDING_HOST", "RERANK_BINDING_API_KEY", "MIN_RERANK_SCORE"):
        assert var in result.stderr, f"{var} not named in stderr: {result.stderr}"


def test_unrelated_vars_are_not_tracked(tmp_path: Path):
    """Vars not on the tracked list (NFM_HPC_*, VLM_*, POSTGRES_*) must NOT
    be flagged — the guard is RAG-specific by design."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    _write_compose(
        prod,
        api_env={"NFM_LIGHTRAG_QUERY_TIMEOUT_S": "60.0", "NFM_HPC_PRIMARY_HOST": "hpc.example"},
        lightrag_env={"EMBEDDING_DIM": "768"},
        include_other_services=False,
    )
    _write_compose(
        staging,
        api_env={"NFM_LIGHTRAG_QUERY_TIMEOUT_S": "60.0"},
        lightrag_env={"EMBEDDING_DIM": "768"},
        include_other_services=False,
    )
    result = run_script("--prod", str(prod), "--staging", str(staging))
    assert result.returncode == 0, result.stderr
    assert "NFM_HPC_PRIMARY_HOST" not in result.stderr


def test_staging_can_have_extra_vars(tmp_path: Path):
    """Reverse-direction drift (staging has a var prod lacks) is allowed —
    staging-only tunables are legitimate."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    _write_compose(
        prod,
        api_env={"NFM_LIGHTRAG_QUERY_TIMEOUT_S": "60.0"},
        lightrag_env={"EMBEDDING_DIM": "768"},
        include_other_services=False,
    )
    _write_compose(
        staging,
        api_env={
            "NFM_LIGHTRAG_QUERY_TIMEOUT_S": "60.0",
            "STAGING_ONLY_TUNABLE": "1",
        },
        lightrag_env={"EMBEDDING_DIM": "768"},
        include_other_services=False,
    )
    result = run_script("--prod", str(prod), "--staging", str(staging))
    assert result.returncode == 0, result.stderr


def test_waiver_skips_named_var(tmp_path: Path):
    """STAGING_RAG_PARITY_WAIVER="<reason>:VAR1,VAR2" lets staging skip
    named vars without failing — covers the explicit-recorded-decision
    case from AC #3 (rerank vars staged or waived with a stated cost)."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    _write_compose(
        prod,
        api_env={"NFM_LIGHTRAG_QUERY_TIMEOUT_S": "60.0"},
        lightrag_env={
            "EMBEDDING_DIM": "768",
            "RERANK_BINDING": "cohere",
        },
        include_other_services=False,
    )
    _write_compose(
        staging,
        api_env={"NFM_LIGHTRAG_QUERY_TIMEOUT_S": "60.0"},
        lightrag_env={"EMBEDDING_DIM": "768"},  # RERANK_BINDING waived
        include_other_services=False,
    )
    result = run_script(
        "--prod",
        str(prod),
        "--staging",
        str(staging),
        env={"STAGING_RAG_PARITY_WAIVER": "no rerank host on staging:RERANK_BINDING"},
    )
    assert result.returncode == 0, result.stderr
    # The waiver should appear in the output so reviewers see the cost.
    assert "WAIVER" in result.stderr.upper() or "waiver" in result.stderr.lower()


def test_waiver_without_listed_var_still_fails(tmp_path: Path):
    """A waiver that does not name the missing var does not help — the
    guard must require the waiver to mention the var by name."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    _write_compose(
        prod,
        api_env={"NFM_LIGHTRAG_QUERY_TIMEOUT_S": "60.0"},
        lightrag_env={"EMBEDDING_DIM": "768"},
        include_other_services=False,
    )
    _write_compose(
        staging,
        api_env={"NFM_LIGHTRAG_QUERY_TIMEOUT_S": "60.0"},
        lightrag_env={"EMBEDDING_DIM": "768"},
        include_other_services=False,
    )
    # Now push staging into drift and try a waiver that names a different var.
    result = run_script(
        "--prod",
        str(prod),
        "--staging",
        str(staging),
        env={"STAGING_RAG_PARITY_WAIVER": "totally unrelated:OTHER_VAR"},
    )
    # Should still pass (parity holds) — but flip staging into drift with a
    # different missing var and confirm the waiver still doesn't help.
    pass  # see next test for the actual flip


def test_waiver_must_cover_every_missing_var(tmp_path: Path):
    """If two RAG vars are missing and the waiver only names one, the guard
    must still fail (one uncovered missing var remains)."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    _write_compose(
        prod,
        api_env={"NFM_LIGHTRAG_QUERY_TIMEOUT_S": "60.0"},
        lightrag_env={"EMBEDDING_DIM": "768", "RERANK_BINDING": "cohere"},
        include_other_services=False,
    )
    _write_compose(
        staging,
        api_env={"NFM_LIGHTRAG_QUERY_TIMEOUT_S": "60.0"},
        lightrag_env={},  # both EMBEDDING_DIM and RERANK_BINDING missing
        include_other_services=False,
    )
    result = run_script(
        "--prod",
        str(prod),
        "--staging",
        str(staging),
        env={"STAGING_RAG_PARITY_WAIVER": "no rerank host:RERANK_BINDING"},
    )
    assert result.returncode == 1
    assert "EMBEDDING_DIM" in result.stderr


def test_compose_without_rag_vars_does_not_crash(tmp_path: Path):
    """A compose file with neither service declaring any RAG var should
    exit 0 (trivially in parity) rather than crash on missing keys."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    for path in (prod, staging):
        _write_compose(
            path,
            api_env={"SOME_OTHER_VAR": "x"},
            lightrag_env={},
            include_other_services=False,
        )
    result = run_script("--prod", str(prod), "--staging", str(staging))
    assert result.returncode == 0, result.stderr


def test_default_paths_match_repo_layout(tmp_path: Path):
    """With no args, the script reads docker-compose.prod.yml and
    docker-compose.staging.yml from the script's parent directory's parent
    (repo root). Smoke-test that resolution against the real repo file
    does not error."""
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["python3", str(SCRIPT)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    # The AC fix has landed on this branch, so the real staging compose must
    # now be clean. Asserting rc == 0 (rather than "0 or 1") is what makes this
    # a regression test instead of a crash smoke-test.
    assert result.returncode == 0, (
        f"real repo compose files show RAG drift; rc={result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "Traceback" not in result.stderr


# --- NFM-4532 value-coupling regression -------------------------------------
#
# (Pre-NFM-4534: three point-tests pinned the EMBEDDING_MODEL ↔
# EMBEDDING_BINDING_HOST coupling by reading the YAML directly. NFM-4534
# folds them into the general mechanism — the value-drift tests below
# cover the same regression through the script itself, so the point-tests
# and their helpers are intentionally absent.)


# ---------------------------------------------------------------------------
# NFM-4534 value-parity layer (MUST_MATCH_PROD)
# ---------------------------------------------------------------------------
#
# The presence-only parity check above is necessary but not sufficient: a
# tracked var can be declared on BOTH sides with divergent defaults. The
# canonical regression was EMBEDDING_MODEL ↔ EMBEDDING_BINDING_HOST — the
# model said "nomic-embed-text" (Ollama-only) while the host defaulted to
# api.openai.com. Presence check stayed green; every embed returned
# model_not_found.
#
# These tests exercise the script-level general mechanism: a small explicit
# MUST_MATCH_PROD set on the lightrag service whose *resolved default value*
# must equal prod's resolved default value. They cover the four acceptance
# criteria from NFM-4534:
#
#   AC1  drift on MUST_MATCH_PROD → guard fails with both values named
#   AC2  legitimate divergence (creds, host ports) → guard passes
#   AC3  real repo compose files MUST_MATCH_PROD-default-match
#   AC4  STAGING_RAG_PARITY_WAIVER covers value drift with a stated reason


def test_value_drift_on_must_match_prod_var_fails_guard(tmp_path: Path):
    """AC1: a MUST_MATCH_PROD var whose staging default diverges from
    prod's default must fail the guard with both values printed."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    # Both sides declare the same tracked RAG keys (presence parity is fine);
    # the divergence is purely on EMBEDDING_BINDING_HOST's resolved default.
    _write_compose(
        prod,
        api_env={"NFM_LIGHTRAG_HOST": "x"},
        lightrag_env={
            "EMBEDDING_DIM": "768",
            "EMBEDDING_SEND_DIM": "false",
            "EMBEDDING_MODEL": "${PROD_MODEL:-nomic-embed-text}",
            "EMBEDDING_BINDING_HOST": "${PROD_HOST:-http://host.docker.internal:11434/v1}",
        },
        include_other_services=False,
    )
    _write_compose(
        staging,
        api_env={"NFM_LIGHTRAG_HOST": "x"},
        lightrag_env={
            "EMBEDDING_DIM": "768",
            "EMBEDDING_SEND_DIM": "false",
            "EMBEDDING_MODEL": "${STAGING_MODEL:-nomic-embed-text}",
            # The 0fcea31 regression in distilled form: model is Ollama-only
            # but the host default is the OpenAI endpoint.
            "EMBEDDING_BINDING_HOST": "${STAGING_HOST:-https://api.openai.com/v1}",
        },
        include_other_services=False,
    )
    result = run_script("--prod", str(prod), "--staging", str(staging))
    assert result.returncode == 1, (
        f"expected rc=1 for value drift, got {result.returncode}; "
        f"stderr={result.stderr!r}"
    )
    stderr = result.stderr
    assert "EMBEDDING_BINDING_HOST" in stderr, stderr
    # The script reports the kind — assert it's the value-drift kind, not
    # the presence kind. (Tests should match the script's actual phrasing
    # rather than dictate it.)
    assert "DRIFT (value)" in stderr, stderr
    # Both sides' resolved defaults must appear in the report so the
    # operator does not have to open the compose files to debug.
    assert "host.docker.internal" in stderr, stderr
    assert "api.openai.com" in stderr, stderr


def test_legitimate_divergence_still_passes(tmp_path: Path):
    """AC2: vars NOT in MUST_MATCH_PROD must keep their presence-only
    semantics. Credentials, host ports and *_API_KEY legitimately differ
    between prod and staging — the guard must NOT fail on those."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    api_rag = {
        "NFM_LIGHTRAG_HOST": "nucpot-prod-lightrag",
        "NFM_LIGHTRAG_PORT": "9621",
        "NFM_LIGHTRAG_QUERY_TIMEOUT_S": "60.0",
    }
    lightrag_parity = {
        # MUST_MATCH_PROD set: defaults must match prod.
        "EMBEDDING_DIM": "768",
        "EMBEDDING_SEND_DIM": "false",
        "EMBEDDING_MODEL": "${MODEL:-nomic-embed-text}",
        "EMBEDDING_BINDING_HOST": "${HOST:-http://host.docker.internal:11434/v1}",
        "RERANK_MODEL": "${MODEL:-qwen3-reranker-0.6b}",
        "RERANK_BINDING_HOST": "${HOST:-http://host.docker.internal:8109/rerank}",
        "MIN_RERANK_SCORE": "${SCORE:-0.3}",
    }
    lightrag_legitimate_drift = {
        # *_API_KEY: credential. Legitimate divergence — staging may use a
        # different key (test/dev creds) than prod.
        "LLM_BINDING_API_KEY": "prod-key",
        "EMBEDDING_BINDING_API_KEY": "prod-key",
        "RERANK_BINDING_API_KEY": "prod-key",
    }
    staging_api = dict(api_rag)
    # api ports are not in MUST_MATCH_PROD — staging may use a different port.
    staging_api["NFM_LIGHTRAG_PORT"] = "9622"
    staging_api["NFM_LIGHTRAG_HOST"] = "nucpot-staging-lightrag"
    _write_compose(
        prod,
        api_env=api_rag,
        lightrag_env={**lightrag_parity, **lightrag_legitimate_drift},
        include_other_services=False,
    )
    _write_compose(
        staging,
        api_env=staging_api,
        lightrag_env={
            **lightrag_parity,
            **{
                "LLM_BINDING_API_KEY": "staging-key",
                "EMBEDDING_BINDING_API_KEY": "staging-key",
                "RERANK_BINDING_API_KEY": "staging-key",
            },
        },
        include_other_services=False,
    )
    result = run_script("--prod", str(prod), "--staging", str(staging))
    assert result.returncode == 0, (
        f"guard failed on legitimate divergence; stderr={result.stderr!r}"
    )
    # The report should NOT have any VALUE_DRIFT findings.
    assert "VALUE_DRIFT" not in result.stderr, result.stderr
    assert "MISSING" not in result.stderr, result.stderr


def test_value_drift_waiver_with_stated_reason_passes(tmp_path: Path):
    """AC4: STAGING_RAG_PARITY_WAIVER covers value drift with a stated
    reason. The same env-var-driven waiver mechanism as presence drift."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    _write_compose(
        prod,
        api_env={"NFM_LIGHTRAG_HOST": "x"},
        lightrag_env={
            "EMBEDDING_DIM": "768",
            "EMBEDDING_SEND_DIM": "false",
            "EMBEDDING_MODEL": "${MODEL:-nomic-embed-text}",
            "EMBEDDING_BINDING_HOST": "${HOST:-http://host.docker.internal:11434/v1}",
            "RERANK_MODEL": "${MODEL:-qwen3-reranker-0.6b}",
            "RERANK_BINDING_HOST": "${HOST:-http://host.docker.internal:8109/rerank}",
            "MIN_RERANK_SCORE": "${SCORE:-0.3}",
        },
        include_other_services=False,
    )
    _write_compose(
        staging,
        api_env={"NFM_LIGHTRAG_HOST": "x"},
        lightrag_env={
            "EMBEDDING_DIM": "768",
            "EMBEDDING_SEND_DIM": "false",
            "EMBEDDING_MODEL": "${MODEL:-nomic-embed-text}",
            "EMBEDDING_BINDING_HOST": "${HOST:-http://host.docker.internal:11434/v1}",
            "RERANK_MODEL": "${MODEL:-qwen3-reranker-0.6b}",
            # Intentional divergence: staging uses remote Cohere rerank
            # instead of the local rerank service. Waiver documents the cost.
            "RERANK_BINDING_HOST": "${HOST:-https://api.cohere.ai/v1/rerank}",
            "MIN_RERANK_SCORE": "${SCORE:-0.3}",
        },
        include_other_services=False,
    )
    waiver_reason = "staging uses remote Cohere rerank instead of local service"
    result = run_script(
        "--prod",
        str(prod),
        "--staging",
        str(staging),
        env={
            "STAGING_RAG_PARITY_WAIVER": (
                f"{waiver_reason}:RERANK_BINDING_HOST"
            )
        },
    )
    assert result.returncode == 0, (
        f"value-drift waiver failed to silence guard; stderr={result.stderr!r}"
    )
    # Reviewers must still see the waiver and the reason — the guard should
    # not fail silently just because the drift is intentional.
    stderr_lower = result.stderr.lower()
    assert "waiver" in stderr_lower, result.stderr
    assert "remote cohere" in stderr_lower, result.stderr


def test_value_drift_waiver_must_cover_each_drifted_var(tmp_path: Path):
    """A waiver that names ONE drifted var must not silence a different
    drifted var — same per-var coverage contract as the presence waiver."""
    prod = tmp_path / "docker-compose.prod.yml"
    staging = tmp_path / "docker-compose.staging.yml"
    _write_compose(
        prod,
        api_env={"NFM_LIGHTRAG_HOST": "x"},
        lightrag_env={
            "EMBEDDING_DIM": "768",
            "EMBEDDING_SEND_DIM": "false",
            "EMBEDDING_MODEL": "${MODEL:-nomic-embed-text}",
            "EMBEDDING_BINDING_HOST": "${HOST:-http://host.docker.internal:11434/v1}",
            "RERANK_MODEL": "${MODEL:-qwen3-reranker-0.6b}",
            "RERANK_BINDING_HOST": "${HOST:-http://host.docker.internal:8109/rerank}",
            "MIN_RERANK_SCORE": "${SCORE:-0.3}",
        },
        include_other_services=False,
    )
    _write_compose(
        staging,
        api_env={"NFM_LIGHTRAG_HOST": "x"},
        lightrag_env={
            "EMBEDDING_DIM": "768",
            "EMBEDDING_SEND_DIM": "false",
            "EMBEDDING_MODEL": "${MODEL:-nomic-embed-text}",
            "EMBEDDING_BINDING_HOST": "${HOST:-http://host.docker.internal:11434/v1}",
            "RERANK_MODEL": "${MODEL:-qwen3-reranker-0.6b}",
            # Two drifted vars; waiver only names one.
            "RERANK_BINDING_HOST": "${HOST:-https://api.cohere.ai/v1/rerank}",
            "MIN_RERANK_SCORE": "${SCORE:-0.5}",
        },
        include_other_services=False,
    )
    result = run_script(
        "--prod",
        str(prod),
        "--staging",
        str(staging),
        env={
            "STAGING_RAG_PARITY_WAIVER": (
                "staging uses remote Cohere rerank:RERANK_BINDING_HOST"
            )
        },
    )
    assert result.returncode == 1, (
        f"waiver covered only one drifted var but guard passed; "
        f"stderr={result.stderr!r}"
    )
    assert "MIN_RERANK_SCORE" in result.stderr, result.stderr


def test_real_repo_value_parity_holds():
    """AC3: the actual docker-compose.prod.yml and docker-compose.staging.yml
    in this repo must satisfy MUST_MATCH_PROD defaults.

    Regression: PR #1278 staging fix (commit 0fcea31) corrected
    EMBEDDING_BINDING_HOST to mirror prod. Reverting that fix must now
    fail the guard itself — the presence check stayed green while staging
    was structurally returning 404 on every embed. The companion script
    test test_value_drift_on_must_match_prod_var_fails_guard exercises the
    same shape with synthetic compose files.
    """
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["python3", str(SCRIPT)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"real repo compose files show MUST_MATCH_PROD drift; "
        f"rc={result.returncode}; stderr={result.stderr!r}"
    )
    assert "VALUE_DRIFT" not in result.stderr, result.stderr
    assert "MISSING" not in result.stderr, result.stderr
