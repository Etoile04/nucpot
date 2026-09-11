#!/usr/bin/env python3
"""NFM-4681: prod compose must wire slowapi to Redis (DB 2).

Stops the deploy from silently regressing the 4-worker API to a
per-process ``memory://`` counter. The script parses the compose file
without instantiating Docker, so it runs in CI (~50 ms) and in the
sanity-check loop on the deploy host.

Usage::

    python scripts/check_prod_ratelimit_storage.py
    python scripts/check_prod_ratelimit_storage.py --compose-file <path>

Exit codes::

    0  compose wires RATE_LIMIT_STORAGE_URI to a Redis URI on DB 2
    1  compose file missing, unparsable, or lacks the storage URI
    2  the storage URI points somewhere other than a Redis DB-2 path
       (e.g. memory://, redis:// without the expected DB suffix)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover - PyYAML is in the api env
    sys.stderr.write(
        "check_prod_ratelimit_storage: PyYAML is required "
        "(pip install pyyaml). "
        f"Underlying error: {exc}\n"
    )
    sys.exit(1)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"

EXPECTED_REDIS_DBS = {"2"}
REDIS_URI_RE = re.compile(r"^redis://[^/]+/(\d+)$")


def _resolve_api_storage_uri(compose: dict) -> str | None:
    """Return the ``RATE_LIMIT_STORAGE_URI`` value on the api service, or ``None``."""
    services = compose.get("services") or {}
    api = services.get("api") or {}
    env = api.get("environment") or {}
    if isinstance(env, dict):
        return env.get("RATE_LIMIT_STORAGE_URI")
    if isinstance(env, list):
        for item in env:
            if not isinstance(item, str):
                continue
            key, sep, value = item.partition("=")
            if key.strip() == "RATE_LIMIT_STORAGE_URI":
                return value.strip().strip("'\"") if sep else None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=DEFAULT_COMPOSE,
        help="Path to docker-compose.prod.yml (default: %(default)s)",
    )
    args = parser.parse_args()

    compose_path: Path = args.compose_file
    if not compose_path.is_file():
        sys.stderr.write(
            f"check_prod_ratelimit_storage: compose file not found at {compose_path}\n"
        )
        return 1

    try:
        compose = yaml.safe_load(compose_path.read_text())
    except yaml.YAMLError as exc:
        sys.stderr.write(f"check_prod_ratelimit_storage: failed to parse {compose_path}: {exc}\n")
        return 1

    if not isinstance(compose, dict):
        sys.stderr.write("check_prod_ratelimit_storage: compose root is not a mapping\n")
        return 1

    storage_uri = _resolve_api_storage_uri(compose)
    if not storage_uri:
        sys.stderr.write(
            "check_prod_ratelimit_storage: services.api.environment is "
            "missing RATE_LIMIT_STORAGE_URI (NFM-4681 AC-2)\n"
        )
        return 1

    if storage_uri == "memory://":
        sys.stderr.write(
            "check_prod_ratelimit_storage: RATE_LIMIT_STORAGE_URI is "
            "memory:// — prod runs --workers 4 and the per-process counter "
            "gives 4× the per-IP budget before the first 429 (NFM-4681)\n"
        )
        return 2

    match = REDIS_URI_RE.match(storage_uri)
    if not match:
        sys.stderr.write(
            f"check_prod_ratelimit_storage: RATE_LIMIT_STORAGE_URI "
            f"{storage_uri!r} is not a redis://host:port/<db> URI\n"
        )
        return 2

    db = match.group(1)
    if db not in EXPECTED_REDIS_DBS:
        sys.stderr.write(
            f"check_prod_ratelimit_storage: redis DB {db!r} is outside "
            f"the NFM-4681 expected DBs {sorted(EXPECTED_REDIS_DBS)} — "
            "DB 0/1 are reserved for the Celery broker/result backend\n"
        )
        return 2

    print(f"check_prod_ratelimit_storage: OK — prod api RATE_LIMIT_STORAGE_URI={storage_uri}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
