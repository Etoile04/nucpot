"""NFM-4794 — datasource UUID must never reach ``DataSource.title``.

Regression for the deterministic ``process_literature_task`` failure on
datasource ``49034bf0-f58e-4900-889d-11342c77c518`` (parent NFM-4791):

1. ``literature_service.process_literature`` re-runs the merged LLM +
   heuristic batch through ``_post_process_extracted`` with
   ``source_reference=str(ds.id)`` — the RAW datasource UUID.
2. Heuristic items never carry ``source_file`` (they emit ``source``),
   so the back-fill stamped ``item["source_file"] = "<uuid>"``.
3. The mapper title chain promoted it: ``title = reference or
   source_file or placeholder`` → ``title = "<uuid>"``.
4. The NFM-4088 DOI-empty guard correctly refused (ValueError) and the
   whole batch was dropped.

The fix (AC2) has two layers, both tested here:
* ``_post_process_extracted`` no longer back-fills a UUID-shaped
  ``source_reference`` into ``source_file`` (root-cause removal), and
* the mapper excludes UUID-shaped ``source_file`` from the title
  fallback chain and from ``_has_any_provenance`` (defence-in-depth),
  so genuinely-unattributed items route to the NFM-4105 sentinel.

The ``_reject_uuid_title`` guard itself is untouched and still
exercised (AC2 constraint: guard unchanged).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    DataSource,
    PropertyCategory,
    PropertyType,
)
from nfm_db.schemas.extraction import ExtractedProperty
from nfm_db.services.extraction_pipeline import _post_process_extracted
from nfm_db.services.extraction_to_db_mapper import map_and_persist
from nfm_db.services.extraction_to_db_mapper_lookups import (
    _has_any_provenance,
    _reject_uuid_title,
)

DS_UUID = "49034bf0-f58e-4900-889d-11342c77c518"
SENTINEL_TITLE = "Unattributed (no source provenance)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_property_type(
    db: AsyncSession,
    *,
    category_slug: str = "thermal",
    property_name: str = "melting_point",
) -> PropertyType:
    category = PropertyCategory(
        name=category_slug, slug=category_slug, description=f"{category_slug} properties"
    )
    db.add(category)
    await db.flush()

    pt = PropertyType(
        category_id=category.id,
        name=property_name,
        slug=property_name,
        value_type="scalar",
    )
    db.add(pt)
    await db.commit()
    await db.refresh(pt)
    return pt


def _llm_item(
    *,
    source_file: str | None = None,
    reference: str | None = None,
    source_doi: str | None = None,
    property_name: str = "lattice_constant",
    value: str = "5.47",
) -> dict[str, Any]:
    """LLM-shaped item as it leaves the extractor (pre-post-process)."""
    return {
        "material_name": "UO2",
        "composition": "UO2",
        "property_category": "physical",
        "property": property_name,
        "value": value,
        "unit": "angstrom",
        "confidence": "high",
        # reference/source_file/source_doi omitted by keyword — the
        # failing 49034bf0 items omitted all three.
        **({"reference": reference} if reference is not None else {}),
        **({"source_file": source_file} if source_file is not None else {}),
        **({"source_doi": source_doi} if source_doi is not None else {}),
    }


def _heuristic_item(
    *,
    property_name: str = "bulk_modulus",
    value: float = 207.5,
) -> dict[str, Any]:
    """Heuristic-shaped item exactly as ``heuristic_extract`` emits it —
    ``source`` key (not ``source_file``), ``source_doi: None``, no
    ``reference``."""
    return {
        "element_system": "UO2",
        "material_name": "UO2",
        "composition": "UO2",
        "phase": "Unknown",
        "property_name": property_name,
        "value": value,
        "unit": "GPa",
        "method": "heuristic_regex",
        "source": DS_UUID,
        "source_doi": None,
        "confidence": "medium",
        "uncertainty": 5.0,
        "temperature": None,
        "cache_level": "L2",
        "property_category": "mechanical",
    }


# ---------------------------------------------------------------------------
# Layer 1: pipeline back-fill (root cause)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPostProcessUuidBackfill:
    """``_post_process_extracted`` must not stamp UUID ids as provenance."""

    async def test_uuid_source_reference_is_not_backfilled(self) -> None:
        items = [_llm_item(), _heuristic_item()]

        processed = _post_process_extracted(items, DS_UUID)

        for out in processed:
            assert not out.get("source_file"), (
                f"UUID-shaped source_reference must not be back-filled into "
                f"source_file (got {out.get('source_file')!r}) — NFM-4794"
            )

    async def test_path_source_reference_still_backfilled(self) -> None:
        """Legitimate file-path references keep the existing behaviour."""
        items = [_llm_item()]

        processed = _post_process_extracted(items, "literature/UO2_paper.md")

        assert processed[0]["source_file"] == "literature/UO2_paper.md"

    async def test_existing_source_file_never_overwritten(self) -> None:
        items = [_llm_item(source_file="literature/keep.md")]

        processed = _post_process_extracted(items, DS_UUID)

        assert processed[0]["source_file"] == "literature/keep.md"

    async def test_title_reference_still_backfilled(self) -> None:
        """``ontofuel_extract`` passes ``ds.title`` for datasource mode —
        a paper title is an informative label and must stay eligible.
        (Title ASCII-folded from the 49034bf0 row: "A DFT study for
        alpha-phase uranium and uranium alloys".)"""
        title = "A DFT study for alpha-phase uranium and uranium alloys"
        items = [_llm_item()]

        processed = _post_process_extracted(items, title)

        assert processed[0]["source_file"] == title


# ---------------------------------------------------------------------------
# Layer 2: mapper defence-in-depth
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMapperUuidSourceFileDefence:
    """Even if a UUID-shaped ``source_file`` sneaks into the mapper, it
    must not become ``DataSource.title`` nor fake provenance."""

    async def test_uuid_source_file_not_promoted_to_title(
        self, db_session: AsyncSession
    ) -> None:
        await _seed_property_type(db_session, property_name="lattice_constant")

        # Pre-fix this raised ValueError("UUID-pattern title") and
        # dropped the whole batch (NFM-4791 traceback).
        result = await map_and_persist(
            db_session,
            [
                {
                    "material_name": "UO2",
                    "composition": "UO2",
                    "property_category": "physical",
                    "property": "lattice_constant",
                    "value": "5.47",
                    "unit": "angstrom",
                    "confidence": "high",
                    "source_file": DS_UUID,
                    "source_doi": None,
                }
            ],
        )

        uuid_titles = (
            (
                await db_session.execute(
                    select(DataSource).where(DataSource.title == DS_UUID)
                )
            )
            .scalars()
            .all()
        )
        assert not uuid_titles, "UUID-shaped title reached DataSource.title"
        # No provenance → deterministic sentinel routing (NFM-4105).
        sentinel = (
            (
                await db_session.execute(
                    select(DataSource).where(DataSource.title == SENTINEL_TITLE)
                )
            )
            .scalars()
            .all()
        )
        assert len(sentinel) == 1
        assert result.created_sources + result.reused_entities >= 1

    async def test_uuid_source_file_is_not_provenance(self) -> None:
        no_file = ExtractedProperty.model_validate(
            {
                "material_name": "UO2",
                "composition": "UO2",
                "property": "lattice_constant",
                "value": "5.47",
                "unit": "angstrom",
                "confidence": "high",
            }
        )
        with_uuid_file = no_file.model_copy(update={"source_file": DS_UUID})
        with_real_file = no_file.model_copy(
            update={"source_file": "literature/UO2_paper.md"}
        )

        assert _has_any_provenance(with_uuid_file) is False
        assert _has_any_provenance(with_real_file) is True
        assert _has_any_provenance(no_file) is False

    async def test_guard_still_rejects_uuid_title(self) -> None:
        """AC2 constraint: the NFM-4088 guard is unchanged and exercised."""
        with pytest.raises(ValueError, match="UUID-pattern title"):
            _reject_uuid_title(DS_UUID)


# ---------------------------------------------------------------------------
# AC3: end-to-end id-driven chain regression
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdDrivenExtractionChain:
    """The exact failing shape of the 49034bf0 reextract: LLM items with
    no reference/source_file/doi + heuristic items, post-processed with
    the raw datasource UUID (as ``literature_service`` does), then
    mapped. Must complete deterministically — no ValueError."""

    async def test_chain_routes_to_sentinel_without_value_error(
        self, db_session: AsyncSession
    ) -> None:
        await _seed_property_type(db_session, property_name="lattice_constant")
        await _seed_property_type(
            db_session, category_slug="mechanical", property_name="bulk_modulus"
        )

        batch: list[dict[str, Any]] = [
            _llm_item(),  # LLM item: no reference, no source_file, no doi
            _heuristic_item(),  # heuristic item: "source"=uuid, no source_file
        ]

        # Mirror literature_service.process_literature Step 3b: the
        # merged batch is re-run through _post_process_extracted with
        # the RAW datasource UUID as source_reference.
        processed = _post_process_extracted(batch, DS_UUID)

        result = await map_and_persist(db_session, processed)

        uuid_titles = (
            (
                await db_session.execute(
                    select(DataSource).where(DataSource.title == DS_UUID)
                )
            )
            .scalars()
            .all()
        )
        assert not uuid_titles

        sentinel = (
            (
                await db_session.execute(
                    select(DataSource).where(DataSource.title == SENTINEL_TITLE)
                )
            )
            .scalars()
            .all()
        )
        assert len(sentinel) == 1, (
            "genuinely-unattributed items must converge on the sentinel row"
        )
        assert result.created_measurements >= 1, "properties were not persisted"
