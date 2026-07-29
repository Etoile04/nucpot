"""MinerU PDF parsing client (NFM-MINERU-1).

Wraps the MinerU 精准解析 API (v4) for structured PDF → Markdown conversion
with formula recognition, table extraction, and reading-order layout
analysis. Used as the primary PDF parser for the literature pipeline
(`_parse_pdf_to_markdown` in :mod:`nfm_db.services.literature_service`),
with a PyMuPDF fallback for resilience.

API reference: https://mineru.net/apiManage/docs

Flow:
    1. POST /api/v4/file-urls/batch  → 申请上传 URL (signed PUT to OSS)
    2. PUT  <signed_url>             → 上传 PDF 字节
    3. GET  /api/v4/extract/task/{task_id}  → 轮询直到 done/failed
    4. GET  <full_zip_url>           → 下载包含 full.md 的 zip
    5. 解压取 full.md 内容

Configuration (env vars, read directly like ``vision_client`` does):

* ``MINERU_ENABLED``            — "true"/"1" to enable (default "true")
* ``MINERU_API_KEY``            — Bearer token from mineru.net API 管理
* ``MINERU_API_BASE``           — override base URL (default https://mineru.net)
* ``MINERU_MODEL_VERSION``      — "vlm" (default, recommended) / "pipeline" / "MinerU-HTML"
* ``MINERU_POLL_INTERVAL``      — seconds between polls (default 3)
* ``MINERU_TIMEOUT_SECONDS``    — total polling timeout (default 600)
* ``MINERU_LANGUAGE``           — "ch" (default) / "en" / "ch_server" / etc.
* ``MINERU_ENABLE_FORMULA``     — "true" (default) to keep formula LaTeX
* ``MINERU_ENABLE_TABLE``       — "true" (default) to keep tables as markdown
* ``MINERU_IS_OCR``             — "true" to enable OCR (default "false")
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
import zipfile
from dataclasses import dataclass
from typing import Final

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults / constants
# ---------------------------------------------------------------------------

DEFAULT_API_BASE: Final = "https://mineru.net"
DEFAULT_POLL_INTERVAL: Final = 3
DEFAULT_TIMEOUT_SECONDS: Final = 600  # 10 min for big papers
DEFAULT_MODEL_VERSION: Final = "vlm"
DEFAULT_LANGUAGE: Final = "ch"

# MinerU file size / page limits (精准 API 模式)
MAX_FILE_SIZE_BYTES: Final = 200 * 1024 * 1024  # 200 MB
MAX_PAGES: Final = 200

# State values returned by MinerU's poll endpoint
_STATE_DONE = "done"
_STATE_FAILED = "failed"
_STATE_PENDING = "pending"
_STATE_RUNNING = "running"
_STATE_CONVERTING = "converting"

# Quota warning thresholds (page count per account per day)
DAILY_HIGH_PRIORITY_PAGE_LIMIT: Final = 1000

# --------------------------------------------------------------------------- #
# Proxy bypass for MinerU CDN downloads
# --------------------------------------------------------------------------- #
# The MinerU result-zip CDN (cdn-mineru.openxlab.org.cn) and the Aliyun OSS
# upload endpoints cannot complete TLS through the deployment's HTTP proxy
# (host.docker.internal:7892).  The proxy's CONNECT tunnel establishes, but
# the subsequent TLS handshake aborts with ``unexpected eof while reading``
# because the proxy's TLS stack is incompatible with Tengine's edge config.
# Direct (proxy-less) connections succeed, so we explicitly bypass the proxy
# for these hosts in every download path.  Keep the list broad enough to
# cover future CDN bucket migrations.
_NO_PROXY_HOSTS: Final = (
    "cdn-mineru.openxlab.org.cn",
    ".openxlab.org.cn",
    ".aliyuncs.com",
    "mineru.net",
)


def _should_bypass_proxy(url: str) -> bool:
    """Return True if *url*'s host matches a CDN/OSS domain that must not go through the HTTP proxy."""
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    return any(
        host == h or host.endswith(h) if h.startswith(".") else host == h
        for h in _NO_PROXY_HOSTS
    )


