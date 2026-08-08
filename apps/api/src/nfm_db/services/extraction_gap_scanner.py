"""Ontology-driven extraction gap scanner."""
from __future__ import annotations

from typing import Any

from nfm_db.services.gap_scanner import GapScanService


class ExtractionGapScanner(GapScanService):
    """Scan extraction chunks against ontology expectations."""
    @staticmethod
    def _entity_types(ontology: Any) -> list[dict[str, Any]]:
        data = ontology.ontology_data
        if not isinstance(data, dict):
            return []
        raw = data.get("entity_types")
        if isinstance(raw, dict):
            return [{"name": name, **definition} for name, definition in raw.items() if isinstance(name, str) and isinstance(definition, dict)]
        return [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []

__all__ = ["ExtractionGapScanner"]
