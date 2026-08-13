"""Integration tests for ``/extraction-gaps/{gap_id}/source-text`` security.

NFM-2781 HOTFIX CR1 — proves that the path-traversal allowlist guards
the endpoint end-to-end.  Three attack vectors must be rejected, one
happy path inside the allowlist must succeed.

Uses :class:`monkeypatch` on ``get_settings().source_base`` so we can
point the endpoint at a tmp allowlist without touching the production
``/var/nfm-data/sources/`` path.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.config import get_settings
from nfm_db.models import (
    ExtractionChunk,
    ExtractionGap,
    ExtractionJob,
    OntologyVersion,
    User,
)
from nfm_db.models.user import BlogRole


async def _seed_user(session: AsyncSession, *, role: BlogRole) -> User:
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        username=f"seed_{role.value}_{user_id.hex[:8]}",
        email=f"seed_{user_id.hex[:8]}@test.com",
        hashed_password="hashed",
        blog_role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_ontology(session: AsyncSession) -> OntologyVersion:
    user = await _seed_user(session, role=BlogRole.DOMAIN_EXPERT)
    ov = OntologyVersion(
        version="1.0.0",
        status="published",
        created_by=user.id,
        ontology_data={"entity_types": [], "relation_types": []},
    )
    session.add(ov)
    await session.flush()
    return ov


async def _seed_job(session: AsyncSession) -> ExtractionJob:
    """Seed a minimal ExtractionJob for FK target of ExtractionChunk.job_id."""
    job = ExtractionJob(status="pending")
    session.add(job)
    await session.flush()
    return job


async def _seed_chunk(
    session: AsyncSession,
    *,
    source_reference: str | None,
    source_span: dict | None = None,
    content: str = "synthetic chunk body",
) -> ExtractionChunk:
    job = await _seed_job(session)
    chunk = ExtractionChunk(
        job_id=job.id,
        chunk_index=0,
        source_reference=source_reference,
        source_span=source_span or {"start": 0, "end": 8},
        content=content,
    )
    session.add(chunk)
    await session.flush()
    return chunk


async def _seed_gap(
    session: AsyncSession,
    *,
    chunk_id: uuid.UUID | None,
    ontology_version: str,
) -> ExtractionGap:
    gap = ExtractionGap(
        chunk_id=chunk_id,
        ontology_version=ontology_version,
        entity_type="NuclearMaterial",
        property="density",
        gap_status="open",
    )
    session.add(gap)
    await session.flush()
    return gap


@pytest.fixture()
def source_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Override ``Settings.source_base`` to a tmp dir for the test."""
    base = tmp_path / "sources"
    base.mkdir()

    monkeypatch.setattr(get_settings(), "source_base", str(base))
    monkeypatch.setenv("NFM_SOURCE_BASE", str(base))
    return base


class TestSourceTextPathSafety:
    """End-to-end tests for the allowlist guard."""

    async def test_happy_path_inside_allowlist_returns_snippet(
        self,
        async_client,
        db_session: AsyncSession,
        source_base: Path,
    ) -> None:
        """A chunk whose source_reference points inside the allowlist works."""
        target = source_base / "doc.txt"
        target.write_text("hello world")

        chunk = await _seed_chunk(
            db_session,
            source_reference=str(target),
            source_span={"start": 0, "end": 5},
        )
        ov = await _seed_ontology(db_session)
        gap = await _seed_gap(
            db_session, chunk_id=chunk.id, ontology_version=ov.version
        )
        await db_session.commit()

        resp = await async_client.get(
            f"/api/v1/extraction-gaps/{gap.id}/source-text",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["available"] is True
        assert data["snippet"] == "hello"

    async def test_parent_directory_escape_returns_generic_error(
        self,
        async_client,
        db_session: AsyncSession,
        source_base: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A ``..`` chain that escapes the allowlist is rejected."""
        chunk = await _seed_chunk(
            db_session,
            source_reference="../../../../etc/passwd",
        )
        ov = await _seed_ontology(db_session)
        gap = await _seed_gap(
            db_session, chunk_id=chunk.id, ontology_version=ov.version
        )
        await db_session.commit()

        with caplog.at_level(logging.WARNING, logger="nfm_db.security"):
            resp = await async_client.get(
                f"/api/v1/extraction-gaps/{gap.id}/source-text",
            )

        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert data["available"] is False
        assert data["error"] == "Source path outside allowlist"
        # Response MUST NOT leak the attempted path
        assert "../../../../etc/passwd" not in str(body)
        # Security log MUST capture the attempt
        assert any(
            "../../../../etc/passwd" in rec.message
            for rec in caplog.records
            if rec.name == "nfm_db.security"
        )

    async def test_absolute_path_outside_allowlist_rejected(
        self,
        async_client,
        db_session: AsyncSession,
        source_base: Path,
    ) -> None:
        """Absolute path to /etc/passwd is rejected."""
        chunk = await _seed_chunk(
            db_session,
            source_reference="/etc/passwd",
        )
        ov = await _seed_ontology(db_session)
        gap = await _seed_gap(
            db_session, chunk_id=chunk.id, ontology_version=ov.version
        )
        await db_session.commit()

        resp = await async_client.get(
            f"/api/v1/extraction-gaps/{gap.id}/source-text",
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["available"] is False
        assert data["error"] == "Source path outside allowlist"

    async def test_symlink_escape_rejected(
        self,
        async_client,
        db_session: AsyncSession,
        source_base: Path,
    ) -> None:
        """A symlink inside the allowlist pointing outside is rejected."""
        # Outside file (sibling of allowlist, both under tmp_path).
        outside = source_base.parent / "outside.txt"
        outside.write_text("outside")

        link = source_base / "evil.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):  # pragma: no cover
            pytest.skip("symlinks not supported in this environment")

        chunk = await _seed_chunk(
            db_session,
            source_reference=str(link),
        )
        ov = await _seed_ontology(db_session)
        gap = await _seed_gap(
            db_session, chunk_id=chunk.id, ontology_version=ov.version
        )
        await db_session.commit()

        resp = await async_client.get(
            f"/api/v1/extraction-gaps/{gap.id}/source-text",
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["available"] is False
        assert data["error"] == "Source path outside allowlist"
