"""Async HTTP client for the LightRAG sidecar service (NFM-862).

Wraps the LightRAG REST API (default port 9621):
  POST /documents/text  — ingest text documents
  POST /query           — semantic / graph queries
  GET  /health          — service health check

Configuration via environment variables:
  NFM_LIGHTRAG_HOST  - LightRAG server host (default: "localhost")
  NFM_LIGHTRAG_PORT  - LightRAG server port (default: 9621)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 9621

# ---------------------------------------------------------------------------
# Timeouts (NFM-2565)
# ---------------------------------------------------------------------------
# Read and write paths have very different latency budgets, so they no longer
# share a single constant.
#
#   query  — serves a *synchronous user request* (api/v1/kg.py semantic search).
#            The caller already degrades to Postgres full-text search on
#            LightRAGClientError, so a short ceiling costs nothing but the
#            LightRAG answer; a long one costs the user a blank screen. The
#            previous shared 60s meant a stalled sidecar blocked the browser for
#            a full minute before the (fast, working) fallback ran — the real
#            cause of the long-standing "LightRAG query timeout" reports.
#
#   ingest — runs in a fire-and-forget background task
#            (kg_lightrag_sync.fire_ingest_to_lightrag). Nobody is waiting, and
#            entity extraction over a large document legitimately takes minutes.
#            Cutting this to seconds would turn working ingests into failures.
_DEFAULT_QUERY_TIMEOUT = 8.0
_DEFAULT_INGEST_TIMEOUT = 300.0

# Retained for backward compatibility: callers that pass ``timeout=`` explicitly
# still override both paths, and the transport-level default keeps the old
# value for any request that specifies neither.
_DEFAULT_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Env-var overrides (NFM-3404 / NFM-3425 — ADR §2.1 single source of truth)
# ---------------------------------------------------------------------------
# ``NFM_LIGHTRAG_QUERY_TIMEOUT_S`` binds the read/write/pool ceiling for the
# query path (defaulting to ``_DEFAULT_QUERY_TIMEOUT`` below); the connect
# ceiling was previously hardcoded at 5 s and now reads from
# ``NFM_LIGHTRAG_QUERY_CONNECT_S``. Both fall back to the constants above
# when the env vars are unset, preserving pre-NFM-3404 behaviour for
# existing callers and tests.
_ENV_QUERY_TIMEOUT_S = "NFM_LIGHTRAG_QUERY_TIMEOUT_S"
_ENV_QUERY_CONNECT_S = "NFM_LIGHTRAG_QUERY_CONNECT_S"
_DEFAULT_QUERY_CONNECT_S = 5.0


def _read_env_float(name: str, default: float) -> float:
    """Read a float from ``os.environ[name]``, falling back to ``default``.

    Empty / whitespace strings, ``None``, and non-numeric values all fall
    through to ``default``. This matches the contract documented in
    ADR-NFM-3404 §2.1: env vars are the source of truth, but the module
    constants are the safe fallback for callers that explicitly clear the
    environment.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "LightRAG env override %s=%r is not a float; using default %s",
            name,
            raw,
            default,
        )
        return default


def _resolve_query_timeout(
    explicit_query: float | None,
    explicit_legacy: float | None,
) -> float:
    """Resolution chain for the query read budget.

    Explicit ``query_timeout=`` wins over the legacy ``timeout=`` kwarg,
    which wins over the ``NFM_LIGHTRAG_QUERY_TIMEOUT_S`` env var, which wins
    over the module constant ``_DEFAULT_QUERY_TIMEOUT``. Each step is
    optional; an unset env var or empty string falls straight through to
    the next level (see ``_read_env_float``).
    """
    if explicit_query is not None:
        return explicit_query
    if explicit_legacy is not None:
        return explicit_legacy
    return _read_env_float(_ENV_QUERY_TIMEOUT_S, _DEFAULT_QUERY_TIMEOUT)


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class LightRAGClientError(Exception):
    """Raised when a LightRAG API call fails."""


