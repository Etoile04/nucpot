"""Tests for the NFM-4742 LightRAGClient additions.

Covers:

* ``list_document_buckets`` projecting all three ``/documents`` shapes
* ``delete_document`` accepting 200/202/204/404 and rejecting unsafe ids
* the legacy 1.5.4 ``statuses`` envelope round-trips without loss
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nfm_db.services.lightrag_client import (
    LightRAGClient,
    LightRAGClientError,
)


def _make_client(*, get_payload: dict | list | None = None) -> LightRAGClient:
    """Build a LightRAGClient whose ``_http_client`` returns ``get_payload``."""
    response = MagicMock()
    response.json.return_value = get_payload or {}
    response.raise_for_status = MagicMock()
    response.status_code = 200
    response.text = ""
    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=response)
    http_client.delete = AsyncMock(return_value=response)
    client = LightRAGClient(host="localhost", port=9621)
    client._http_client = http_client  # type: ignore[attr-defined]
    return client


class TestListDocumentBuckets:
    """NFM-4742 §3.2 envelope extraction."""

    @pytest.mark.asyncio
    async def test_statuses_envelope_round_trip(self) -> None:
        payload = {
            "statuses": {
                "processed": [{"id": "data_source:a"}],
                "failed": [{"id": "data_source:b", "error_message": "boom"}],
                "processing": [{"id": "data_source:c"}],
            }
        }
        client = _make_client(get_payload=payload)
        envelope = await client.list_document_buckets()
        assert set(envelope.keys()) == {"processed", "failed", "processing"}
        assert envelope["processed"][0]["id"] == "data_source:a"
        assert envelope["failed"][0]["error_message"] == "boom"

    @pytest.mark.asyncio
    async def test_legacy_documents_envelope(self) -> None:
        payload = {"documents": [{"id": "data_source:a"}]}
        client = _make_client(get_payload=payload)
        envelope = await client.list_document_buckets()
        assert envelope == {"processed": [{"id": "data_source:a"}]}

    @pytest.mark.asyncio
    async def test_bare_list_envelope(self) -> None:
        payload = [{"id": "data_source:a"}, {"id": "data_source:b"}]
        client = _make_client(get_payload=payload)
        envelope = await client.list_document_buckets()
        assert envelope == {
            "processed": [
                {"id": "data_source:a"},
                {"id": "data_source:b"},
            ]
        }

    @pytest.mark.asyncio
    async def test_empty_envelope_returns_empty_dict(self) -> None:
        client = _make_client(get_payload={})
        envelope = await client.list_document_buckets()
        assert envelope == {}

    @pytest.mark.asyncio
    async def test_non_dict_rows_are_filtered(self) -> None:
        payload = {"statuses": {"processed": [{"id": "ok"}, "junk", None]}}
        client = _make_client(get_payload=payload)
        envelope = await client.list_document_buckets()
        assert envelope == {"processed": [{"id": "ok"}]}

    @pytest.mark.asyncio
    async def test_http_status_error_raises(self) -> None:
        import httpx

        response = MagicMock()
        response.status_code = 500
        response.text = "boom"
        http_client = MagicMock()
        http_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "boom", request=MagicMock(), response=response
            )
        )
        client = LightRAGClient(host="localhost", port=9621)
        client._http_client = http_client  # type: ignore[attr-defined]
        with pytest.raises(LightRAGClientError, match="HTTP 500"):
            await client.list_document_buckets()


class TestDeleteDocument:
    """NFM-4742 §3.3 single-doc delete."""

    @pytest.mark.asyncio
    async def test_2xx_is_success(self) -> None:
        for status in (200, 202, 204):
            response = MagicMock()
            response.status_code = status
            client = _make_client()
            client._http_client.delete = AsyncMock(return_value=response)  # type: ignore[attr-defined]
            await client.delete_document(doc_id="data_source:abc")

    @pytest.mark.asyncio
    async def test_404_is_idempotent_success(self) -> None:
        response = MagicMock()
        response.status_code = 404
        client = _make_client()
        client._http_client.delete = AsyncMock(return_value=response)  # type: ignore[attr-defined]
        await client.delete_document(doc_id="data_source:abc")

    @pytest.mark.asyncio
    async def test_5xx_raises(self) -> None:
        response = MagicMock()
        response.status_code = 500
        response.text = "internal"
        client = _make_client()
        client._http_client.delete = AsyncMock(return_value=response)  # type: ignore[attr-defined]
        with pytest.raises(LightRAGClientError, match="HTTP 500"):
            await client.delete_document(doc_id="data_source:abc")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("doc_id", ["", "../etc/passwd", "foo/bar", "foo..bar"])
    async def test_unsafe_doc_ids_rejected(self, doc_id: str) -> None:
        client = _make_client()
        with pytest.raises(ValueError):
            await client.delete_document(doc_id=doc_id)

    @pytest.mark.asyncio
    async def test_http_error_wraps_lightrag_error(self) -> None:
        import httpx

        client = _make_client()
        client._http_client.delete = AsyncMock(  # type: ignore[attr-defined]
            side_effect=httpx.ConnectError("nope")
        )
        with pytest.raises(LightRAGClientError, match="DELETE /documents/"):
            await client.delete_document(doc_id="data_source:abc")
