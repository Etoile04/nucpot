"""Material ORM models.

Phase 1 core tables: material_categories, materials, material_aliases,
material_compositions.
Stores material definitions with hierarchical categories, aliases, and composition.
"""

import uuid
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from nfm_db.models.dft_calculation import DFTCalculation
    from nfm_db.models.property import Dataset


class MaterialCategory(TimestampMixin, Base):
    """High-level material category (oxide fuels, metal fuels, etc.)."""

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
    parent: Mapped["MaterialCategory | None"] = relationship(
        remote_side="MaterialCategory.id",
        back_populates="children",
    )
    children: Mapped[list["MaterialCategory"]] = relationship(
        back_populates="parent",
    )
    materials: Mapped[list["Material"]] = relationship(
        back_populates="category",
    )

    def __repr__(self) -> str:
        return f"<MaterialCategory id={self.id!s} name={self.name!r}>"


class Material(TimestampMixin, Base):
    """A nuclear fuel material or related compound."""

    __tablename__ = "materials"
    __table_args__ = (
        Index("idx_materials_category", "category_id"),
        Index("idx_materials_formula", "formula"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(500))
    formula: Mapped[str | None] = mapped_column(String(200), nullable=True)
    crystal_structure: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
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
    )
    composition: Mapped[list["MaterialComposition"]] = relationship(
        back_populates="material",
    )
    datasets: Mapped[list["Dataset"]] = relationship(
        back_populates="material",
    )
    dft_calculations: Mapped[list["DFTCalculation"]] = relationship(
        back_populates="material",
    )

    def __repr__(self) -> str:
        return f"<Material id={self.id!s} name={self.name!r}>"


class MaterialAlias(TimestampMixin, Base):
    """Alternative name for a material (IUPAC, common name, CAS, etc.)."""

    __tablename__ = "material_aliases"
    __table_args__ = (
        UniqueConstraint(
            "material_id",
            "alias_name",
            name="uq_material_aliases_material_name",
        ),
        CheckConstraint(
            "alias_type IN ('common_name', 'iupac_name', 'cas_number', "
            "'legacy_name', 'abbreviation', 'trademark', 'other')",
            name="ck_material_aliases_alias_type",
        ),
        Index("idx_mat_aliases_material", "material_id"),
        Index("idx_mat_aliases_alias_name", "alias_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"),
    )
    alias_name: Mapped[str] = mapped_column(String(500))
    alias_type: Mapped[str] = mapped_column(String(50))
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # -- relationships --
    material: Mapped["Material"] = relationship(back_populates="aliases")

    def __repr__(self) -> str:
        return f"<MaterialAlias id={self.id!s} alias={self.alias_name!r}>"


class MaterialComposition(TimestampMixin, Base):
    """Elemental composition of a material."""

    __tablename__ = "material_compositions"
    __table_args__ = (
        CheckConstraint(
            "fraction >= 0 AND fraction <= 1",
            name="ck_material_compositions_fraction",
        ),
        Index("idx_mat_comp_material", "material_id"),
        Index("idx_mat_comp_element", "element"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"),
    )
    element: Mapped[str] = mapped_column(String(20))
    fraction: Mapped[float] = mapped_column(Numeric(10, 6))

    # -- relationships --
    material: Mapped["Material"] = relationship(back_populates="composition")

    def __repr__(self) -> str:
        return (
            f"<MaterialComposition id={self.id!s} "
            f"element={self.element!r} fraction={self.fraction}>"
        )
