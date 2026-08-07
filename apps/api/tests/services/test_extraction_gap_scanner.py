from uuid import uuid4

import pytest
from unittest.mock import AsyncMock, Mock

from nfm_db.services.extraction_gap_scanner import ExtractionGapScanner


@pytest.mark.asyncio
async def test_scanner_accepts_mapping_ontology_shape():
    session = Mock(add=Mock(), flush=AsyncMock())
    scanner = ExtractionGapScanner(session)
    ontology_id = uuid4()
    ontology = Mock(id=ontology_id, ontology_data={"entity_types": {"Fuel": {"properties": ["density"]}}})
    scanner._load_ontology = AsyncMock(return_value=ontology)
    scanner._load_chunks = AsyncMock(return_value=[])
    scanner._load_existing_gaps = AsyncMock(return_value=[])
    result = await scanner.scan_for_gaps(job_id=uuid4(), ontology_version_id=ontology_id)
    assert result.total_expected == 1
    assert result.gaps_found == 1
    assert result.gaps_created == 1
    assert result.scan_duration_ms >= 0
    assert session.add.call_count == 2
