#!/usr/bin/env python3
"""NFM-4532 — staging ↔ prod RAG env-var parity guard.

Context
--------
NFM-4492 / PR #1272 (8s→30s) and PR #1275 (30s→60s + nomic-embed-text + 5
rerank vars) added three prod RAG knobs that the staging canary never
received:

  1. ``NFM_LIGHTRAG_QUERY_TIMEOUT_S`` on the api service — without it
     staging falls back to the 8s hardcoded default in
     ``apps/api/src/nfm_db/services/lightrag_client.py::_DEFAULT_QUERY_TIMEOUT``
     and any first-hit hybrid query times out before the LLM cache lands.
  2. ``EMBEDDING_DIM=768`` on the lightrag service — without it LightRAG
     assumes 1536 and rejects every embed with
     ``Embedding dimension mismatch detected: total elements (768)
     cannot be evenly divided by expected dimension (1536)``.
  3. Five rerank vars (``RERANK_BINDING``, ``RERANK_MODEL``,
     ``RERANK_BINDING_HOST``, ``RERANK_BINDING_API_KEY``,
     ``MIN_RERANK_SCORE``) on the lightrag service — without them
     staging cannot validate any rerank-touching change.

Per ADR-013 / NFM-4502, RAG changes are supposed to be canaried on G2
staging first. With the drift in place the canary was a false-negative
generator (timed out or failed embedding regardless of the change under
test). This script closes that gap: any prod RAG env var that is absent
from staging fails the guard, period.

What it enforces
----------------
For every var name in :data:`API_RAG_KEYS` declared on the prod ``api``
service, that name must also be declared on the staging ``api``
service — either inline under ``environment:`` or via a referenced
``env_file:``. Same one-way check on the ``lightrag`` service for
:data:`LIGHTRAG_RAG_KEYS`. Reverse-direction drift (staging declaring a
var prod lacks) is allowed — staging-only tunables are legitimate.

Waiver mechanism
----------------
``STAGING_RAG_PARITY_WAIVER`` may be set to ``"<reason>:VAR1,VAR2,..."``
to skip specific missing vars (each must be named by exact match). The
guard prints every waivered var in its output so reviewers see the cost
of the canary's reduced fidelity. Every other missing var still fails.
The waiver covers the AC #3 case (rerank vars staged OR waived with an
explicit recorded decision and a stated fidelity cost).

Exit codes
----------
  0  every tracked prod RAG var is present on staging (or waived)
  1  one or more tracked prod RAG vars are absent on staging with no waiver
  2  operational error (file missing, YAML unparseable) — never fails
     "just because", the operator can retry after fixing the input

Usage
-----
  ./scripts/check_staging_rag_parity.py
  ./scripts/check_staging_rag_parity.py --prod docker-compose.prod.yml \\
      --staging docker-compose.staging.yml
  STAGING_RAG_PARITY_WAIVER='no rerank host:RERANK_BINDING,RERANK_BINDING_HOST' \\
      ./scripts/check_staging_rag_parity.py

NFM-4532 issue: filed by LE from an idle-heartbeat repo-hygiene sweep.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover — pyyaml is a runtime dep
    print(
        "check_staging_rag_parity: PyYAML is required (apt: python3-yaml; "
        "pip: pyyaml). Original error: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


# ---------------------------------------------------------------------------
# Tracked RAG env vars (NFM-4532)
# ---------------------------------------------------------------------------
# Keep this list narrow on purpose: the guard is RAG-specific by design.
# Unrelated vars (NFM_HPC_*, VLM_*, POSTGRES_*, etc.) are out of scope — the
# G4b deploy-drift alarm (scripts/check_deploy_drift.py) is the durable
# catch-all for general env drift.

# api service: read-side knobs the API container must see to call LightRAG
# within prod's actual latency budget. NFM_LIGHTRAG_HOST / PORT are
# mandatory in BOTH compose files already (the LightRAG sidecar cannot be
# reached without them) — tracked for symmetry so a future regression on
# those fails loud rather than silently.
API_RAG_KEYS: frozenset[str] = frozenset(
    {
        "NFM_LIGHTRAG_HOST",
        "NFM_LIGHTRAG_PORT",
        "NFM_LIGHTRAG_QUERY_TIMEOUT_S",
    }
)

# lightrag service: every LLM/embedding/rerank knob that controls what
# the canary validates. PR #1272 added EMBEDDING_SEND_DIM (A3 / NFM-3803);
# we track it because toggling it on prod without staging parity would
# break hybrid storage on prod but not on staging — exactly the
# false-negative class NFM-4532 was filed to eliminate.
LIGHTRAG_RAG_KEYS: frozenset[str] = frozenset(
    {
        # LLM
        "LLM_BINDING",
        "LLM_MODEL",
        "LLM_BINDING_HOST",
        "LLM_BINDING_API_KEY",
        # Embedding
        "EMBEDDING_BINDING",
        "EMBEDDING_MODEL",
        "EMBEDDING_DIM",
        "EMBEDDING_SEND_DIM",
        "EMBEDDING_BINDING_HOST",
        "EMBEDDING_BINDING_API_KEY",
        # Rerank
        "RERANK_BINDING",
        "RERANK_MODEL",
        "RERANK_BINDING_HOST",
        "RERANK_BINDING_API_KEY",
        "MIN_RERANK_SCORE",
    }
)

WAIVER_ENV = "STAGING_RAG_PARITY_WAIVER"
WAIVER_PATTERN = re.compile(r"^([^:]+):([A-Z][A-Z0-9_]*(?:,[A-Z][A-Z0-9_]*)*)\s*$")


@dataclass(frozen=True)
class Drift:
    """One missing prod RAG env var on staging."""

    service: str  # 'api' or 'lightrag'
    var: str  # e.g. 'NFM_LIGHTRAG_QUERY_TIMEOUT_S'


# ---------------------------------------------------------------------------
# Compose parsing
# ---------------------------------------------------------------------------


def _load_compose(path: Path) -> dict:
    """Load a docker-compose YAML file. Missing/unparseable input is an
    operational error (rc 2) — never a parity failure, the operator can
    retry after fixing the input."""
    if not path.exists():
        raise FileNotFoundError(f"compose file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read {path}: {exc}") from exc
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"unparseable YAML in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise RuntimeError(f"{path} did not parse as a mapping (got {type(loaded).__name__})")
    return loaded


def _resolve_env_file(env_file_ref: str, compose_path: Path) -> Path:
    """Resolve an env_file reference relative to the compose file's
    directory — mirrors docker compose's own resolution so a reference of
    ``docker/.env.staging.api`` in docker-compose.staging.yml lands at
    <repo>/docker/.env.staging.api regardless of cwd."""
    p = Path(env_file_ref)
    if p.is_absolute():
        return p
    return (compose_path.parent / p).resolve()


def _read_env_file_keys(path: Path) -> set[str]:
    """Read an env_file (KEY=VALUE lines, one per line) and return its key
    names. Comment lines (``#``) and blank lines are skipped. The values
    themselves are never inspected — parity is a key-name contract only
    (NFM-2221 rationale: a parity script that prints values is a secret
    leak waiting to happen)."""
    keys: set[str] = set()
    if not path.exists():
        return keys
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # KEY=VALUE, KEY="VALUE", KEY='VALUE' — match on the leading token.
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if match:
            keys.add(match.group(1))
    return keys


def collect_service_env_keys(compose_path: Path, service_name: str) -> set[str]:
    """Return the union of env-var NAMES declared on ``service_name`` in
    the compose file at ``compose_path`` — inline ``environment:`` block
    plus every referenced ``env_file:``.

    The ``environment:`` value can be either a mapping (``FOO: bar``) or a
    list of bare names (``- FOO``); the latter is rare on this repo but
    handled correctly so a future refactor does not regress.
    """
    compose = _load_compose(compose_path)
    services = compose.get("services") or {}
    if not isinstance(services, dict):
        raise RuntimeError(
            f"{compose_path}: top-level 'services' must be a mapping, "
            f"got {type(services).__name__}"
        )
    service = services.get(service_name)
    if not isinstance(service, dict):
        raise RuntimeError(
            f"{compose_path}: service '{service_name}' is missing or not a mapping"
        )

    keys: set[str] = set()

    environment = service.get("environment")
    if environment is not None:
        if isinstance(environment, dict):
            keys.update(str(k) for k in environment.keys())
        elif isinstance(environment, list):
            for entry in environment:
                if isinstance(entry, str):
                    # Plain "FOO" — adopt the name verbatim.
                    keys.add(entry)
                elif isinstance(entry, dict):
                    # {"VAR": value} inside a list form — take the key.
                    keys.update(str(k) for k in entry.keys())
        else:
            raise RuntimeError(
                f"{compose_path}: services.{service_name}.environment must "
                f"be a mapping or list, got {type(environment).__name__}"
            )

    env_file = service.get("env_file")
    if env_file is not None:
        refs = env_file if isinstance(env_file, list) else [env_file]
        for ref in refs:
            resolved = _resolve_env_file(str(ref), compose_path)
            keys.update(_read_env_file_keys(resolved))

    return keys


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def parse_waiver(raw: str | None) -> tuple[str, set[str]]:
    """Parse ``STAGING_RAG_PARITY_WAIVER`` (NFM-4532) into ``(reason,
    vars)``. Format: ``"<reason>:VAR1,VAR2,..."``. The reason is free text
    (no embedded colons — the FIRST colon separates reason from list); the
    var list is comma-separated. Empty / malformed waiver = empty vars set
    AND empty reason (the caller treats this as no waiver)."""
    if not raw:
        return "", set()
    raw = raw.strip()
    if not raw:
        return "", set()
    match = WAIVER_PATTERN.match(raw)
    if not match:
        return "", set()
    reason = match.group(1).strip()
    vars_ = {v.strip() for v in match.group(2).split(",") if v.strip()}
    return reason, vars_


def diff_parity(
    prod_keys: dict[str, set[str]], staging_keys: dict[str, set[str]]
) -> list[Drift]:
    """Return every tracked prod RAG var absent from staging's matching
    service. A var declared in BOTH services is in parity; a var declared
    only in staging is allowed (staging-only extras are legitimate)."""
    drifts: list[Drift] = []
    for service, tracked in (
        ("api", API_RAG_KEYS),
        ("lightrag", LIGHTRAG_RAG_KEYS),
    ):
        prod_set = prod_keys.get(service, set())
        for var in sorted(tracked):
            if var in prod_set and var not in staging_keys.get(service, set()):
                drifts.append(Drift(service=service, var=var))
    return drifts


def render_report(
    drifts: list[Drift],
    waived: set[str],
    waiver_reason: str,
) -> str:
    """Human-readable parity report. The waiver line is emitted only when
    a waiver was actually applied — empty waivers stay silent so a clean
    run reads as clean."""
    lines = ["staging RAG parity check (NFM-4532):"]
    if not drifts:
        lines.append("  OK — every tracked prod RAG env var is present on staging.")
    else:
        lines.append(f"  DRIFT: {len(drifts)} tracked prod RAG env var(s) missing on staging:")
        for entry in drifts:
            waived_marker = " [WAIVED]" if entry.var in waived else ""
            lines.append(f"    - services.{entry.service}.{entry.var}{waived_marker}")
    if waived:
        lines.append("")
        lines.append(
            f"  Waiver applied (env {WAIVER_ENV}): reason={waiver_reason!r} "
            f"vars={sorted(waived)}"
        )
        lines.append(
            "  Reviewers: the canary runs without the waived var(s); record the "
            "fidelity cost in the related PR description per AC #3."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


REPO_ROOT_DEFAULT_PROD = "docker-compose.prod.yml"
REPO_ROOT_DEFAULT_STAGING = "docker-compose.staging.yml"


def _default_repo_paths() -> tuple[Path, Path]:
    """Resolve the repo-relative defaults. The script lives at
    scripts/check_staging_rag_parity.py so the repo root is parents[1]."""
    here = Path(__file__).resolve()
    repo_root = here.parents[1]
    return repo_root / REPO_ROOT_DEFAULT_PROD, repo_root / REPO_ROOT_DEFAULT_STAGING


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "NFM-4532 staging↔prod RAG env-var parity guard. Fails when a "
            "tracked prod RAG env var is missing from staging without an "
            "explicit waiver. See header docstring for the full contract."
        ),
    )
    parser.add_argument(
        "--prod",
        default=None,
        help=f"path to docker-compose.prod.yml (default: {REPO_ROOT_DEFAULT_PROD} "
        "resolved relative to repo root)",
    )
    parser.add_argument(
        "--staging",
        default=None,
        help=f"path to docker-compose.staging.yml (default: {REPO_ROOT_DEFAULT_STAGING} "
        "resolved relative to repo root)",
    )
    parser.add_argument(
        "--api-service",
        default="api",
        help="compose service name for the FastAPI container (default: api)",
    )
    parser.add_argument(
        "--lightrag-service",
        default="lightrag",
        help="compose service name for the LightRAG sidecar (default: lightrag)",
    )
    args = parser.parse_args(argv)

    if args.prod:
        prod_path = Path(args.prod).resolve()
    else:
        prod_path = _default_repo_paths()[0]
    if args.staging:
        staging_path = Path(args.staging).resolve()
    else:
        staging_path = _default_repo_paths()[1]

    try:
        prod_api = collect_service_env_keys(prod_path, args.api_service)
        prod_lightrag = collect_service_env_keys(prod_path, args.lightrag_service)
        staging_api = collect_service_env_keys(staging_path, args.api_service)
        staging_lightrag = collect_service_env_keys(staging_path, args.lightrag_service)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"check_staging_rag_parity: OPERATIONAL ERROR — {exc}", file=sys.stderr)
        print("not failing parity; fix the input and re-run.", file=sys.stderr)
        return 2

    drifts = diff_parity(
        {"api": prod_api, "lightrag": prod_lightrag},
        {"api": staging_api, "lightrag": staging_lightrag},
    )

    waiver_reason, waiver_vars = parse_waiver(os.environ.get(WAIVER_ENV))
    unwaived = [entry for entry in drifts if entry.var not in waiver_vars]
    applied_waiver = {entry.var for entry in drifts if entry.var in waiver_vars}

    # Findings go to stderr so CI logs always see them (even when stdout is
    # piped). The exit code is the single source of truth for pass/fail;
    # operators should not have to grep stdout to know whether the guard
    # found drift.
    print(
        render_report(
            drifts=unwaived,
            waived=applied_waiver,
            waiver_reason=waiver_reason,
        ),
        file=sys.stderr,
    )
    print(
        f"  (prod={prod_path}, staging={staging_path}; "
        f"prod api keys={len(prod_api)} lightrag keys={len(prod_lightrag)}; "
        f"staging api keys={len(staging_api)} lightrag keys={len(staging_lightrag)})",
        file=sys.stderr,
    )

    return 0 if not unwaived else 1


if __name__ == "__main__":
    sys.exit(run())
