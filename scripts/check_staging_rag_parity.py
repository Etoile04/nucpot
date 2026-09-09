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

NFM-4534 — value-parity layer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
For every var name in :data:`MUST_MATCH_PROD_DEFAULTS` declared on BOTH
prod and staging, the *resolved default value* on staging must equal the
resolved default value on prod. ``${VAR:-default}`` compose interpolation
is parsed to extract the default; a literal value is taken as-is.

This catches the sharp edge that presence-only parity misses: a tracked
var can be declared on both sides with divergent defaults, breaking the
canary while the presence check stays green. Canonical regression:
PR #1278 staging fix (commit 0fcea31) corrected ``EMBEDDING_BINDING_HOST``
to mirror prod; reverting that fix now fails the guard itself.

The set is deliberately small (see :data:`MUST_MATCH_PROD_DEFAULTS`).
Vars legitimately allowed to diverge — credentials, host ports,
``*_API_KEY``, ``POSTGRES_*``, service names — are NOT in this set; the
presence check above continues to enforce key-only parity on those, and
the guard stays silent on value drift outside MUST_MATCH_PROD.

Waiver mechanism
----------------
``STAGING_RAG_PARITY_WAIVER`` may be set to ``"<reason>:VAR1,VAR2,..."``
to skip specific missing vars (each must be named by exact match). The
guard prints every waivered var in its output so reviewers see the cost
of the canary's reduced fidelity. Every other missing var still fails.
The waiver covers the AC #3 case (rerank vars staged OR waived with an
explicit recorded decision and a stated fidelity cost).

NFM-4534 extends the same mechanism to value drift: when a
MUST_MATCH_PROD var's staging default diverges from prod's default,
``STAGING_RAG_PARITY_WAIVER="<reason>:VAR"`` silences it just like a
presence drift, with the same per-var coverage contract — a waiver
covering one drifted var does NOT silence a different drifted var.
Reviewers see the waiver line and the stated reason in the report
either way.

Exit codes
----------
  0  every tracked prod RAG env var is present on staging (or waived);
     every MUST_MATCH_PROD var's staging default equals prod's (or waived)
  1  one or more tracked prod RAG env vars are absent on staging with no
     waiver, OR one or more MUST_MATCH_PROD vars' staging default diverges
     from prod's default with no waiver
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

# NFM-4534 — value-parity layer.
#
# A small explicit set of vars whose *resolved default value* on staging
# must equal prod's. The set is deliberately narrow: vars outside it keep
# the presence-only semantics (credentials, host ports, service names are
# legitimately allowed to differ between prod and staging).
#
# Each entry below couples with another var in the set or with a behavior
# in the lightrag service — divergence is structural, not a tunables
# difference, so the canary's fidelity is silently reduced without this
# check.
#
#   EMBEDDING_MODEL         — Ollama-only models (e.g. nomic-embed-text) 404
#                              on api.openai.com. Coupled with
#                              EMBEDDING_BINDING_HOST.
#   EMBEDDING_DIM           — LightRAG assumes 1536 when unset and rejects
#                              every embed with "Embedding dimension mismatch".
#   EMBBEDING_SEND_DIM      — must match prod so the canary exercises the
#                              same ?dimensions= behavior.
#   EMBEDDING_BINDING_HOST  — coupled with EMBEDDING_MODEL (see above).
#   RERANK_MODEL            — model name; the canary must invoke the same
#                              model as prod to validate any rerank change.
#   RERANK_BINDING_HOST     — coupled with RERANK_MODEL; local rerank vs
#                              remote cohere is a semantic, not a tunable,
#                              difference.
#   MIN_RERANK_SCORE        — cutoff value; if staging uses a higher/lower
#                              threshold than prod, the canary's pass/fail
#                              no longer reflects prod.
MUST_MATCH_PROD_DEFAULTS: frozenset[str] = frozenset(
    {
        "EMBEDDING_MODEL",
        "EMBEDDING_DIM",
        "EMBEDDING_SEND_DIM",
        "EMBEDDING_BINDING_HOST",
        "RERANK_MODEL",
        "RERANK_BINDING_HOST",
        "MIN_RERANK_SCORE",
    }
)

WAIVER_ENV = "STAGING_RAG_PARITY_WAIVER"
WAIVER_PATTERN = re.compile(r"^([^:]+):([A-Z][A-Z0-9_]*(?:,[A-Z][A-Z0-9_]*)*)\s*$")


DRIFT_KIND_MISSING = "MISSING"
DRIFT_KIND_VALUE_DRIFT = "VALUE_DRIFT"


