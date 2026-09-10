"""Tests for the literature dedup layer (NFM-4549, G1-C).

Covers:
- ``normalize_doi`` — DOI prefix / whitespace / case folding
- ``resolve_literature_dataset`` — DOI hit, content_hash fallback, miss path
- ``compute_dedupe_key`` — deterministic over (dataset, property, source, value_hash)
- The DB-level UNIQUE on ``property_measurements.dedupe_key`` rejects a
  second row with the same key (AC-9 — Owen 92-row case study).

Pattern follows ``test_dedup_service.py``: hermetic SQLite + aiosqlite.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import JSON, event
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
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
from nfm_db.services.literature_dedup import (
    compute_dedupe_key,
    normalize_doi,
    resolve_literature_dataset,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _replace_jsonb(metadata) -> None:
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = JSON()


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
        assert (
            normalize_doi("https://doi.org/10.1234/foo")
            == "10.1234/foo"
        )

    def test_strips_url_prefix_http(self) -> None:
        assert (
            normalize_doi("http://dx.doi.org/10.1234/foo")
            == "10.1234/foo"
        )

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

        hit = await resolve_literature_dataset(
            db_session, doi="10.1234/BEELER", content_hash=None
        )
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

        hit = await resolve_literature_dataset(
            db_session, doi=None, content_hash="sha256:abc"
        )
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

    async def test_miss_returns_none(
        self, db_session: AsyncSession, material: Material
    ) -> None:
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

        hit = await resolve_literature_dataset(
            db_session, doi="   ", content_hash="sha256:zzz"
        )
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
        assert compute_dedupe_key(d, p, s1, "v") != compute_dedupe_key(
            d, p, s2, "v"
        )

    def test_changes_with_dataset(self) -> None:
        d1, d2 = uuid.uuid4(), uuid.uuid4()
        p = uuid.uuid4()
        s = uuid.uuid4()
        assert compute_dedupe_key(d1, p, s, "v") != compute_dedupe_key(
            d2, p, s, "v"
        )

    def test_changes_with_property_type(self) -> None:
        d = uuid.uuid4()
        p1, p2 = uuid.uuid4(), uuid.uuid4()
        s = uuid.uuid4()
        assert compute_dedupe_key(d, p1, s, "v") != compute_dedupe_key(
            d, p2, s, "v"
        )


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
                    dedupe_key=compute_dedupe_key(
                        ds.id, property_type.id, data_source.id, vh
                    ),
                    value_scalar=float(vh),
                    review_status="pending",
                )
            )
        await db_session.flush()  # no IntegrityError
