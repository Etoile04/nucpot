"""Literature processing service (NFM-1487 / NFM-1485-2).

End-to-end pipeline for a single :class:`DataSource` row:

    PDF bytes → Markdown (PyMuPDF) → ontofuel_extract → extraction_to_db_mapper
    → GraphBuilder.build_from_extraction → KG nodes/edges

The orchestrator is :func:`process_literature` (async).  It is invoked from
:mod:`nfm_db.services.literature_dispatcher` (Celery task body) via the sync
wrapper :func:`process_literature_sync`, which spins up its own async DB
session and bridges the Celery worker loop with ``asyncio.run``.

Status transitions on ``DataSource.parse_status``:

    uploaded → parsing → extracting → completed
                              ↘ failed

The 'extracting' label bridges parse and the LLM call (kept on the column
for visibility into the downstream stage).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from nfm_db.services.storage import StorageBackend

from nfm_db.database import async_session_factory
from nfm_db.models.source import DataSource
from nfm_db.services.health_event_emitter import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    build_context,
    emit_health_event,
    emit_health_event_sync,
)

logger = logging.getLogger(__name__)

# Re-exported for mypy; the emitter validates at write time.
EVENT_GENERIC_SILENT_CATCH = "generic_silent_catch"

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

#: Status values written to ``DataSource.parse_status`` during the pipeline.
PARSE_STATUS_UPLOADED = "uploaded"
PARSE_STATUS_PARSING = "parsing"
PARSE_STATUS_EXTRACTING = "extracting"
PARSE_STATUS_COMPLETED = "completed"
PARSE_STATUS_FAILED = "failed"

#: Cap for parse_error strings so a runaway stack trace doesn't blow the row.
MAX_ERROR_LEN = 1000

# ---------------------------------------------------------------------------
# Storage accessor (lazy so tests can patch the module-level reference)
# ---------------------------------------------------------------------------


def _get_storage() -> StorageBackend:
    """Return the configured :class:`StorageBackend`.

    Imported lazily so the literature service module loads even when
    optional dependencies (S3 backend, etc.) are not installed.
    """
    from nfm_db.services.storage import get_storage

    return get_storage()


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from a sync context safely.

    Celery prefork workers may already have an event loop running.
    ``asyncio.run()`` raises ``RuntimeError: asyncio.run() cannot be
    called from a running event loop`` in that case.  This helper runs
    the coroutine in a fresh thread with its own event loop, avoiding
    the conflict.
    """
    import asyncio as _aio
    import concurrent.futures as _cf

    try:
        # Fast path: no running loop → asyncio.run is safe.
        _aio.get_running_loop()
        # If we reach here, a loop IS running → use a worker thread.
        with _cf.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_aio.run, coro).result()
    except RuntimeError:
        # No running loop → safe to use asyncio.run directly.
        return _aio.run(coro)


# ---------------------------------------------------------------------------
# PDF → Markdown (PyMuPDF)
# ---------------------------------------------------------------------------