#: Host-side curl bridge. Set ``MINERU_BRIDGE_URL`` to override (e.g. for
#: a deployment behind a remote bridge exposed via Cloudflare Tunnel).
DEFAULT_BRIDGE_URL: Final = "http://host.docker.internal:9630"


def _build_bridge_url(cdn_url: str) -> str:
    """Wrap *cdn_url* into a download call to the host-side bridge.

    The bridge is a small stdlib HTTP server (see
    ``~/.local/bin/mineru-bridge.py``) that runs ``/usr/bin/curl`` on
    the host so the TLS handshake uses macOS LibreSSL/SecureTransport
    instead of the broken OpenSSL 3.x bundled with the container.
    """
    import urllib.parse

    base = os.environ.get("MINERU_BRIDGE_URL", DEFAULT_BRIDGE_URL).rstrip("/")
    return f"{base}/download?url={urllib.parse.quote(cdn_url, safe='')}"


# ---------------------------------------------------------------------------
# Settings helpers (read env directly, no pydantic — matches vision_client)
# ---------------------------------------------------------------------------


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var (matches EXTRACTION_STUB_MODE convention)."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("true", "1", "yes", "on")


def mineru_enabled() -> bool:
    """Whether the MinerU backend is enabled.

    Defaults to True so production picks it up automatically; set
    ``MINERU_ENABLED=false`` to force PyMuPDF fallback only (useful in
    dev or when MinerU is unreachable).
    """
    return _env_bool("MINERU_ENABLED", True)


def _load_dotenv_into_environ() -> None:
    """Best-effort load of a project-local .env file into os.environ.

    The application's :class:`nfm_db.config.Settings` uses
    ``pydantic-settings`` with ``env_prefix="NFM_"``, which means it
    silently ignores env vars without the prefix — including the
    MinerU token if it lives in ``.env`` under the conventional
    ``MINERU_API_KEY`` / ``MinerU_API_KEY`` names. We perform a tiny
    manual load here (no external dependency) so the same ``.env``
    file works for both the Settings model and direct ``os.environ``
    reads done by :func:`mineru_api_key`.

    Search order (walks up from this file's directory to find a ``.env``
    at each level, then merges into ``os.environ``). The project root's
    ``.env`` always wins because we iterate parents from outermost to
    innermost — innermost can only set keys that the root didn't.

    Safe to call repeatedly — already-loaded keys are left intact.
    Existing process env always wins over the .env file.
    """
    if os.environ.get("_MINERU_DOTENV_LOADED") == "1":
        return
    try:
        from pathlib import Path

        # Walk UP from this file's location first, so the project root
        # ``.env`` populates the env. Then fill in from $CWD/.env so a
        # developer can override locally without editing the canonical
        # file. In either case existing process env wins.
        seen: set[Path] = set()
        ordered: list[Path] = []
        for parent in Path(__file__).resolve().parents:
            env_path = (parent / ".env").resolve()
            if env_path not in seen:
                seen.add(env_path)
                ordered.append(env_path)
        cwd_env = Path.cwd().resolve() / ".env"
        if cwd_env not in seen:
            ordered.append(cwd_env)

        for env_path in ordered:
            if not env_path.is_file():
                continue
            try:
                for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
            except OSError:
                continue
    finally:
        os.environ["_MINERU_DOTENV_LOADED"] = "1"


