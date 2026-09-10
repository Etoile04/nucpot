"""Tests for the literature dedup layer (NFM-4549, G1-C).

Covers:
- ``normalize_doi`` — DOI prefix / whitespace / case folding
- ``resolve_literature_dataset`` — DOI hit, content_hash fallback, miss path
- ``compute_dedupe_key`` — deterministic over (dataset, property, source, value_hash)
- The DB-level UNIQUE on ``property_measurements.dedupe_key`` rejects a
  second row with the same key (AC-9 — Owen 92-row case study).
- ``extraction_to_db_mapper.map_and_persist`` actually populates
  ``dedupe_key`` on every new measurement (regression guard for the
  E2E QA 2026-09-10 probe: two identical mapper calls must collapse).
- ``extraction_to_db_mapper.map_and_persist`` stamps fresh Datasets
  with ``literature_doi`` / ``literature_content_hash`` so the
  cross-row DOI → content_hash ladder can hit.

Pattern follows ``test_dedup_service.py``: hermetic SQLite + aiosqlite.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import JSON, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nfm_db.models import Base
from nfm_db.models.material import Material, MaterialCategory
from nfm_db.models.property import (
    Dataset,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.models.source import DataSource
from nfm_db.services.extraction_to_db_mapper import map_and_persist
from nfm_db.services.literature_dedup import (
    compute_dedupe_key,
    normalize_doi,
    resolve_literature_dataset,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _replace_jsonb(metadata) -> None:
    """SQLite shim — drop ``PG_JSONB`` types AND ``::type`` server_default casts.

    The local copy in ``conftest._replace_jsonb`` was incomplete (NFM-4549
    pre-rebase test): it only swapped ``JSONB`` column types for ``JSON``
    and left ``server_default='[]'::jsonb`` literals untouched, which
    SQLite rejects at CREATE TABLE with ``unrecognized token: ":"``. After
    G1-B (NFM-4548, abc345a39) was merged into ``main``, several
    ``dataset_versions`` columns now ship with ``::jsonb`` defaults and the
    bare JSON-type swap is no longer enough — the cast stripping below is
    required. Mirrors ``conftest._replace_jsonb`` so this hermetic fixture
    can keep using ``Base.metadata.create_all`` directly.
    """
    import re

    from sqlalchemy import text as sa_text
    from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB

    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = JSON()
            default = getattr(col.server_default, "arg", None)
            default_sql = getattr(default, "text", None)
            if isinstance(default_sql, str) and "::" in default_sql:
                from sqlalchemy import DefaultClause
                col.server_default = DefaultClause(
                    sa_text(re.sub(r"::\s*\w+", "", default_sql))
                )


@pytest.fixture
async def db_session():
    """Hermetic SQLite session with all tables created.

    Mirrors the ``test_dedup_service.db_session`` fixture so the two
    test modules can coexist in CI without cross-talk.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        dbapi_conn.commit()

    _replace_jsonb(Base.metadata)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def material(db_session: AsyncSession) -> Material:
    cat = MaterialCategory(name="Fuel", slug="fuel")
    db_session.add(cat)
    await db_session.flush()
    mat = Material(name="UO2", formula="UO2", category_id=cat.id)
    db_session.add(mat)
    await db_session.flush()
    return mat


@pytest.fixture
async def property_type(db_session: AsyncSession) -> PropertyType:
    cat = PropertyCategory(name="thermal", slug="thermal")
    db_session.add(cat)
    await db_session.flush()
    pt = PropertyType(
        name="melting_point",
        slug="melting_point",
        category_id=cat.id,
        value_type="scalar",
        unit_id=None,
    )
    db_session.add(pt)
    await db_session.flush()
    return pt


@pytest.fixture
async def data_source(db_session: AsyncSession) -> DataSource:
    ds = DataSource(title="Source A", source_type="literature")
    db_session.add(ds)
    await db_session.flush()
    return ds


# ---------------------------------------------------------------------------
# normalize_doi (pure)
# ---------------------------------------------------------------------------