@dataclass(frozen=True)
class Drift:
    """One parity finding between prod and staging.

    ``kind`` is either :data:`DRIFT_KIND_MISSING` (NFM-4532 — var declared
    on prod but absent on staging) or :data:`DRIFT_KIND_VALUE_DRIFT`
    (NFM-4534 — var declared on both sides with divergent default values,
    for vars in :data:`MUST_MATCH_PROD_DEFAULTS`). For value-drift
    findings, ``prod_value`` / ``staging_value`` carry the two resolved
    defaults so the report can name them.
    """

    service: str  # 'api' or 'lightrag'
    var: str  # e.g. 'NFM_LIGHTRAG_QUERY_TIMEOUT_S'
    kind: str = DRIFT_KIND_MISSING
    prod_value: str = ""
    staging_value: str = ""


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


def _read_env_file_pairs(path: Path) -> dict[str, str]:
    """Like :func:`_read_env_file_keys` but returns KEY→VALUE pairs. Used
    for NFM-4534 value-parity so env_file-sourced vars can also have their
    resolved default compared. Values are taken verbatim — env_files in
    this repo use plain ``KEY=VALUE`` (no ``${VAR:-default}`` interpolation
    inside the file itself)."""
    pairs: dict[str, str] = {}
    if not path.exists():
        return pairs
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if not match:
            continue
        key = match.group(1)
        value = match.group(2).strip()
        # Strip matching surrounding quotes (single or double) — docker
        # compose treats "VALUE" and 'value' as literals; the YAML loader
        # already does this for inline env, but env_file parsing is on us.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        pairs[key] = value
    return pairs


def _compose_default(expr: object) -> str:
    """Extract the ``default`` from a ``${VAR:-default}`` compose
    interpolation. Returns the value as-is when it is a plain literal.
    ``None`` and empty strings map to ``""`` so callers can compare
    defaults uniformly.

    This is the reference implementation lifted from
    ``scripts/tests/test_check_staging_rag_parity.py::test_ollama_only_embedding_model_is_not_pointed_at_openai``
    (commit ``a12a826``); folding it into the script means the point-tests
    no longer need a parallel copy.
    """
    if expr is None:
        return ""
    raw = str(expr).strip()
    if not raw:
        return ""
    if not (raw.startswith("${") and raw.endswith("}")):
        return raw
    inner = raw[2:-1]
    if ":-" not in inner:
        return raw
    return inner.split(":-", 1)[1]


def collect_service_env_keys(compose_path: Path, service_name: str) -> set[str]:
    """Return the union of env-var NAMES declared on ``service_name`` in
    the compose file at ``compose_path`` — inline ``environment:`` block
    plus every referenced ``env_file:``.

    The ``environment:`` value can be either a mapping (``FOO: bar``) or a
    list of bare names (``- FOO``); the latter is rare on this repo but
    handled correctly so a future refactor does not regress.

    Kept as a thin wrapper over :func:`collect_service_env` so the
    presence-only path stays allocation-cheap when callers only need keys
    (the env_file parsing is shared either way).
    """
    return set(collect_service_env(compose_path, service_name).keys())


