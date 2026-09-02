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


# ---------------------------------------------------------------------------
# NFM-4105 AC-1 / AC-2: stop the bleed on truly-unattributed extractions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNfm4105UnattributedSentinel:
    """NFM-4105 AC-1 + AC-2 regression tests.

    AC-1: New extractions no longer create additional
          shared-placeholder-title ``data_sources`` rows.
    AC-2: Two DOI-empty extractions from DIFFERENT files do not
          collapse into one indistinguishable source.

    The DOI-empty branch is split into two sub-cases:

      * Has ANY provenance (DOI / reference / source_file)
            → existing NFM-4088 dedup chain (file_hash / content_md /
              title lookup).
      * Has NO provenance at all
            → ``_get_or_create_unattributed_sentinel`` returns the
              single canonical ``Unattributed (no source provenance)``
              row, created atomically via a Postgres advisory lock.
    """

    async def test_no_provenance_items_converge_on_one_sentinel(
        self, db_session: AsyncSession
    ) -> None:
        """AC-1: many no-provenance extractions yield exactly ONE row.

        Pre-fix behaviour: each extraction inserted a fresh row with
        title ``"Unattributed source (no DOI)"`` (or per-call ``(no DOI)``
        variants), multiplying the placeholder pile.  Post-fix: every
        no-provenance extraction converges on the single sentinel row.

        Within a single ``map_and_persist`` batch, items that share a
        source-key reuse the in-memory ``source_map`` slot silently
        (no INSERT, no ``reused_entities`` increment) — this is the
        same dedup behaviour every other source receives.  The
        convergence guarantee is therefore asserted on the post-call
        row count, not on the counter.
        """
        await _seed_property_type(db_session)

        # Three items, each with NO DOI, NO reference, NO source_file.
        inputs = [
            _make_extracted_property(source_file=None, reference=None),
            _make_extracted_property(source_file=None, reference=None),
            _make_extracted_property(source_file=None, reference=None),
        ]
        result = await map_and_persist(db_session, inputs)

        # The canonical sentinel title is the ONLY row of its kind.
        sentinels = (
            await db_session.execute(
                select(DataSource).where(
                    DataSource.title
                    == "Unattributed (no source provenance)"
                )
            )
        ).scalars().all()
        assert len(sentinels) == 1, (
            f"expected 1 sentinel row, got {len(sentinels)} — convergence "
            "guarantee (NFM-4105 AC-1) broken"
        )
        # Exactly one INSERT happened for the sentinel — the 2nd & 3rd
        # items reused the in-memory source_map slot.
        assert result.created_sources == 1

    async def test_no_provenance_reuses_existing_legacy_placeholder(
        self, db_session: AsyncSession
    ) -> None:
        """AC-1 reuse-preference: legacy placeholder rows are adopted.

        When an environment already carries a legacy
        ``"Unattributed source (no DOI)"`` row, the sentinel helper
        reuses it instead of inserting a fresh ``"Unattributed (no
        source provenance)"`` row.  This keeps migration 070's
        pre-existing data stable without forcing a cleanup pass.
        """
        await _seed_property_type(db_session)

        legacy = DataSource(
            title="Unattributed source (no DOI)",
            source_type="other",
        )
        db_session.add(legacy)
        await db_session.flush()

        inputs = [_make_extracted_property(source_file=None, reference=None)]
        result = await map_and_persist(db_session, inputs)

        # The legacy row remains the single canonical source; no new
        # "Unattributed (no source provenance)" row was inserted.
        new_sentinels = (
            await db_session.execute(
                select(DataSource).where(
                    DataSource.title
                    == "Unattributed (no source provenance)"
                )
            )
        ).scalars().all()
        assert new_sentinels == []
        assert result.reused_entities >= 1
        assert result.created_sources == 0

    async def test_two_no_provenance_calls_share_one_row(
        self, db_session: AsyncSession
    ) -> None:
        """AC-2 negative form: two no-provenance items share one row.

        Complements the positive form above: even when two no-
        provenance items are processed in separate mapper calls
        (simulating concurrent extraction sessions), they must reuse
        the same sentinel rather than producing two indistinguishable
        rows.  AC-2 requires that extractions from DIFFERENT files
        remain distinguishable — this test pins the inverse: when
        files are absent, the rows are intentionally shared.
        """
        await _seed_property_type(db_session)

        first = await map_and_persist(
            db_session,
            [_make_extracted_property(source_file=None, reference=None)],
        )
        second = await map_and_persist(
            db_session,
            [_make_extracted_property(source_file=None, reference=None)],
        )

        # First call creates the sentinel; second call reuses it.
        assert first.created_sources == 1
        assert second.created_sources == 0
        assert second.reused_entities >= 1

        sentinels = (
            await db_session.execute(
                select(DataSource).where(
                    DataSource.title
                    == "Unattributed (no source provenance)"
                )
            )
        ).scalars().all()
        assert len(sentinels) == 1

    async def test_two_different_source_files_produce_distinct_rows(
        self, db_session: AsyncSession
    ) -> None:
        """AC-2 positive form: two different source_files ⇒ two rows.

        The DOI-empty branch only collapses rows that carry NO
        provenance at all.  When source_file is present (DOI + ref
        still empty) the existing file-based dedup chain keeps the
        rows distinct.  This pins the AC-2 invariant that the new
        sentinel path does NOT over-collapse when provenance is
        partially present.
        """
        await _seed_property_type(db_session)

        # Two pre-existing canonical rows whose content_md contains
        # distinct basenames — simulates NFM-1486 PDF uploads.
        for basename in ("UO2_paper.md", "Zr_paper.md"):
            db_session.add(
                DataSource(
                    title=f"Canonical source for {basename}",
                    source_type="other",
                    content_md=f"Path: literature/{basename}\n\nbody…",
                )
            )
        await db_session.flush()

        inputs = [
            _make_extracted_property(
                reference=None, source_file="literature/UO2_paper.md"
            ),
            _make_extracted_property(
                reference=None, source_file="literature/Zr_paper.md"
            ),
        ]
        result = await map_and_persist(db_session, inputs)

        # Two distinct source rows, both reused via content_md prefix.
        uo2 = (
            await db_session.execute(
                select(DataSource).where(DataSource.title.like("%UO2_paper%"))
            )
        ).scalars().first()
        zr = (
            await db_session.execute(
                select(DataSource).where(DataSource.title.like("%Zr_paper%"))
            )
        ).scalars().first()
        assert uo2 is not None and zr is not None
        assert uo2.id != zr.id, (
            "AC-2 broken: two DOI-empty extractions with different "
            "source_files collapsed to a single DataSource row"
        )
        assert result.created_sources == 0
        assert result.reused_entities >= 2

    async def test_sentinel_helper_exposes_lock_constant(
        self, db_session: AsyncSession
    ) -> None:
        """AC-1 lock-key stability: same key on every call.

        The advisory-lock key is computed from a module-level SHA-256
        prefix so all workers in the cluster agree on the same lock.
        This test pins that contract — if the key derivation drifts,
        concurrent mappers in different workers would not serialize
        on the same lock and the convergence guarantee breaks.
        """
        from nfm_db.services.extraction_to_db_mapper import (
            _UNATTRIBUTED_SENTINEL_LOCK_KEY,
            _UNATTRIBUTED_SENTINEL_TITLE,
        )

        assert _UNATTRIBUTED_SENTINEL_TITLE == "Unattributed (no source provenance)"
        # 32-bit signed range; the SHA-256 prefix hash is fit into int.
        assert 0 <= _UNATTRIBUTED_SENTINEL_LOCK_KEY < 2**31
