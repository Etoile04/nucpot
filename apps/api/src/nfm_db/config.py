"""Application configuration via environment variables."""

import json
import os
from typing import Annotated

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, NoDecode

# ---------------------------------------------------------------------------
# Version lock — must match docker/lightrag.Dockerfile ARG LIGHTRAG_VERSION
# ---------------------------------------------------------------------------
LIGHTRAG_VERSION: str = os.environ.get("LIGHTRAG_VERSION", "1.5.4")

# Default CORS origin set when NFM_CORS_ORIGINS is unset, blank, or unparseable.
# Mirrors the historical local-dev convenience list so a broken env never
# degrades the API to "no allowed origins" silently.
_DEFAULT_CORS_ORIGINS: list[str] = ["http://localhost:3000"]


def _parse_cors_origins(value: object) -> list[str]:
    """Tolerantly parse ``NFM_CORS_ORIGINS`` from any source.

    The field is declared with :class:`pydantic_settings.NoDecode` so this
    function receives the raw env string instead of pydantic-settings
    attempting ``json.loads`` first. We accept three shapes in order of
    preference:

    1. ``list`` — passed through (programmatic construction, tests).
    2. JSON list — ``["https://a","https://b"]`` (NFM-4077 staging env_file).
    3. Comma-separated — ``https://a,https://b`` (ad-hoc operator format,
       also what the legacy ``main.py`` ``CORS_ORIGINS`` reader expects).

    NFM-4090 hardening: any malformed input (the ``docker compose`` v5.5.0
    YAML re-parse that produced ``[ "url" ]`` with whitespace, the
    ``STAGING_CORS_ORIGINS:-[...]`` shell default that lost its outer
    brackets, etc.) falls through to :data:`_DEFAULT_CORS_ORIGINS` rather
    than crashing ``get_settings()`` during ``alembic upgrade head``.
    """
    if value is None:
        return list(_DEFAULT_CORS_ORIGINS)
    if isinstance(value, list):
        cleaned = [str(v).strip() for v in value if v is not None and str(v).strip()]
        return cleaned if cleaned else list(_DEFAULT_CORS_ORIGINS)
    if not isinstance(value, str):
        return list(_DEFAULT_CORS_ORIGINS)
    s = value.strip()
    if not s:
        return list(_DEFAULT_CORS_ORIGINS)
    # JSON-shaped input (starts with [ or {): try to parse. Only accept a
    # list of strings — JSON objects are not a valid shape for CORS and
    # fall through to the comma-split rescue path.
    if s.startswith(("[", "{")):
        try:
            parsed = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            cleaned = [str(v).strip() for v in parsed if v is not None and str(v).strip()]
            return cleaned if cleaned else list(_DEFAULT_CORS_ORIGINS)
    items = [o.strip() for o in s.split(",") if o.strip()]
    return items if items else list(_DEFAULT_CORS_ORIGINS)


def _resolve_service_jwt_ttl() -> int:
    """Resolve the service-account JWT TTL from environment.

    Reads ``NUCPOT_SERVICE_JWT_TTL_MINUTES`` (NFM-1973 / NFM-1972 AC-1
    contract) and falls back to the Pydantic default if the variable is
    unset or unparseable.  We do not route this through the standard
    ``NFM_`` prefix because the AC explicitly named the env var.
    """
    raw = os.environ.get("NUCPOT_SERVICE_JWT_TTL_MINUTES")
    if raw is None or not raw.strip():
        return 30
    try:
        value = int(raw)
    except ValueError:
        return 30
    if value <= 0:
        return 30
    return value


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str = "postgresql+asyncpg://nfm:nfm@localhost:5432/nfm_db"
    debug: bool = True
    # NFM-4090 defense-in-depth: declare with NoDecode + BeforeValidator so
    # the raw NFM_CORS_ORIGINS env value reaches our tolerant parser
    # without pydantic-settings first attempting json.loads and crashing
    # on malformed input (the SRE-detected crash loop in this issue).
    cors_origins: Annotated[list[str], NoDecode, BeforeValidator(_parse_cors_origins)] = list(
        _DEFAULT_CORS_ORIGINS
    )
    secret_key: str = "CHANGE_THIS_IN_PRODUCTION"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8 hours
    # NFM-1973 / NFM-1972 AC-1: TTL for service-account JWTs.
    # Configurable via NUCPOT_SERVICE_JWT_TTL_MINUTES env var, default 30 minutes.
    # Independent of human-user TTL so operators can tighten/loosen service
    # tokens without affecting interactive sessions.
    service_jwt_ttl_minutes: int = _resolve_service_jwt_ttl()
    blog_content_dir: str = "content/blog"
    # NFM-2781 HOTFIX CR1: allowlist base for
    # ``get_gap_source_text``.  ``chunk.source_reference`` strings must
    # resolve to a path inside this directory; anything outside is
    # rejected by :func:`nfm_db.core.path_safety.safe_resolve`.  Wired
    # via ``NFM_SOURCE_BASE`` env var.
    source_base: str = "/var/nfm-data/sources/"
    # NFM-3070: allowed backup root directories.  ``backup_dir`` query
    # parameter on the admin backup endpoints is rejected unless it
    # resolves inside one of these roots.  Comma-separated via
    # ``NFM_BACKUP_DIR_ROOTS`` env var.
    backup_dir_roots: list[str] = ["/var/backups/nucpot"]
    lightrag_host: str = "localhost"
    lightrag_port: int = 9621
    lightrag_version: str = LIGHTRAG_VERSION
    # NFM-3575 / NFM-3548-A: feature gate for the Phase 5.3 priority
    # scoring refactor.  When False (default) callers fall back to the
    # pre-refactor scoring path; when True ``extraction_pipeline`` (see
    # NFM-3548-B) reads from ``services/priority.py``.  Wired via the
    # ``NFM_PRIORITY_V2_ENABLED`` env var.
    priority_v2_enabled: bool = False
    # NFM-3575 / NFM-3548-A: optional JSON override for the priority
    # weight table consumed by ``services/priority.py``.  Expected
    # keys: ``ontology``, ``atf``, ``citation``.  When unset the module
    # defaults (0.4 / 0.3 / 0.3) apply.  Wired via the
    # ``NFM_PRIORITY_WEIGHTS`` env var.
    priority_weights: str = ""

    model_config = {"env_file": ".env", "env_prefix": "NFM_", "extra": "ignore"}


def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
