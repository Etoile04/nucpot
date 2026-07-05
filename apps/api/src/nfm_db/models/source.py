"""Data source and author ORM models.

Phase 1 core tables: data_sources, authors, data_source_authors.
Stores literature references with author management.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, TimestampMixin

if TYPE_CHECKING:
    from nfm_db.models.property import Dataset


class DataSource(TimestampMixin, Base):
    """A data source (journal article, report, database, etc.)."""

    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("doi", name="uq_data_sources_doi"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(1000))
    journal: Mapped[str | None] = mapped_column(String(500), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pages: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_type: Mapped[str] = mapped_column(String(50))
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # -- relationships --
    authors: Mapped[list["Author"]] = relationship(
        secondary="data_source_authors",
        back_populates="data_sources",
        overlaps="data_source_authors",
    )
    data_source_authors: Mapped[list["DataSourceAuthor"]] = relationship(
        back_populates="data_source",
    )
    datasets: Mapped[list["Dataset"]] = relationship(
        back_populates="source",
    )

    def __repr__(self) -> str:
        return f"<DataSource id={self.id!s} title={self.title!r}>"


class Author(TimestampMixin, Base):
    """An author of a data source."""

    __tablename__ = "authors"
    __table_args__ = (
        UniqueConstraint("orcid", name="uq_authors_orcid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    full_name: Mapped[str] = mapped_column(String(300))
    last_name: Mapped[str] = mapped_column(String(100))
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    orcid: Mapped[str | None] = mapped_column(String(19), nullable=True)
    affiliation: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # -- relationships --
    data_sources: Mapped[list["DataSource"]] = relationship(
        secondary="data_source_authors",
        back_populates="authors",
        overlaps="data_source_authors",
    )
    data_source_authors: Mapped[list["DataSourceAuthor"]] = relationship(
        back_populates="author",
    )

    def __repr__(self) -> str:
        return f"<Author id={self.id!s} full_name={self.full_name!r}>"


class DataSourceAuthor(TimestampMixin, Base):
    """Junction table linking data sources to authors with order."""

    __tablename__ = "data_source_authors"
    __table_args__ = (
        UniqueConstraint(
            "data_source_id",
            "author_order",
            name="uq_data_source_authors_source_order",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"),
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("authors.id", ondelete="CASCADE"),
    )
    author_order: Mapped[int] = mapped_column(Integer)
    is_corresponding: Mapped[bool] = mapped_column(Boolean, default=False)

    # -- relationships --
    data_source: Mapped["DataSource"] = relationship(
        back_populates="data_source_authors",
        overlaps="authors,data_sources",
    )
    author: Mapped["Author"] = relationship(
        back_populates="data_source_authors",
        overlaps="data_sources,authors",
    )

    def __repr__(self) -> str:
        return (
            f"<DataSourceAuthor id={self.id!s} "
            f"source={self.data_source_id!s} author={self.author_id!s}>"
        )
