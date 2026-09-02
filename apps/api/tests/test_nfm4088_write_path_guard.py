"""NFM-4088 — write-path guard tests for ``extraction_to_db_mapper``.

Covers the DOI-empty branch dedup logic introduced alongside migration
070_d2_dedup_bad_data_sources:

* AC-4: ``_reject_uuid_title`` raises on UUID-pattern titles and stays
  silent on real references.
* AC-3: title-based dedup in the DOI-empty branch reuses an existing
  ``DataSource`` row when its ``title`` already exists.
* AC-3: content-md ``LIKE`` fallback reuses an existing ``DataSource``
  whose ``content_md`` matches the item's ``source_file`` basename.
* AC-3: when all lookups miss, a fresh ``DataSource`` is inserted.
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
from nfm_db.services.extraction_to_db_mapper import (
    _UUID_TITLE_PATTERN,
    _reject_uuid_title,
    map_and_persist,
)

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


def _make_extracted_property(
    *,
    source_file: str | None = "literature/UO2_paper.md",
    material_name: str | None = "UO2",
    composition: str | None = "UO2",
    property_name: str = "melting_point",
    value: str = "2800",
    unit: str = "K",
    reference: str | None = "Smith et al., J. Nucl. Mater.",
    source_doi: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source_file": source_file,
        "material_name": material_name,
        "composition": composition,
        "property_category": "thermal",
        "property": property_name,
        "value": value,
        "unit": unit,
        "confidence": "high",
    }
    if reference is not None:
        out["reference"] = reference
    if source_doi is not None:
        out["source_doi"] = source_doi
    return out


# ---------------------------------------------------------------------------
# Pure-function tests (no DB)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUUIDTitlePattern:
    """Tests for the UUID regex constant."""

    @pytest.mark.parametrize(
        "candidate",
        [
            "9320cb50-eb65-4178-8d2e-c56aeb848b21",
            "00000000-0000-0000-0000-000000000000",
            "ABCDEF01-2345-6789-ABCD-EF0123456789",
        ],
    )
    def test_matches_canonical_uuid_strings(self, candidate: str) -> None:
        assert _UUID_TITLE_PATTERN.match(candidate) is not None

    @pytest.mark.parametrize(
        "candidate",
        [
            "Smith et al., J. Nucl. Mater.",
            "Unattributed source (no DOI)",
            "Owen, M.W.D., Cooper, Rushton et al.",
            "",
            "9320cb50-eb65-4178-8d2e-c56aeb848b2",  # truncated
            "9320cb50-eb65-4178-8d2e-c56aeb848b21z",  # trailing junk
        ],
    )
    def test_rejects_non_uuid_titles(self, candidate: str) -> None:
        assert _UUID_TITLE_PATTERN.match(candidate) is None


@pytest.mark.unit
class TestRejectUUIDTitle:
    """``_reject_uuid_title`` refuses INSERTs whose title is a UUID."""

    def test_uuid_title_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="UUID-pattern title"):
            _reject_uuid_title("9320cb50-eb65-4178-8d2e-c56aeb848b21")

    @pytest.mark.parametrize(
        "good_title",
        [
            "Smith et al., J. Nucl. Mater.",
            "Owen, M.W.D., Cooper, Rushton et al.",
            "Unattributed source (no DOI)",
        ],
    )
    def test_real_reference_passes_silently(self, good_title: str) -> None:
        _reject_uuid_title(good_title)


# ---------------------------------------------------------------------------
# Mapper-level integration tests (in-memory SQLite)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDoiEmptyDedup:
    """DOI-empty branch dedup behaviour (NFM-4088 AC-3)."""

    async def test_uuid_title_is_rejected(self, db_session: AsyncSession) -> None:
        """A row whose ``reference`` is a UUID string must raise rather than persist."""
        await _seed_property_type(db_session)

        with pytest.raises(ValueError, match="UUID-pattern title"):
            await map_and_persist(
                db_session,
                [
                    _make_extracted_property(
                        reference="deadbeef-1234-5678-9012-abcdefabcdef",
                    )
                ],
            )

    async def test_title_dedup_with_existing_source(self, db_session: AsyncSession) -> None:
        """Two items with the same reference (DOI-empty) reuse one row."""
        await _seed_property_type(db_session)

        # Pre-create an existing DataSource whose title matches the
        # candidate reference.  This simulates the post-migration state
        # where a canonical row already exists for that reference.
        existing = DataSource(
            title="Smith et al., J. Nucl. Mater.",
            source_type="other",
        )
        db_session.add(existing)
        await db_session.flush()

        inputs = [
            _make_extracted_property(reference="Smith et al., J. Nucl. Mater."),
            _make_extracted_property(
                property_name="melting_point",
                value="3120",
                reference="Smith et al., J. Nucl. Mater.",
            ),
        ]
        result = await map_and_persist(db_session, inputs)

        sources = (
            await db_session.execute(
                select(DataSource).where(
                    DataSource.title == "Smith et al., J. Nucl. Mater."
                )
            )
        ).scalars().all()
        assert len(sources) == 1
        assert result.created_sources == 0
        assert result.reused_entities >= 1

    async def test_placeholder_title_dedup(self, db_session: AsyncSession) -> None:
        """Two items both falling back to the placeholder title reuse one row."""
        await _seed_property_type(db_session)

        # Existing row bearing the placeholder title (post-migration state).
        existing = DataSource(
            title="Unattributed source (no DOI)",
            source_type="other",
        )
        db_session.add(existing)
        await db_session.flush()

        # The first item has no reference AND no source_file → falls
        # back to the placeholder title.  The dedup branch must reuse
        # the existing row rather than create a duplicate.
        inputs = [
            _make_extracted_property(source_file=None, reference=None),
        ]
        result = await map_and_persist(db_session, inputs)

        sources = (
            await db_session.execute(
                select(DataSource).where(
                    DataSource.title == "Unattributed source (no DOI)"
                )
            )
        ).scalars().all()
        assert len(sources) == 1
        assert result.reused_entities >= 1

    async def test_content_md_prefix_dedup(self, db_session: AsyncSession) -> None:
        """Two items whose ``source_file`` matches a parsed ``content_md`` reuse one row."""
        await _seed_property_type(db_session)

        # Pre-create a DataSource whose content_md contains the
        # basename of ``item.source_file``.  This simulates a PDF
        # uploaded via NFM-1486 whose parsed text retained the
        # original file path.
        existing = DataSource(
            title="Canonical source for UO2_paper.md",
            source_type="other",
            content_md="Path: literature/UO2_paper.md\n\nUO2 main content...",
        )
        db_session.add(existing)
        await db_session.flush()

        # Item has neither DOI nor a usable reference → it falls back
        # to source_file as title.  The dedup should find the existing
        # row via the content_md LIKE prefix and reuse it.
        inputs = [
            _make_extracted_property(
                reference=None,
                source_file="literature/UO2_paper.md",
            )
        ]
        result = await map_and_persist(db_session, inputs)

        sources = (
            await db_session.execute(
                select(DataSource).where(
                    DataSource.title.like("%UO2_paper%")
                )
            )
        ).scalars().all()
        assert len(sources) == 1, (
            f"expected 1 source for the content_md prefix, got {len(sources)}"
        )
        assert result.reused_entities >= 1

    async def test_no_match_inserts_fresh_source(self, db_session: AsyncSession) -> None:
        """When all lookups miss, a fresh DataSource row is INSERTed."""
        await _seed_property_type(db_session)

        inputs = [
            _make_extracted_property(
                reference="Jones et al., J. Nucl. Mater. (2024)",
            )
        ]
        result = await map_and_persist(db_session, inputs)

        assert result.created_sources == 1
        sources = (
            await db_session.execute(select(DataSource))
        ).scalars().all()
        assert any(
            s.title == "Jones et al., J. Nucl. Mater. (2024)" for s in sources
        )
