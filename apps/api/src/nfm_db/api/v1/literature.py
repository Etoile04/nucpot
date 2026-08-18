"""Literature Management API endpoints (NFM-1488 / NFM-1485-3).

Rewritten upload endpoint accepts real PDF uploads via multipart/form-data.
New ``from-doi`` endpoint fetches paper content by DOI.

Literature status machine:
  uploaded → parsing → extracting → completed
                          ↘               ↘
                          failed           failed

Endpoints:
- POST   /upload           — Upload a PDF (multipart)
- POST   /from-doi        — Fetch paper by DOI (JSON)
- GET    /{id}/status       — Check processing status
- GET    /{id}             — Full literature detail
- GET    /                  — List (paginated, filterable)
- GET    /search           — Full-text search
- POST   /{id}/reextract   — Trigger re-extraction
- DELETE /{id}             — Delete literature and associated data
"""

from __future__ import annotations

import hashlib
import logging
import math
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_domain_expert, require_editor
from nfm_db.database import get_db
from nfm_db.models.extraction_figure import ExtractionFigure
from nfm_db.models.extraction_result import ExtractionResult
from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.models.source import DataSource
from nfm_db.models.user import User
from nfm_db.schemas.common import ApiResponse, PaginatedResponse
from nfm_db.schemas.literature import (
    ExtractionResultItem,
    LiteratureDetailResponse,
    LiteratureFigure,
    LiteratureListItem,
    LiteratureReextractResponse,
    LiteratureStatusResponse,
    LiteratureUploadResponse,
)
from nfm_db.services.gap_scanner import compute_literature_recall
from nfm_db.services.provenance import parse_provenance
from nfm_db.services.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/literature", tags=["文献管理"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum file size for PDF uploads (50 MB).
MAX_UPLOAD_SIZE = 50 * 1024 * 1024

#: First 5 bytes of a valid PDF file.
PDF_MAGIC = b"%PDF-"

# ---------------------------------------------------------------------------
# Request / response schemas local to this module
# ---------------------------------------------------------------------------


