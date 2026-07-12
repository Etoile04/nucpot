"""Regression tests for FastAPI lifespan wiring (NFM-1222 HIGH-2 / NFM-1245).

Round-2 code review rejected commit e6e1900 because the commit message claimed
main.py gained a lifespan handler that closes the shared LightRAG client, but
``git show --stat e6e1900`` listed only kg_lightrag_sync.py, rag_provider.py,
and tests — main.py was untouched and ``close_lightrag_client()`` had no caller.

These tests drive the ASGI lifespan protocol directly so a regression that
removes or breaks the wiring in ``nfm_db.main`` fails the suite immediately.
"""

from __future__ import annotations

import importlib
import types
from unittest.mock import AsyncMock, patch

import pytest


def _load_main_module() -> types.ModuleType:
    """Import nfm_db.main so we can inspect the module-level app + lifespan."""
    return importlib.import_module("nfm_db.main")


class TestMainLifespanWiring:
    """Verify main.py wires a lifespan that closes the LightRAG client."""

    def test_main_source_passes_lifespan_to_fastapi(self) -> None:
        """main.py must pass ``lifespan=`` to ``FastAPI(...)`` and call close_lightrag_client().

        Round-2 review rejected commit e6e1900 because the commit message claimed
        the lifespan was added but ``git show --stat e6e1900`` listed only
        kg_lightrag_sync.py / rag_provider.py / tests — main.py was untouched.
        This source-level test fails the same way a Code Reviewer would catch it.
        """
        from pathlib import Path

        main_path = Path(__file__).resolve().parents[1] / "src" / "nfm_db" / "main.py"
        text = main_path.read_text(encoding="utf-8")

        assert "lifespan=" in text, (
            "main.py does not pass lifespan=<fn> to FastAPI(...) — "
            "shared httpx.AsyncClient will leak on shutdown (NFM-1245 HIGH-2)."
        )
        assert "close_lightrag_client" in text, (
            "main.py does not reference close_lightrag_client() — "
            "lifespan exists but doesn't actually close the shared client."
        )
        assert "await close_lightrag_client" in text, (
            "main.py references close_lightrag_client but does not await it "
            "in the lifespan — async close would be a no-op."
        )

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_calls_close_lightrag_client(self) -> None:
        """Driving the ASGI lifespan startup→shutdown must await close_lightrag_client()."""
        main = _load_main_module()

        close_mock = AsyncMock(return_value=None)

        # Patch at the importer's location: main.py does
        # `from nfm_db.services.lightrag_lifecycle import close_lightrag_client`,
        # so the binding the lifespan coroutine holds is `nfm_db.main.close_lightrag_client`.
        with patch(
            "nfm_db.main.close_lightrag_client",
            close_mock,
        ):
            lifespan_cm = main.app.router.lifespan_context(main.app)
            async with lifespan_cm:
                # Application is "running" — the client may or may not exist.
                pass
            # On exit (shutdown), close_lightrag_client must have been awaited
            # exactly once.
            assert close_mock.await_count == 1, (
                "close_lightrag_client was not awaited during lifespan shutdown — "
                "NFM-1245 HIGH-2 still open."
            )


class TestLifespanModuleImport:
    """Sanity: importing main.py must succeed even if the lifespan is exercised."""

    def test_main_module_imports(self) -> None:
        main = _load_main_module()
        assert hasattr(main, "app")
        assert main.app is not None