class LightRAGConflictError(LightRAGClientError):
    """Raised when a LightRAG API call returns HTTP 409.

    NFM-4758 / NFM-4730-FixA: the sidecar returns ``409 Document storage
    already contains '<doc_id>'`` when an ingest tries to register a
    ``file_source`` marker that is already in ``lightrag_doc_status``.
    Callers that want idempotency on reextract can detect this specific
    status, delete the stale marker via :meth:`LightRAGClient.delete_document`,
    and retry the ingest.  Structured fields (``status_code``,
    ``response_body``, ``doc_id``) are preserved so the caller can
    dispatch on them without re-parsing the message text.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        response_body: str = "",
        doc_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.doc_id = doc_id


# ---------------------------------------------------------------------------
# Module-level config helper
# ---------------------------------------------------------------------------


def is_lightrag_configured() -> bool:
    """Check if LightRAG host is configured in environment."""
    return bool(os.environ.get("NFM_LIGHTRAG_HOST"))


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class LightRAGClient:
    """Async HTTP client for the LightRAG sidecar service.

    Usage::

        client = LightRAGClient()  # reads NFM_LIGHTRAG_HOST/PORT from env
        healthy = await client.health_check()
        result = await client.ingest(text="...", file_source="doc.pdf")
        answer = await client.query(query="What is UO2?")
    """

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        timeout: float | None = None,
        query_timeout: float | None = None,
        ingest_timeout: float | None = None,
    ) -> None:
        """Construct a client.

        Args:
            host: LightRAG host; falls back to ``NFM_LIGHTRAG_HOST``.
            port: LightRAG port; falls back to ``NFM_LIGHTRAG_PORT``.
            timeout: Legacy single-value override. When given it applies to
                **both** query and ingest, preserving the pre-NFM-2565
                behaviour for existing callers and tests.
            query_timeout: Per-request ceiling for :meth:`query` and
                :meth:`health_check`. Defaults to 8s.
            ingest_timeout: Per-request ceiling for :meth:`ingest`.
                Defaults to 300s.
        """
        self.host = host or os.environ.get("NFM_LIGHTRAG_HOST", _DEFAULT_HOST)
        self.port = port or int(os.environ.get("NFM_LIGHTRAG_PORT", str(_DEFAULT_PORT)))

        # An explicit ``timeout=`` collapses both paths onto that value.
        # NFM-3404 §2.1: when neither kwarg is passed, the query budget also
        # honours ``NFM_LIGHTRAG_QUERY_TIMEOUT_S`` (falls back to the module
        # constant ``_DEFAULT_QUERY_TIMEOUT`` if unset).
        self.query_timeout = _resolve_query_timeout(query_timeout, timeout)
        self.ingest_timeout = (
            ingest_timeout
            if ingest_timeout is not None
            else timeout
            if timeout is not None
            else _DEFAULT_INGEST_TIMEOUT
        )
        # ``self.timeout`` stays the transport-level default so attribute reads
        # in existing code keep working.
        self.timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT

        self._base_url = f"http://{self.host}:{self.port}"

        # NFM-4719: record the loop on which this client was created so the
        # shared lifecycle helper can detect Celery worker re-entry (each
        # task spins up a fresh ``asyncio.run`` loop, but the module-level
        # singleton client is bound to whichever loop first called
        # ``get_shared_lightrag_client()``).  When the loop changes, the
        # underlying ``httpx.AsyncClient`` is bound to a closed selector
        # and every POST raises ``RuntimeError: Event loop is closed``,
        # which the previous ``except Exception`` in
        # ``ingest_kg_to_lightrag`` silently swallowed — leading to the
        # prod "inline ingest done (nodes=N edges=M)" log + empty VDB
        # symptom (NFM-4680 / NFM-4717).
        try:
            self._loop: asyncio.AbstractEventLoop | None = (
                asyncio.get_running_loop()
            )
        except RuntimeError:
            # Constructed off-loop (e.g. test setup). The lifecycle helper
            # will rebind on first use.
            self._loop = None

        # NFM-3367 / NFM-3404: split the transport-level timeout so a stalled
        # TCP handshake cannot blow past the per-request query budget.
        #
        # ``read``/``write``/``pool`` are slaved to ``self.query_timeout`` —
        # not the legacy ``self.timeout`` — because the query budget is the
        # *binding* ceiling for a synchronous user request. When a caller
        # passes the legacy ``timeout=`` kwarg, ``self.query_timeout`` has
        # already absorbed that value (see ``__init__``), so the legacy
        # path keeps working too.
        #
        # ``connect`` is bounded so the TCP handshake itself cannot consume
        # the whole query budget; it honours ``NFM_LIGHTRAG_QUERY_CONNECT_S``
        # (ADR §2.1) and falls back to ``_DEFAULT_QUERY_CONNECT_S`` (5 s).
        self._connect_timeout = _read_env_float(
            _ENV_QUERY_CONNECT_S, _DEFAULT_QUERY_CONNECT_S
        )
        self._http_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                connect=self._connect_timeout,
                read=self.query_timeout,
                write=self.query_timeout,
                pool=self.query_timeout,
            ),
        )

    @property
    def base_url(self) -> str:
        """The base URL for the LightRAG service."""
        return self._base_url

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Check if the LightRAG service is healthy.

        Returns True if the service responds with HTTP 200, False otherwise.
        Connection errors and non-200 responses both return False.
        """
        try:
            response = await self._http_client.get(
                "/health",
                timeout=self.query_timeout,
            )
            return response.status_code == 200
        except httpx.HTTPError:
            logger.warning(
                "LightRAG health check failed: host=%s, port=%d",
                self.host,
                self.port,
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Documents — NFM-4539 RAG-D
    # ------------------------------------------------------------------

    async def list_indexed_documents(self) -> list[str]:
        """Return the set of ``data_source:<uuid>`` markers currently indexed.

        NFM-4539 RAG-D §4.2: the daily reconciliation task pulls the full
        completed-literature list and diffs it against the LightRAG index.
        We only need a stable set of identity markers; the sidecar's
        ``/documents`` endpoint returns the raw payload, and we project
        down to the ``data_source:<uuid>`` tag the ``ingest()`` payload
        stamps on each document.

        NFM-4636: LightRAG 1.5.4 (the prod sidecar build) answers with a
        ``{"statuses": {"processed": [...], "analyzing": [...], ...}}``
        envelope, NOT the bare array / ``{"documents": [...]}`` shapes
        this method originally handled — so it silently returned ``[]``
        and every completed literature read as drift.  Additionally, a
        document only counts as *indexed* once its analysis pipeline
        finished (status ``processed``); rows still ``analyzing`` /
        ``processing`` or ``failed`` are not retrievable from the index
        and must not reconcile as covered.
        """
        try:
            response = await self._http_client.get(
                "/documents",
                timeout=self.query_timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LightRAGClientError(
                f"LightRAG /documents failed: HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LightRAGClientError(
                f"LightRAG /documents failed: {exc}"
            ) from exc

        body = response.json()
        rows: list[Any] = self._extract_document_rows(body)
        markers: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            source = (
                row.get("data_source")
                or row.get("file_source")
                or row.get("file_path")
            )
            if source:
                markers.add(str(source))
            # Fallback: some LightRAG versions only carry ``id`` shaped as
            # ``data_source:<uuid>``; record both shapes.
            rid = row.get("id")
            if isinstance(rid, str) and rid.startswith("data_source:"):
                markers.add(rid)
        return sorted(markers)

    @staticmethod
    def _extract_document_rows(body: Any) -> list[Any]:
        """Project the three ``/documents`` envelope shapes to row dicts.

        * bare JSON array (oldest builds) — all rows, no status known;
        * ``{"documents": [...]}`` (older builds) — all rows;
        * ``{"statuses": {<status>: [...]}}`` (1.5.4, prod) — only rows
          whose bucket means *analysis finished and the doc is in the
          index* (``processed``).  ``analyzing`` / ``processing`` rows
          are in-flight and ``failed`` rows are absent from the index,
          so reconciling them as covered would lie.
        """
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            documents = body.get("documents")
            if isinstance(documents, list):
                return documents
            statuses = body.get("statuses")
            if isinstance(statuses, dict):
                rows: list[Any] = []
                for status, status_rows in statuses.items():
                    if status == "processed" and isinstance(status_rows, list):
                        rows.extend(status_rows)
                return rows
        return []

    async def delete_document(self, doc_id: str) -> dict[str, Any]:
        """Delete a single document from the LightRAG sidecar.

        NFM-4758 / NFM-4730-FixA: used by the reextract idempotency path
        to evict a stale ``data_source:<uuid>`` (or ``kg_pipeline``)
        marker from ``lightrag_doc_status`` after the ingest returns 409
        ``Document storage already contains '<doc_id>'``.  Hits the
        upstream ``DELETE /documents?doc_id=<doc_id>`` endpoint.

        Args:
            doc_id: The document identifier — for KG auto-ingest this is
                the ``source`` marker stamped on the document
                (``data_source:<uuid>`` or ``kg_pipeline``).

        Returns:
            Parsed JSON response from the sidecar (typically
            ``{"status": "deleted", ...}``).

        Raises:
            LightRAGClientError: On transport failure or non-2xx response.
                A 404 (already absent) is treated as success — the caller's
                invariant ``the marker is gone`` still holds, and the
                sidecar's deletion of an unknown id is a no-op in practice.
        """
        try:
            response = await self._http_client.delete(
                "/documents",
                params={"doc_id": doc_id},
                timeout=self.query_timeout,
            )
        except httpx.HTTPError as exc:
            raise LightRAGClientError(
                f"LightRAG /documents DELETE failed: {exc}"
            ) from exc

        # Treat 404 as already-deleted so the caller's invariant
        # ``the marker is gone before the retry`` still holds without
        # surfacing a spurious error from a doc the sidecar has never
        # seen (e.g. the marker was wiped out-of-band).
        if response.status_code == 404:
            return {"status": "deleted", "doc_id": doc_id, "already_absent": True}

        if response.status_code >= 400:
            raise LightRAGClientError(
                f"LightRAG /documents DELETE failed: HTTP {response.status_code} - {response.text}"
            )

        # The sidecar may return an empty body for 204 No Content.
        try:
            return response.json()
        except ValueError:
            return {"status": "deleted", "doc_id": doc_id}

    # ------------------------------------------------------------------
    # NFM-4742 F-3 §3 — bucket enumeration + delete for replay
    # ------------------------------------------------------------------

    async def list_document_buckets(self) -> dict[str, list[dict[str, Any]]]:
        """Return the raw ``/documents`` ``statuses`` envelope.

        Unlike :meth:`list_indexed_documents` (which projects only the
        ``processed`` bucket for index-coverage reconciliation), this
        returns **every** bucket the sidecar reports so the audit task
        can count ``failed`` / ``processing`` / ``pending`` rows and
        classify failure reasons (NFM-4742 F-3 §3.2).

        NFM-4636: LightRAG 1.5.4 (the prod sidecar build) answers with
        a ``{"statuses": {<status>: [...]}}`` envelope, NOT the bare
        array / ``{"documents": [...]}`` shapes; we project both for
        forward-compat with older builds (so a stale deployment still
        reports ``processed`` rows under the ``processed`` synthetic
        bucket) and so unit tests can drive the legacy shapes directly.

        Returns an empty dict when the sidecar is unreachable so the
        caller can decide whether to surface an ``error`` audit row or
        silently no-op.
        """
        try:
            response = await self._http_client.get(
                "/documents",
                timeout=self.query_timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LightRAGClientError(
                f"LightRAG /documents failed: HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LightRAGClientError(
                f"LightRAG /documents failed: {exc}"
            ) from exc

        body = response.json()
        return self._extract_statuses_envelope(body)

    @staticmethod
    def _extract_statuses_envelope(body: Any) -> dict[str, list[dict[str, Any]]]:
        """Normalise the three ``/documents`` shapes to a status→rows map.

        Unknown / empty responses return an empty dict so the audit task
        can still log a structured ``bucket_counts`` audit row with
        zeros instead of crashing on a sidecar format change.
        """
        if isinstance(body, list):
            # Oldest builds — every row is implicitly ``processed``; we
            # keep that semantic so the bucket report matches what the
            # sidecar actually has.
            return {"processed": [r for r in body if isinstance(r, dict)]}
        if isinstance(body, dict):
            statuses = body.get("statuses")
            if isinstance(statuses, dict):
                return {
                    str(status): [r for r in rows if isinstance(r, dict)]
                    for status, rows in statuses.items()
                    if isinstance(rows, list)
                }
            documents = body.get("documents")
            if isinstance(documents, list):
                return {
                    "processed": [r for r in documents if isinstance(r, dict)]
                }
        return {}

    async def delete_document_by_id(self, *, doc_id: str) -> None:
        """Delete a single LightRAG document by id.

        Used by the F-3 processing reaper (NFM-4742 §3) to evict
        ``processing`` rows stranded ``>=24h`` so the canonical ingest
        path can re-attempt; also used by operator-driven replay when
        a doc is stuck in ``failed`` with an empty error_message and
        needs a fresh ingest cycle to surface the real failure.

        LightRAG 1.5.4 ``DELETE /documents/{id}`` is the documented
        end-point.  The sidecar may respond 200 (success) or 404
        (already gone, idempotent) — both are treated as success.
        """
        if not doc_id:
            raise ValueError("doc_id is required")
        # Reject anything that isn't a safe slug — LightRAG ids look like
        # ``data_source:<uuid>`` or ``nfm-4505-fresh-<uuid>`` and we don't
        # want a malicious marker smuggling path traversal into the URL.
        if "/" in doc_id or ".." in doc_id:
            raise ValueError(f"unsafe doc_id: {doc_id!r}")
        try:
            response = await self._http_client.delete(
                f"/documents/{doc_id}",
                timeout=self.query_timeout,
            )
        except httpx.HTTPError as exc:
            raise LightRAGClientError(
                f"LightRAG DELETE /documents/{doc_id} failed: {exc}"
            ) from exc
        if response.status_code in (200, 202, 204, 404):
            return
        raise LightRAGClientError(
            f"LightRAG DELETE /documents/{doc_id} failed: "
            f"HTTP {response.status_code} - {response.text}"
        )

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    async def ingest(
        self,
        *,
        text: str,
        file_source: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a text document into the LightRAG knowledge graph.

        Args:
            text: Document text content to ingest.
            file_source: Optional source identifier.

        Returns:
            Parsed JSON response from LightRAG (includes track_id).

        Raises:
            LightRAGClientError: On server errors or connection failures.
        """
        payload: dict[str, Any] = {"text": text}
        if file_source is not None:
            payload["file_source"] = file_source

        try:
            response = await self._http_client.post(
                "/documents/text",
                json=payload,
                timeout=self.ingest_timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            # NFM-4758 / NFM-4730-FixA: 409 carries the structured
            # ``Document storage already contains '<doc_id>'`` body and
            # signals an idempotent re-extract, NOT a transport failure.
            # Raise a typed subclass so callers can detect the conflict
            # and retry-after-DELETE without parsing the message text.
            if exc.response.status_code == 409:
                response_text = exc.response.text
                raise LightRAGConflictError(
                    f"LightRAG ingest failed: HTTP 409 - {response_text}",
                    status_code=409,
                    response_body=response_text,
                    doc_id=file_source,
                ) from exc
            raise LightRAGClientError(
                f"LightRAG ingest failed: HTTP {exc.response.status_code} - {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LightRAGClientError(f"LightRAG ingest failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def query(
        self,
        *,
        query: str,
        mode: str = "mix",
        include_references: bool = False,
    ) -> dict[str, Any]:
        """Query the LightRAG knowledge graph.

        Args:
            query: Natural language query.
            mode: Query mode (local, global, hybrid, mix, naive).
            include_references: Whether to include source references.

        Returns:
            Parsed JSON response with answer and optional references.

        Raises:
            LightRAGClientError: On server errors or connection failures.
        """
        payload: dict[str, Any] = {
            "query": query,
            "mode": mode,
            "include_references": include_references,
        }

        try:
            response = await self._http_client.post(
                "/query",
                json=payload,
                timeout=self.query_timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise LightRAGClientError(
                f"LightRAG query failed: HTTP {exc.response.status_code} - {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LightRAGClientError(f"LightRAG query failed: {exc}") from exc

    async def query_data(
        self,
        *,
        query: str,
        mode: str = "mix",
    ) -> dict[str, Any]:
        """Query the LightRAG index for structured retrieval data only.

        NFM-4804 item 2: hits the sidecar's ``POST /query/data`` endpoint
        (LightRAG 1.5.4 ``aquery_data`` — ``only_need_context=True``, no LLM
        generation) which ALWAYS includes a ``data.references`` array.  This
        is the refill vehicle for the cache-hit degradation where
        ``POST /query`` replays the cached answer text with an empty
        reference list: the answer stays cached-fast while citations are
        rebuilt from fresh retrieval at a fraction of the LLM cost.

        Args:
            query: Natural language query.
            mode: Query mode (local, global, hybrid, mix, naive).

        Returns:
            Parsed JSON envelope, e.g.::

                {
                    "status": "success",
                    "message": "...",
                    "data": {
                        "entities": [...],
                        "relationships": [...],
                        "chunks": [...],
                        "references": [{"reference_id": "1", "file_path": "..."}],
                    },
                }

        Raises:
            LightRAGClientError: On server errors or connection failures.
        """
        payload: dict[str, Any] = {
            "query": query,
            "mode": mode,
        }

        try:
            response = await self._http_client.post(
                "/query/data",
                json=payload,
                timeout=self.query_timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise LightRAGClientError(
                f"LightRAG /query/data failed: HTTP {exc.response.status_code} - {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LightRAGClientError(f"LightRAG /query/data failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http_client.aclose()

    async def __aenter__(self) -> LightRAGClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
