"""Sys-path bootstrap for parity tests (NFM-2891).

The V2 extraction steps live under ``apps/api/src/nfm_db/...`` which is
not on the repo-root ``pythonpath``. Import the steps directly here so
the rest of the package can write ``from nfm_db.services.extraction...``
without per-test boilerplate.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_APPS_API_SRC = _REPO_ROOT / "apps" / "api" / "src"

if str(_APPS_API_SRC) not in sys.path:
    sys.path.insert(0, str(_APPS_API_SRC))
