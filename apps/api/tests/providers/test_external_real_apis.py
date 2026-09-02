"""Real-API integration tests for ``ExternalDataSourceClient`` (BUG-24 / NFM-3875).

The OpenKIM and Materials Project queries in ``external_data_sources.py`` used
to be placeholders that returned a hard-coded dict with ``"note": "Placeholder"``.
This suite verifies they now hit the real services:

* OpenKIM   — ``POST https://query.openkim.org/api/get_available_models``
  (anonymous; uses ``species=[...]`` + ``model_interface=["mo"]``).
* Materials Project — ``GET https://api.materialsproject.org/materials/summary/``
  with the ``X-API-KEY`` header set from ``MATERIALS_PROJECT_API_KEY``.

No real network calls are made — every test injects an ``httpx.AsyncClient``
backed by a mock transport.  The tests verify request shape (URL, headers,
body) and response mapping; they intentionally avoid asserting on payload
shape the dispatcher does not own.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import Request, Response

from nfm_db.services.external_data_sources import (
    ExternalDataSource,
    ExternalDataSourceClient,
    RateLimiter,
    _query_cache,
    _rate_limiters,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _RecordingTransport:
    """httpx MockTransport that records the request and replies from a fixture."""

    def __init__(
        self,
        *,
        response_status: int = 200,
        response_json: Any = None,
        response_text: str | None = None,
    ) -> None:
        self.requests: list[Request] = []
        self.response_status = response_status
        self.response_json = response_json
        self.response_text = response_text

    async def handle_async_request(self, request: Request) -> Response:
        self.requests.append(request)
        if self.response_text is not None:
            return Response(self.response_status, text=self.response_text)
        return Response(self.response_status, json=self.response_json)


def _build_client(transport: httpx.AsyncBaseTransport) -> ExternalDataSourceClient:
    """Build a client with an injected httpx transport and a fixed API key."""
    return ExternalDataSourceClient(
        timeout=5.0,
        api_key="test-mp-key-32chars-abcdefghijkl",
        client=httpx.AsyncClient(timeout=5.0, transport=transport),
    )


@pytest.fixture
def _clean_globals() -> None:
    """Reset the dispatcher's module-level cache + rate limiters per test."""
    _query_cache.clear()
    for source in ExternalDataSource:
        _rate_limiters[source] = RateLimiter(rate=60)
    yield
    _query_cache.clear()


# ---------------------------------------------------------------------------
# OpenKIM real-API tests
# ---------------------------------------------------------------------------


