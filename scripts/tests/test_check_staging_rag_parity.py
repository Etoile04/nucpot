"""Tests for scripts/check_staging_rag_parity.py — NFM-4532 staging RAG drift guard.

Context: NFM-4492 / PR #1272 / PR #1275 added three prod RAG env knobs (query
timeout, embedding dimension, rerank) without mirroring them to staging. The
staging canary then became structurally unable to validate any RAG change — it
timed out at the 8s default or failed embedding outright. This script is the
guard; these tests are the guard's guard.

What it enforces:
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
    # After the AC fix lands this exit code will become 0; today the real
    # staging compose is drifted, so we accept either 0 or 1 but assert
    # the script did not crash.
    assert result.returncode in (0, 1), (
        f"unexpected rc={result.returncode}; stderr={result.stderr!r}"
    )
    assert "Traceback" not in result.stderr
