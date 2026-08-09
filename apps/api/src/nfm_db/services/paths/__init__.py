"""Gap fill path handlers (NFM-2649).

Concrete implementations of the :class:`GapFillPath` protocol that
satisfy gap-fill requests for one of three source preferences:

- :class:`LiteratureFillPath` — schedules a literature search.
- :class:`DFTFillPath` — creates a placeholder DFT calculation.
- :class:`ExternalDBFillPath` — queries external data sources.

Each handler is independently instantiable and tested with mocked
dependencies.  The dispatch router (see ADR-NFM-2577) selects the
matching handler based on ``source_preference``.
"""

from __future__ import annotations

from nfm_db.services.paths.base import DispatchResult, GapFillPath
from nfm_db.services.paths.dft_fill import DFTFillPath
from nfm_db.services.paths.external_db_fill import ExternalDBFillPath
from nfm_db.services.paths.literature_fill import LiteratureFillPath

__all__ = [
    "DFTFillPath",
    "DispatchResult",
    "ExternalDBFillPath",
    "GapFillPath",
    "LiteratureFillPath",
]