def mineru_api_key() -> str | None:
    """Return the configured MinerU API token, or None if unset."""
    # Ensure .env has been loaded once into os.environ so legacy
    # ``MINERU_API_KEY`` / ``MinerU_API_KEY`` keys (which pydantic-settings
    # ignores due to its NFM_ prefix) are visible.
    _load_dotenv_into_environ()
    # Try multiple naming conventions for ergonomics with existing .env files
    for var in ("MINERU_API_KEY", "MinerU_API_KEY", "NFM_MINERU_API_KEY"):
        val = os.environ.get(var)
        if val:
            return val.strip()
    return None


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MinerUError(Exception):
    """Base exception for MinerU client errors."""


class MinerUConfigError(MinerUError):
    """Configuration is missing or invalid (no API key, disabled, etc.)."""


class MinerUAPIError(MinerUError):
    """MinerU API returned an error code or HTTP failure."""


class MinerUTimeoutError(MinerUError):
    """Polling for task result timed out."""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MinerUResult:
    """Successful parse result.

    Attributes:
        markdown:    The full Markdown content of ``full.md``.
        task_id:     MinerU batch identifier (useful for support tickets).
        state:       Final state from the batch poll — normally ``done``.
        pages:       Page count returned by MinerU, or ``None`` if the
                     API omitted it.
        elapsed_seconds: Wall-clock time spent on the whole pipeline.
        used_fallback: True when the PyMuPDF fallback path produced the
                       markdown (i.e. MinerU failed or wasn't enabled).
        zip_bytes:   Optional raw bytes of the result zip. Populated
                     by :meth:`MinerUClient.parse_pdf` when ``return_zip=True``
                     so the caller can persist images and layout.json.
                     ``None`` when the caller only asked for the markdown.
    """

    markdown: str
    task_id: str
    state: str
    pages: int | None = None
    elapsed_seconds: float = 0.0
    used_fallback: bool = False
    zip_bytes: bytes | None = None


