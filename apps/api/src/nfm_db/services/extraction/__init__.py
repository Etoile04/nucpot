"""Clean-slate extraction pipeline types (NFM-2679, NFM-2677 strangler-fig P1).

This sub-package hosts the new pipeline decomposition types. It must NOT
import from the legacy ``extraction_pipeline`` / ``extraction_orchestrator``
modules — the strangler-fig replaces those incrementally, side-by-side.
"""

from nfm_db.services.extraction.types import ExtractionChunk, ExtractionStep

__all__ = ["ExtractionChunk", "ExtractionStep"]