class DoiRequest(BaseModel):
    """Request body for POST /literature/from-doi."""

    doi: str = Field(
        ..., description="Digital Object Identifier (e.g. 10.1016/j.jnucmat.2020.152307)"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_source_or_404(
    source_id: uuid.UUID,
    db: AsyncSession,
) -> DataSource:
    """Fetch a DataSource or raise 404."""
    source = await db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Literature not found")
    return source


# ---------------------------------------------------------------------------
# Extraction result merge (NFM-2224)
# ---------------------------------------------------------------------------

# Per-row caps so a misbehaving literature (e.g. 10 000 KG edges) cannot
# blow out the JSON response. Frontend only renders the first screenful anyway.
_MAX_MANUAL_RESULTS = 200
_MAX_KG_NODES_PER_SOURCE = 200
_MAX_KG_EDGES_PER_SOURCE = 400


def _row_to_manual_item(er: ExtractionResult) -> ExtractionResultItem:
    """Shape one row from the legacy ``extraction_results`` table."""
    return ExtractionResultItem(
        source_type="manual",
        id=str(er.id),
        property_name=er.property_name,
        item_type=er.item_type,
        item_data=er.item_data or {},
        value=er.value,
        confidence=er.confidence,
        review_status=er.review_status,
        source_page=er.source_page,
        source_paragraph=er.source_paragraph,
        # NFM-2247: how this item was produced. Empty list = unknown,
        # which the frontend badge renders as 来源未知 rather than
        # guessing from confidence or review_status.
        provenance=parse_provenance(er.extraction_method),
        created_at=er.created_at.isoformat() if er.created_at else None,
    )


def _kg_node_to_item(node: KGNode) -> ExtractionResultItem:
    """Shape one OntoFuel ``kg_nodes`` row into the unified response item."""
    props = node.properties or {}
    return ExtractionResultItem(
        source_type="kg_node",
        id=str(node.id),
        property_name=node.label,
        item_type=node.node_type,
        item_data=props,
        value=props.get("value"),
        unit=props.get("unit"),
        confidence=node.confidence,
        source_node_id=str(node.id),
        source_page=props.get("source_page"),
        source_paragraph=props.get("source_paragraph"),
        # NFM-2247 — same contract as the legacy branch above.
        provenance=parse_provenance(node.extraction_method),
        created_at=node.created_at.isoformat() if node.created_at else None,
    )


def _kg_edge_to_item(edge: KGEdge) -> ExtractionResultItem:
    """Shape one OntoFuel ``kg_edges`` row into the unified response item."""
    return ExtractionResultItem(
        source_type="kg_edge",
        id=str(edge.id),
        property_name=edge.relation_type,
        item_type="edge",
        item_data=edge.properties or {},
        value=None,
        confidence=edge.confidence,
        source_node_id=str(edge.source_node_id),
        source_target_id=str(edge.target_node_id),
        provenance=parse_provenance(edge.extraction_method),
        created_at=edge.created_at.isoformat() if edge.created_at else None,
    )


def _dedupe_and_sort(
    items: list[ExtractionResultItem],
) -> list[ExtractionResultItem]:
    """Remove duplicate items by a source-type-aware identity; sort newest first.

    Manual entries and kg_node rows share identity on
    ``(property_name, value)`` — when both collide the manual entry wins
    because the manual list is merged first (insertion order is preserved
    by the ``seen`` set).

    kg_edge rows are relations, not scalar values: their ``value`` field
    is intentionally ``None`` and two distinct edges that share a
    ``relation_type`` (e.g. ``UO2 --hasProperty--> density`` and
    ``UO2 --hasProperty--> melting_point``) MUST NOT collapse. Edges
    are therefore identified by the triple
    ``(relation_type, source_node_id, target_node_id)``. The DB enforces
    uniqueness on that triple per ``data_source_id`` (UniqueConstraint
    ``uq_kg_edges_source_target_relation``), so two legitimate edges
    will never collide on it.
    """
    seen: set[tuple[object, ...]] = set()
    deduped: list[ExtractionResultItem] = []
    for item in items:
        if item.source_type == "kg_edge":
            key = (
                item.property_name,
                item.source_node_id,
                item.source_target_id,
            )
        else:
            key = (item.property_name, item.value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    deduped.sort(key=lambda row: row.created_at or "", reverse=True)
    return deduped


async def _collect_extraction_results(
    source_id: uuid.UUID,
    db: AsyncSession,
) -> list[ExtractionResultItem]:
    """Merge extraction rows from every source attached to ``source_id``.

    Sources merged (most-recent wins on dedup key):

    1. ``extraction_results`` table — manually-entered items (``source_type='manual'``).
    2. ``kg_nodes`` table — OntoFuel LLM-extracted entity nodes (``source_type='kg_node'``).
    3. ``kg_edges`` table — OntoFuel LLM-extracted relations (``source_type='kg_edge'``).

    Each result row carries the ``source_type`` discriminator defined in
    :class:`ExtractionResultItem` (NFM-2224 AC-1 / AC-2). Duplicate rows
    (same ``property_name`` + ``value``) are collapsed, keeping the
    manual entry in preference to LLM-derived ones.
    """
    # --- 1. legacy manual entries ------------------------------------------
    er_rows = (
        await db.execute(
            select(ExtractionResult)
            .where(ExtractionResult.source_id == source_id)
            .order_by(ExtractionResult.created_at.desc())
            .limit(_MAX_MANUAL_RESULTS)
        )
    ).scalars().all()

    # --- 2. OntoFuel KG nodes ----------------------------------------------
    kg_node_rows = (
        await db.execute(
            select(KGNode)
            .where(KGNode.source_id == source_id)
            .order_by(KGNode.created_at.desc())
            .limit(_MAX_KG_NODES_PER_SOURCE)
        )
    ).scalars().all()

    # --- 3. OntoFuel KG edges ----------------------------------------------
    kg_edge_rows = (
        await db.execute(
            select(KGEdge)
            .where(KGEdge.source_id == source_id)
            .order_by(KGEdge.created_at.desc())
            .limit(_MAX_KG_EDGES_PER_SOURCE)
        )
    ).scalars().all()

    combined: list[ExtractionResultItem] = (
        [_row_to_manual_item(er) for er in er_rows]
        + [_kg_node_to_item(n) for n in kg_node_rows]
        + [_kg_edge_to_item(e) for e in kg_edge_rows]
    )
    return _dedupe_and_sort(combined)


def _source_to_detail(source: DataSource) -> LiteratureDetailResponse:
    """Convert a DataSource to a LiteratureDetailResponse."""
    return LiteratureDetailResponse(
        id=source.id,
        title=source.title,
        doi=source.doi,
        journal=source.journal,
        year=source.year,
        abstract=source.abstract,
        status=source.parse_status or "uploaded",
        parse_error=source.parse_error,
        source_id=source.id,
        extraction_results=[],
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _source_to_list_item(source: DataSource) -> LiteratureListItem:
    """Convert a DataSource to a LiteratureListItem."""
    return LiteratureListItem(
        id=source.id,
        title=source.title,
        doi=source.doi,
        journal=source.journal,
        year=source.year,
        abstract=source.abstract,
        status=source.parse_status or "uploaded",
        source_id=source.id,
        created_at=source.created_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/upload",
    response_model=ApiResponse[LiteratureUploadResponse],
    summary="上传PDF文件用于提取",
    description="上传PDF文件进行解析和提取。\n\nUpload a PDF file for parsing and extraction.",
)
async def upload_literature(
    current_user: Annotated[User, Depends(require_editor)],
    file: UploadFile = File(..., description="PDF file to upload (max 50 MB)"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[LiteratureUploadResponse]:
    """Upload a PDF file for parsing and extraction.

    Accepts multipart/form-data with a ``file`` field.  Validates content type
    and file size, computes SHA-256 for idempotency, persists via the storage
    backend, creates a DataSource row, and dispatches background Celery processing.

    Returns immediately with ``{literature_id, status: "parsing"}`` — the actual
    PDF→Markdown→extraction runs in the worker.
    """
    # --- Read and validate file bytes -----------------------------------
    raw_bytes = await file.read()

    # AC #3: file_size ≤ 50 MB → 413.
    if len(raw_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(raw_bytes)} bytes (max {MAX_UPLOAD_SIZE})",
        )

    # AC #2: content_type starts with application/pdf AND magic bytes → 415.
    content_type = file.content_type or ""
    if not content_type.startswith("application/pdf") or raw_bytes[:5] != PDF_MAGIC:
        raise HTTPException(
            status_code=415,
            detail="Only PDF files are accepted (content_type must be application/pdf).",
        )

    # --- Compute SHA-256 hash ------------------------------------------
    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    # --- Idempotency: same hash → return existing record ----------------
    existing_stmt = select(DataSource).where(DataSource.file_hash == file_hash)
    existing_result = await db.execute(existing_stmt)
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        return ApiResponse(
            success=True,
            data=LiteratureUploadResponse(
                literature_id=existing.id,
                status=existing.parse_status,
            ),
        )

    # --- Save file via storage backend ----------------------------------
    datasource_id = uuid.uuid4()
    filename = file.filename or f"{datasource_id}.pdf"
    title = filename.rsplit(".pdf", 1)[0] if filename.endswith(".pdf") else filename

    from nfm_db.services.storage import get_storage

    storage = get_storage()
    file_path = storage.save(datasource_id, filename, raw_bytes)

    # --- Create DataSource row ------------------------------------------
    source = DataSource(
        id=datasource_id,
        file_path=file_path,
        file_hash=file_hash,
        file_size=len(raw_bytes),
        parse_status="parsing",
        original_filename=filename,
        source_type="journal_article",
        title=title,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    # --- Dispatch background processing ----------------------------------
    from nfm_db.services.literature_dispatcher import (
        schedule_literature_processing,
    )

    schedule_literature_processing(source.id)

    return ApiResponse(
        success=True,
        data=LiteratureUploadResponse(
            literature_id=source.id,
            status="parsing",
        ),
    )


@router.post(
    "/from-doi",
    response_model=ApiResponse[LiteratureUploadResponse],
    summary="通过DOI获取文献",
    description="通过DOI获取文献内容并创建数据源。\n\nFetch paper content by DOI and create a data source.",
)
async def from_doi_literature(
    request: DoiRequest,
    current_user: Annotated[User, Depends(require_editor)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[LiteratureUploadResponse]:
    """Fetch paper content by DOI and create a DataSource.

    Accepts ``application/json`` with a ``doi`` field.  Validates the DOI
    format, checks idempotency, fetches content via the doi_fetcher, saves
    the Markdown to storage, creates a DataSource row with
    ``parse_status='parsed'``, and dispatches background extraction.

    Returns ``{literature_id, status: "parsed"}`` immediately.
    """
    doi = request.doi.strip()

    # --- Validate DOI format (AC #7: malformed → 400) -------------------
    from nfm_db.services.doi_fetcher import DOIFetchError, fetch_paper_content, validate_doi_format

    if not validate_doi_format(doi):
        raise HTTPException(
            status_code=400,
            detail="Invalid DOI format. Expected: 10.xxxx/yyyy.",
        )

    # --- Idempotency: same DOI → return existing record -----------------
    existing_stmt = select(DataSource).where(DataSource.doi == doi)
    existing_result = await db.execute(existing_stmt)
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        return ApiResponse(
            success=True,
            data=LiteratureUploadResponse(
                literature_id=existing.id,
                status=existing.parse_status,
            ),
        )

    # --- Fetch content via doi_fetcher (AC #8: failure → 502) ---------
    try:
        md_content = fetch_paper_content(doi)
    except (DOIFetchError, Exception) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"DOI fetch failed: {exc}",
        )

    # --- Save Markdown to storage --------------------------------------
    datasource_id = uuid.uuid4()
    md_filename = f"{doi}.md"
    md_bytes = md_content.encode("utf-8")

    from nfm_db.services.storage import get_storage

    storage = get_storage()
    file_path = storage.save(datasource_id, md_filename, md_bytes)

    # --- Create DataSource row (AC #6: status='parsed') -----------------
    file_hash = hashlib.sha256(md_bytes).hexdigest()
    source = DataSource(
        id=datasource_id,
        doi=doi,
        content_md=md_content,
        file_path=file_path,
        file_hash=file_hash,
        file_size=len(md_bytes),
        parse_status="parsed",
        original_filename=md_filename,
        source_type="journal_article",
        title=f"DOI: {doi}",
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    # --- Dispatch background processing ----------------------------------
    from nfm_db.services.literature_dispatcher import (
        schedule_literature_processing,
    )

    schedule_literature_processing(source.id)

    return ApiResponse(
        success=True,
        data=LiteratureUploadResponse(
            literature_id=source.id,
            status="parsed",
        ),
    )


@router.get(
    "/search",
    response_model=ApiResponse[PaginatedResponse[LiteratureListItem]],
    summary="文献全文搜索",
    description="在标题、摘要和DOI字段中全文搜索。\n\nSearch across title, abstract, and DOI fields.",
)
async def search_literature(
    q: str = Query(..., min_length=1, description="Search query"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[LiteratureListItem]]:
    """在标题、摘要和DOI字段中全文搜索。

    Search across title, abstract, and DOI fields.
    """
    stmt = (
        select(DataSource)
        .where(
            DataSource.source_type == "journal_article",
            or_(
                DataSource.title.ilike(f"%{q}%"),
                DataSource.abstract.ilike(f"%{q}%"),
                DataSource.doi.ilike(f"%{q}%"),
            ),
        )
        .order_by(DataSource.created_at.desc())
    )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    pages = max(1, math.ceil(total / limit))
    stmt = stmt.offset((page - 1) * limit).limit(limit)
    result = await db.execute(stmt)
    sources = result.scalars().all()

    items = [_source_to_list_item(s) for s in sources]

    return ApiResponse(
        success=True,
        data=PaginatedResponse(
            items=items,
            total=total,
            page=page,
            limit=limit,
            pages=pages,
        ),
    )


@router.get(
    "/{literature_id}/status",
    response_model=ApiResponse[LiteratureStatusResponse],
    summary="获取文献处理状态",
    description="返回当前处理状态和进度。\n\nReturn the current processing status and progress.",
)
async def get_literature_status(
    literature_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[LiteratureStatusResponse]:
    """返回当前处理状态和进度。

    Return the current processing status and progress.
    """
    source = await _get_source_or_404(literature_id, db)

    status = source.parse_status or "uploaded"
    progress = 100 if status in ("completed", "failed") else 50 if status == "extracting" else 10

    return ApiResponse(
        success=True,
        data=LiteratureStatusResponse(
            id=source.id,
            status=status,
            progress=progress,
            error=source.parse_error,
        ),
    )


@router.get(
    "/{literature_id}",
    response_model=ApiResponse[LiteratureDetailResponse],
    summary="获取文献完整详情",
    description="返回文献完整详情，包括已提取的实体。\n\nReturn the full literature detail including extracted entities.",
)
async def get_literature_detail(
    literature_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[LiteratureDetailResponse]:
    """返回文献完整详情，包括已提取的实体。

    Return the full literature detail including extracted entities.
    """
    source = await _get_source_or_404(literature_id, db)

    # Load extraction results linked to this source via source_id (legacy).
    # `extraction_results` table is now only one of three merged sources; see
    # `_collect_extraction_results` for the full KG-aware merge (NFM-2224).
    fig_stmt = (
        select(ExtractionFigure)
        .where(ExtractionFigure.source_id == literature_id)
        .order_by(ExtractionFigure.page_number.asc())
        .limit(200)
    )
    fig_result = await db.execute(fig_stmt)
    figures = [
        LiteratureFigure(
            id=fig.id,
            page_number=fig.page_number,
            figure_type=fig.figure_type,
            image_path=fig.image_path,
            caption=fig.caption,
            confidence=fig.confidence,
            provenance=parse_provenance(fig.extraction_method),
        )
        for fig in fig_result.scalars().all()
    ]

    extraction_results = await _collect_extraction_results(literature_id, db)

    return ApiResponse(
        success=True,
        data=LiteratureDetailResponse(
            id=source.id,
            title=source.title,
            doi=source.doi,
            journal=source.journal,
            year=source.year,
            abstract=source.abstract,
            status=source.parse_status or "uploaded",
            parse_error=source.parse_error,
            source_id=source.id,
            content_md=source.content_md,
            figures=figures,
            extraction_results=extraction_results,
            created_at=source.created_at,
            updated_at=source.updated_at,
        ),
    )


@router.get(
    "",
    response_model=ApiResponse[PaginatedResponse[LiteratureListItem]],
    summary="获取文献列表（分页）",
    description="返回分页文献列表，支持年份和搜索筛选。\n\nReturn paginated literature list with optional filters.",
)
async def list_literature(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    status: str | None = Query(
        None,
        description="按状态过滤 (uploaded / parsing / extracting / completed / failed / placeholder / parsed)",
    ),
    year_min: int | None = Query(None),
    year_max: int | None = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[LiteratureListItem]]:
    """返回分页文献列表，支持年份和搜索筛选。

    Return paginated literature list with optional filters.
    """
    stmt = select(DataSource).where(
        DataSource.source_type == "journal_article",
    )

    # Apply filters.
    if search:
        stmt = stmt.where(
            or_(
                DataSource.title.ilike(f"%{search}%"),
                DataSource.abstract.ilike(f"%{search}%"),
                DataSource.doi.ilike(f"%{search}%"),
            )
        )
    if status:
        stmt = stmt.where(DataSource.parse_status == status)
    if year_min is not None:
        stmt = stmt.where(DataSource.year >= year_min)
    if year_max is not None:
        stmt = stmt.where(DataSource.year <= year_max)

    # Total count.
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Sort.
    sort_col = getattr(DataSource, sort_by, DataSource.created_at)
    if sort_order == "asc":
        stmt = stmt.order_by(sort_col.asc())
    else:
        stmt = stmt.order_by(sort_col.desc())

    # Paginate.
    pages = max(1, math.ceil(total / limit))
    stmt = stmt.offset((page - 1) * limit).limit(limit)
    result = await db.execute(stmt)
    sources = result.scalars().all()

    items = [_source_to_list_item(s) for s in sources]

    return ApiResponse(
        success=True,
        data=PaginatedResponse(
            items=items,
            total=total,
            page=page,
            limit=limit,
            pages=pages,
        ),
    )


@router.post(
    "/{literature_id}/reextract",
    response_model=ApiResponse[LiteratureReextractResponse],
    summary="触发文献重新提取",
    description="触发文献项的重新提取流程。\n\nTrigger a re-extraction of the literature item.",
)
async def reextract_literature(
    literature_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_editor)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[LiteratureReextractResponse]:
    """触发文献项的重新提取流程。

    Trigger a re-extraction of the literature item.

    For now this flips ``parse_status`` back to ``uploaded`` and
    schedules a new Celery task via :func:`schedule_literature_processing`.
    """
    source = await _get_source_or_404(literature_id, db)

    # Only allow re-extraction on items that already have parsed content
    # OR on items that previously failed to parse — items with no
    # content_md will be skipped from heuristic fallback.
    source.parse_status = "uploaded"
    source.parse_error = None
    await db.commit()

    # Schedule background processing.
    try:
        from nfm_db.services.literature_dispatcher import (
            schedule_literature_processing,
        )

        task_id = schedule_literature_processing(source.id)
        logger.info(
            "reextract_literature: scheduled task_id=%s for %s",
            task_id,
            source.id,
        )
    except Exception:  # pragma: no cover — broker errors are non-fatal here
        logger.exception("reextract_literature: broker scheduling failed")

    return ApiResponse(
        success=True,
        data=LiteratureReextractResponse(
            id=source.id,
            message="Re-extraction triggered",
            status="extracting",
        ),
    )


@router.delete(
    "/{literature_id}",
    response_model=ApiResponse[dict[str, str]],
    summary="删除文献项及关联数据",
    description="删除文献项及其所有关联的提取数据。\n\nDelete a literature item and all associated extraction data.",
)
async def delete_literature(
    literature_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_editor)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict[str, str]]:
    """删除文献项及其所有关联的提取数据。

    Delete a literature item and all associated extraction data.

    Uses raw SQL ``DELETE FROM data_sources WHERE id = ...`` to let
    PostgreSQL's ``ON DELETE CASCADE`` / ``ON DELETE SET NULL`` handle
    child rows automatically.  The previous ORM-based approach (loading
    each child row then ``await db.delete(child)``) caused SQLAlchemy
    to emit ``UPDATE datasets SET source_id=NULL`` before the parent
    DELETE — but ``datasets.source_id`` is NOT NULL, so the UPDATE
    raised ``NotNullViolationError``.  Raw SQL avoids this entirely.
    """
    # Verify the literature exists (raises 404 if not).
    await _get_source_or_404(literature_id, db)

    # Raw SQL DELETE — DB-level CASCADE handles:
    #   datasets (CASCADE), data_source_authors (CASCADE),
    #   extraction_figures (SET NULL), kg_nodes (SET NULL),
    #   kg_edges (SET NULL), extraction_results (no FK → stays).
    from sqlalchemy import text as _sa_text

    # First clean up extraction_results (no FK CASCADE on this table).
    await db.execute(
        _sa_text(
            "DELETE FROM extraction_results WHERE source_id = :sid"
        ).bindparams(sid=literature_id)
    )

    # Now delete the data_source — CASCADE handles the rest.
    await db.execute(
        _sa_text(
            "DELETE FROM data_sources WHERE id = :sid"
        ).bindparams(sid=literature_id)
    )
    await db.commit()

    return ApiResponse(
        success=True,
        data={"message": f"Literature {literature_id} deleted"},
    )


@router.get(
    "/{literature_id}/files/{file_path:path}",
    summary="Serve a stored file (image, asset) for this literature",
    description="Read and serve a file from the literature's storage directory. "
    "Used by the detail panel to render extracted images referenced in content_md.",
)
async def get_literature_file(
    literature_id: uuid.UUID,
    file_path: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve a file (e.g. extracted image) from storage.

    The ``file_path`` is a relative path within the literature's storage
    directory, e.g. ``images/<hash>.jpg``.  Path traversal (``..``) is
    rejected by the storage layer's safety validator.
    """
    # Verify the literature exists (raises 404 if not).
    await _get_source_or_404(literature_id, db)

    storage = get_storage()
    full_path = f"{literature_id}/{file_path}"
    try:
        data = storage.read(full_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {file_path}",
        )
    except Exception:
        raise HTTPException(
            status_code=404,
            detail=f"File not accessible: {file_path}",
        )

    # Guess content type from extension.
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    content_type = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "svg": "image/svg+xml",
        "pdf": "application/pdf",
    }.get(ext, "application/octet-stream")

    return Response(content=data, media_type=content_type)


# ---------------------------------------------------------------------------
# GET /api/v1/literature/{id}/recall (NFM-2697-T4 / ADR §3)
# ---------------------------------------------------------------------------


class LiteratureGapItem(BaseModel):
    """One open/filling gap surfaced in the per-literature recall payload."""

    entity_type: str
    property: str
    gap_status: str


class LiteratureRecallResponse(BaseModel):
    """Per-literature recall metrics (NFM-2697-T4 ADR §3)."""

    recall_rate: float = Field(
        ...,
        description="Fraction of expected properties covered (0.0-1.0).",
    )
    extracted_slots: int = Field(
        ...,
        description="Number of (entity_type, property) pairs covered.",
    )
    expected_slots: int = Field(
        ...,
        description="Total (entity_type, property) pairs declared by the ontology.",
    )
    gaps: list[LiteratureGapItem] = Field(
        default_factory=list,
        description="Per-gap detail for every open/filling gap linked to this literature.",
    )


@router.get(
    "/{literature_id}/recall",
    response_model=ApiResponse[LiteratureRecallResponse],
    summary="Per-literature recall metrics",
    description=(
        "计算指定文献相对于指定本体版本的召回率。\n\n"
        "Compute per-literature recall metrics: recall_rate, "
        "extracted_slots, expected_slots, and the list of open/filling "
        "gaps linked to this literature's chunks."
    ),
)
async def get_literature_recall(
    literature_id: uuid.UUID = Path(
        ...,
        description="Literature (DataSource) id.",
    ),
    ontology_version: uuid.UUID = Query(
        ...,
        description="Ontology version id to measure recall against.",
    ),
    session: AsyncSession = Depends(get_db),
    _current_user: Annotated[User, Depends(require_domain_expert)] = ...,  # type: ignore[assignment]
) -> ApiResponse[LiteratureRecallResponse]:
    """Return per-literature recall for ``(literature, ontology_version)``.

    Errors:
    - 404: literature_id or ontology_version not found.
    - 422: ontology_version query param missing (FastAPI validation).
    """
    try:
        result = await compute_literature_recall(
            session, literature_id, ontology_version,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return ApiResponse(
        success=True,
        data=LiteratureRecallResponse(
            recall_rate=result.recall_rate,
            extracted_slots=result.extracted_slots,
            expected_slots=result.expected_slots,
            gaps=[
                LiteratureGapItem(
                    entity_type=g.entity_type,
                    property=g.property,
                    gap_status=g.gap_status,
                )
                for g in result.gaps
            ],
        ),
    )
