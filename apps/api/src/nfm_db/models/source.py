"""Data source and author ORM models.

Phase 1 core tables: data_sources, authors, data_source_authors.
Tracks literature references and their authorship provenance.
"""

import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, TimestampMixin

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nfm_db.models.property import Dataset


class DataSource(TimestampMixin, Base):
    """A literature reference or data source (paper, report, database)."""

    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("doi", name="uq_data_sources_doi"),
        Index("idx_data_sources_year", "year"),
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
    datasets: Mapped[list["Dataset"]] = relationship(back_populates="source")
    author_links: Mapped[list["DataSourceAuthor"]] = relationship(
        back_populates="source",
    )

    def __repr__(self) -> str:
        return f"<DataSource id={self.id!s} title={self.title!r:.50}>"


class Author(TimestampMixin, Base):
    """A researcher / author."""

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
    source_links: Mapped[list["DataSourceAuthor"]] = relationship(
        back_populates="author",
    )

    def __repr__(self) -> str:
        return f"<Author id={self.id!s} name={self.full_name!r}>"


class DataSourceAuthor(TimestampMixin, Base):
    """Many-to-many join: authors of a data source."""

    __tablename__ = "data_source_authors"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "author_id",
            name="uq_data_source_authors_source_author",
        ),
        Index("idx_dsa_source", "source_id"),
        Index("idx_dsa_author", "author_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        index=True,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("authors.id", ondelete="CASCADE"),
        index=True,
    )
    author_order: Mapped[int] = mapped_column(Integer)
    is_corresponding: Mapped[bool] = mapped_column(Boolean, default=False)

    # -- relationships --
    source: Mapped["DataSource"] = relationship(
        back_populates="author_links",
        foreign_keys=[source_id],
    )
    author: Mapped["Author"] = relationship(
        back_populates="source_links",
        foreign_keys=[author_id],
    )

    def __repr__(self) -> str:
        return (
            f"<DataSourceAuthor id={self.id!s} "
            f"source={self.source_id!s} author={self.author_id!s}>"
        )
