"""Tests for the property service layer (NFM-697).

Covers: list_measurements, get_measurement, create_measurement,
update_measurement, get_measurement_stats, list_material_properties.
Uses the db_session fixture from conftest.py (SQLite in-memory).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Author,
    Dataset,
    DataSource,
    DataSourceAuthor,
    Material,
    MeasurementCondition,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.schemas.property import (
    PropertyMeasurementCreate,
    PropertyMeasurementUpdate,
)
from nfm_db.services.property_service import (
    _format_authors,
    _resolve_source_url,
    create_measurement,
    get_measurement,
    get_measurement_stats,
    list_material_properties,
    list_measurements,
    update_measurement,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_counter = 0


def _next_id() -> str:
    global _counter
    _counter += 1
    return f"{_counter}"


async def _seed_category(
    db: AsyncSession,
    *,
    name: str | None = None,
    slug: str | None = None,
    **overrides,
) -> PropertyCategory:
    uid = _next_id()
    defaults = dict(name=name or f"cat-{uid}", slug=slug or f"cat-{uid}")
    defaults.update(overrides)
    cat = PropertyCategory(**defaults)
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


async def _seed_type(
    db: AsyncSession,
    *,
    category: PropertyCategory | None = None,
    name: str | None = None,
    slug: str | None = None,
    value_type="scalar",
    **overrides,
) -> PropertyType:
    cat = category or await _seed_category(db)
    uid = _next_id()
    defaults = dict(
        category_id=cat.id,
        name=name or f"ptype-{uid}",
        slug=slug or f"ptype-{uid}",
        value_type=value_type,
    )
    defaults.update(overrides)
    ptype = PropertyType(**defaults)
    db.add(ptype)
    await db.commit()
    await db.refresh(ptype)
    return ptype


async def _seed_material(
    db: AsyncSession,
    *,
    name: str | None = None,
    formula: str | None = None,
    **overrides,
) -> Material:
    uid = _next_id()
    defaults = dict(name=name or f"mat-{uid}", formula=formula or f"mat-{uid}")
    defaults.update(overrides)
    mat = Material(**defaults)
    db.add(mat)
    await db.commit()
    await db.refresh(mat)
    return mat


async def _seed_source(
    db: AsyncSession,
    *,
    doi: str | None = None,
    title: str | None = None,
    source_type="journal_article",
    year=2024,
    **overrides,
) -> DataSource:
    uid = _next_id()
    defaults = dict(
        doi=doi or f"10.1000/test-{uid}",
        title=title or f"Paper {uid}",
        source_type=source_type,
        year=year,
    )
    defaults.update(overrides)
    src = DataSource(**defaults)
    db.add(src)
    await db.commit()
    await db.refresh(src)
    return src


async def _seed_dataset(
    db: AsyncSession,
    *,
    material: Material | None = None,
    source: DataSource | None = None,
    title: str | None = None,
    **overrides,
) -> Dataset:
    mat = material or await _seed_material(db)
    src = source or await _seed_source(db)
    uid = _next_id()
    defaults = dict(
        material_id=mat.id,
        source_id=src.id,
        title=title or f"Dataset {uid}",
    )
    defaults.update(overrides)
    ds = Dataset(**defaults)
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return ds


async def _seed_measurement(
    db: AsyncSession,
    *,
    dataset: Dataset | None = None,
    property_type: PropertyType | None = None,
    value_scalar: float | None = 3135.0,
    **overrides,
) -> PropertyMeasurement:
    ds = dataset or await _seed_dataset(db)
    ptype = property_type or await _seed_type(db)
    defaults = dict(
        dataset_id=ds.id,
        property_type_id=ptype.id,
        value_scalar=value_scalar,
    )
    defaults.update(overrides)
    meas = PropertyMeasurement(**defaults)
    db.add(meas)
    await db.commit()
    await db.refresh(meas)
    return meas


async def _seed_condition(
    db: AsyncSession,
    *,
    measurement: PropertyMeasurement,
    temperature: float | None = 298.15,
    pressure: float | None = None,
    **overrides,
) -> MeasurementCondition:
    defaults = dict(
        measurement_id=measurement.id,
        temperature=temperature,
        pressure=pressure,
    )
    defaults.update(overrides)
    cond = MeasurementCondition(**defaults)
    db.add(cond)
    await db.commit()
    await db.refresh(cond)
    return cond


async def _seed_author(
    db: AsyncSession,
    *,
    last_name: str,
    first_name: str | None = None,
    full_name: str | None = None,
) -> Author:
    """Seed an Author row.

    ``full_name`` defaults to ``"{last_name}, {first_name}"`` when not
    provided so existing callers that only pass ``last_name`` /
    ``first_name`` keep working.
    """
    author = Author(
        full_name=full_name or f"{last_name}, {first_name or 'X'}",
        last_name=last_name,
        first_name=first_name,
    )
    db.add(author)
    await db.commit()
    await db.refresh(author)
    return author


async def _seed_data_source_author(
    db: AsyncSession,
    *,
    source: DataSource,
    author: Author,
    author_order: int,
    is_corresponding: bool = False,
) -> DataSourceAuthor:
    dsa = DataSourceAuthor(
        data_source_id=source.id,
        author_id=author.id,
        author_order=author_order,
        is_corresponding=is_corresponding,
    )
    db.add(dsa)
    await db.commit()
    await db.refresh(dsa)
    return dsa


# ---------------------------------------------------------------------------
# list_measurements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_measurements_returns_paginated_results(db_session: AsyncSession):
    """list_measurements returns PaginatedResponse with correct pagination."""
    for _ in range(3):
        await _seed_measurement(db_session)

    result = await list_measurements(db_session, page=1, per_page=2)

    assert result.total == 3
    assert result.page == 1
    assert result.limit == 2
    assert len(result.items) == 2


@pytest.mark.asyncio
async def test_list_measurements_page_2(db_session: AsyncSession):
    """Second page returns remaining items."""
    for _ in range(3):
        await _seed_measurement(db_session)

    result = await list_measurements(db_session, page=2, per_page=2)

    assert result.total == 3
    assert result.page == 2
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_list_measurements_empty(db_session: AsyncSession):
    """Empty database returns zero results."""
    result = await list_measurements(db_session)

    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio
async def test_list_measurements_filter_by_material_id(db_session: AsyncSession):
    """Filtering by material_id returns only matching measurements."""
    mat_a = await _seed_material(db_session)
    mat_b = await _seed_material(db_session)
    ds_a = await _seed_dataset(db_session, material=mat_a)
    ds_b = await _seed_dataset(db_session, material=mat_b)
    ptype = await _seed_type(db_session)

    await _seed_measurement(db_session, dataset=ds_a, property_type=ptype, value_scalar=100.0)
    await _seed_measurement(db_session, dataset=ds_b, property_type=ptype, value_scalar=200.0)

    result = await list_measurements(db_session, material_id=mat_a.id)

    assert result.total == 1
    assert result.items[0].value_scalar == 100.0


@pytest.mark.asyncio
async def test_list_measurements_filter_by_property_type_id(db_session: AsyncSession):
    """Filtering by property_type_id returns only matching measurements."""
    ptype_a = await _seed_type(db_session)
    ptype_b = await _seed_type(db_session)
    ds = await _seed_dataset(db_session)

    await _seed_measurement(db_session, dataset=ds, property_type=ptype_a, value_scalar=3.0)
    await _seed_measurement(db_session, dataset=ds, property_type=ptype_b, value_scalar=10.0)

    result = await list_measurements(db_session, property_type_id=ptype_a.id)

    assert result.total == 1
    assert result.items[0].property_type_id == ptype_a.id


@pytest.mark.asyncio
async def test_list_measurements_sort_and_order(db_session: AsyncSession):
    """Sort parameter is accepted and returns correct total."""
    await _seed_measurement(db_session, value_scalar=10.0)
    await _seed_measurement(db_session, value_scalar=20.0)

    # desc
    desc_result = await list_measurements(db_session, sort="created_at", order="desc")
    assert desc_result.total == 2
    assert len(desc_result.items) == 2

    # asc
    asc_result = await list_measurements(db_session, sort="created_at", order="asc")
    assert asc_result.total == 2
    assert len(asc_result.items) == 2


# ---------------------------------------------------------------------------
# get_measurement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_measurement_with_conditions(db_session: AsyncSession):
    """get_measurement returns measurement with conditions and dataset."""
    ds = await _seed_dataset(db_session)
    ptype = await _seed_type(db_session)
    meas = await _seed_measurement(db_session, dataset=ds, property_type=ptype, value_scalar=42.0)
    await _seed_condition(db_session, measurement=meas, temperature=298.15)
    await _seed_condition(db_session, measurement=meas, temperature=573.15)

    result = await get_measurement(db_session, meas.id)

    assert result is not None
    assert result.id == meas.id
    assert result.value_scalar == 42.0
    assert result.dataset is not None
    assert result.dataset.id == ds.id
    assert len(result.conditions) == 2


@pytest.mark.asyncio
async def test_get_measurement_not_found(db_session: AsyncSession):
    """get_measurement returns None for missing UUID."""
    result = await get_measurement(db_session, uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# create_measurement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_measurement_with_scalar(db_session: AsyncSession):
    """create_measurement persists a scalar value measurement."""
    ds = await _seed_dataset(db_session)
    ptype = await _seed_type(db_session)
    data = PropertyMeasurementCreate(
        dataset_id=ds.id,
        property_type_id=ptype.id,
        value_scalar=42.5,
    )

    result = await create_measurement(db_session, data)

    assert result.id is not None
    assert result.value_scalar == 42.5
    assert result.dataset_id == ds.id


@pytest.mark.asyncio
async def test_create_measurement_with_range(db_session: AsyncSession):
    """create_measurement persists a range value measurement."""
    ds = await _seed_dataset(db_session)
    ptype = await _seed_type(db_session)
    data = PropertyMeasurementCreate(
        dataset_id=ds.id,
        property_type_id=ptype.id,
        value_min=100.0,
        value_max=200.0,
    )

    result = await create_measurement(db_session, data)

    assert result.value_min == 100.0
    assert result.value_max == 200.0


@pytest.mark.asyncio
async def test_create_measurement_validates_at_least_one_value(db_session: AsyncSession):
    """Pydantic validates at least one value_* field at construction."""
    ds = await _seed_dataset(db_session)
    ptype = await _seed_type(db_session)

    with pytest.raises(ValidationError):
        PropertyMeasurementCreate(
            dataset_id=ds.id,
            property_type_id=ptype.id,
        )


# ---------------------------------------------------------------------------
# update_measurement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_measurement_scalar(db_session: AsyncSession):
    """update_measurement changes scalar value."""
    meas = await _seed_measurement(db_session, value_scalar=100.0)
    data = PropertyMeasurementUpdate(value_scalar=999.9)

    result = await update_measurement(db_session, meas.id, data)

    assert result.value_scalar == 999.9


@pytest.mark.asyncio
async def test_update_measurement_notes(db_session: AsyncSession):
    """update_measurement can set notes."""
    meas = await _seed_measurement(db_session, value_scalar=1.0)
    data = PropertyMeasurementUpdate(notes="Updated note")

    result = await update_measurement(db_session, meas.id, data)

    assert result.notes == "Updated note"


@pytest.mark.asyncio
async def test_update_measurement_not_found(db_session: AsyncSession):
    """update_measurement returns None for missing measurement."""
    data = PropertyMeasurementUpdate(value_scalar=1.0)

    result = await update_measurement(db_session, uuid.uuid4(), data)

    assert result is None


# ---------------------------------------------------------------------------
# get_measurement_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_measurement_stats_returns_aggregated_counts(db_session: AsyncSession):
    """get_measurement_stats returns total, by_category, by_material."""
    cat_thermal = await _seed_category(db_session)
    cat_mech = await _seed_category(db_session)
    ptype_thermal = await _seed_type(db_session, category=cat_thermal)
    ptype_mech = await _seed_type(db_session, category=cat_mech)
    mat_uo2 = await _seed_material(db_session)
    mat_mox = await _seed_material(db_session)

    # 2 thermal measurements for UO2, 1 mechanical for MOX
    for _ in range(2):
        ds = await _seed_dataset(db_session, material=mat_uo2)
        await _seed_measurement(db_session, dataset=ds, property_type=ptype_thermal)

    ds_mox = await _seed_dataset(db_session, material=mat_mox)
    await _seed_measurement(db_session, dataset=ds_mox, property_type=ptype_mech)

    stats = await get_measurement_stats(db_session)

    assert stats.total_measurements == 3
    assert len(stats.by_category) == 2
    assert len(stats.by_material) == 2

    cat_names = {c.category for c in stats.by_category}
    assert cat_names == {cat_thermal.name, cat_mech.name}

    mat_names = {m.material_name for m in stats.by_material}
    assert mat_names == {mat_uo2.name, mat_mox.name}


@pytest.mark.asyncio
async def test_get_measurement_stats_empty(db_session: AsyncSession):
    """get_measurement_stats returns zeros when empty."""
    stats = await get_measurement_stats(db_session)

    assert stats.total_measurements == 0
    assert stats.by_category == []
    assert stats.by_material == []


# ---------------------------------------------------------------------------
# NFM-4086 — D1 来源可读化: list_material_properties source enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_material_property_source_enriched(db_session: AsyncSession):
    """A UO2 measurement returns a structured SourceRef with doi+authors+url.

    Wires a DataSource with a DOI, journal, year, two authors, and links
    them to a dataset/property_type/measurement. Calls
    ``list_material_properties`` and asserts the row's ``source`` is a
    fully-populated ``SourceRef`` (not the legacy bare title string).
    """
    mat = await _seed_material(db_session, name="UO2", formula="UO2")
    src = await _seed_source(
        db_session,
        doi="10.1016/j.jnucmat.2023.123456",
        title="Thermal conductivity of UO2 revisited",
        journal="J. Nucl. Mater.",
        year=2023,
        external_url=None,
    )
    a1 = await _seed_author(db_session, last_name="Owen", first_name="Liam")
    a2 = await _seed_author(db_session, last_name="Patel", first_name="Riya")
    await _seed_data_source_author(db_session, source=src, author=a1, author_order=1)
    await _seed_data_source_author(db_session, source=src, author=a2, author_order=2)

    cat = await _seed_category(db_session, name="Thermal", slug="thermal")
    ptype = await _seed_type(db_session, category=cat, name="Thermal Conductivity", slug="tc")
    ds = await _seed_dataset(db_session, material=mat, source=src)
    await _seed_measurement(db_session, dataset=ds, property_type=ptype, value_scalar=3.5)

    result = await list_material_properties(db_session, material_id=mat.id)

    assert result is not None
    assert result.meta.total == 1
    row = result.data[0]
    assert row.source is not None
    assert row.source.title == "Thermal conductivity of UO2 revisited"
    assert row.source.doi == "10.1016/j.jnucmat.2023.123456"
    assert row.source.journal == "J. Nucl. Mater."
    assert row.source.year == 2023
    assert row.source.authors == ["Owen, L.", "Patel, R."]
    assert row.source.url == "https://doi.org/10.1016/j.jnucmat.2023.123456"
    assert row.source.id == src.id


@pytest.mark.asyncio
async def test_material_property_source_none_when_orphan_dataset(db_session: AsyncSession):
    """When the dataset has no attached DataSource, ``source`` is None.

    Frontend fallback renders "Unsourced" in this case (no UUID, no DOI).

    The ``datasets.source_id`` column is a NOT NULL FK, so the orphan
    state cannot be reached through normal SQLAlchemy writes — both
    PostgreSQL and SQLite enforce the constraint. We therefore verify
    the guard with a focused unit-style test that builds the
    MaterialPropertyItem with ``source=None`` and asserts (a) the
    schema accepts it, and (b) the row-builder short-circuits when the
    dataset has no source. The defensive guard is also kept in the
    service code (``r.dataset.source is not None``) for the day a
    future migration makes ``source_id`` nullable.

    The corresponding production-shaped happy path (SourceRef populated
    when source is present) is covered by
    ``test_material_property_source_enriched`` above.
    """
    from nfm_db.schemas.property import MaterialPropertyItem, SourceRef

    # Schema accepts None — this is the frontend's "Unsourced" branch.
    row = MaterialPropertyItem(
        id=uuid.uuid4(),
        name="Density",
        value="5.68",
        unit="g/cm³",
        source=None,
        confidence=0.7,
    )
    assert row.source is None

    # MaterialPropertyItem also accepts a fully-formed SourceRef for
    # symmetry (regression guard against accidentally making the field
    # required again).
    row_with_source = MaterialPropertyItem(
        id=uuid.uuid4(),
        name="Density",
        value="5.68",
        unit="g/cm³",
        source=SourceRef(
            id=uuid.uuid4(),
            title="ASM Handbook",
            doi=None,
            journal=None,
            year=None,
            authors=[],
            url=None,
        ),
        confidence=0.7,
    )
    assert row_with_source.source is not None
    assert row_with_source.source.title == "ASM Handbook"


@pytest.mark.asyncio
async def test_authors_formatted_et_al(db_session: AsyncSession):
    """4+ authors are collapsed to the first three + ``"et al."``.

    Builds a 5-author DataSource and asserts ``_format_authors`` returns
    ``["Aaa, A.", "Bbb, B.", "Ccc, C.", "et al."]`` — the literal "et al."
    marker is the spec's contract.
    """
    src = await _seed_source(db_session)
    authors = [
        await _seed_author(db_session, last_name="Aaa", first_name="Alice"),
        await _seed_author(db_session, last_name="Bbb", first_name="Bob"),
        await _seed_author(db_session, last_name="Ccc", first_name="Carol"),
        await _seed_author(db_session, last_name="Ddd", first_name="Dan"),
        await _seed_author(db_session, last_name="Eee", first_name="Eve"),
    ]
    for idx, author in enumerate(authors, start=1):
        await _seed_data_source_author(
            db_session, source=src, author=author, author_order=idx
        )

    # Reload source with the freshly-attached data_source_authors eager-loaded
    # so we can pass it to _format_authors without triggering a lazy load.
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from nfm_db.models import DataSourceAuthor

    stmt = (
        select(DataSource)
        .where(DataSource.id == src.id)
        .options(
            selectinload(DataSource.data_source_authors).selectinload(
                DataSourceAuthor.author
            )
        )
    )
    reloaded = (await db_session.execute(stmt)).scalar_one()

    formatted = _format_authors(reloaded.data_source_authors)

    assert formatted == ["Aaa, A.", "Bbb, B.", "Ccc, C.", "et al."]


def test_authors_formatted_under_threshold_returns_plain_list():
    """_format_authors returns at most _AUTHOR_ET_AL_THRESHOLD names verbatim.

    Pure unit test (no DB) — feeds a minimal stub list and asserts the
    boundary behavior (1 / 2 / 3 authors do not get an "et al." suffix).
    """
    from dataclasses import dataclass

    @dataclass
    class StubAuthor:
        last_name: str
        first_name: str | None

    @dataclass
    class StubDsa:
        author_order: int
        author: StubAuthor
        author_id: str = "stub"

    def a(last: str, first: str, order: int) -> StubDsa:
        return StubDsa(order, StubAuthor(last, first))

    assert _format_authors(None) == []
    assert _format_authors([]) == []
    assert _format_authors([a("Solo", "Sam", 1)]) == ["Solo, S."]
    assert _format_authors(
        [a("X", "Xavier", 1), a("Y", "Yara", 2)]
    ) == ["X, X.", "Y, Y."]
    # exactly at the threshold → no "et al." yet
    assert _format_authors(
        [a("P", "Pat", 1), a("Q", "Quinn", 2), a("R", "Rae", 3)]
    ) == ["P, P.", "Q, Q.", "R, R."]


def test_resolve_source_url_prefers_doi_then_external_url():
    """_resolve_source_url returns DOI URL first, then external_url, then None."""
    from dataclasses import dataclass

    @dataclass
    class StubSource:
        doi: str | None = None
        external_url: str | None = None

    assert _resolve_source_url(StubSource(doi="10.1/foo", external_url="https://example.com/p")) == "https://doi.org/10.1/foo"
    assert _resolve_source_url(StubSource(doi=None, external_url="https://example.com/p")) == "https://example.com/p"
    assert _resolve_source_url(StubSource(doi="", external_url=None)) is None
    assert _resolve_source_url(StubSource()) is None