class TestNormalizeDoi:
    def test_lowercases(self) -> None:
        assert normalize_doi("10.1234/ABC") == "10.1234/abc"

    def test_strips_whitespace(self) -> None:
        assert normalize_doi("  10.1234/xyz  ") == "10.1234/xyz"
        assert normalize_doi("\t10.1234/xyz\n") == "10.1234/xyz"

    def test_collapses_internal_whitespace(self) -> None:
        # Some metadata exports pack a space after the slash.
        assert normalize_doi("10.1234/ abc") == "10.1234/abc"

    def test_strips_url_prefix_https(self) -> None:
        assert normalize_doi("https://doi.org/10.1234/foo") == "10.1234/foo"

    def test_strips_url_prefix_http(self) -> None:
        assert normalize_doi("http://dx.doi.org/10.1234/foo") == "10.1234/foo"

    def test_strips_doi_prefix_scheme(self) -> None:
        assert normalize_doi("doi:10.1234/foo") == "10.1234/foo"
        assert normalize_doi("DOI: 10.1234/foo") == "10.1234/foo"

    def test_passes_through_already_clean(self) -> None:
        assert normalize_doi("10.1234/foo.bar") == "10.1234/foo.bar"

    def test_none_returns_none(self) -> None:
        assert normalize_doi(None) is None

    def test_empty_string_returns_none(self) -> None:
        # Empty/blank DOIs are not usable as keys — return None so the
        # caller falls back to content_hash matching.
        assert normalize_doi("") is None
        assert normalize_doi("   ") is None

    def test_deterministic(self) -> None:
        # Two callers that normalize the same string get the same key —
        # this is the round-trip the dedup lookup relies on.
        a = normalize_doi("  HTTPS://DOI.ORG/10.1234/FOO  ")
        b = normalize_doi("https://doi.org/10.1234/FOO")
        assert a == b == "10.1234/foo"


# ---------------------------------------------------------------------------
# resolve_literature_dataset (DB-backed)
# ---------------------------------------------------------------------------


class TestResolveLiteratureDataset:
    async def test_doi_hit_returns_existing_dataset(
        self, db_session: AsyncSession, material: Material
    ) -> None:
        existing = Dataset(
            material_id=material.id,
            title="Beeler 2018",
            literature_doi="10.1234/beeler",
        )
        db_session.add(existing)
        await db_session.flush()

        hit = await resolve_literature_dataset(db_session, doi="10.1234/BEELER", content_hash=None)
        assert hit is not None
        assert hit.id == existing.id

    async def test_doi_hit_normalizes_prefix_and_case(
        self, db_session: AsyncSession, material: Material
    ) -> None:
        # The stored DOI is bare; the query passes the URL-prefixed form.
        existing = Dataset(
            material_id=material.id,
            title="Beeler 2018",
            literature_doi="10.1234/beeler",
        )
        db_session.add(existing)
        await db_session.flush()

        hit = await resolve_literature_dataset(
            db_session,
            doi="https://doi.org/10.1234/BEELER",
            content_hash=None,
        )
        assert hit is not None
        assert hit.id == existing.id

    async def test_content_hash_fallback_after_doi_miss(
        self, db_session: AsyncSession, material: Material
    ) -> None:
        # No DOI in DB; the same content_hash arrives from a fresh
        # upload (e.g. preprint vs. publisher version of the same PDF).
        existing = Dataset(
            material_id=material.id,
            title="Calhoun 2018",
            literature_content_hash="sha256:abc",
        )
        db_session.add(existing)
        await db_session.flush()

        hit = await resolve_literature_dataset(db_session, doi=None, content_hash="sha256:abc")
        assert hit is not None
        assert hit.id == existing.id

    async def test_doi_takes_priority_over_content_hash(
        self, db_session: AsyncSession, material: Material
    ) -> None:
        # Two datasets — one matches by DOI, another by content_hash.
        # When both could match, the DOI is the stronger signal and
        # must be returned (ADR-017 §2.5: DOI is primary).
        doi_ds = Dataset(
            material_id=material.id,
            title="DOI match",
            literature_doi="10.1234/primary",
            literature_content_hash="sha256:same",
        )
        hash_ds = Dataset(
            material_id=material.id,
            title="Hash-only match",
            literature_content_hash="sha256:same",
        )
        db_session.add_all([doi_ds, hash_ds])
        await db_session.flush()

        hit = await resolve_literature_dataset(
            db_session,
            doi="10.1234/primary",
            content_hash="sha256:same",
        )
        assert hit is not None
        assert hit.id == doi_ds.id

    async def test_miss_returns_none(self, db_session: AsyncSession, material: Material) -> None:
        # Sanity: empty DB + both signals present → no hit.
        hit = await resolve_literature_dataset(
            db_session,
            doi="10.1234/unknown",
            content_hash="sha256:unknown",
        )
        assert hit is None

    async def test_handles_unusable_doi_and_falls_back_to_hash(
        self, db_session: AsyncSession, material: Material
    ) -> None:
        # The DOI normalizer returns None for blank inputs — the dedup
        # resolver must skip the DOI lookup and try content_hash only.
        existing = Dataset(
            material_id=material.id,
            title="Matched by hash",
            literature_content_hash="sha256:zzz",
        )
        db_session.add(existing)
        await db_session.flush()

        hit = await resolve_literature_dataset(db_session, doi="   ", content_hash="sha256:zzz")
        assert hit is not None
        assert hit.id == existing.id


