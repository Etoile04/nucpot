"""OpenKIM query API provider.

API docs: https://openkim.org/doc/usage/kim-query/
Endpoints verified 2026-06-19, documented in `OPENKIM_API.md`.

List models: POST {OPENKIM_API_BASE}/get_available_models (returns JSON array of KIM IDs)
Model detail: GET https://openkim.org/id/<KIM_LONG_ID> (HTML with citation_* <meta> tags)
"""

import os

OPENKIM_API_BASE = os.getenv("OPENKIM_API_BASE", "https://query.openkim.org/api")
OPENKIM_DETAIL_BASE = os.getenv("OPENKIM_DETAIL_BASE", "https://openkim.org/id")
OPENKIM_CACHE_TTL_SECONDS = int(os.getenv("OPENKIM_CACHE_TTL_SECONDS", "300"))
OPENKIM_TIMEOUT = float(os.getenv("OPENKIM_TIMEOUT", "5.0"))
