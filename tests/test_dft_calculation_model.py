"""Tests for DFTCalculation ORM model.

Verifies table creation, column types, constraints, defaults, and relationships.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from nfm_db.models import Base, DFTCalculation, Material


@pytest.fixture()
def engine() -> Engine:
    """Create an in-memory SQLite engine for model tests."""
    from sqlalchemy import create_engine

    return create_engine("sqlite:///:memory:")


@pytest.fixture()
def tables(engine: Engine) -> Engine:
    """Create only the tables needed for DFT calculation tests.

    Creating all tables via Base.metadata.create_all() fails on SQLite
    because some models use JSONB (PostgreSQL-only).  Instead, create
    only the material and dft_calculations tables.
    """
    # Create materials tables (required FK target)
    from nfm_db.models.material import Material, MaterialAlias, MaterialCategory, MaterialComposition

    tables_to_create = [
        MaterialCategory.__table__,
        Material.__table__,
        MaterialAlias.__table__,
        MaterialComposition.__table__,
        DFTCalculation.__table__,
    ]
    for table in tables_to_create:
        table.create(engine, checkfirst=True)
    return engine


@pytest.fixture()
def sample_material(tables: Engine) -> uuid.UUID:
    """Insert and return a sample Material for FK references via ORM."""
    from nfm_db.models.material import Material, MaterialCategory

    with Session(tables) as session:
        cat = MaterialCategory(name="Alloys", slug="alloys", sort_order=0)
        session.add(cat)
        session.flush()

        mat = Material(
            name="U-10Zr",
            formula="UZr10",
            crystal_structure="BCC",
            category_id=cat.id,
            is_active=True,
        )
        session.add(mat)
        session.flush()
        return mat.id


class TestDFTCalculationTable:
    """Verify dft_calculations table schema."""

    def test_table_exists(self, tables: Engine) -> None:
        inspector = inspect(tables)
        assert "dft_calculations" in inspector.get_table_names()

    def test_columns_present(self, tables: Engine) -> None:
        inspector = inspect(tables)
        columns = {col["name"] for col in inspector.get_columns("dft_calculations")}
        expected = {
            "id",
            "calculation_id",
            "material_id",
            "functional",
            "cutoff_energy",
            "kpoint_mesh",
            "kpoint_density",
            "convergence_criteria",
            "exchange_correlation",
            "pseudopotential",
            "spin_polarization",
            "formation_energy",
            "cohesive_energy",
            "lattice_distortion",
            "status",
            "notes",
            "computation_metadata",
            "created_at",
            "updated_at",
        }
        assert expected <= columns

    def test_id_is_primary_key(self, tables: Engine) -> None:
        inspector = inspect(tables)
        pk = inspector.get_pk_constraint("dft_calculations")
        assert pk["constrained_columns"] == ["id"]

    def test_timestamp_columns_exist(self, tables: Engine) -> None:
        inspector = inspect(tables)
        columns = {col["name"] for col in inspector.get_columns("dft_calculations")}
        assert "created_at" in columns
        assert "updated_at" in columns


class TestDFTCalculationColumns:
    """Verify column types and constraints."""

    def test_calculation_id_unique(self, tables: Engine) -> None:
        inspector = inspect(tables)
        uqs = inspector.get_unique_constraints("dft_calculations")
        # SQLite inspector may use 'column_names' or 'columns'
        col_names: set[str] = set()
        for uq in uqs:
            names = uq.get("column_names", uq.get("columns", []))
            col_names.update(names)
        assert "calculation_id" in col_names

    def test_energy_columns_nullable(self, tables: Engine) -> None:
        inspector = inspect(tables)
        columns = {col["name"]: col for col in inspector.get_columns("dft_calculations")}
        assert columns["formation_energy"]["nullable"] is True
        assert columns["cohesive_energy"]["nullable"] is True
        assert columns["lattice_distortion"]["nullable"] is True

    def test_functional_not_nullable(self, tables: Engine) -> None:
        inspector = inspect(tables)
        columns = {col["name"]: col for col in inspector.get_columns("dft_calculations")}
        assert columns["functional"]["nullable"] is False

    def test_cutoff_energy_not_nullable(self, tables: Engine) -> None:
        inspector = inspect(tables)
        columns = {col["name"]: col for col in inspector.get_columns("dft_calculations")}
        assert columns["cutoff_energy"]["nullable"] is False

    def test_status_default_is_pending(self) -> None:
        """Verify the ORM-level default for status is 'pending'."""
        assert DFTCalculation.status.property.columns[0].default.arg == "pending"

    def test_computation_metadata_nullable(self, tables: Engine) -> None:
        inspector = inspect(tables)
        columns = {col["name"]: col for col in inspector.get_columns("dft_calculations")}
        assert columns["computation_metadata"]["nullable"] is True

    def test_material_id_nullable(self, tables: Engine) -> None:
        inspector = inspect(tables)
        columns = {col["name"]: col for col in inspector.get_columns("dft_calculations")}
        assert columns["material_id"]["nullable"] is True


class TestDFTCalculationRelationships:
    """Verify ORM relationships."""

    def test_material_relationship_exists(self, tables: Engine) -> None:
        assert hasattr(DFTCalculation, "material")
        assert hasattr(Material, "dft_calculations")

    def test_material_fk_exists(self, tables: Engine) -> None:
        inspector = inspect(tables)
        fks = inspector.get_foreign_keys("dft_calculations")
        material_fks = [
            fk for fk in fks if "materials" in str(fk.get("referred_table", ""))
        ]
        assert len(material_fks) >= 1


class TestDFTCalculationDefaults:
    """Verify default values."""

    def test_status_default_pending(self) -> None:
        assert DFTCalculation.status.property.columns[0].default.arg == "pending"


class TestDFTCalculationORM:
    """Verify ORM round-trip operations."""

    def test_create_and_retrieve(self, tables: Engine) -> None:
        from nfm_db.models.material import Material, MaterialCategory

        with Session(tables) as session:
            cat = MaterialCategory(name="Alloys", slug="alloys", sort_order=0)
            session.add(cat)
            session.flush()

            mat = Material(
                name="U-10Zr",
                formula="UZr10",
                crystal_structure="BCC",
                category_id=cat.id,
                is_active=True,
            )
            session.add(mat)
            session.flush()

            calc = DFTCalculation(
                calculation_id="DFT-001",
                material_id=mat.id,
                functional="PBE",
                cutoff_energy=520.0,
                kpoint_mesh="4x4x4",
                kpoint_density=8.0,
                convergence_criteria="1e-5 eV",
                formation_energy=-2.34,
                cohesive_energy=-5.67,
                lattice_distortion=0.032,
                status="completed",
                computation_metadata={"vasp_version": "6.4.0", "encut": 520},
                notes="Test calculation for U-10Zr alloy",
            )
            session.add(calc)
            session.flush()

            retrieved = session.get(DFTCalculation, calc.id)
            assert retrieved is not None
            assert retrieved.calculation_id == "DFT-001"
            assert retrieved.functional == "PBE"
            assert retrieved.cutoff_energy == 520.0
            assert retrieved.formation_energy == -2.34
            assert retrieved.status == "completed"
            # Verify FK relationship in same session
            assert retrieved.material is not None
            assert retrieved.material.name == "U-10Zr"

    def test_create_minimal(self, tables: Engine) -> None:
        with Session(tables) as session:
            calc = DFTCalculation(
                calculation_id="DFT-MIN",
                functional="PBE",
                cutoff_energy=400.0,
            )
            session.add(calc)
            session.flush()

            retrieved = session.get(DFTCalculation, calc.id)
            assert retrieved is not None
            assert retrieved.status == "pending"
            assert retrieved.formation_energy is None
            assert retrieved.material_id is None

    def test_repr(self, tables: Engine) -> None:
        with Session(tables) as session:
            calc = DFTCalculation(
                calculation_id="DFT-REPR",
                functional="PBE",
                cutoff_energy=500.0,
            )
            session.add(calc)
            session.flush()

            repr_str = repr(calc)
            assert "DFTCalculation" in repr_str
            assert "DFT-REPR" in repr_str
