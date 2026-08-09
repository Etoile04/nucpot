"""Unit tests for EntityExtractor pipeline step — NFM-2683.

Covers:

- Section with mixed entity kinds (material_name, formula,
  property_value, unit) → multiple entity chunks emitted.
- Section with no entities → empty result.
- Entity _source_span validity: offsets lie within the parent
  section text, and ``section_text[span.start:span.end]`` yields
  the extracted entity text.
- Idempotency: two calls with the same input produce identical
  output lists.
- Protocol conformance: EntityExtractor satisfies ExtractionStep.
"""

from __future__ import annotations

import pytest

from nfm_db.pipeline.entity_extractor import (
    ENTITY_KINDS,
    EntityChunk,
    EntityExtractor,
    extract_entities_from_section,
)
from nfm_db.pipeline.extraction_step import (
    ExtractionStep,
    StepContext,
    StepResult,
    is_extraction_step,
)

# ---------------------------------------------------------------------------
# Test data — section-level ExtractionChunkData inputs
# ---------------------------------------------------------------------------

SECTION_UO2_PROPERTIES = (
    "UO2 is uranium dioxide, a common nuclear fuel material. "
    "Its lattice constant is 5.47 Å and the bulk modulus measures "
    "207.5 GPa at room temperature. "
    "The thermal conductivity is 7.5 W/(m·K). "
    "UO₂ has a face-centered cubic (FCC) crystal structure."
)

SECTION_NO_ENTITIES = (
    "This section discusses general safety protocols for "
    "handling radioactive materials in laboratory environments."
)

# ---------------------------------------------------------------------------
# 1. Section with mixed entity kinds
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMixedEntityKinds:
    """EntityExtractor extracts all four entity kinds from a rich section."""

    @pytest.mark.asyncio
    async def test_extracts_material_names(self) -> None:
        """At least one entity with kind 'material_name' is emitted."""
        result = await extract_entities_from_section(SECTION_UO2_PROPERTIES)
        kinds = {e.entity_kind for e in result}
        assert "material_name" in kinds

    @pytest.mark.asyncio
    async def test_extracts_formulas(self) -> None:
        """At least one entity with kind 'formula' is emitted."""
        result = await extract_entities_from_section(SECTION_UO2_PROPERTIES)
        kinds = {e.entity_kind for e in result}
        assert "formula" in kinds

    @pytest.mark.asyncio
    async def test_extracts_property_values(self) -> None:
        """At least one entity with kind 'property_value' is emitted."""
        result = await extract_entities_from_section(SECTION_UO2_PROPERTIES)
        kinds = {e.entity_kind for e in result}
        assert "property_value" in kinds

    @pytest.mark.asyncio
    async def test_extracts_units(self) -> None:
        """At least one entity with kind 'unit' is emitted."""
        result = await extract_entities_from_section(SECTION_UO2_PROPERTIES)
        kinds = {e.entity_kind for e in result}
        assert "unit" in kinds

    @pytest.mark.asyncio
    async def test_all_kinds_are_canonical(self) -> None:
        """Every emitted entity's kind belongs to ENTITY_KINDS."""
        result = await extract_entities_from_section(SECTION_UO2_PROPERTIES)
        for entity in result:
            assert entity.entity_kind in ENTITY_KINDS, (
                f"Unknown entity_kind: {entity.entity_kind!r}"
            )

    @pytest.mark.asyncio
    async def test_multiple_entities_per_section(self) -> None:
        """A rich section produces more than one entity chunk."""
        result = await extract_entities_from_section(SECTION_UO2_PROPERTIES)
        assert len(result) >= 2


# ---------------------------------------------------------------------------
# 2. Section with no entities
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoEntities:
    """Sections without recognizable material entities yield empty."""

    @pytest.mark.asyncio
    async def test_empty_result_for_no_entities(self) -> None:
        result = await extract_entities_from_section(SECTION_NO_ENTITIES)
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_string_yields_empty(self) -> None:
        result = await extract_entities_from_section("")
        assert result == []