# ---------------------------------------------------------------------------
# compute_dedupe_key (pure)
# ---------------------------------------------------------------------------


class TestComputeDedupeKey:
    def test_deterministic(self) -> None:
        d = uuid.uuid4()
        p = uuid.uuid4()
        s = uuid.uuid4()
        a = compute_dedupe_key(d, p, s, "v1")
        b = compute_dedupe_key(d, p, s, "v1")
        assert a == b
        # And it's not None / not empty.
        assert a and isinstance(a, str)

    def test_changes_with_value_hash(self) -> None:
        d = uuid.uuid4()
        p = uuid.uuid4()
        s = uuid.uuid4()
        a = compute_dedupe_key(d, p, s, "v1")
        b = compute_dedupe_key(d, p, s, "v2")
        assert a != b

    def test_changes_with_source(self) -> None:
        d = uuid.uuid4()
        p = uuid.uuid4()
        s1, s2 = uuid.uuid4(), uuid.uuid4()
        assert compute_dedupe_key(d, p, s1, "v") != compute_dedupe_key(d, p, s2, "v")

    def test_changes_with_dataset(self) -> None:
        d1, d2 = uuid.uuid4(), uuid.uuid4()
        p = uuid.uuid4()
        s = uuid.uuid4()
        assert compute_dedupe_key(d1, p, s, "v") != compute_dedupe_key(d2, p, s, "v")

    def test_changes_with_property_type(self) -> None:
        d = uuid.uuid4()
        p1, p2 = uuid.uuid4(), uuid.uuid4()
        s = uuid.uuid4()
        assert compute_dedupe_key(d, p1, s, "v") != compute_dedupe_key(d, p2, s, "v")


# ---------------------------------------------------------------------------
# AC-9 — dedupe_key UNIQUE constraint
# ---------------------------------------------------------------------------


class TestDedupeKeyUniqueConstraint:
    async def test_second_insert_with_same_key_raises(
        self,
        db_session: AsyncSession,
        material: Material,
        property_type: PropertyType,
        data_source: DataSource,
    ) -> None:
        ds = Dataset(material_id=material.id, title="AC-9 dataset")
        db_session.add(ds)
        await db_session.flush()

        key = compute_dedupe_key(ds.id, property_type.id, data_source.id, "0.5")

        first = PropertyMeasurement(
            dataset_id=ds.id,
            property_type_id=property_type.id,
            method="",
            dedupe_key=key,
            value_scalar=0.5,
            review_status="pending",
        )
        db_session.add(first)
        await db_session.flush()

        dup = PropertyMeasurement(
            dataset_id=ds.id,
            property_type_id=property_type.id,
            method="",
            dedupe_key=key,
            value_scalar=0.5,
            review_status="pending",
        )
        db_session.add(dup)
        with pytest.raises(IntegrityError):
            await db_session.flush()
        # Roll back the failed flush so the fixture teardown is clean.
        await db_session.rollback()

    async def test_different_value_hash_allowed(
        self,
        db_session: AsyncSession,
        material: Material,
        property_type: PropertyType,
        data_source: DataSource,
    ) -> None:
        # Sanity: the constraint is keyed on value_hash, so a different
        # measurement on the same (dataset, property, source) is fine.
        ds = Dataset(material_id=material.id, title="AC-9b dataset")
        db_session.add(ds)
        await db_session.flush()

        for vh in ("0.5", "0.6", "0.7"):
            db_session.add(
                PropertyMeasurement(
                    dataset_id=ds.id,
                    property_type_id=property_type.id,
                    method="",
                    dedupe_key=compute_dedupe_key(ds.id, property_type.id, data_source.id, vh),
                    value_scalar=float(vh),
                    review_status="pending",
                )
            )
        await db_session.flush()  # no IntegrityError