def collect_service_env(compose_path: Path, service_name: str) -> dict[str, str]:
    """Return KEY→raw-value for every env var declared on
    ``service_name`` in ``compose_path`` — inline ``environment:`` block
    plus every referenced ``env_file:``. Values are the raw compose
    representation (a literal string OR a ``${VAR:-default}`` expression);
    callers that want the resolved default should pass the result through
    :func:`_compose_default`.

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

    pairs: dict[str, str] = {}

    environment = service.get("environment")
    if environment is not None:
        if isinstance(environment, dict):
            for k, v in environment.items():
                pairs[str(k)] = "" if v is None else str(v)
        elif isinstance(environment, list):
            for entry in environment:
                if isinstance(entry, str):
                    # Plain "FOO" — adopt the name verbatim, no value.
                    if "=" in entry:
                        key, _, value = entry.partition("=")
                        key = key.strip()
                        if key:
                            pairs[key] = value.strip().strip('"').strip("'")
                    else:
                        pairs[entry] = ""
                elif isinstance(entry, dict):
                    # {"VAR": value} inside a list form — take the pair.
                    for k, v in entry.items():
                        pairs[str(k)] = "" if v is None else str(v)
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
            pairs.update(_read_env_file_pairs(resolved))

    return pairs


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


def diff_value_parity(
    prod_values: dict[str, dict[str, str]],
    staging_values: dict[str, dict[str, str]],
) -> list[Drift]:
    """NFM-4534 — return every MUST_MATCH_PROD var whose staging resolved
    default diverges from prod's resolved default.

    A var is only checked when declared on BOTH sides (the presence layer
    above catches missing-in-staging cases). ``${VAR:-default}`` interpolation
    is parsed to extract the default; a literal value is taken as-is. The
    two resolved defaults travel with the finding so the report can name
    them.

    Out-of-scope vars (credentials, host ports, anything NOT in
    :data:`MUST_MATCH_PROD_DEFAULTS`) are not inspected — the presence
    layer still enforces key-only parity on those.
    """
    drifts: list[Drift] = []
    for service in ("api", "lightrag"):
        prod_svc = prod_values.get(service, {})
        staging_svc = staging_values.get(service, {})
        for var in sorted(MUST_MATCH_PROD_DEFAULTS):
            if var not in prod_svc or var not in staging_svc:
                # presence drift is the other layer's responsibility; we
                # only act when both sides have the var.
                continue
            prod_default = _compose_default(prod_svc[var])
            staging_default = _compose_default(staging_svc[var])
            if prod_default != staging_default:
                drifts.append(
                    Drift(
                        service=service,
                        var=var,
                        kind=DRIFT_KIND_VALUE_DRIFT,
                        prod_value=prod_default,
                        staging_value=staging_default,
                    )
                )
    return drifts


def render_report(
    drifts: list[Drift],
    waived: set[str],
    waiver_reason: str,
) -> str:
    """Human-readable parity report. The waiver line is emitted only when
    a waiver was actually applied — empty waivers stay silent so a clean
    run reads as clean.

    Drifts are grouped by kind: presence drifts (NFM-4532) first, then
    value drifts (NFM-4534). When there are no drifts at all, the OK line
    spans both layers so a clean run reads as clean."""
    lines = ["staging RAG parity check (NFM-4532/4534):"]
    if not drifts:
        lines.append(
            "  OK — every tracked prod RAG env var is present on staging "
            "with MUST_MATCH_PROD defaults in sync."
        )
    else:
        missing = [d for d in drifts if d.kind == DRIFT_KIND_MISSING]
        value = [d for d in drifts if d.kind == DRIFT_KIND_VALUE_DRIFT]
        if missing:
            lines.append(
                f"  DRIFT (presence): {len(missing)} tracked prod RAG env "
                f"var(s) missing on staging:"
            )
            for entry in missing:
                waived_marker = " [WAIVED]" if entry.var in waived else ""
                lines.append(
                    f"    - services.{entry.service}.{entry.var}{waived_marker}"
                )
        if value:
            lines.append(
                f"  DRIFT (value): {len(value)} MUST_MATCH_PROD env var(s) "
                f"with divergent default on staging:"
            )
            for entry in value:
                waived_marker = " [WAIVED]" if entry.var in waived else ""
                lines.append(
                    f"    - services.{entry.service}.{entry.var}: "
                    f"prod={entry.prod_value!r} staging={entry.staging_value!r}"
                    f"{waived_marker}"
                )
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
            "NFM-4532/4534 staging↔prod RAG env-var parity guard. Fails when "
            "a tracked prod RAG env var is missing from staging without an "
            "explicit waiver, OR when a MUST_MATCH_PROD var's staging "
            "default diverges from prod's. The same STAGING_RAG_PARITY_WAIVER "
            "env var covers both classes. See header docstring for the "
            "full contract."
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
        prod_api = collect_service_env(prod_path, args.api_service)
        prod_lightrag = collect_service_env(prod_path, args.lightrag_service)
        staging_api = collect_service_env(staging_path, args.api_service)
        staging_lightrag = collect_service_env(staging_path, args.lightrag_service)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"check_staging_rag_parity: OPERATIONAL ERROR — {exc}", file=sys.stderr)
        print("not failing parity; fix the input and re-run.", file=sys.stderr)
        return 2

    prod_values = {"api": prod_api, "lightrag": prod_lightrag}
    staging_values = {"api": staging_api, "lightrag": staging_lightrag}

    # NFM-4532 — presence parity: a tracked prod var declared on staging?
    presence_drifts = diff_parity(
        {k: set(v.keys()) for k, v in prod_values.items()},
        {k: set(v.keys()) for k, v in staging_values.items()},
    )
    # NFM-4534 — value parity: MUST_MATCH_PROD defaults agree across sides?
    value_drifts = diff_value_parity(prod_values, staging_values)
    drifts = presence_drifts + value_drifts

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
