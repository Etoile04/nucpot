"""Material ORM models.

Phase 1 core tables: material_categories, materials, material_aliases,
material_compositions.
Central entity for nuclear fuel materials and their taxonomy.
"""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, TimestampMixin

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nfm_db.models.property import Dataset


class MaterialCategory(TimestampMixin, Base):
    """Hierarchical taxonomy for materials (fuel, cladding, coolant, etc.)."""

    __tablename__ = "material_categories"
    __table_args__ = (
        UniqueConstraint("name", name="uq_material_categories_name"),
        UniqueConstraint("slug", name="uq_material_categories_slug"),
        Index("idx_mat_cat_parent", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("material_categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # -- relationships --
    children: Mapped[list["MaterialCategory"]] = relationship(
        back_populates="parent",
    )
    parent: Mapped["MaterialCategory | None"] = relationship(
        back_populates="children",
        remote_side=[id],
    )
    materials: Mapped[list["Material"]] = relationship(back_populates="category")

    def __repr__(self) -> str:
        return f"<MaterialCategory id={self.id!s} name={self.name!r}>"


class Material(TimestampMixin, Base):
    """A nuclear fuel material (e.g., UO2, Zircaloy-4)."""

    __tablename__ = "materials"
    __table_args__ = (
        UniqueConstraint(
            "name",
            "formula",
            name="uq_materials_name_formula",
        ),
        Index("idx_materials_category", "category_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(500))
    formula: Mapped[str | None] = mapped_column(String(200), nullable=True)
    crystal_structure: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("material_categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # -- relationships --
    category: Mapped["MaterialCategory | None"] = relationship(
        back_populates="materials",
    )
    aliases: Mapped[list["MaterialAlias"]] = relationship(
        back_populates="material",
        cascade="all, delete-orphan",
    )
    compositions: Mapped[list["MaterialComposition"]] = relationship(
        back_populates="material",
        cascade="all, delete-orphan",
    )
    datasets: Mapped[list["Dataset"]] = relationship(back_populates="material")

    def __repr__(self) -> str:
        return f"<Material id={self.id!s} name={self.name!r}>"


class MaterialAlias(TimestampMixin, Base):
    """Alternative name for a material (common name, IUPAC, CAS, legacy)."""

    __tablename__ = "material_aliases"
    __table_args__ = (
        UniqueConstraint(
            "alias_name",
            "alias_type",
            name="uq_material_aliases_name_type",
        ),
        Index("idx_material_aliases_material", "material_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"),
        index=True,
    )
    alias_name: Mapped[str] = mapped_column(String(500))
    alias_type: Mapped[str] = mapped_column(String(50))
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # -- relationships --
    material: Mapped["Material"] = relationship(back_populates="aliases")

    def __repr__(self) -> str:
        return (
            f"<MaterialAlias id={self.id!s} "
            f"alias={self.alias_name!r} type={self.alias_type!r}>"
        )


class MaterialComposition(TimestampMixin, Base):
    """Element fraction within a material (atomic fractions summing to 1)."""

    __tablename__ = "material_compositions"
    __table_args__ = (
        UniqueConstraint(
            "material_id",
            "element",
            name="uq_material_compositions_material_element",
        ),
        CheckConstraint(
            "fraction >= 0 AND fraction <= 1",
            name="ck_material_compositions_fraction_range",
        ),
        Index("idx_material_compositions_material", "material_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"),
        index=True,
    )
    element: Mapped[str] = mapped_column(String(20))
    fraction: Mapped[float] = mapped_column(Numeric(6, 4))

    # -- relationships --
    material: Mapped["Material"] = relationship(back_populates="compositions")

    def __repr__(self) -> str:
        return (
            f"<MaterialComposition id={self.id!s} "
            f"element={self.element!r} fraction={self.fraction}>"
        )