class TestOpenKIMRealAPI:
    @pytest.mark.asyncio
    async def test_post_targets_get_available_models(self, _clean_globals) -> None:
        transport = _RecordingTransport(
            response_json=[
                "EAM_Dynamo_ErcolessiAdams_1994_Al__MO_123629422045_006",
                "EAM_Dynamo_AngeloMoodyBaskes_1995_NiAlH__MO_418978237058_006",
            ],
        )
        client = _build_client(transport)
        try:
            result = await client.query_openkim("U", None)
        finally:
            await client.aclose()

        assert result is not None
        assert result["source"] == "openkim"
        assert result["species"] == "U"
        # POST to the documented query.openkim.org endpoint, no auth header
        assert len(transport.requests) == 1
        req = transport.requests[0]
        assert req.method == "POST"
        assert req.url.host == "query.openkim.org"
        assert req.url.path.endswith("/get_available_models")
        assert "authorization" not in {k.lower() for k in req.headers}

    @pytest.mark.asyncio
    async def test_species_filter_sent_in_form_body(self, _clean_globals) -> None:
        transport = _RecordingTransport(response_json=[])
        client = _build_client(transport)
        try:
            await client.query_openkim("UO2", None)
        finally:
            await client.aclose()

        req = transport.requests[0]
        # httpx encodes form data as application/x-www-form-urlencoded
        body = req.content.decode()
        assert "species=" in body
        # Element filter must round-trip as a JSON-encoded list (per OpenKIM API)
        assert "%5B" in body or "[" in body
        # model_interface must be present, set to "mo"
        assert "model_interface" in body

    @pytest.mark.asyncio
    async def test_kim_ids_mapped_into_potentials(self, _clean_globals) -> None:
        kim_ids = [
            "EAM_Dynamo_ErcolessiAdams_1994_Al__MO_123629422045_006",
            "EAM_Dynamo_AngeloMoodyBaskes_1995_NiAlH__MO_418978237058_006",
        ]
        transport = _RecordingTransport(response_json=kim_ids)
        client = _build_client(transport)
        try:
            result = await client.query_openkim("Al", None)
        finally:
            await client.aclose()

        assert result is not None
        assert isinstance(result["potentials"], list)
        assert len(result["potentials"]) == 2
        # Each entry carries the KIM ID; no "Placeholder" note survives
        assert all("MO_" in p["kim_id"] for p in result["potentials"])
        assert all(p["source"] == "openkim" for p in result["potentials"])
        assert "note" not in result or "Placeholder" not in result.get("note", "")

    @pytest.mark.asyncio
    async def test_empty_response_is_empty_list_not_none(self, _clean_globals) -> None:
        transport = _RecordingTransport(response_json=[])
        client = _build_client(transport)
        try:
            result = await client.query_openkim("Xx", None)
        finally:
            await client.aclose()

        # Real-API empty → structured empty response, NOT None
        assert result is not None
        assert result["potentials"] == []

    @pytest.mark.asyncio
    async def test_connect_error_returns_empty_potentials(self, _clean_globals) -> None:
        class _FailingTransport:
            async def handle_async_request(self, request: Request) -> Response:
                raise httpx.ConnectError("connection refused")

        client = _build_client(_FailingTransport())
        try:
            result = await client.query_openkim("U", None)
        finally:
            await client.aclose()

        # Degrade to a structured empty response so callers don't have to
        # special-case None for "API down".
        assert result is not None
        assert result["potentials"] == []

    @pytest.mark.asyncio
    async def test_non_list_response_degrades_to_empty(self, _clean_globals) -> None:
        # OpenKIM returns {"error": "..."} on bad input; we must not crash
        transport = _RecordingTransport(response_json={"error": "missing species"})
        client = _build_client(transport)
        try:
            result = await client.query_openkim("U", None)
        finally:
            await client.aclose()

        assert result is not None
        assert result["potentials"] == []


# ---------------------------------------------------------------------------
# Materials Project real-API tests
# ---------------------------------------------------------------------------