def _parse_pdf_to_markdown(
    pdf_bytes: bytes,
    *,
    ds_id: Any | None = None,
    storage: Any | None = None,
) -> str:
    """Convert PDF bytes to Markdown.

    Prefers MinerU (NFM-MINERU-1) for structured output with formula
    LaTeX and table recognition. Falls back to PyMuPDF raw text extraction
    when MinerU is disabled, the API key is missing, or the request fails.
    Both code paths are deliberately tolerant: a failure in the primary
    path must not block extraction — the downstream LLM is robust to
    plain text.

    Raises whatever ``fitz`` raises on malformed PDFs when the fallback
    is used so the caller can capture and re-raise with the truncated
    error message.
    """
    markdown: str | None = None
    parser_used = "pymupdf"

    # ---- Primary: MinerU (v4 精准解析 API) -----------------------------
    try:
        from nfm_db.services.mineru_client import (
            MinerUClient,
            MinerUError,
            mineru_api_key,
            mineru_enabled,
        )

        if mineru_enabled():
            try:
                client = MinerUClient(
                    api_key=mineru_api_key(),
                    poll_interval=0.5,
                    timeout_seconds=300,
                )
                zip_bytes_for_assets: bytes | None = None
                if ds_id is not None and storage is not None:
                    result = _run_async(
                        client.parse_pdf(pdf_bytes, filename="upload.pdf", return_zip=True)
                    )
                    markdown = result.markdown
                    zip_bytes_for_assets = result.zip_bytes
                else:
                    from nfm_db.services.mineru_client import (
                        parse_pdf_to_markdown as _md_only,
                    )

                    markdown = _md_only(pdf_bytes, filename="upload.pdf")

                parser_used = "mineru"
                logger.info(
                    "_parse_pdf_to_markdown: mineru OK chars=%d (file=%d bytes)",
                    len(markdown or ""),
                    len(pdf_bytes),
                )

                if ds_id is not None and storage is not None and zip_bytes_for_assets is not None:
                    try:
                        markdown = _persist_mineru_assets(
                            storage=storage,
                            ds_id=ds_id,
                            zip_bytes=zip_bytes_for_assets,
                            markdown=markdown or "",
                        )
                    except Exception:  # pragma: no cover — defensive
                        logger.exception(
                            "Failed to persist MinerU images for %s; "
                            "leaving markdown with bare ``images/`` refs.",
                            ds_id,
                        )
            except MinerUError as exc:
                # Config / API / timeout / network — all recoverable.
                # Log and fall through to PyMuPDF rather than crashing
                # the Celery pipeline.
                logger.warning(
                    "_parse_pdf_to_markdown: mineru failed (%s) — falling back to PyMuPDF",
                    exc,
                )
    except ImportError as exc:
        # mineru_client module missing — treat as "MinerU not configured".
        # Previously silent: the whole corpus would quietly parse via
        # PyMuPDF with no signal that the better extractor was absent.
        emit_health_event_sync(
            event_type="fallback_triggered",
            severity=SEVERITY_WARNING,
            source_service="mineru_extraction",
            context=build_context(exc, fallback="pymupdf"),
        )
        logger.warning("_parse_pdf_to_markdown: mineru_client unavailable — using PyMuPDF")

    if markdown:
        return markdown

    # ---- Fallback: PyMuPDF raw text ------------------------------------
    import fitz  # type: ignore[import-untyped]

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        parts: list[str] = []
        for page in doc:
            parts.append(page.get_text())
        md_text = "\n\n".join(parts)
        if parser_used != "pymupdf":
            logger.warning(
                "_parse_pdf_to_markdown: using PyMuPDF fallback (%d chars)",
                len(md_text),
            )
        return md_text
    finally:
        doc.close()


def _persist_mineru_assets(
    *,
    storage: Any,
    ds_id: Any,
    zip_bytes: bytes,
    markdown: str,
) -> str:
    """Save the MinerU zip's images and rewrite ``images/<hash>`` references.

    Returns the markdown with ``images/<hash>.jpg`` references rewritten
    to ``data_sources/{ds_id}/images/<hash>.jpg`` so consumers can
    resolve the links without ad-hoc remapping.
    """
    from nfm_db.services.mineru_client import MinerUClient

    assets = MinerUClient.parse_zip_assets(zip_bytes)
    if not assets.images:
        logger.info(
            "_persist_mineru_assets: no images in zip for %s (markdown=%d chars)",
            ds_id,
            len(markdown),
        )
        return markdown

    saved = 0
    for name, data in assets.images.items():
        storage.save(ds_id, f"images/{name}", data)
        saved += 1
    logger.info(
        "_persist_mineru_assets: saved %d images for %s (markdown=%d chars)",
        saved,
        ds_id,
        len(markdown),
    )

    remap_prefix = f"data_sources/{ds_id}/images"
    markdown = assets.remap_image_paths(remap_prefix)
    return markdown


