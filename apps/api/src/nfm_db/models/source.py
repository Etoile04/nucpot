"""Source data models (F01 / NFM-870).

SQLAlchemy ORM models for managing literature sources:
- DataSource: Core publication record (journal article, conference paper, etc.)
- Author: Researcher / contributor
- DataSourceAuthor: Junction table linking sources to authors
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, TimestampMixin


class DataSource(Base, TimestampMixin):
    """A literature source (journal article, conference paper, book, etc.).

    Stores metadata for a single publication including bibliographic
    details and a link to the external source (e.g., Zotero key).
    """

    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    journal: Mapped[str | None] = mapped_column(String(500), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pages: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="journal_article",
        server_default="journal_article",
    )
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    external_key: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Zotero item key or other external identifier",
    )

    # Relationships
    author_links: Mapped[list[DataSourceAuthor]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<DataSource id={self.id} title={self.title[:50]!r}>"


class Author(Base, TimestampMixin):
    """A researcher / contributor linked to one or more data sources."""

    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(500), nullable=False)
    last_name: Mapped[str] = mapped_column(String(200), nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    orcid: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True)
    affiliation: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Relationships
    source_links: Mapped[list[DataSourceAuthor]] = relationship(
        back_populates="author",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Author id={self.id} full_name={self.full_name!r}>"


class DataSourceAuthor(Base, TimestampMixin):
    """Junction table linking DataSource to Author.

    Records the authorship relationship including ordering and
    whether the author is the corresponding author.
    """

    __tablename__ = "data_source_authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("data_sources.id"),
        nullable=False,
    )
    author_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("authors.id"),
        nullable=False,
    )
    author_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_corresponding: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    # Relationships
    source: Mapped[DataSource] = relationship(back_populates="author_links")
    author: Mapped[Author] = relationship(back_populates="source_links")

    def __repr__(self) -> str:
        return (
            f"<DataSourceAuthor source_id={self.data_source_id} "
            f"author_id={self.author_id} order={self.author_order}>"
        )