class TestMaterialsProjectRealAPI:
    @pytest.mark.asyncio
    async def test_get_targets_materials_summary(self, _clean_globals) -> None:
        transport = _RecordingTransport(
            response_json={
                "data": [{"material_id": "mp-aaabxgaz", "formula": "UO2"}],
                "meta": {"total_doc": 1, "max_limit": 1000},
            },
        )
        client = _build_client(transport)
        try:
            result = await client.query_materials_project("UO2", None)
        finally:
            await client.aclose()

        assert result is not None
        assert result["source"] == "materials_project"
        assert result["formula"] == "UO2"
        assert len(transport.requests) == 1
        req = transport.requests[0]
        assert req.method == "GET"
        assert req.url.host == "api.materialsproject.org"
        assert req.url.path.startswith("/materials/summary")

    @pytest.mark.asyncio
    async def test_x_api_key_header_is_sent(self, _clean_globals) -> None:
        transport = _RecordingTransport(
            response_json={"data": [], "meta": {"total_doc": 0}},
        )
        client = _build_client(transport)
        try:
            await client.query_materials_project("UO2", None)
        finally:
            await client.aclose()

        req = transport.requests[0]
        assert req.headers.get("x-api-key") == "test-mp-key-32chars-abcdefghijkl"

    @pytest.mark.asyncio
    async def test_url_uses_formula_limit_and_fields(self, _clean_globals) -> None:
        transport = _RecordingTransport(
            response_json={"data": [], "meta": {"total_doc": 0}},
        )
        client = _build_client(transport)
        try:
            await client.query_materials_project("ZrO2", None)
        finally:
            await client.aclose()

        req = transport.requests[0]
        # All three filters must be present on the URL
        params = dict(req.url.params)
        assert params.get("formula") == "ZrO2"
        assert params.get("_limit") is not None
        # _fields must include at least material_id and formula
        assert "material_id" in params.get("_fields", "")

    @pytest.mark.asyncio
    async def test_data_array_mapped_into_materials(self, _clean_globals) -> None:
        transport = _RecordingTransport(
            response_json={
                "data": [
                    {
                        "material_id": "mp-aaabxgaz",
                        "formula": "UO2",
                        "elements": ["O", "U"],
                        "band_gap": 0.0,
                        "energy_per_atom": -10.687,
                        "density": 9.46,
                    },
                    {
                        "material_id": "mp-abcdefghi",
                        "formula": "UO2",
                        "elements": ["O", "U"],
                        "band_gap": 1.2,
                    },
                ],
                "meta": {"total_doc": 2},
            },
        )
        client = _build_client(transport)
        try:
            result = await client.query_materials_project("UO2", None)
        finally:
            await client.aclose()

        assert result is not None
        assert isinstance(result["materials"], list)
        assert len(result["materials"]) == 2
        ids = {m["material_id"] for m in result["materials"]}
        assert ids == {"mp-aaabxgaz", "mp-abcdefghi"}
        assert all(m["source"] == "materials_project" for m in result["materials"])

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty_materials(self, _clean_globals) -> None:
        transport = _RecordingTransport(
            response_json={"data": [], "meta": {"total_doc": 0}},
        )
        client = _build_client(transport)
        try:
            result = await client.query_materials_project("Xx", None)
        finally:
            await client.aclose()

        assert result is not None
        assert result["materials"] == []

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_none(self, _clean_globals, monkeypatch) -> None:
        # Remove the env var AND the explicit override → dispatcher must
        # short-circuit and report the missing configuration.
        monkeypatch.delenv("MATERIALS_PROJECT_API_KEY", raising=False)
        transport = _RecordingTransport(
            response_json={"data": [], "meta": {"total_doc": 0}},
        )
        client = ExternalDataSourceClient(
            timeout=5.0,
            api_key=None,
            client=httpx.AsyncClient(timeout=5.0, transport=transport),
        )
        try:
            result = await client.query_materials_project("UO2", None)
        finally:
            await client.aclose()

        assert result is None
        # No HTTP request must have been made
        assert transport.requests == []

    @pytest.mark.asyncio
    async def test_api_key_read_from_env_when_not_injected(
        self, _clean_globals, monkeypatch
    ) -> None:
        monkeypatch.setenv("MATERIALS_PROJECT_API_KEY", "env-key-32chars-xyz12345")
        transport = _RecordingTransport(
            response_json={"data": [], "meta": {"total_doc": 0}},
        )
        # No explicit api_key → constructor should pick up the env var
        client = ExternalDataSourceClient(
            timeout=5.0,
            client=httpx.AsyncClient(timeout=5.0, transport=transport),
        )
        try:
            await client.query_materials_project("UO2", None)
        finally:
            await client.aclose()

        req = transport.requests[0]
        assert req.headers.get("x-api-key") == "env-key-32chars-xyz12345"

    @pytest.mark.asyncio
    async def test_401_logs_key_regen_hint_and_returns_none(self, _clean_globals, caplog) -> None:
        transport = _RecordingTransport(
            response_status=401,
            response_json={"detail": "Unauthorized"},
        )
        client = _build_client(transport)
        try:
            with caplog.at_level("ERROR", logger="nfm_db.services.external_data_sources"):
                result = await client.query_materials_project("UO2", None)
        finally:
            await client.aclose()

        # 401 → None, and the operator must see a clear hint to regenerate the key.
        assert result is None
        joined = "\n".join(rec.message for rec in caplog.records)
        assert "MATERIALS_PROJECT_API_KEY" in joined or "materialsproject.org" in joined

    @pytest.mark.asyncio
    async def test_connect_error_returns_empty_materials(self, _clean_globals) -> None:
        class _FailingTransport:
            async def handle_async_request(self, request: Request) -> Response:
                raise httpx.ConnectError("dns resolution failed")

        client = _build_client(_FailingTransport())
        try:
            result = await client.query_materials_project("UO2", None)
        finally:
            await client.aclose()

        assert result is not None
        assert result["materials"] == []

    @pytest.mark.asyncio
    async def test_non_json_response_degrades(self, _clean_globals) -> None:
        transport = _RecordingTransport(
            response_status=200,
            response_text="<html>some maintenance page</html>",
        )
        client = _build_client(transport)
        try:
            result = await client.query_materials_project("UO2", None)
        finally:
            await client.aclose()

        # Unexpected body → structured empty response (don't crash)
        assert result is not None
        assert result["materials"] == []