async def _extract_via_mineru_vlm(
    db: AsyncSession,
    ds: Any,
    *,
    max_images: int = 20,
) -> list[dict[str, Any]]:
    """Run the MinerU + VLM extraction pipeline for a single DataSource.

    Independent helper: bypasses _parse_pdf_to_markdown to keep the
    text-extraction path unchanged. Always uses MinerU when available.

    Returns:
        A list of dict figures/tables ready to be merged into job.figures
        and job.tables. Empty list on any failure (caller treats as soft).
    """
    import os

    from nfm_db.services.mineru_client import MinerUClient, MinerUError
    from nfm_db.services.mineru_vision_extractor import (
        extract_figures_with_mineru,
        to_job_figure,
    )

    api_key = os.environ.get("MINERU_API_KEY")
    if not api_key:
        logger.warning("_extract_via_mineru_vlm: MINERU_API_KEY not set, skipping")
        return []

    storage = _get_storage()
    try:
        pdf_bytes = storage.read(ds.file_path)
    except Exception as exc:
        logger.warning("_extract_via_mineru_vlm: failed to read PDF for %s: %s", ds.id, exc)
        return []

    mineru_client = MinerUClient(api_key=api_key, poll_interval=0.5, timeout_seconds=300)

    # Use the existing VisionClient settings but build a plain async callable
    # adapter. extract_figures_with_mineru falls back to callable-style if
    # chat.completions.create isn't available.
    try:
        from nfm_db.services.vision_client import VisionClient

        vision = VisionClient()

        async def _vlm_call(messages: list[dict[str, Any]], *, timeout: float) -> str:
            """Plain async callable → OpenAI-compat /v1/chat/completions.

            Important: the caller (`vlm_extract` / `vlm_verify` in
            `mineru_vision_extractor.py`) builds multimodal messages with both
            `text` and `image_url` content parts — the VLM cannot see the
            image unless those parts reach the wire. Pass `messages` through
            verbatim; the canonical payload shape lives in
            `VisionClient._http_call`.

            An earlier version of this adapter normalized content to plain
            text, silently dropping `image_url` parts. That bug shipped to
            prod as a "100% HIGH" verification rate while the model was
            actually captioning from the text prompt alone. Guarded here:
            if any input message contains a non-text part, assert at least
            one `image_url` reaches the payload. If normalization logic is
            ever reintroduced, that assertion will fire rather than silently
            regress.
            """
            import httpx

            url = vision.base_url.rstrip("/") + "/chat/completions"
            payload = {
                "model": vision.model,
                "messages": messages,
                "max_tokens": 1500,
                "temperature": 0.0,
                "stream": False,
            }

            # Defensive guard: a non-text part in input must reach output.
            # Without this, anyone "simplifying" the payload back to a text
            # string would silently strip images again (the bug this comment
            # is here to prevent recurring).
            def _has_non_text_part(_msgs: list[dict[str, Any]]) -> bool:
                for _m in _msgs:
                    _c = _m.get("content")
                    if isinstance(_c, list):
                        for _p in _c:
                            if _p.get("type") != "text":
                                return True
                return False

            def _payload_has_image_url(_payload: dict[str, Any]) -> bool:
                for _m in _payload.get("messages", []):
                    _c = _m.get("content")
                    if isinstance(_c, list):
                        for _p in _c:
                            if _p.get("type") == "image_url":
                                return True
                return False

            if _has_non_text_part(messages) and not _payload_has_image_url(payload):
                raise ValueError(
                    "_vlm_call: input contains a non-text part (image_url) "
                    "but the outgoing payload does not — image would be "
                    "silently dropped. Refusing to send the request."
                )
            async with httpx.AsyncClient(timeout=timeout) as c:
                resp = await c.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {vision.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            return str(data["choices"][0]["message"]["content"])

        vlm_client: Any = _vlm_call
    except Exception as exc:
        logger.warning("_extract_via_mineru_vlm: could not initialize VLM client: %s", exc)
        return []

    try:
        results = await extract_figures_with_mineru(
            pdf_bytes=pdf_bytes,
            vlm_client=vlm_client,
            mineru_client=mineru_client,
            max_images=max_images,
        )
    except MinerUError as exc:
        logger.warning("_extract_via_mineru_vlm: MinerU failed for %s: %s", ds.id, exc)
        return []
    except Exception as exc:
        logger.warning("_extract_via_mineru_vlm: unexpected error for %s: %s", ds.id, exc)
        return []

    figures: list[dict[str, Any]] = []
    for r in results:
        fig_dict = to_job_figure(r, source_reference=str(ds.id))
        if fig_dict is not None:
            figures.append(fig_dict)

    # Summary log
    high = sum(1 for r in results if (r.verification or {}).get("accuracy") == "high")
    med = sum(1 for r in results if (r.verification or {}).get("accuracy") == "medium")
    low = sum(1 for r in results if (r.verification or {}).get("accuracy") == "low")
    logger.info(
        "_extract_via_mineru_vlm: %s — %d figures (high=%d med=%d low=%d)",
        ds.id,
        len(figures),
        high,
        med,
        low,
    )
    return figures


# ---------------------------------------------------------------------------
# Duplicate-hash short-circuit
# ---------------------------------------------------------------------------


async def _find_completed_by_hash(
    db: AsyncSession,
    file_hash: str,
    *,
    exclude_id: UUID,
) -> DataSource | None:
    """Return the first sibling :class:`DataSource` already parsed for *file_hash*.

    Used to short-circuit an expensive PDF re-parse when an identical file
    has already been processed.  We require ``parse_status='completed'``
    *and* a non-null ``content_md`` so we never adopt a partial parse.
    """
    stmt = (
        select(DataSource)
        .where(
            DataSource.file_hash == file_hash,
            DataSource.id != exclude_id,
            DataSource.parse_status == PARSE_STATUS_COMPLETED,
            DataSource.content_md.is_not(None),
        )
        .order_by(DataSource.updated_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


async def process_literature(db: AsyncSession, datasource_id: UUID) -> dict[str, Any]:
    """Run the full PDF/DOI pipeline for *datasource_id*.

    1. Load the :class:`DataSource` row; no-op if missing.
    2. If ``content_md`` is null:
       - hash-based short-circuit against already-parsed siblings, or
       - read bytes via storage, convert PDF → Markdown via PyMuPDF.
    3. Set ``parse_status='extracting'`` and call
       :func:`ontofuel_extract` with ``source_type='datasource'``.
    4. Persist extraction results via
       :func:`nfm_db.services.extraction_to_db_mapper.map_and_persist`.
    5. Build KG nodes/edges via
       :class:`nfm_db.services.kg_re.GraphBuilder`.
    6. Set ``parse_status='completed'``.

    Any uncaught exception flips ``parse_status`` to ``'failed'`` with a
    truncated ``parse_error`` and is re-raised so the Celery scheduler
    can decide whether to retry.

    Returns a small status dict for the Celery task body to log.
    """
    # --- Step 1: load the DataSource row ----------------------------
    ds = await db.get(DataSource, datasource_id)
    if ds is None:
        logger.warning(
            "process_literature: DataSource %s not found — skipping",
            datasource_id,
        )
        return {
            "datasource_id": str(datasource_id),
            "status": "skipped",
            "reason": "not_found",
        }

    # --- Step 1b: short-circuit known placeholder/fixture datasources
    # (no real PDF artifact, no extractable content). These should be
    # silently skipped so the operator can see clean parse_status in
    # the UI without us running LLM extraction on test data (2026-07-28).
    if ds.parse_status == "placeholder":
        logger.info(
            "process_literature: datasource_id=%s is marked placeholder — skipping",
            ds.id,
        )
        return {
            "datasource_id": str(ds.id),
            "status": "skipped",
            "reason": "placeholder",
        }

    try:
        # --- Step 2: ensure content_md --------------------------------
        pdf_bytes_for_meta: bytes | None = None
        if ds.content_md is None:
            reused_from: UUID | None = None

            # Step 2a: duplicate-hash short-circuit.
            if ds.file_hash:
                sibling = await _find_completed_by_hash(db, ds.file_hash, exclude_id=ds.id)
                if sibling is not None and sibling.content_md is not None:
                    ds.content_md = sibling.content_md
                    reused_from = sibling.id
                    logger.info(
                        "process_literature: short-circuited PDF parse via "
                        "duplicate hash datasource_id=%s sibling_id=%s hash=%s",
                        ds.id,
                        sibling.id,
                        ds.file_hash,
                    )

            # Step 2b: if still empty, parse the PDF.
            if ds.content_md is None:
                if not ds.file_path:
                    # No upload artifact — cannot parse. Mark the datasource
                    # as failed and return cleanly instead of crashing in
                    # storage.read() (was IsADirectoryError, 2026-07-28).
                    logger.warning(
                        "process_literature: datasource_id=%s has no file_path "
                        "(no PDF upload recorded) — marking failed and skipping",
                        ds.id,
                    )
                    ds.parse_status = "failed"
                    ds.parse_error = "no file_path recorded for this datasource"
                    await db.commit()
                    return {
                        "datasource_id": str(ds.id),
                        "status": "skipped",
                        "reason": "no_file_path",
                    }

                ds.parse_status = PARSE_STATUS_PARSING
                await db.commit()

                storage = _get_storage()
                pdf_bytes = storage.read(ds.file_path)
                pdf_bytes_for_meta = pdf_bytes  # reuse for metadata extraction
                # its extracted images and rewrite the markdown references
                # (otherwise the saved ``content_md`` keeps broken
                # ``images/<hash>`` links because the zip is discarded).
                ds.content_md = _parse_pdf_to_markdown(
                    pdf_bytes,
                    ds_id=ds.id,
                    storage=storage,
                )

            logger.info(
                "process_literature: datasource_id=%s parsed content_md_chars=%d reused_from=%s",
                ds.id,
                len(ds.content_md or ""),
                reused_from,
            )

        # --- Step 2c: extract bibliographic metadata from PDF + content_md --
        # NFM-3301: populate DOI, journal, year, abstract (and improve
        # title) using two strategies: PDF binary metadata (fast) and
        # regex from parsed Markdown (broader).  Only writes fields
        # that are currently null to avoid overwriting curated values.
        try:
            from nfm_db.services.bibliographic_metadata import (
                extract_metadata_combined,
            )

            bib = extract_metadata_combined(pdf_bytes_for_meta, ds.content_md)
            if bib["title"] is not None:
                ds.title = bib["title"]
            if bib["doi"] is not None and ds.doi is None:
                ds.doi = bib["doi"]
            if bib["year"] is not None and ds.year is None:
                ds.year = bib["year"]
            if bib["journal"] is not None and ds.journal is None:
                ds.journal = bib["journal"]
            if bib["abstract"] is not None and ds.abstract is None:
                ds.abstract = bib["abstract"]
            logger.info(
                "process_literature: datasource_id=%s "
                "bibliographic metadata extracted title=%s doi=%s year=%s journal=%s abstract_chars=%d",
                ds.id,
                bib["title"] is not None,
                bib["doi"] is not None,
                bib["year"] is not None,
                bib["journal"] is not None,
                len(bib["abstract"] or ""),
            )
        except Exception:  # pragma: no cover — defensive
            logger.exception(
                "process_literature: bibliographic metadata extraction "
                "failed for datasource_id=%s (non-fatal)",
                ds.id,
            )

        # --- Step 3: extracting ----------------------------------------
        ds.parse_status = PARSE_STATUS_EXTRACTING
        await db.commit()

        from nfm_db.services.extraction_pipeline import ontofuel_extract

        raw_properties = await ontofuel_extract(
            source_reference=str(ds.id),
            source_type="datasource",
            db=db,
        )

        # --- Step 3b: heuristic supplement (NFM-3424) --------------------
        # Always run the regex extractor alongside the LLM path.
        # The LLM catches narrative prose; the heuristic catches inline
        # values in tables, captions, and figure descriptions that the
        # LLM may skip.  Results are deduplicated by (element_system,
        # property_name, value) so overlapping hits don't create duplicates.
        if ds.content_md:
            try:
                from nfm_db.services.heuristic_extractor import heuristic_extract

                heuristic_props = heuristic_extract(
                    ds.content_md,
                    source_reference=str(ds.id),
                )

                if heuristic_props:
                    # Dedup key matches heuristic_extractor's own dedup.
                    existing_keys = {
                        (r.get("element_system"), r.get("property_name"), f"{r.get("value", 0):g}")
                        for r in raw_properties
                    }
                    new_count = 0
                    for item in heuristic_props:
                        key = (
                            item.get("element_system"),
                            item.get("property_name"),
                            f"{item.get("value", 0):g}",
                        )
                        if key not in existing_keys:
                            raw_properties.append(item)
                            existing_keys.add(key)
                            new_count += 1
                    logger.info(
                        "process_literature: datasource_id=%s heuristic supplement "
                        "added %d new properties (total now %d)",
                        ds.id,
                        new_count,
                        len(raw_properties),
                    )
            except Exception:  # pragma: no cover — defensive
                logger.exception(
                    "Heuristic extractor failed for %s; keeping LLM results",
                    ds.id,
                )

        logger.info(
            "process_literature: datasource_id=%s extracted %d properties",
            ds.id,
            len(raw_properties),
        )

        # --- Step 4: persist via extraction_to_db_mapper ---------------
        # ``build_result`` carries the pre-commit LightRAG ingest payload
        # (NFM-2871). It MUST be dispatched via dispatch_build_result()
        # AFTER db.commit() below — never before, otherwise we ship ghost
        # entities on rollback. NFM-2928 wires the second caller.
        build_result: Any | None = None
        if raw_properties:
            from nfm_db.services.extraction_to_db_mapper import map_and_persist

            mapping = await map_and_persist(db, raw_properties)
            logger.info(
                "process_literature: datasource_id=%s mapped "
                "sources=%d materials=%d datasets=%d measurements=%d "
                "reused=%d dedup_meas=%d",
                ds.id,
                mapping.created_sources,
                mapping.created_materials,
                mapping.created_datasets,
                mapping.created_measurements,
                mapping.reused_entities,
                mapping.skipped_duplicate_measurements,
            )

            # --- Step 5: build KG nodes/edges -------------------------
            from nfm_db.services.kg_re import GraphBuilder

            builder = GraphBuilder(db, sync_to_age=False)
            build_result = await builder.build_from_extraction(raw_properties, source_id=ds.id)

            # --- Step 5a: bridge KG → staging (NFM-3478 Layer B) ------
            # The viewer reads _ref_gap_fill_staging, not kg_nodes; without
            # this bridge a freshly extracted paper stays invisible to the
            # ontology viewer. Corpus id: explicit metadata_.corpus_id if
            # present, else slugified DOI (matches CORPUS_ID_RE).
            try:
                from nfm_db.services.kg_to_staging_bridge import (
                    _slugify,
                    bridge_kg_to_staging,
                )

                meta = ds.metadata_ or {}
                corpus_id = str(meta.get("corpus_id") or "").strip()
                if not corpus_id and ds.doi:
                    corpus_id = _slugify(ds.doi)
                if corpus_id:
                    bridged = await bridge_kg_to_staging(
                        db,
                        source_id=ds.id,
                        corpus_id=corpus_id,
                        source_doi=ds.doi,
                    )
                    logger.info(
                        "process_literature: datasource_id=%s — bridged %d "
                        "rows to _ref_gap_fill_staging (corpus=%s)",
                        ds.id,
                        bridged,
                        corpus_id,
                    )
                else:
                    logger.info(
                        "process_literature: datasource_id=%s — no corpus_id, "
                        "KG→staging bridge skipped",
                        ds.id,
                    )
            except Exception:
                logger.warning(
                    "process_literature: datasource_id=%s — KG→staging bridge failed (non-fatal)",
                    ds.id,
                    exc_info=True,
                )
        else:
            logger.info(
                "process_literature: datasource_id=%s — nothing to extract",
                ds.id,
            )

        # --- Step 5b: MinerU + VLM extraction (figures + tables) -
        # NFM-1366 (follow-up): replaces PageSplitter-based multimodal
        # detection with MinerU's pre-extracted images + VLM structured
        # extraction. Higher accuracy (50% high-confidence on Landa 2011)
        # because each image is cropped tightly by MinerU's layout
        # analysis rather than VLM scanning a 1700x2200 page.
        # Runs even when raw_properties is empty — figures can exist
        # without any text-extracted properties.
        if ds.file_path:
            try:
                mineru_figures = await _extract_via_mineru_vlm(db, ds)
                if mineru_figures:
                    logger.info(
                        "process_literature: datasource_id=%s — MinerU+VLM "
                        "extracted %d figures/tables",
                        ds.id,
                        len(mineru_figures),
                    )
                    # NFM-929: persist VLM figure/table extractions.
                    # Previously the results were only logged — every figure
                    # row was ephemeral and extraction_figures stayed empty,
                    # so table-heavy papers (DFT studies) lost their primary
                    # numerical content before it reached review/KG.
                    from nfm_db.models.extraction_figure import ExtractionFigure

                    fig_rows = [
                        ExtractionFigure(
                            source_id=ds.id,
                            figure_type=fig.get("figure_type"),
                            caption=(fig.get("title") or "")[:500] or None,
                            image_path=fig.get("image_ref"),
                            extracted_data=fig,
                            confidence=float(fig.get("confidence") or 0.0),
                            extraction_method=fig.get("extraction_method") or "mineru_vlm",
                        )
                        for fig in mineru_figures
                    ]
                    db.add_all(fig_rows)
                    logger.info(
                        "process_literature: datasource_id=%s — persisted "
                        "%d rows to extraction_figures",
                        ds.id,
                        len(fig_rows),
                    )
            except Exception:
                logger.warning(
                    "process_literature: datasource_id=%s — MinerU+VLM stage failed (non-fatal)",
                    ds.id,
                    exc_info=True,
                )

        # --- Step 6: completed -----------------------------------------
        ds.parse_status = PARSE_STATUS_COMPLETED
        ds.parse_error = None
        await db.commit()

        # NFM-2928: dispatch the carried BuildResult to LightRAG AFTER the
        # commit so we never ship ghost entities on rollback. dispatch_build_result
        # is the single public entry point for BuildResult consumption.
        if build_result is not None:
            from nfm_db.services.kg_re import dispatch_build_result

            dispatch_build_result(build_result)

        logger.info(
            "process_literature: datasource_id=%s completed",
            ds.id,
        )
        return {
            "datasource_id": str(ds.id),
            "status": "completed",
            "extracted": len(raw_properties),
        }

    except Exception as exc:
        # --- Step 8: failure path --------------------------------------
        # NFM-3322: the upstream LLM extract (or any earlier step) can
        # leave the SQLAlchemy ``db`` session in an aborted transaction
        # state. asyncpg surfaces that as
        # ``InFailedSQLTransactionError: current transaction is aborted,
        # commands ignored until end of transaction block``. Using the
        # same aborted session here to persist the failure status would
        # silently drop the write and leave ``DataSource.parse_status``
        # stuck at ``'extracting'`` forever (the bug the worker hit in
        # prod). Try the input session first (cheap path for the common
        # case); if it raises — typically because the transaction is
        # poisoned — open a FRESH session via :func:`async_session_factory`
        # as a fallback so the failure-status write cannot be blocked.
        err_msg = str(exc)[:MAX_ERROR_LEN]
        original_exc = exc
        status_persisted = await _persist_failure_status(
            db,
            datasource_id,
            err_msg,
        )
        if not status_persisted:
            logger.warning(
                "process_literature: input session refused failure-status "
                "write for datasource_id=%s; falling back to a fresh "
                "async_session_factory session (NFM-3322)",
                datasource_id,
            )
            try:
                async with async_session_factory() as fresh_session:
                    fresh = await fresh_session.get(DataSource, datasource_id)
                    if fresh is not None:
                        fresh.parse_status = PARSE_STATUS_FAILED
                        fresh.parse_error = err_msg
                        await fresh_session.commit()
            except Exception:
                logger.exception(
                    "process_literature: failed to persist failure status "
                    "for datasource_id=%s (both sessions)",
                    datasource_id,
                )
                try:
                    async with async_session_factory() as rollback_session:
                        await rollback_session.rollback()
                except Exception as rollback_exc:
                    # A failed rollback on a FRESH session means the DB
                    # itself is unreachable. ``rollback_failed`` is
                    # not in the NFM-2211-B spec enum; ``_prepare``
                    # coerces it to ``generic_silent_catch`` while
                    # keeping the original label in the payload.
                    await emit_health_event(
                        event_type=EVENT_GENERIC_SILENT_CATCH,
                        severity=SEVERITY_ERROR,
                        source_service="literature_service",
                        context=build_context(
                            rollback_exc,
                            datasource_id=str(datasource_id),
                            reported_event_type="rollback_failed",
                        ),
                    )

        logger.exception(
            "process_literature: pipeline failed for datasource_id=%s: %s",
            datasource_id,
            err_msg,
            extra={"datasource_id": str(datasource_id)},
        )
        # Re-raise the ORIGINAL upstream cause. Bare ``raise`` would
        # surface the secondary "session is aborted" symptom (from the
        # inner try/except) instead of the real cause, hiding the
        # upstream error from operators and any Celery retry logic.
        raise original_exc from None


async def _persist_failure_status(
    db: AsyncSession,
    datasource_id: UUID,
    err_msg: str,
) -> bool:
    """Write ``parse_status='failed'`` + truncated ``parse_error`` on ``db``.

    Returns ``True`` when the write succeeded, ``False`` if the input
    session refused the operation (typically because its transaction is
    aborted upstream). NFM-3322 callers should fall back to a fresh
    :func:`async_session_factory` session on ``False``.

    This is a no-op when the row was deleted between extract-failure
    and re-load — the worker logs and moves on rather than 500ing.
    """
    try:
        # Re-load in case the session was invalidated by the failure.
        fresh = await db.get(DataSource, datasource_id)
        if fresh is not None:
            fresh.parse_status = PARSE_STATUS_FAILED
            fresh.parse_error = err_msg
            await db.commit()
        return True
    except Exception:
        logger.exception(
            "process_literature: input-session failure-status write "
            "raised for datasource_id=%s; will retry via fresh session",
            datasource_id,
        )
        try:
            await db.rollback()
        except Exception as rollback_exc:
            # A failed rollback leaves the session poisoned; emitting
            # here is why the emitter uses its own session.
            # ``rollback_failed`` is not in the NFM-2211-B spec enum;
            # ``_prepare`` coerces it to ``generic_silent_catch`` while
            # keeping the original label in the payload.
            await emit_health_event(
                event_type=EVENT_GENERIC_SILENT_CATCH,
                severity=SEVERITY_ERROR,
                source_service="literature_service",
                context=build_context(
                    rollback_exc,
                    datasource_id=str(datasource_id),
                    reported_event_type="rollback_failed",
                ),
            )
        return False


# ---------------------------------------------------------------------------
# Sync wrapper for the Celery worker
# ---------------------------------------------------------------------------


def process_literature_sync(datasource_id: UUID | str) -> dict[str, Any]:
    """Synchronous bridge for Celery → :func:`process_literature`.

    Spins up its own :class:`AsyncSession` (the Celery task body runs in a
    plain worker thread, not an event loop).  Mirrors the event-loop
    detection pattern in :mod:`nfm_db.services.celery_app` so unit tests
    that exercise this helper from inside an ``async def`` test still
    get a clean run.
    """
    if isinstance(datasource_id, str):
        datasource_id = UUID(datasource_id)

    async def _run() -> dict[str, Any]:
        async with async_session_factory() as session:
            return await process_literature(session, datasource_id)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Normal Celery context: no loop running.  Use asyncio.run directly.
        return asyncio.run(_run())

    # Already inside an event loop (e.g. pytest-asyncio).  Run in a worker
    # thread with its own loop so we don't nest.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _run())
        return future.result()


__all__ = [
    "MAX_ERROR_LEN",
    "PARSE_STATUS_COMPLETED",
    "PARSE_STATUS_EXTRACTING",
    "PARSE_STATUS_FAILED",
    "PARSE_STATUS_PARSING",
    "PARSE_STATUS_UPLOADED",
    "process_literature",
    "process_literature_sync",
]