@dataclass(frozen=True)
class MinerUZipAssets:
    """Assets extracted from a MinerU result zip.

    The v4 MinerU zip layout is::

        <uuid>_origin.pdf
        <uuid>_model.json
        <uuid>_content_list.json
        layout.json
        full.md
        images/<hash>.jpg
        images/<hash>.jpg
        ...

    Attributes:
        markdown:     The ``full.md`` content (with ``images/<hash>``
                      references that resolve to *images*).
        images:       Mapping of image filename → bytes for every file
                      under the zip's ``images/`` directory. The keys
                      are the bare filenames (e.g.
                      ``3e56d8c4...3f4d6.jpg``); callers remap them to
                      storage-friendly paths before persisting.
        layout_json:  Raw bytes of ``layout.json`` (PyMuPDF layout
                      information) or ``None`` if the zip didn't
                      include one.
        media_root:   The zip's top-level directory name (typically a
                      ``<uuid>``); useful when remapping relative
                      markdown references.

    Use :meth:`remap_image_paths` to rewrite the markdown's
    ``images/<hash>`` references to wherever the caller stored the
    images (e.g. ``data_sources/{uuid}/images/<hash>``).
    """

    markdown: str
    images: dict[str, bytes]
    layout_json: bytes | None = None
    media_root: str = ""

    def remap_image_paths(self, prefix: str) -> str:
        """Return *markdown* with every ``images/<hash>`` rewritten to ``<prefix>/<hash>``.

        *prefix* is the storage root the caller will use, e.g.
        ``f"data_sources/{ds.id}/images"``. The rewrite is a single
        string-substitution so it works even for markdown that
        contains regex-conflicting characters.
        """
        if not self.images:
            return self.markdown
        out = self.markdown
        for name in self.images:
            out = out.replace(f"images/{name}", f"{prefix}/{name}")
        return out


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class MinerUClient:
    """Synchronous-ish async client for MinerU 精准解析 API (v4).

    Use :func:`parse_pdf` as the high-level entry point. Internally it
    uses :class:`httpx.AsyncClient`; from a Celery worker (sync context)
    the call site wraps it in :func:`asyncio.run`.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        model_version: str | None = None,
        language: str | None = None,
        enable_formula: bool | None = None,
        enable_table: bool | None = None,
        is_ocr: bool | None = None,
        poll_interval: float | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else mineru_api_key()) or ""
        if not self.api_key:
            raise MinerUConfigError(
                "MINERU_API_KEY is not set. Add it to .env.prod or set MINERU_ENABLED=false."
            )
        self.api_base = (
            api_base
            if api_base is not None
            else _env_str("MINERU_API_BASE", DEFAULT_API_BASE)
        ).rstrip("/")
        self.model_version = (
            model_version
            if model_version is not None
            else _env_str("MINERU_MODEL_VERSION", DEFAULT_MODEL_VERSION)
        )
        self.language = (
            language if language is not None else _env_str("MINERU_LANGUAGE", DEFAULT_LANGUAGE)
        )
        self.enable_formula = (
            enable_formula
            if enable_formula is not None
            else _env_bool("MINERU_ENABLE_FORMULA", True)
        )
        self.enable_table = (
            enable_table
            if enable_table is not None
            else _env_bool("MINERU_ENABLE_TABLE", True)
        )
        self.is_ocr = (
            is_ocr if is_ocr is not None else _env_bool("MINERU_IS_OCR", False)
        )
        self.poll_interval = (
            poll_interval
            if poll_interval is not None
            else float(_env_str("MINERU_POLL_INTERVAL", str(DEFAULT_POLL_INTERVAL)))
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float(_env_str("MINERU_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
        )

    # ----- public API ----------------------------------------------------

    async def parse_pdf(
        self,
        pdf_bytes: bytes,
        *,
        filename: str = "upload.pdf",
        data_id: str | None = None,
        return_zip: bool = False,
    ) -> MinerUResult:
        """Parse a PDF and return Markdown content.

        Steps (MinerU 精准解析 v4 file-upload-batch flow):
            1. POST /api/v4/file-urls/batch  → 获取 batch_id + file_urls (signed OSS PUT URLs)
            2. PUT  <signed_url>             → 上传 PDF 字节
            3. GET  /api/v4/extract-results/batch/{batch_id}  → 轮询直到 done/failed
            4. GET  <full_zip_url>           → 下载结果 zip
            5. 解压 full.md (and return zip_bytes if return_zip=True)

        Set ``return_zip=True`` to keep the raw zip bytes in the returned
        result so the caller can persist images and layout.json via
        :func:`parse_zip_assets`.

        Raises :class:`MinerUError` on any failure path.
        """
        if len(pdf_bytes) > MAX_FILE_SIZE_BYTES:
            raise MinerUConfigError(
                f"PDF size {len(pdf_bytes)} bytes exceeds MinerU limit "
                f"({MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB)"
            )

        started = time.monotonic()
        async with httpx.AsyncClient(timeout=60.0) as client:
            upload_urls, batch_id = await self._apply_upload_urls(
                client, filename=filename, data_id=data_id
            )
            await self._upload_pdf(client, upload_urls[0], pdf_bytes)

            poll_result = await self._poll_until_done(client, batch_id)
            if poll_result.state != _STATE_DONE:
                raise MinerUAPIError(
                    f"MinerU batch {batch_id} ended in state={poll_result.state}"
                )
            if not poll_result.full_zip_url:
                raise MinerUAPIError(
                    f"MinerU batch {batch_id} returned state=done but no full_zip_url"
                )

            zip_bytes = await self._fetch_zip_bytes(poll_result.full_zip_url)
            markdown = self._extract_markdown(zip_bytes)

        elapsed = time.monotonic() - started
        logger.info(
            "MinerU parsed PDF: filename=%s batch_id=%s pages=%s chars=%d elapsed=%.1fs zip=%d bytes",
            filename,
            batch_id,
            poll_result.pages,
            len(markdown),
            elapsed,
            len(zip_bytes),
        )
        return MinerUResult(
            markdown=markdown,
            task_id=batch_id,
            state=poll_result.state,
            pages=poll_result.pages,
            elapsed_seconds=elapsed,
            zip_bytes=zip_bytes if return_zip else None,
        )

    # ----- internal helpers ---------------------------------------------

    async def _apply_upload_urls(
        self,
        client: httpx.AsyncClient,
        *,
        filename: str,
        data_id: str | None,
    ) -> tuple[list[str], str]:
        """POST /api/v4/file-urls/batch → (file_urls, batch_id)."""
        payload: dict[str, object] = {
            "files": [
                {
                    "name": filename,
                    **({"data_id": data_id} if data_id else {}),
                }
            ],
            "model_version": self.model_version,
            "enable_formula": self.enable_formula,
            "enable_table": self.enable_table,
            "language": self.language,
        }
        if self.is_ocr:
            # apply to the file entry rather than the batch envelope
            payload["files"][0]["is_ocr"] = True  # type: ignore[index]
        resp = await client.post(
            f"{self.api_base}/api/v4/file-urls/batch",
            json=payload,
            headers=self._auth_headers(),
        )
        self._raise_for_status(resp, "file-urls/batch")
        body = resp.json()
        self._raise_for_code(body, "file-urls/batch")
        data = body["data"]
        return list(data["file_urls"]), str(data["batch_id"])

    async def _upload_pdf(
        self,
        client: httpx.AsyncClient,
        upload_url: str,
        pdf_bytes: bytes,
    ) -> None:
        """PUT the PDF bytes to the signed OSS upload URL.

        No Content-Type per MinerU docs.
        """
        resp = await client.put(upload_url, content=pdf_bytes)
        # OSS PUT returns 200 on success; 201 may appear too
        if resp.status_code not in (200, 201, 204):
            raise MinerUAPIError(
                f"PDF upload to OSS failed: HTTP {resp.status_code}: {resp.text[:200]}"
            )

    async def _poll_until_done(
        self,
        client: httpx.AsyncClient,
        batch_id: str,
    ) -> _PollResult:
        """GET /api/v4/extract-results/batch/{batch_id} until done/failed.

        The batch endpoint returns a list under ``data.extract_result``;
        we aggregate over all files and consider the batch "done" only
        when every file's state is ``done``. The first file's
        ``full_zip_url`` is returned.
        """
        deadline = time.monotonic() + self.timeout_seconds
        last_summary = ""
        while time.monotonic() < deadline:
            resp = await client.get(
                f"{self.api_base}/api/v4/extract-results/batch/{batch_id}",
                headers=self._auth_headers(),
            )
            self._raise_for_status(resp, f"extract-results/batch/{batch_id}")
            body = resp.json()
            self._raise_for_code(body, f"extract-results/batch/{batch_id}")
            data = body["data"] or {}
            extract_results = data.get("extract_result") or []
            if not extract_results:
                last_summary = "no extract_result yet"
                await asyncio.sleep(self.poll_interval)
                continue

            # Find the first terminal file
            any_failed = any(
                r.get("state") == _STATE_FAILED for r in extract_results
            )
            all_done = all(
                r.get("state") == _STATE_DONE for r in extract_results
            )
            any_running = any(
                r.get("state")
                in (_STATE_PENDING, _STATE_RUNNING, _STATE_CONVERTING)
                for r in extract_results
            )

            # Build a human-readable state summary
            last_summary = ", ".join(
                f"{r.get('file_name','?')}={r.get('state','?')}"
                for r in extract_results
            )

            if any_failed:
                # Surface the first failure's err_msg
                first_failed = next(
                    r for r in extract_results
                    if r.get("state") == _STATE_FAILED
                )
                raise MinerUAPIError(
                    f"MinerU batch {batch_id} failed: "
                    f"{first_failed.get('err_msg', 'unknown error')}"
                )

            if all_done:
                first_done = extract_results[0]
                progress = first_done.get("extract_progress") or {}
                pages = progress.get("total_pages")
                return _PollResult(
                    state=_STATE_DONE,
                    full_zip_url=first_done.get("full_zip_url"),
                    err_msg=None,
                    pages=pages,
                )

            if any_running:
                await asyncio.sleep(self.poll_interval)
                continue

            # Unknown state — keep polling a few more times
            await asyncio.sleep(self.poll_interval)

        raise MinerUTimeoutError(
            f"MinerU batch {batch_id} did not complete within "
            f"{self.timeout_seconds}s (last states: {last_summary})"
        )

    async def _download_markdown(
        self,
        client: httpx.AsyncClient,
        full_zip_url: str,
    ) -> str:
        """Backwards-compatibility wrapper: download the zip and return just full.md.

        Equivalent to ``self._extract_markdown(await self._fetch_zip_bytes(url))``.
        Kept around because external callers (e.g. :func:`parse_pdf_to_markdown`)
        used to call this directly; the new :meth:`parse_pdf` now goes via
        :meth:`_extract_markdown` directly without paying the cost of an
        extra ``_download_markdown`` call.
        """
        zip_bytes = await self._fetch_zip_bytes(full_zip_url)
        return self._extract_markdown(zip_bytes)

    @staticmethod
    def _extract_markdown(zip_bytes: bytes) -> str:
        """Extract just the ``full.md`` content from a MinerU result zip.

        The v4 zip layout is::

            <uuid>_origin.pdf
            <uuid>_model.json
            <uuid>_content_list.json
            layout.json
            full.md
            images/<hash>.jpg
            ...

        so we look for any entry whose basename is ``full.md``. Raises
        :class:`MinerUAPIError` if the zip is malformed or missing
        ``full.md``.
        """
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                md_names = [n for n in zf.namelist() if n.endswith("full.md")]
                if not md_names:
                    raise MinerUAPIError(
                        f"MinerU result zip has no full.md (entries: {zf.namelist()[:5]})"
                    )
                with zf.open(md_names[0]) as fh:
                    return fh.read().decode("utf-8", errors="replace")
        except zipfile.BadZipFile as exc:
            raise MinerUAPIError(f"MinerU returned non-zip body: {exc}") from exc

    @staticmethod
    def parse_zip_assets(zip_bytes: bytes) -> MinerUZipAssets:
        """Pull markdown, images, and layout.json out of a MinerU result zip.

        Returns a :class:`MinerUZipAssets` whose ``images`` dict maps
        bare filenames (e.g. ``3e56d8c4111e8463efeebb9c13307a6a05d6d8b31c5a5367384c7c9d3911f4d6.jpg``)
        to their bytes. The markdown still references ``images/<bare>``;
        use :meth:`MinerUZipAssets.remap_image_paths` to rewrite those to
        wherever the caller persists the image bytes (e.g.
        ``data_sources/{uuid}/images/<bare>``).

        Missing ``layout.json`` or no ``images/`` directory is non-fatal —
        the corresponding fields are returned as ``None`` / empty dict.
        """
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                names = zf.namelist()
                # Discover the top-level directory (e.g. ``<uuid>``).
                # The v4 zip sometimes nests entries under that prefix,
                # sometimes not; we cover both layouts.
                media_root = ""
                for n in names:
                    if n.endswith("_origin.pdf"):
                        media_root = n[: -len("_origin.pdf")]
                        break
                    if "/" in n:
                        media_root = n.split("/", 1)[0]
                        break

                # Markdown — first entry whose basename is full.md.
                md_text = ""
                for n in names:
                    if n.endswith("full.md"):
                        with zf.open(n) as fh:
                            md_text = fh.read().decode("utf-8", errors="replace")
                        break
                if not md_text:
                    raise MinerUAPIError(
                        f"MinerU result zip has no full.md (entries: {names[:5]})"
                    )

                # Images — every entry whose parent directory is ``images/``.
                images: dict[str, bytes] = {}
                for n in names:
                    base = n.rsplit("/", 1)[-1]
                    if "/" in n and n.split("/")[-2] == "images" and base:
                        with zf.open(n) as fh:
                            images[base] = fh.read()

                # Layout.json — optional big file. Stored at the zip root
                # (not under images/ or a subdir).
                layout_bytes: bytes | None = None
                for n in names:
                    if n.endswith("layout.json") and "/" not in n:
                        with zf.open(n) as fh:
                            layout_bytes = fh.read()
                        break

                return MinerUZipAssets(
                    markdown=md_text,
                    images=images,
                    layout_json=layout_bytes,
                    media_root=media_root,
                )
        except zipfile.BadZipFile as exc:
            raise MinerUAPIError(f"MinerU returned non-zip body: {exc}") from exc

    async def _fetch_zip_bytes(self, url: str) -> bytes:
        """Download zip bytes via host-side bridge, then pycurl, then urllib, then httpx.

        The MinerU result-zip CDN (cdn-mineru.openxlab.org.cn) sits behind a
        Tengine edge that refuses the TLS ClientHello issued by OpenSSL 3.x
        (the default in python:3.12-slim and hence every production container),
        causing::

            SSL routines::unexpected eof while reading

        The host machine (macOS) carries ``/usr/bin/curl`` linked against
        LibreSSL/SecureTransport, which produces a ClientHello the CDN
        accepts.  The host-side bridge (``~/.local/bin/mineru-bridge.py``,
        launched via ``launchd``) exposes that curl as a plain HTTP endpoint
        at ``http://host.docker.internal:9630/download``.

        This method tries the bridge first when the host is in the bypass
        list; otherwise it falls through to in-container pycurl / urllib /
        httpx with proxy bypass (see :data:`_NO_PROXY_HOSTS`).
        """
        # 0) Host-side curl bridge — most reliable for the MinerU CDN.
        if _should_bypass_proxy(url):
            try:
                bridge_url = _build_bridge_url(url)
                async with httpx.AsyncClient(timeout=120) as c:
                    resp = await c.get(bridge_url)
                    if resp.status_code == 200:
                        return resp.content
                    raise MinerUAPIError(
                        f"bridge HTTP {resp.status_code}: {resp.text[:200]}"
                    )
            except Exception as bridge_exc:
                bridge_err = f"bridge={bridge_exc!r}"
                logger.debug("bridge fetch failed, falling through: %s", bridge_err)
            else:
                bridge_err = ""
        else:
            bridge_err = "bridge=skipped (host not in _NO_PROXY_HOSTS)"

        # 1) pycurl (libcurl bindings) — next most reliable
        import importlib.util

        if importlib.util.find_spec("pycurl") is None:
            pycurl_err = "pycurl not installed"
        else:
            try:
                return await asyncio.to_thread(_pycurl_get, url, 120.0)
            except Exception as exc:
                pycurl_err = f"pycurl={exc!r}"

        # 2) urllib fallback — bypass proxy for CDN hosts (see _NO_PROXY_HOSTS)
        try:
            import ssl
            import urllib.request

            ctx = ssl.create_default_context()
            req = urllib.request.Request(
                url, headers={"User-Agent": "nucpot/1.0"}
            )
            if _should_bypass_proxy(url):
                # ProxyHandler({}) = no proxy; the default opener inherits
                # HTTP_PROXY/HTTPS_PROXY from the environment, which breaks
                # the CDN TLS handshake.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(req, timeout=120) as resp:
                    body: bytes = resp.read()
                    return body
            else:
                with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
                    body = resp.read()
                    return body
        except Exception as urllib_exc:
            urllib_err = f"urllib={urllib_exc!r}"

        # 3) httpx last resort — trust_env=False to skip proxy env vars
        try:
            bypass = _should_bypass_proxy(url)
            async with httpx.AsyncClient(timeout=120, trust_env=not bypass) as c:
                resp = await c.get(url)
                if resp.status_code != 200:
                    raise MinerUAPIError(
                        f"Failed to download MinerU result zip: HTTP {resp.status_code}"
                    )
                return resp.content
        except Exception as httpx_exc:
            raise MinerUAPIError(
                f"Failed to download MinerU result zip from {url}: "
                f"{bridge_err}, pycurl={pycurl_err!r}, urllib={urllib_err!r}, httpx={httpx_exc!r}"
            ) from httpx_exc

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "*/*",
        }

    @staticmethod
    def _raise_for_status(resp: httpx.Response, endpoint: str) -> None:
        if resp.status_code >= 400:
            raise MinerUAPIError(
                f"MinerU {endpoint} HTTP {resp.status_code}: {resp.text[:200]}"
            )

    @staticmethod
    def _raise_for_code(body: dict[str, object], endpoint: str) -> None:
        if body.get("code") not in (0, "0"):
            raise MinerUAPIError(
                f"MinerU {endpoint} returned code={body.get('code')} "
                f"msg={body.get('msg')!r}"
            )


def _pycurl_get(url: str, timeout: float = 120.0) -> bytes:
    """Sync helper that performs a GET via libcurl/pycurl.

    Returns the body as bytes. Raises ``pycurl.error`` on failure.

    The MinerU result-zip CDN (``cdn-mineru.openxlab.org.cn``) sits behind
    a Tengine edge that refuses the TLS ClientHello issued by some HTTP
    proxies during CONNECT tunnelling — the proxy completes CONNECT but
    the subsequent TLS handshake aborts with ``unexpected eof while
    reading``.  Bypassing the proxy (direct connection from the container)
    lets libcurl negotiate TLS directly with the origin, which succeeds.
    We therefore force ``CURLOPT PROXY`` to an empty string for any URL
    whose host is in :data:`_NO_PROXY_HOSTS`.
    """
    import io as _io

    import pycurl  # type: ignore[import-untyped]

    buf = _io.BytesIO()
    c = pycurl.Curl()
    try:
        c.setopt(c.URL, url)
        c.setopt(c.WRITEDATA, buf)
        c.setopt(c.TIMEOUT, int(timeout))
        c.setopt(c.CONNECTTIMEOUT, 15)
        c.setopt(c.FOLLOWLOCATION, True)
        c.setopt(c.SSL_VERIFYPEER, 1)
        c.setopt(c.SSL_VERIFYHOST, 2)
        c.setopt(c.USERAGENT, "nucpot/1.0")
        if _should_bypass_proxy(url):
            c.setopt(c.PROXY, "")
        c.perform()
        status = c.getinfo(c.RESPONSE_CODE)
        if status != 200:
            raise pycurl.error(
                f"HTTP {status} for {url}",
            )
        return buf.getvalue()
    finally:
        c.close()


@dataclass(frozen=True)
class _PollResult:
    state: str
    full_zip_url: str | None
    err_msg: str | None
    pages: int | None


# ---------------------------------------------------------------------------
# High-level convenience (sync wrapper for Celery)
# ---------------------------------------------------------------------------


def parse_pdf_to_markdown(
    pdf_bytes: bytes,
    *,
    filename: str = "upload.pdf",
    data_id: str | None = None,
) -> str:
    """Parse a PDF to Markdown using MinerU (blocking wrapper).

    Call from a Celery worker or other sync code path. Returns the
    Markdown text. Raises :class:`MinerUError` on failure.

    Disabled (raises :class:`MinerUConfigError`) if ``MINERU_ENABLED``
    is false or no API key is set — callers should catch this and
    fall back to PyMuPDF.
    """
    if not mineru_enabled():
        raise MinerUConfigError("MINERU_ENABLED is false")
    client = MinerUClient()
    return asyncio.run(
        client.parse_pdf(pdf_bytes, filename=filename, data_id=data_id)
    ).markdown
