"""Tests for the NFM-4746 ``LightRAGClient.list_document_buckets`` method.

These are focused unit tests for the HTTP client wrapper — the
end-to-end parity between beat output and the direct query is
covered by ``test_rag_audit_beat_health``'s
``test_bucket_counts_match_lightrag_doc_status_query`` test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nfm_db.services.lightrag_client import LightRAGClient, LightRAGClientError


def _make_client(response_body: object) -> LightRAGClient:
    """Construct a client whose ``GET /documents`` returns ``response_body``."""
    client = LightRAGClient(host="localhost", port=9621)
    response = MagicMock()
    response.json.return_value = response_body
    response.raise_for_status = MagicMock()
    client._http_client.get = AsyncMock(return_value=response)
    return client


@pytest.mark.asyncio
async def test_list_document_buckets_returns_statuses_envelope() -> None:
    """LightRAG 1.5.4 ``{"statuses": {...}}`` envelope round-trips intact."""
    body = {
        "statuses": {
            "processed": [{"id": "p1"}, {"id": "p2"}],
            "processing": [{"id": "pr1"}],
            "failed": [{"id": "f1", "error_message": "Identical content already exists"}],
        }
    }
    client = _make_client(body)
    envelope = await client.list_document_buckets()
    assert envelope == {
        "processed": [{"id": "p1"}, {"id": "p2"}],
        "processing": [{"id": "pr1"}],
        "failed": [{"id": "f1", "error_message": "Identical content already exists"}],
    }


@pytest.mark.asyncio
async def test_list_document_buckets_handles_legacy_array_shape() -> None:
    """Oldest builds returned a bare JSON array — every row is ``processed``."""
    body = [{"id": "a"}, {"id": "b"}]
    client = _make_client(body)
    envelope = await client.list_document_buckets()
    assert envelope == {"processed": [{"id": "a"}, {"id": "b"}]}


@pytest.mark.asyncio
async def test_list_document_buckets_handles_legacy_documents_shape() -> None:
    """Older builds wrapped rows in ``{"documents": [...]}``."""
    body = {"documents": [{"id": "x"}]}
    client = _make_client(body)
    envelope = await client.list_document_buckets()
    assert envelope == {"processed": [{"id": "x"}]}


@pytest.mark.asyncio
async def test_list_document_buckets_empty_envelope_on_unknown_shape() -> None:
    """Unknown response → empty dict, no crash."""
    body = {"unexpected": "shape"}
    client = _make_client(body)
    envelope = await client.list_document_buckets()
    assert envelope == {}


@pytest.mark.asyncio
async def test_list_document_buckets_filters_non_dict_rows() -> None:
    """``statuses`` rows that aren't dicts are silently dropped."""
    body = {
        "statuses": {
            "processed": [{"id": "ok"}, "garbage", None],
            "failed": [42, {"id": "ok2"}],
        }
    }
    client = _make_client(body)
    envelope = await client.list_document_buckets()
    assert envelope == {
        "processed": [{"id": "ok"}],
        "failed": [{"id": "ok2"}],
    }


@pytest.mark.asyncio
async def test_list_document_buckets_raises_on_http_error() -> None:
    """``HTTPError`` propagates as ``LightRAGClientError``."""
    client = LightRAGClient(host="localhost", port=9621)
    client._http_client.get = AsyncMock(
        side_effect=LightRAGClientError("LightRAG /documents failed: connection refused")
    )
    with pytest.raises(LightRAGClientError):
        await client.list_document_buckets()
