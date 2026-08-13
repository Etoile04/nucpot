"""Tests for PropertyNormalizer pipeline step — NFM-2684 / NFM-2677-B2.

Covers all 5+ acceptance-criteria scenarios:

1. Temperature unit conversion (°C → K, °F → K, K passthrough)
2. Energy unit conversion (eV → J, MeV → J, J passthrough)
3. Already-canonical value passes through unchanged
4. Unknown unit handled gracefully (passthrough + warning, never silent data loss)
5. Idempotency (already-normalized chunk is a no-op)
6. Conversion metadata preserved for audit
7. _source_span preserved from input chunk
8. Non-property entity_kind chunks excluded
9. Empty/missing input produces empty output
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from nfm_db.pipeline.extraction_step import (
    ExtractionStep,
    StepContext,
    is_extraction_step,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_property_chunk(
    property_name: str = "temperature",
    value: float = 100.0,
    unit: str = "deg C",
    material_name: str = "UO2",
    entity_kind: str = "property_value",
    source_span: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a minimal property_value chunk dict."""
    chunk: dict[str, Any] = {
        "entity_kind": entity_kind,
        "property_name": property_name,
        "value": value,
        "unit": unit,
        "material_name": material_name,
        "_source_span": source_span or {"start": 0, "end": 10},
    }
    chunk.update(extra)
    return chunk


# ===========================================================================
# Protocol conformance
# ===========================================================================