# ---------------------------------------------------------------------------
# Mapper wire-up — E2E QA 2026-09-10 regression guards
# ---------------------------------------------------------------------------
# The component tests above prove the schema, the helper, and the resolver
# work in isolation. They do NOT prove the production write path actually
# fires ``compute_dedupe_key`` on INSERT — which is exactly the gap the
# E2E QA probe found (two identical mapper calls landed two rows because
# the column stayed NULL). These tests exercise ``map_and_persist`` end
# to end and assert the column + ladder columns are populated.


def _make_extraction_input(
    *,
    source_doi: str | None = "10.1234/sample",
    material_name: str = "UO2",
    composition: str = "UO2",
    property_name: str = "melting_point",
    value: str = "2800",
    unit: str = "K",
    reference: str | None = "Smith et al., J. Nucl. Mater.",
    source_file: str | None = "literature/UO2_paper.md",
) -> dict[str, Any]:
    """Build an extraction payload that matches what the real pipeline emits."""
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


class TestMapperWritesDedupeKey:
    """Every mapper INSERT must populate ``dedupe_key`` (AC-9 contract)."""

    async def test_mapper_writes_dedupe_key_on_insert(self, db_session: AsyncSession) -> None:
        # Seed the catalogue rows the mapper requires.
        cat = MaterialCategory(name="Fuel", slug="fuel")
        db_session.add(cat)
        await db_session.flush()
        mat = Material(name="UO2", formula="UO2", category_id=cat.id)
        db_session.add(mat)
        await db_session.flush()

        pcat = PropertyCategory(name="thermal", slug="thermal")
        db_session.add(pcat)
        await db_session.flush()
        pt = PropertyType(
            name="melting_point",
            slug="melting_point",
            category_id=pcat.id,
            value_type="scalar",
        )
        db_session.add(pt)
        await db_session.flush()

        result = await map_and_persist(db_session, [_make_extraction_input(value="2800")])
        assert result.created_measurements == 1

        # The QA probe asserted both rows landed with dedupe_key=NULL.
        # After the wire-up the column MUST be populated.
        rows = (await db_session.execute(select(PropertyMeasurement))).scalars().all()
        assert len(rows) == 1
        assert rows[0].dedupe_key is not None
        assert rows[0].dedupe_key.startswith("sha256:")
        # And it MUST match the helper exactly — no off-by-one in the
        # composite ordering (dataset, property, source, value_hash).
        assert rows[0].dedupe_key == compute_dedupe_key(
            rows[0].dataset_id,
            rows[0].property_type_id,
            # source_id is the FK on the joined dataset row.
            (
                await db_session.execute(
                    select(Dataset.source_id).where(Dataset.id == rows[0].dataset_id)
                )
            ).scalar_one(),
            # value_hash mirrors the mapper's own derivation (sha1 of
            # sorted-JSON value kwargs). Recompute the canonical form so
            # the test fails if either side drifts.
            _expected_value_hash({"value_scalar": 2800.0}),
        )

    async def test_mapper_dedup_via_constraint_on_identical_rerun(
        self, db_session: AsyncSession
    ) -> None:
        """Reproduces the E2E QA 2026-09-10 probe scenario.

        Two ``map_and_persist`` calls with identical inputs must collapse
        to ONE ``PropertyMeasurement`` row (Owen 2023's 92-row case).
        """
        cat = MaterialCategory(name="Fuel", slug="fuel")
        db_session.add(cat)
        await db_session.flush()
        mat = Material(name="UO2", formula="UO2", category_id=cat.id)
        db_session.add(mat)
        await db_session.flush()

        pcat = PropertyCategory(name="thermal", slug="thermal")
        db_session.add(pcat)
        await db_session.flush()
        pt = PropertyType(
            name="melting_point",
            slug="melting_point",
            category_id=pcat.id,
            value_type="scalar",
        )
        db_session.add(pt)
        await db_session.flush()

        first = await map_and_persist(db_session, [_make_extraction_input(value="2800")])
        # Second call with the same payload — without an explicit dedupe_key
        # (the real mapper never passes one). The mapper must catch the
        # IntegrityError internally and count the row as skipped.
        second = await map_and_persist(db_session, [_make_extraction_input(value="2800")])
        assert first.created_measurements == 1
        assert second.created_measurements == 0
        assert second.skipped_duplicate_measurements == 1

        rows = (await db_session.execute(select(PropertyMeasurement))).scalars().all()
        assert len(rows) == 1, (
            f"AC-9 regression: identical mapper calls landed {len(rows)} rows; expected 1."
        )


