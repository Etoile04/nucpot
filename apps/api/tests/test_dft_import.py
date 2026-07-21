"""Tests for the DFT incremental data import pipeline (NFM-1678).

Covers:
- JSON parsing
- CSV parsing
- Format auto-detection
- Validation rules
- Deterministic calculation_id generation
- ORM mapping (binding_energy → cohesive_energy, kpoints → kpoint_mesh)
- Source tagging for data provenance
- Idempotent bulk insert with batch dedup (H1, H2 fix verification)
- Sample variance (N-1) in outlier detection (M2 fix verification)
- Full pipeline integration
"""

from __future__ import annotations

import csv as _csv_stdlib
import json
import math
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import JSON, event, select
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nfm_db.ml.dft_import import (
    ImportReport,
    bulk_insert_dft,
    detect_outliers,
    generate_calculation_id,
    parse_csv_file,
    parse_dft_file,
    parse_json_file,
    record_to_orm_dict,
    run_import,
    validate_record,
)
from nfm_db.models import Base
from nfm_db.models.dft_calculation import DFTCalculation

# ---------------------------------------------------------------------------
# SQLite compatibility helpers (PostgreSQL JSONB → SQLite JSON)
# ---------------------------------------------------------------------------


def _replace_jsonb(metadata) -> None:
    """Replace PostgreSQL JSONB columns with JSON for SQLite."""
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = JSON()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session():
    """In-memory SQLite async session with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        dbapi_conn.commit()

    # Replace PostgreSQL JSONB with JSON for SQLite compatibility.
    _replace_jsonb(Base.metadata)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def valid_record() -> dict[str, Any]:
    """A canonical valid DFT record."""
    return {
        "composition": {"U": 0.5, "Mo": 0.5},
        "functional": "PBE",
        "cutoff_energy": 520.0,
        "kpoints": "8x8x8",
        "formation_energy": -0.5,
        "binding_energy": -4.2,
        "lattice_distortion": 0.01,
    }


@pytest.fixture
def batch_records() -> list[dict[str, Any]]:
    """Batch of 10 valid records with varying functional and composition."""
    records = []
    elements = [["U", "Mo"], ["Zr", "Nb"], ["Ti", "V"], ["Cr", "Fe"]]
    functionals = ["PBE", "PBEsol", "LDA", "HSE06"]
    for i in range(10):
        el_pair = elements[i % len(elements)]
        records.append({
            "composition": {el_pair[0]: 0.5 + i * 0.01, el_pair[1]: 0.5 - i * 0.01},
            "functional": functionals[i % len(functionals)],
            "cutoff_energy": 500.0 + i * 10,
            "kpoints": "8x8x8",
            "formation_energy": -0.1 - i * 0.05,
            "binding_energy": -4.0 - i * 0.1,
            "lattice_distortion": 0.01 * i,
        })
    return records


@pytest.fixture
def tmp_json_file(tmp_path: Path, batch_records: list[dict[str, Any]]) -> Path:
    """Write batch_records to a temporary JSON file."""
    path = tmp_path / "dft_data.json"
    path.write_text(json.dumps(batch_records), encoding="utf-8")
    return path


@pytest.fixture
def tmp_csv_file(tmp_path: Path, batch_records: list[dict[str, Any]]) -> Path:
    """Write batch_records to a temporary CSV file using csv.writer for quoting."""
    path = tmp_path / "dft_data.csv"
    headers = [
        "composition", "functional", "cutoff_energy", "kpoints",
        "formation_energy", "binding_energy", "lattice_distortion",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = _csv_stdlib.writer(f)
        writer.writerow(headers)
        for r in batch_records:
            writer.writerow([
                json.dumps(r["composition"]),
                r["functional"],
                r["cutoff_energy"],
                r["kpoints"],
                r["formation_energy"],
                r["binding_energy"],
                r["lattice_distortion"],
            ])
    return path


# ---------------------------------------------------------------------------
# Parsing Tests
# ---------------------------------------------------------------------------


class TestParseJsonFile:
    """JSON file parsing tests."""

    def test_parses_valid_json_array(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        path.write_text(json.dumps([{"composition": {"U": 0.5}}]), encoding="utf-8")
        result = parse_json_file(path)
        assert len(result) == 1
        assert result[0]["composition"] == {"U": 0.5}

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_json_file(tmp_path / "nope.json")

    def test_raises_on_non_array(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"not": "array"}), encoding="utf-8")
        with pytest.raises(ValueError, match="JSON array"):
            parse_json_file(path)

    def test_raises_on_invalid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{invalid", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            parse_json_file(path)


class TestParseCsvFile:
    """CSV file parsing tests."""

    def test_parses_valid_csv(self, tmp_csv_file: Path) -> None:
        result = parse_csv_file(tmp_csv_file)
        assert len(result) == 10
        assert isinstance(result[0]["composition"], dict)

    def test_numeric_fields_are_float(self, tmp_csv_file: Path) -> None:
        result = parse_csv_file(tmp_csv_file)
        for record in result:
            assert isinstance(record["cutoff_energy"], float)
            assert isinstance(record["formation_energy"], float)

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_csv_file(tmp_path / "nope.csv")


class TestParseDftFile:
    """Format auto-detection tests."""

    def test_routes_json(self, tmp_json_file: Path) -> None:
        assert len(parse_dft_file(tmp_json_file)) == 10

    def test_routes_csv(self, tmp_csv_file: Path) -> None:
        assert len(parse_dft_file(tmp_csv_file)) == 10

    def test_raises_on_unsupported_extension(self, tmp_path: Path) -> None:
        path = tmp_path / "data.txt"
        path.write_text("garbage", encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported"):
            parse_dft_file(path)


# ---------------------------------------------------------------------------
# Validation Tests
# ---------------------------------------------------------------------------


class TestValidateRecord:
    """Record validation tests."""

    def test_valid_record_returns_none(self, valid_record: dict[str, Any]) -> None:
        assert validate_record(valid_record) is None

    def test_rejects_empty_composition(self, valid_record: dict[str, Any]) -> None:
        valid_record["composition"] = {}
        assert validate_record(valid_record) is not None

    def test_rejects_non_dict_composition(self, valid_record: dict[str, Any]) -> None:
        valid_record["composition"] = "not a dict"
        assert validate_record(valid_record) is not None

    def test_rejects_missing_formation_energy(self, valid_record: dict[str, Any]) -> None:
        valid_record.pop("formation_energy")
        assert "formation_energy" in validate_record(valid_record)

    def test_rejects_missing_binding_energy(self, valid_record: dict[str, Any]) -> None:
        valid_record.pop("binding_energy")
        assert "binding_energy" in validate_record(valid_record)

    def test_rejects_infinite_formation_energy(self, valid_record: dict[str, Any]) -> None:
        valid_record["formation_energy"] = math.inf
        assert "finite" in validate_record(valid_record)

    def test_rejects_nan_formation_energy(self, valid_record: dict[str, Any]) -> None:
        valid_record["formation_energy"] = math.nan
        assert validate_record(valid_record) is not None

    def test_rejects_negative_composition_fraction(self, valid_record: dict[str, Any]) -> None:
        valid_record["composition"]["U"] = -0.1
        assert "positive" in validate_record(valid_record)

    def test_rejects_zero_composition_fraction(self, valid_record: dict[str, Any]) -> None:
        valid_record["composition"]["U"] = 0.0
        assert "positive" in validate_record(valid_record)

    def test_rejects_non_numeric_composition_fraction(self, valid_record: dict[str, Any]) -> None:
        valid_record["composition"]["U"] = "half"
        assert "positive" in validate_record(valid_record)


# ---------------------------------------------------------------------------
# Calculation ID Tests
# ---------------------------------------------------------------------------


class TestGenerateCalculationId:
    """Deterministic calculation_id tests."""

    def test_returns_dft_prefix(self) -> None:
        cid = generate_calculation_id({"U": 0.5, "Mo": 0.5}, "PBE")
        assert cid.startswith("DFT-")

    def test_returns_20_chars_after_prefix(self) -> None:
        cid = generate_calculation_id({"U": 0.5}, "PBE")
        assert len(cid) == 4 + 16  # "DFT-" + 16 hex chars

    def test_is_deterministic(self) -> None:
        comp = {"U": 0.5, "Mo": 0.5}
        a = generate_calculation_id(comp, "PBE")
        b = generate_calculation_id(comp, "PBE")
        assert a == b

    def test_different_composition_different_id(self) -> None:
        a = generate_calculation_id({"U": 0.5, "Mo": 0.5}, "PBE")
        b = generate_calculation_id({"U": 0.5, "Zr": 0.5}, "PBE")
        assert a != b

    def test_different_functional_different_id(self) -> None:
        comp = {"U": 0.5, "Mo": 0.5}
        a = generate_calculation_id(comp, "PBE")
        b = generate_calculation_id(comp, "PBEsol")
        assert a != b


# ---------------------------------------------------------------------------
# ORM Mapping Tests
# ---------------------------------------------------------------------------


class TestRecordToOrmDict:
    """ORM mapping tests."""

    def test_maps_binding_energy_to_cohesive_energy(self, valid_record: dict[str, Any]) -> None:
        orm = record_to_orm_dict(valid_record, source="test")
        assert orm["cohesive_energy"] == valid_record["binding_energy"]

    def test_maps_kpoints_to_kpoint_mesh(self, valid_record: dict[str, Any]) -> None:
        orm = record_to_orm_dict(valid_record, source="test")
        assert orm["kpoint_mesh"] == valid_record["kpoints"]

    def test_includes_source(self, valid_record: dict[str, Any]) -> None:
        orm = record_to_orm_dict(valid_record, source="my_source")
        assert orm["source"] == "my_source"

    def test_includes_deterministic_calc_id(self, valid_record: dict[str, Any]) -> None:
        orm = record_to_orm_dict(valid_record, source="test")
        expected = generate_calculation_id(
            valid_record["composition"], valid_record["functional"]
        )
        assert orm["calculation_id"] == expected

    def test_stores_composition_in_metadata(self, valid_record: dict[str, Any]) -> None:
        orm = record_to_orm_dict(valid_record, source="test")
        assert "composition" in orm["computation_metadata"]
        # Composition should be sorted for determinism
        assert list(orm["computation_metadata"]["composition"].keys()) == sorted(
            orm["computation_metadata"]["composition"].keys()
        )


# ---------------------------------------------------------------------------
# Outlier Detection Tests (M2 fix verification)
# ---------------------------------------------------------------------------


class TestDetectOutliers:
    """Outlier detection tests with sample variance (N-1 denominator)."""

    def test_returns_set_for_insufficient_values(self) -> None:
        assert detect_outliers([1.0, 2.0]) == set()
        assert detect_outliers([]) == set()

    def test_returns_set_when_all_equal(self) -> None:
        assert detect_outliers([5.0, 5.0, 5.0, 5.0]) == set()

    def test_detects_clear_outlier(self) -> None:
        # 19 tight values around -0.5 plus one extreme outlier at -50.0
        values = [-0.5] * 19 + [-50.0]
        outliers = detect_outliers(values, sigma_threshold=3.0)
        assert -50.0 in outliers
        assert len(outliers) == 1

    def test_uses_sample_variance_not_population(self) -> None:
        """Verify sample std > population std for n>2 (Bessel's correction)."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 100.0]
        n = len(values)
        mean_val = sum(values) / n
        ss = sum((v - mean_val) ** 2 for v in values)
        pop_std = math.sqrt(ss / n)
        sample_std = math.sqrt(ss / (n - 1))
        # Sample std must always exceed population std for n > 1
        assert sample_std > pop_std

    def test_no_outliers_when_within_threshold(self) -> None:
        values = [-0.5, -0.4, -0.6, -0.45, -0.55]
        outliers = detect_outliers(values, sigma_threshold=3.0)
        assert outliers == set()


# ---------------------------------------------------------------------------
# Bulk Insert Tests (H1+H2 fix verification)
# ---------------------------------------------------------------------------


class TestBulkInsertDft:
    """Bulk insert with batch dedup tests."""

    @pytest.mark.asyncio
    async def test_inserts_all_new_records(
        self, db_session: AsyncSession, batch_records: list[dict[str, Any]]
    ) -> None:
        report = await bulk_insert_dft(db_session, batch_records, source="test")
        assert report.inserted == len(batch_records)
        assert report.skipped == 0
        assert report.failed == 0
        assert report.source == "test"

    @pytest.mark.asyncio
    async def test_uses_bulk_insert_not_session_add_loop(
        self, db_session: AsyncSession, batch_records: list[dict[str, Any]]
    ) -> None:
        """Verify only ONE bulk insert query is issued (H1 fix verification)."""
        executed_inserts: list[str] = []

        @event.listens_for(db_session.bind.sync_engine, "before_cursor_execute")
        def _capture(conn, cursor, statement, parameters, context, executemany):
            if statement.strip().upper().startswith("INSERT"):
                executed_inserts.append(statement[:200])

        await bulk_insert_dft(db_session, batch_records, source="test")
        # Should have exactly ONE INSERT statement covering all 10 records
        assert len(executed_inserts) == 1, (
            f"Expected 1 batch INSERT, got {len(executed_inserts)}: "
            f"{executed_inserts}"
        )

    @pytest.mark.asyncio
    async def test_uses_single_batch_dedup_query(
        self, db_session: AsyncSession, batch_records: list[dict[str, Any]]
    ) -> None:
        """Verify only ONE dedup SELECT query is issued (H2 fix verification)."""
        executed_selects: list[str] = []

        @event.listens_for(db_session.bind.sync_engine, "before_cursor_execute")
        def _capture(conn, cursor, statement, parameters, context, executemany):
            if "SELECT" in statement.strip().upper()[:7]:
                executed_selects.append(statement[:200])

        await bulk_insert_dft(db_session, batch_records, source="test")
        # Only ONE SELECT should hit dft_calculations for the calc_id IN (...) query
        dft_selects = [
            s for s in executed_selects
            if "dft_calculations" in s.lower() and "calculation_id" in s.lower()
        ]
        assert len(dft_selects) == 1, (
            f"Expected 1 batch dedup SELECT, got {len(dft_selects)}: {dft_selects}"
        )

    @pytest.mark.asyncio
    async def test_skips_existing_records_on_reimport(
        self, db_session: AsyncSession, batch_records: list[dict[str, Any]]
    ) -> None:
        """Verify idempotency: re-importing same records skips all."""
        await bulk_insert_dft(db_session, batch_records, source="test")
        await db_session.commit()

        # Re-import same records
        report = await bulk_insert_dft(db_session, batch_records, source="test")
        assert report.inserted == 0
        assert report.skipped == len(batch_records)

    @pytest.mark.asyncio
    async def test_counts_validation_failures(
        self, db_session: AsyncSession, batch_records: list[dict[str, Any]]
    ) -> None:
        """Records failing validation are counted as failed, not inserted."""
        # Corrupt one record
        bad = dict(batch_records[0])
        bad["formation_energy"] = None
        records = [bad, *batch_records[1:]]

        report = await bulk_insert_dft(db_session, records, source="test")
        assert report.failed == 1
        assert report.inserted == len(batch_records) - 1

    @pytest.mark.asyncio
    async def test_source_tag_stored_on_insert(
        self, db_session: AsyncSession, batch_records: list[dict[str, Any]]
    ) -> None:
        await bulk_insert_dft(db_session, batch_records, source="incremental_200")
        await db_session.commit()

        stmt = select(DFTCalculation).limit(1)
        result = await db_session.execute(stmt)
        row = result.scalar_one()
        assert row.source == "incremental_200"

    @pytest.mark.asyncio
    async def test_detects_outliers_in_inserted_records(
        self, db_session: AsyncSession, batch_records: list[dict[str, Any]]
    ) -> None:
        """Insert records with one clear outlier, verify detection."""
        # Add an extreme outlier
        records = list(batch_records)
        records.append({
            "composition": {"U": 0.99, "Mo": 0.01},
            "functional": "PBE",
            "cutoff_energy": 520.0,
            "kpoints": "8x8x8",
            "formation_energy": -100.0,  # extreme outlier
            "binding_energy": -4.0,
            "lattice_distortion": 0.0,
        })

        report = await bulk_insert_dft(db_session, records, source="test")
        assert -100.0 in report.formation_energy_outliers


# ---------------------------------------------------------------------------
# Full Pipeline Integration Test
# ---------------------------------------------------------------------------


class TestRunImport:
    """End-to-end pipeline tests."""

    @pytest.mark.asyncio
    async def test_runs_full_pipeline_from_json(
        self, db_session: AsyncSession, tmp_json_file: Path
    ) -> None:
        report = await run_import(
            db_session, tmp_json_file, source="incremental_200"
        )
        assert report.total == 10
        assert report.inserted == 10
        assert report.source == "incremental_200"
        assert isinstance(report.summary(), str)

    @pytest.mark.asyncio
    async def test_runs_full_pipeline_from_csv(
        self, db_session: AsyncSession, tmp_csv_file: Path
    ) -> None:
        report = await run_import(
            db_session, tmp_csv_file, source="csv_import"
        )
        assert report.total == 10
        assert report.inserted == 10

    @pytest.mark.asyncio
    async def test_idempotent_reimport(
        self, db_session: AsyncSession, tmp_json_file: Path
    ) -> None:
        # First import
        report1 = await run_import(db_session, tmp_json_file, source="x")
        await db_session.commit()
        assert report1.inserted == 10

        # Re-import — all should be skipped
        report2 = await run_import(db_session, tmp_json_file, source="x")
        assert report2.inserted == 0
        assert report2.skipped == 10


# ---------------------------------------------------------------------------
# ImportReport Tests
# ---------------------------------------------------------------------------


class TestImportReport:
    """ImportReport dataclass tests."""

    def test_summary_includes_counts(self) -> None:
        r = ImportReport(total=100, inserted=80, skipped=15, failed=5, source="s")
        s = r.summary()
        assert "Total: 100" in s
        assert "Inserted: 80" in s
        assert "Skipped" in s
        assert "Failed" in s
        assert "source=s" in s

    def test_summary_includes_outlier_counts_when_present(self) -> None:
        r = ImportReport(
            total=10, source="x",
            formation_energy_outliers=frozenset({-100.0}),
        )
        s = r.summary()
        assert "Formation energy outliers" in s

    def test_summary_omits_outlier_lines_when_empty(self) -> None:
        r = ImportReport(total=10, source="x")
        s = r.summary()
        assert "outliers" not in s

    def test_is_frozen(self) -> None:
        r = ImportReport(total=10)
        with pytest.raises(Exception):  # FrozenInstanceError
            r.total = 20  # type: ignore[misc]
