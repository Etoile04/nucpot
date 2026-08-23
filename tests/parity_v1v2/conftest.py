"""Sys-path bootstrap for V1<->V2 parity tests (NFM-3539).

Both the V1 stub path (``nfm_db.services.extraction_pipeline``) and the
V2 orchestrator (``nfm_db.services.extraction_orchestrator_v2``) live
under ``apps/api/src``. Add it to ``sys.path`` so the tests import as if
the API package were installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_APPS_API_SRC = _REPO_ROOT / "apps" / "api" / "src"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_APPS_API_SRC) not in sys.path:
    sys.path.insert(0, str(_APPS_API_SRC))