class TestMapperStampsLiteratureIdentity:
    """New Datasets get ``literature_doi`` + ``literature_content_hash``."""

    async def test_mapper_stamps_literature_doi_and_hash(self, db_session: AsyncSession) -> None:
        cat = MaterialCategory(name="Fuel", slug="fuel")
        db_session.add(cat)
        await db_session.flush()
        mat = Material(name="UO2", formula="UO2", category_id=cat.id)
        db_session.add(mat)
        await db_session.flush()

        pcat = PropertyCategory(name="thermal", slug="thermal")
        db_session.add(pcat)
        await db_session.flush()
        pt = PropertyType(
            name="melting_point",
            slug="melting_point",
            category_id=pcat.id,
            value_type="scalar",
        )
        db_session.add(pt)
        await db_session.flush()

        # Pre-create the DataSource with a known DOI + file_hash (the
        # extraction_doi is the URL-prefixed form; the mapper must
        # normalize via normalize_doi before stamping).
        ds = DataSource(
            doi="HTTPS://DOI.ORG/10.1234/SAMPLE",
            title="Smith et al., J. Nucl. Mater.",
            source_type="journal_article",
            file_hash="sha256:file-content-hash",
        )
        db_session.add(ds)
        await db_session.flush()

        result = await map_and_persist(
            db_session,
            [_make_extraction_input(source_doi=ds.doi)],
        )
        assert result.created_datasets == 1

        datasets = (await db_session.execute(select(Dataset))).scalars().all()
        assert len(datasets) == 1
        # The mapper normalizes via normalize_doi (URL prefix stripped,
        # lowercased). E2E QA's probe found literature_doi stayed NULL —
        # after the wire-up it MUST match the normalized form.
        assert datasets[0].literature_doi == normalize_doi(ds.doi)
        # And the file_hash is propagated verbatim for the content_hash
        # fallback ladder.
        assert datasets[0].literature_content_hash == "sha256:file-content-hash"

    async def test_resolve_literature_dataset_finds_newly_stamped_dataset(
        self, db_session: AsyncSession
    ) -> None:
        """Round-trip: after the mapper stamps a Dataset, the resolver hits."""
        cat = MaterialCategory(name="Fuel", slug="fuel")
        db_session.add(cat)
        await db_session.flush()
        mat = Material(name="UO2", formula="UO2", category_id=cat.id)
        db_session.add(mat)
        await db_session.flush()

        pcat = PropertyCategory(name="thermal", slug="thermal")
        db_session.add(pcat)
        await db_session.flush()
        pt = PropertyType(
            name="melting_point",
            slug="melting_point",
            category_id=pcat.id,
            value_type="scalar",
        )
        db_session.add(pt)
        await db_session.flush()

        ds = DataSource(
            doi="10.1234/round-trip",
            title="Round trip",
            source_type="journal_article",
            file_hash="sha256:rt",
        )
        db_session.add(ds)
        await db_session.flush()

        await map_and_persist(db_session, [_make_extraction_input(source_doi=ds.doi)])

        # Re-resolve using a different shape of the DOI — the resolver
        # must hit the freshly stamped row.
        hit = await resolve_literature_dataset(
            db_session,
            doi="https://doi.org/10.1234/round-trip",
            content_hash=None,
        )
        assert hit is not None
        assert hit.literature_doi == "10.1234/round-trip"


def _expected_value_hash(value_kwargs: dict[str, Any]) -> str:
    """Mirror :func:`extraction_to_db_mapper._value_hash` for test assertions.

    The mapper's own helper uses SHA-1 over a sorted-JSON canonicalization
    of the value kwargs. We recompute the same form so the test fails if
    either side changes shape (drift detector).
    """
    import hashlib
    import json as _json

    serialised = _json.dumps(
        {k: v for k, v in value_kwargs.items() if v is not None},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha1(serialised.encode("utf-8")).hexdigest()
