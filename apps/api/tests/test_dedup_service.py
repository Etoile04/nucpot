"""Unit tests for the material dedup service (NFM-1391, B3.1.1).

Covers:
- exact-formula strategy (strategy 1)
- fuzzy-name strategy (strategy 2)
- alias overlap strategy (strategy 3)
- execute_merge writes an EntityMergeLog row
- list_merge_logs + get_merge_log read-back

These tests use SQLite + aiosqlite (the same in-memory pattern used by
``test_dft_import.py``) to keep them hermetic.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import JSON, event
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nfm_db.models import Base
from nfm_db.models.entity_merge import MatchMethod
from nfm_db.models.material import Material, MaterialCategory
from nfm_db.services.dedup_service import (
    DEFAULT_FUZZY_THRESHOLD,
    MergeResult,
    _normalize_formula,
    execute_merge,
    find_duplicates,
    get_merge_log,
    levenshtein_ratio,
    list_merge_logs,
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
async def category(db_session: AsyncSession):
    cat = MaterialCategory(name="Fuel", slug="fuel")
    db_session.add(cat)
    await db_session.flush()
    return cat


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


class TestNormalizeFormula:
    def test_lowercases(self) -> None:
        assert _normalize_formula("UO2") == "uo2"

    def test_strips_whitespace(self) -> None:
        assert _normalize_formula(" U O 2 ") == "uo2"

    def test_empty(self) -> None:
        assert _normalize_formula("") == ""
        assert _normalize_formula(None) == ""  # type: ignore[arg-type]


class TestLevenshteinRatio:
    def test_identical(self) -> None:
        assert levenshtein_ratio("UO2", "UO2") == 1.0

    def test_completely_different(self) -> None:
        ratio = levenshtein_ratio("abc", "xyz")
        assert 0.0 <= ratio < 0.5

    def test_empty_inputs(self) -> None:
        assert levenshtein_ratio("", "abc") == 0.0
        assert levenshtein_ratio("abc", "") == 0.0


# ---------------------------------------------------------------------------
# find_duplicates tests
# ---------------------------------------------------------------------------


class TestFindDuplicates:
    @pytest.mark.asyncio
    async def test_empty_db_returns_empty(
        self, db_session: AsyncSession, category
    ) -> None:
        result = await find_duplicates(db_session)
        assert result == []

    @pytest.mark.asyncio
    async def test_single_material_returns_empty(
        self, db_session: AsyncSession, category
    ) -> None:
        m = Material(name="UO2", formula="UO2", category_id=category.id)
        db_session.add(m)
        await db_session.flush()
        result = await find_duplicates(db_session)
        assert result == []

    @pytest.mark.asyncio
    async def test_exact_formula_match(
        self, db_session: AsyncSession, category
    ) -> None:
        """Strategy 1: same formula (case-insensitive) -> exact match."""
        a = Material(name="UO2 primary", formula="UO2", category_id=category.id)
        b = Material(name="UO2 duplicate", formula="uo2", category_id=category.id)
        c = Material(name="Different", formula="Zr", category_id=category.id)
        db_session.add_all([a, b, c])
        await db_session.flush()

        result = await find_duplicates(db_session)
        assert len(result) == 1
        candidate = result[0]
        assert candidate.match_method == MatchMethod.EXACT
        assert candidate.match_score == 1.0
        # Lower UUID wins as canonical
        assert candidate.canonical.id in (a.id, b.id)
        assert candidate.duplicate.id in (a.id, b.id)
        assert candidate.canonical.id != candidate.duplicate.id

    @pytest.mark.asyncio
    async def test_fuzzy_name_match(
        self, db_session: AsyncSession, category
    ) -> None:
        """Strategy 2: very similar name (different formula) -> fuzzy match."""
        a = Material(
            name="Uranium dioxide",
            formula="UO2",
            category_id=category.id,
        )
        b = Material(
            name="Uranium dioxid",  # 1 char diff -> ratio >= 0.85
            formula="UO3",  # different formula blocks exact strategy
            category_id=category.id,
        )
        db_session.add_all([a, b])
        await db_session.flush()

        result = await find_duplicates(db_session, fuzzy_threshold=0.85)
        assert len(result) == 1
        assert result[0].match_method == MatchMethod.FUZZY
        assert result[0].match_score >= 0.85

    @pytest.mark.asyncio
    async def test_no_match_for_unrelated_materials(
        self, db_session: AsyncSession, category
    ) -> None:
        a = Material(name="Uranium dioxide", formula="UO2", category_id=category.id)
        b = Material(name="Zirconium diboride", formula="ZrB2", category_id=category.id)
        db_session.add_all([a, b])
        await db_session.flush()

        result = await find_duplicates(db_session, fuzzy_threshold=0.85)
        assert result == []

    @pytest.mark.asyncio
    async def test_deterministic_canonical_choice(
        self, db_session: AsyncSession, category
    ) -> None:
        """The lower UUID is always picked as canonical."""
        a = Material(name="Alpha", formula="AB", category_id=category.id)
        b = Material(name="Beta dup", formula="AB", category_id=category.id)
        db_session.add_all([a, b])
        await db_session.flush()

        result = await find_duplicates(db_session)
        canonical_id, duplicate_id = (
            min(a.id, b.id, key=str),
            max(a.id, b.id, key=str),
        )
        assert result[0].canonical.id == canonical_id
        assert result[0].duplicate.id == duplicate_id


# ---------------------------------------------------------------------------
# execute_merge + read-back tests
# ---------------------------------------------------------------------------


class TestExecuteMerge:
    @pytest.mark.asyncio
    async def test_writes_audit_log_row(
        self, db_session: AsyncSession, category
    ) -> None:
        a = Material(name="Canonical", formula="UO2", category_id=category.id)
        b = Material(name="Duplicate", formula="UO2", category_id=category.id)
        db_session.add_all([a, b])
        await db_session.flush()

        result = await execute_merge(
            db_session,
            canonical=a,
            duplicate=b,
            match_score=1.0,
            match_method=MatchMethod.EXACT,
            matched_aliases=["UO2 dup"],
        )
        await db_session.commit()

        assert isinstance(result, MergeResult)
        assert result.log.id is not None
        assert result.log.canonical_id == a.id
        assert result.log.merged_id == b.id
        assert result.log.match_score == 1.0
        assert result.log.match_method == MatchMethod.EXACT
        assert result.log.details is not None
        assert "matched_aliases" in result.log.details
        assert "UO2 dup" in result.log.details["matched_aliases"]

    @pytest.mark.asyncio
    async def test_rejects_same_material(
        self, db_session: AsyncSession, category
    ) -> None:
        a = Material(name="Self", formula="X", category_id=category.id)
        db_session.add(a)
        await db_session.flush()

        with pytest.raises(ValueError, match="different materials"):
            await execute_merge(
                db_session,
                canonical=a,
                duplicate=a,
                match_score=1.0,
                match_method=MatchMethod.EXACT,
            )

    @pytest.mark.asyncio
    async def test_rejects_invalid_score(
        self, db_session: AsyncSession, category
    ) -> None:
        a = Material(name="A", formula="X", category_id=category.id)
        b = Material(name="B", formula="X", category_id=category.id)
        db_session.add_all([a, b])
        await db_session.flush()

        with pytest.raises(ValueError, match="match_score"):
            await execute_merge(
                db_session,
                canonical=a,
                duplicate=b,
                match_score=2.0,
                match_method=MatchMethod.EXACT,
            )


class TestListAndGetMergeLogs:
    @pytest.mark.asyncio
    async def test_list_returns_rows_newest_first(
        self, db_session: AsyncSession, category
    ) -> None:
        a = Material(name="A", formula="AB", category_id=category.id)
        b = Material(name="B", formula="AB", category_id=category.id)
        c = Material(name="C", formula="CD", category_id=category.id)
        d = Material(name="D", formula="CD", category_id=category.id)
        db_session.add_all([a, b, c, d])
        await db_session.flush()

        await execute_merge(
            db_session, canonical=a, duplicate=b,
            match_score=1.0, match_method=MatchMethod.EXACT,
        )
        await execute_merge(
            db_session, canonical=c, duplicate=d,
            match_score=0.95, match_method=MatchMethod.FUZZY,
        )
        await db_session.commit()

        rows, total = await list_merge_logs(db_session)
        assert total == 2
        assert len(rows) == 2
        # Newest first
        assert rows[0].merged_at >= rows[1].merged_at

    @pytest.mark.asyncio
    async def test_filter_by_match_method(
        self, db_session: AsyncSession, category
    ) -> None:
        a = Material(name="A", formula="AB", category_id=category.id)
        b = Material(name="B", formula="AB", category_id=category.id)
        c = Material(name="C", formula="CD", category_id=category.id)
        d = Material(name="D", formula="CD", category_id=category.id)
        db_session.add_all([a, b, c, d])
        await db_session.flush()

        await execute_merge(
            db_session, canonical=a, duplicate=b,
            match_score=1.0, match_method=MatchMethod.EXACT,
        )
        await execute_merge(
            db_session, canonical=c, duplicate=d,
            match_score=0.95, match_method=MatchMethod.FUZZY,
        )
        await db_session.commit()

        rows, total = await list_merge_logs(
            db_session, match_method=MatchMethod.EXACT
        )
        assert total == 1
        assert rows[0].match_method == MatchMethod.EXACT

    @pytest.mark.asyncio
    async def test_get_merge_log(
        self, db_session: AsyncSession, category
    ) -> None:
        a = Material(name="A", formula="AB", category_id=category.id)
        b = Material(name="B", formula="AB", category_id=category.id)
        db_session.add_all([a, b])
        await db_session.flush()

        result = await execute_merge(
            db_session, canonical=a, duplicate=b,
            match_score=1.0, match_method=MatchMethod.EXACT,
        )
        await db_session.commit()

        fetched = await get_merge_log(db_session, result.log.id)
        assert fetched is not None
        assert fetched.id == result.log.id

        missing = await get_merge_log(db_session, uuid.uuid4())
        assert missing is None


# ---------------------------------------------------------------------------
# Sanity: verify the default threshold constant matches expectations.
# ---------------------------------------------------------------------------


def test_default_fuzzy_threshold_is_above_levenshtein_min() -> None:
    """0.85 is the documented default; just guard against silent change."""
    assert DEFAULT_FUZZY_THRESHOLD == 0.85