@pytest.mark.unit
class TestPropertyNormalizerProtocol:
    """PropertyNormalizer satisfies the ExtractionStep Protocol."""

    def test_step_type_is_map(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        assert PropertyNormalizer.step_type == "map"

    def test_has_input_keys(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        assert isinstance(PropertyNormalizer.input_keys, tuple)
        assert len(PropertyNormalizer.input_keys) > 0

    def test_satisfies_protocol(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        assert is_extraction_step(PropertyNormalizer())

    def test_protocol_isinstance(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        assert isinstance(PropertyNormalizer(), ExtractionStep)


# ===========================================================================
# AC 1: Temperature unit conversion
# ===========================================================================


@pytest.mark.unit
class TestTemperatureConversion:
    """Temperature: °C → K, °F → K, K passthrough."""

    @pytest.mark.asyncio
    async def test_celsius_to_kelvin(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        chunk = _make_property_chunk(value=0.0, unit="deg C")
        ctx = StepContext(job_id="job-1", values={"mapped_chunks": [chunk]})

        result = await step.execute(ctx)

        assert result.skipped is False
        normalized = result.outputs["normalized_chunks"]
        assert len(normalized) == 1
        assert math.isclose(normalized[0]["value"], 273.15, abs_tol=1e-9)
        assert normalized[0]["unit"] == "K"
        assert normalized[0]["chunk_type"] == "property_normalized"
        assert normalized[0]["step_order"] == 4

    @pytest.mark.asyncio
    async def test_fahrenheit_to_kelvin(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        chunk = _make_property_chunk(value=212.0, unit="deg F")
        ctx = StepContext(job_id="job-1", values={"mapped_chunks": [chunk]})

        result = await step.execute(ctx)
        normalized = result.outputs["normalized_chunks"]
        assert math.isclose(normalized[0]["value"], 373.15, abs_tol=1e-9)
        assert normalized[0]["unit"] == "K"

    @pytest.mark.asyncio
    async def test_kelvin_passthrough(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        chunk = _make_property_chunk(value=300.0, unit="K")
        ctx = StepContext(job_id="job-1", values={"mapped_chunks": [chunk]})

        result = await step.execute(ctx)
        normalized = result.outputs["normalized_chunks"]
        assert math.isclose(normalized[0]["value"], 300.0, abs_tol=1e-9)
        assert normalized[0]["unit"] == "K"


# ===========================================================================
# AC 2: Energy unit conversion
# ===========================================================================


@pytest.mark.unit
class TestEnergyConversion:
    """Energy: eV → J, MeV → J, J passthrough."""

    @pytest.mark.asyncio
    async def test_ev_to_joules(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        chunk = _make_property_chunk(
            property_name="energy", value=1.0, unit="eV"
        )
        ctx = StepContext(job_id="job-1", values={"mapped_chunks": [chunk]})

        result = await step.execute(ctx)
        normalized = result.outputs["normalized_chunks"]
        assert math.isclose(
            normalized[0]["value"], 1.602176634e-19, rel_tol=1e-9
        )
        assert normalized[0]["unit"] == "J"

    @pytest.mark.asyncio
    async def test_mev_to_joules(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        chunk = _make_property_chunk(
            property_name="energy", value=1.0, unit="MeV"
        )
        ctx = StepContext(job_id="job-1", values={"mapped_chunks": [chunk]})

        result = await step.execute(ctx)
        normalized = result.outputs["normalized_chunks"]
        assert math.isclose(
            normalized[0]["value"], 1.602176634e-13, rel_tol=1e-9
        )
        assert normalized[0]["unit"] == "J"

    @pytest.mark.asyncio
    async def test_joules_passthrough(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        chunk = _make_property_chunk(
            property_name="energy", value=1.5, unit="J"
        )
        ctx = StepContext(job_id="job-1", values={"mapped_chunks": [chunk]})

        result = await step.execute(ctx)
        normalized = result.outputs["normalized_chunks"]
        assert math.isclose(normalized[0]["value"], 1.5, abs_tol=1e-9)
        assert normalized[0]["unit"] == "J"


# ===========================================================================
# AC 3: Already-canonical value passes through unchanged
# ===========================================================================


@pytest.mark.unit
class TestCanonicalPassthrough:
    """Already-canonical unit passes through with factor=1.0."""

    @pytest.mark.asyncio
    async def test_pressure_pa_passthrough(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        chunk = _make_property_chunk(
            property_name="pressure", value=101325.0, unit="Pa"
        )
        ctx = StepContext(job_id="job-1", values={"mapped_chunks": [chunk]})

        result = await step.execute(ctx)
        normalized = result.outputs["normalized_chunks"]
        assert math.isclose(normalized[0]["value"], 101325.0, abs_tol=1e-6)
        assert normalized[0]["unit"] == "Pa"
        assert normalized[0]["metadata"]["conversion_factor"] == 1.0


# ===========================================================================
# AC 4: Unknown unit handled gracefully
# ===========================================================================


@pytest.mark.unit
class TestUnknownUnit:
    """Unknown unit: passthrough + warning metadata, never silent data loss."""

    @pytest.mark.asyncio
    async def test_unknown_unit_passthrough_with_warning(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        chunk = _make_property_chunk(
            property_name="temperature", value=500.0, unit="furlong"
        )
        ctx = StepContext(job_id="job-1", values={"mapped_chunks": [chunk]})

        result = await step.execute(ctx)
        normalized = result.outputs["normalized_chunks"]
        assert len(normalized) == 1
        # Value passes through unchanged — no silent data loss.
        assert normalized[0]["value"] == 500.0
        assert normalized[0]["unit"] == "furlong"
        # Warning metadata flags the unknown unit.
        assert normalized[0]["metadata"].get("warning") is not None
        assert "unknown" in normalized[0]["metadata"]["warning"].lower()


# ===========================================================================
# AC 5: Idempotency
# ===========================================================================


@pytest.mark.unit
class TestIdempotency:
    """Normalizing an already-normalized chunk is a no-op."""

    @pytest.mark.asyncio
    async def test_idempotent_no_double_normalize(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        # First pass: 0°C → 273.15 K
        chunk = _make_property_chunk(value=0.0, unit="deg C")
        ctx = StepContext(job_id="job-1", values={"mapped_chunks": [chunk]})
        result1 = await step.execute(ctx)
        normalized = result1.outputs["normalized_chunks"]

        assert math.isclose(normalized[0]["value"], 273.15, abs_tol=1e-9)
        assert normalized[0]["unit"] == "K"

        # Second pass: already-normalized chunk → unchanged
        ctx2 = StepContext(
            job_id="job-1", values={"mapped_chunks": normalized}
        )
        result2 = await step.execute(ctx2)
        normalized2 = result2.outputs["normalized_chunks"]

        assert math.isclose(normalized2[0]["value"], 273.15, abs_tol=1e-9)
        assert normalized2[0]["unit"] == "K"


# ===========================================================================
# Metadata & source span preservation
# ===========================================================================


@pytest.mark.unit
class TestMetadataAndSourceSpan:
    """Conversion metadata preserved; _source_span forwarded."""

    @pytest.mark.asyncio
    async def test_metadata_fields_present(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        chunk = _make_property_chunk(
            value=100.0, unit="deg C",
            source_span={"start": 5, "end": 20},
        )
        ctx = StepContext(job_id="job-1", values={"mapped_chunks": [chunk]})

        result = await step.execute(ctx)
        meta = result.outputs["normalized_chunks"][0]["metadata"]

        assert meta["original_unit"] == "deg C"
        assert meta["normalized_unit"] == "K"
        assert "conversion_factor" in meta

    @pytest.mark.asyncio
    async def test_source_span_preserved(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        span = {"start": 42, "end": 99, "page": 3}
        chunk = _make_property_chunk(
            property_name="pressure", value=1.0, unit="MPa",
            source_span=span,
        )
        ctx = StepContext(job_id="job-1", values={"mapped_chunks": [chunk]})

        result = await step.execute(ctx)
        assert result.outputs["normalized_chunks"][0]["_source_span"] == span


# ===========================================================================
# Non-property entity_kind filtering
# ===========================================================================


@pytest.mark.unit
class TestEntityKindFiltering:
    """Chunks with entity_kind != property_value/unit are excluded."""

    @pytest.mark.asyncio
    async def test_non_property_chunk_excluded(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        chunk = _make_property_chunk(
            entity_kind="material",
            material_name="UO2",
            value=0.0,
            unit="",
            property_name="",
        )
        ctx = StepContext(job_id="job-1", values={"mapped_chunks": [chunk]})

        result = await step.execute(ctx)
        assert len(result.outputs["normalized_chunks"]) == 0

    @pytest.mark.asyncio
    async def test_mixed_chunks_only_normalize_properties(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        chunk_mat = _make_property_chunk(
            entity_kind="material",
            material_name="UO2",
            value=0.0,
            unit="",
            property_name="",
        )
        chunk_prop = _make_property_chunk(value=100.0, unit="deg C")
        ctx = StepContext(
            job_id="job-1",
            values={"mapped_chunks": [chunk_mat, chunk_prop]},
        )

        result = await step.execute(ctx)
        normalized = result.outputs["normalized_chunks"]
        assert len(normalized) == 1
        assert normalized[0]["property_name"] == "temperature"


# ===========================================================================
# Empty / missing input
# ===========================================================================


@pytest.mark.unit
class TestEmptyInput:
    """Empty input produces empty output without error."""

    @pytest.mark.asyncio
    async def test_empty_chunk_list(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        ctx = StepContext(job_id="job-1", values={"mapped_chunks": []})

        result = await step.execute(ctx)
        assert len(result.outputs["normalized_chunks"]) == 0

    @pytest.mark.asyncio
    async def test_missing_input_key(self) -> None:
        from nfm_db.pipeline.property_normalizer import PropertyNormalizer

        step = PropertyNormalizer()
        ctx = StepContext(job_id="job-1")

        result = await step.execute(ctx)
        assert len(result.outputs.get("normalized_chunks", [])) == 0
