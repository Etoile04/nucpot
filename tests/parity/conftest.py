"""Sys-path bootstrap for parity tests (NFM-2891).

The V2 extraction steps live under ``apps/api/src/nfm_db/...`` which is
not on the repo-root ``pythonpath``. Import the steps directly here so
the rest of the package can write ``from nfm_db.services.extraction...``
without per-test boilerplate.

NFM-5126: this conftest is also load-order robust. The repo root carries
a legacy partial ``src/nfm_db`` (NFM-700 router stubs for
``tests/api/v1``; no ``__init__.py``, no ``services/``) that sits on the
root ``pythonpath``. In a full ``pytest tests/`` traversal the api tests
collect first and cache that partial in ``sys.modules``; a plain
``sys.path.insert`` then arrives too late — ``nfm_db`` is already
resolved and ``nfm_db.services`` fails with ModuleNotFoundError
(pre-existing on main; invisible to CI, whose backend job runs from
``apps/api``). Drop any cached ``nfm_db`` that does not cover
``apps/api/src`` so the parity tests re-resolve the full package.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_APPS_API_SRC = _REPO_ROOT / "apps" / "api" / "src"

if str(_APPS_API_SRC) not in sys.path:
    sys.path.insert(0, str(_APPS_API_SRC))

_cached = sys.modules.get("nfm_db")
_cached_paths = [str(p) for p in getattr(_cached, "__path__", [])]
if _cached is not None and not any(str(_APPS_API_SRC) in p for p in _cached_paths):
    for _name in [n for n in sys.modules if n == "nfm_db" or n.startswith("nfm_db.")]:
        del sys.modules[_name]