# ---------------------------------------------------------------------------
# 3. Entity span validity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSourceSpanValidity:
    """Each entity's _source_span points into the parent section."""

    @pytest.mark.asyncio
    async def test_span_offsets_within_section(self) -> None:
        result = await extract_entities_from_section(SECTION_UO2_PROPERTIES)
        for entity in result:
            span = entity.source_span
            assert 0 <= span["start"] < len(SECTION_UO2_PROPERTIES), (
                f"span.start={span['start']} out of range for "
                f"section of length {len(SECTION_UO2_PROPERTIES)}"
            )
            assert span["start"] < span["end"] <= len(
                SECTION_UO2_PROPERTIES
            ), (
                f"span.end={span['end']} out of range for "
                f"section of length {len(SECTION_UO2_PROPERTIES)}"
            )

    @pytest.mark.asyncio
    async def test_span_text_matches_entity_text(self) -> None:
        """section_text[start:end] == entity.text."""
        result = await extract_entities_from_section(SECTION_UO2_PROPERTIES)
        for entity in result:
            span = entity.source_span
            extracted_text = SECTION_UO2_PROPERTIES[
                span["start"] : span["end"]
            ]
            assert extracted_text == entity.text, (
                f"Span text mismatch: {extracted_text!r} != {entity.text!r} "
                f"(kind={entity.entity_kind}, span={span})"
            )

    @pytest.mark.asyncio
    async def test_entity_chunk_has_step_order_3(self) -> None:
        result = await extract_entities_from_section(SECTION_UO2_PROPERTIES)
        for entity in result:
            assert entity.step_order == 3

    @pytest.mark.asyncio
    async def test_entity_chunk_type_is_entity(self) -> None:
        result = await extract_entities_from_section(SECTION_UO2_PROPERTIES)
        for entity in result:
            assert entity.chunk_type == "entity"


# ---------------------------------------------------------------------------
# 4. Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdempotency:
    """Two calls with identical input produce identical output."""

    @pytest.mark.asyncio
    async def test_same_input_same_output(self) -> None:
        result1 = await extract_entities_from_section(
            SECTION_UO2_PROPERTIES
        )
        result2 = await extract_entities_from_section(
            SECTION_UO2_PROPERTIES
        )
        assert len(result1) == len(result2)
        for e1, e2 in zip(result1, result2, strict=True):
            assert e1 == e2


# ---------------------------------------------------------------------------
# 5. Protocol conformance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEntityExtractorProtocol:
    """EntityExtractor satisfies the ExtractionStep Protocol."""

    def test_is_extraction_step(self) -> None:
        step = EntityExtractor()
        assert isinstance(step, ExtractionStep)
        assert is_extraction_step(step) is True

    def test_step_type_is_extract(self) -> None:
        step = EntityExtractor()
        assert step.step_type == "extract"

    def test_input_keys_include_sections(self) -> None:
        step = EntityExtractor()
        assert "sections" in step.input_keys

    @pytest.mark.asyncio
    async def test_execute_returns_step_result(self) -> None:
        step = EntityExtractor()
        ctx = StepContext(
            job_id="job-1",
            values={"sections": SECTION_UO2_PROPERTIES},
        )
        result = await step.execute(ctx)
        assert isinstance(result, StepResult)
        assert "entities" in result.produced_keys
        assert isinstance(result.outputs.get("entity_count"), int)

    @pytest.mark.asyncio
    async def test_execute_does_not_mutate_context(self) -> None:
        step = EntityExtractor()
        ctx = StepContext(
            job_id="job-1",
            values={"sections": SECTION_UO2_PROPERTIES},
        )
        original_values = dict(ctx.values)
        await step.execute(ctx)
        assert ctx.values == original_values

    @pytest.mark.asyncio
    async def test_execute_produces_entities_key(self) -> None:
        """The produced 'entities' key contains a list of EntityChunk."""
        step = EntityExtractor()
        ctx = StepContext(
            job_id="job-1",
            values={"sections": SECTION_UO2_PROPERTIES},
        )
        result = await step.execute(ctx)
        entities = result.outputs.get("entities")
        assert isinstance(entities, list)
        # At least one entity from the rich section.
        assert len(entities) >= 1
        assert all(isinstance(e, EntityChunk) for e in entities)
