"""NFM-4789 — silent-zero extraction must record an explicit skip reason.

Prod evidence (lit 7b41e85a, 2026-09-12): a single 967-char segment whose
extraction tasks complete in 0.2-1.5s with ``extracted=0`` and NO log line,
NO marker, NO skip reason — a successful-but-empty LLM response was
indistinguishable from "nothing to extract" (product ruling: the silent
zero IS the defect).

Contract under test (AC2 + AC3):

1. When the merged + post-processed property list is empty,
   ``ontofuel_extract`` emits a ``logger.warning`` carrying an explicit,
   greppable skip reason (source, chunk count, content length, per-chunk
   outcomes) and fires a best-effort ``no_properties`` DataSource marker.
2. A single short segment still flows chunk → extract exactly once (no
   small-input fast skip anywhere).
3. Non-empty results emit nothing.
4. The marker reference is the DataSource UUID, not the display title
   (the retitling reassignment silently disabled NFM-3358's failure
   marker for every titled datasource — same fix family).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import OntologyVersion, User
from nfm_db.models.source import DataSource
from nfm_db.services.extraction_pipeline import ontofuel_extract

_ONTOLOGY_DATA: dict[str, Any] = {
    "entity_types": [
        {
            "name": "NuclearFuel",
            "description": "Base class for nuclear fuels",
            "required_properties": ["name", "composition"],
        },
    ],
    "relation_types": [],
}

# Same order of magnitude as lit 7b41e85a's single segment.
_THIN_CONTENT = "U-Mo alloy tensile notes. " * 37  # 962 chars
assert 900 < len(_THIN_CONTENT) < 1000


@pytest.fixture(autouse=True)
def _no_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No LLM_API_KEY, no stub mode: the env gate only applies to the
    default stack — injected ``llm_call`` bypasses it entirely."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("EXTRACTION_STUB_MODE", raising=False)


async def _seed_published_ontology(db: AsyncSession) -> None:
    user = User(
        username=f"onto-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="hashed",
    )
    db.add(user)
    await db.flush()
    db.add(
        OntologyVersion(
            version=f"1.0.{uuid.uuid4().int % 1000}",
            status="published",
            created_by=user.id,
            ontology_data=_ONTOLOGY_DATA,
        )
    )
    await db.flush()


async def _add_titled_datasource(
    db: AsyncSession,
    *,
    content_md: str,
    title: str = "Thin segment paper",
) -> DataSource:
    """A titled datasource row — title must NOT be usable as marker ref."""
    ds = DataSource(
        title=title,
        source_type="uploaded_pdf",
        parse_status="completed",
        content_md=content_md,
    )
    db.add(ds)
    await db.flush()
    return ds


async def _empty_llm(**kwargs: Any) -> list[dict[str, Any]]:
    """Stand-in for a successful LLM response with zero properties."""
    return []


async def _one_property_llm(**kwargs: Any) -> list[dict[str, Any]]:
    return [
        {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "lattice_constant",
            "value": 5.47,
            "unit": "angstrom",
            "method": "DFT",
            "source_doi": None,
            "confidence": "high",
        },
    ]


class TestSilentZeroSkipReason:
    @pytest.mark.asyncio
    async def test_empty_result_emits_skip_reason_log_and_marker(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Empty LLM output → warning with skip reason + no_properties marker."""
        await _seed_published_ontology(db_session)
        ds = await _add_titled_datasource(db_session, content_md=_THIN_CONTENT)

        marker_calls: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "nfm_db.services.extraction_pipeline._mark_extraction_no_properties",
            lambda ref, reason: marker_calls.append((ref, reason)),
        )

        with caplog.at_level(
            logging.WARNING,
            logger="nfm_db.services.extraction_pipeline",
        ):
            result = await ontofuel_extract(
                str(ds.id),
                "datasource",
                db=db_session,
                llm_call=_empty_llm,
            )

        assert result == []

        # Explicit skip reason in the worker log: chunk count, content
        # length, and the per-chunk outcome must all be visible.
        skip_logs = [r for r in caplog.records if "no_parseable_properties" in r.getMessage()]
        assert skip_logs, (
            "empty extraction returned without any logged skip reason — "
            "silent zero (the NFM-4789 defect)"
        )
        message = skip_logs[0].getMessage()
        assert "chunks=1" in message
        assert f"content_len={len(_THIN_CONTENT)}" in message
        assert "outcomes=" in message
        assert "list:0" in message  # per-chunk outcome recorded

        # Best-effort marker fired with the DataSource UUID as ref.
        assert len(marker_calls) == 1
        marker_ref, reason = marker_calls[0]
        assert marker_ref == str(ds.id)
        assert "chunks=1" in reason

    @pytest.mark.asyncio
    async def test_single_short_segment_invokes_llm_once(
        self,
        db_session: AsyncSession,
    ) -> None:
        """AC3 regression guard: 967-char single segment is chunked into
        exactly 1 chunk and the LLM is invoked exactly once — there is no
        small-input fast skip in the chunk → extract path."""
        await _seed_published_ontology(db_session)
        ds = await _add_titled_datasource(db_session, content_md=_THIN_CONTENT)

        calls: list[dict[str, Any]] = []

        async def counting_llm(**kwargs: Any) -> list[dict[str, Any]]:
            calls.append(kwargs)
            return await _empty_llm(**kwargs)

        await ontofuel_extract(
            str(ds.id),
            "datasource",
            db=db_session,
            llm_call=counting_llm,
        )

        assert len(calls) == 1, (
            f"expected exactly one LLM call for the single short segment, "
            f"got {len(calls)} — a fast-skip or mis-chunking appeared"
        )

    @pytest.mark.asyncio
    async def test_nonempty_result_emits_no_skip(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Extraction success emits no skip reason and no marker."""
        await _seed_published_ontology(db_session)
        ds = await _add_titled_datasource(db_session, content_md=_THIN_CONTENT)

        marker_calls: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "nfm_db.services.extraction_pipeline._mark_extraction_no_properties",
            lambda ref, reason: marker_calls.append((ref, reason)),
        )

        with caplog.at_level(
            logging.WARNING,
            logger="nfm_db.services.extraction_pipeline",
        ):
            result = await ontofuel_extract(
                str(ds.id),
                "datasource",
                db=db_session,
                llm_call=_one_property_llm,
            )

        assert result  # non-empty post-processed extraction
        assert marker_calls == []
        assert not [r for r in caplog.records if "no_parseable_properties" in r.getMessage()]

    @pytest.mark.asyncio
    async def test_llm_failure_marker_uses_uuid_not_title(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """NFM-3358 repair: the failure marker must receive the DataSource
        UUID — the retitling reassignment previously handed it the display
        title, whose UUID parse fails, silently disabling the marker for
        every titled datasource."""
        await _seed_published_ontology(db_session)
        ds = await _add_titled_datasource(db_session, content_md=_THIN_CONTENT)

        failure_calls: list[str] = []
        monkeypatch.setattr(
            "nfm_db.services.extraction_pipeline._mark_extraction_failure",
            lambda ref, exc: failure_calls.append(ref),
        )

        async def raising_llm(**kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError("LLM returned empty content with finish_reason=length")

        result = await ontofuel_extract(
            str(ds.id),
            "datasource",
            db=db_session,
            llm_call=raising_llm,
        )

        assert result == []
        assert failure_calls == [str(ds.id)], (
            "failure marker must be keyed on the DataSource UUID, not the "
            "retitled display name (retitling silently disabled NFM-3358)"
        )
