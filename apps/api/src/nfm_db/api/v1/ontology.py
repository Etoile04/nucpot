"""Ontology NVL graph endpoint (Phase 1 backend NVL API — NFM-270 / NFM-266).

``GET /api/v1/ontology/corpora/{corpus_id}/graph`` emits the versioned NFM-227
NVL contract envelope derived read-only from ``_ref_gap_fill_staging``. The
viewer (Phase 0) swaps its static data URL for this endpoint with zero code
change (contract-as-firewall invariant).
"""

from __future__ import annotations

from datetime import UTC
from email.utils import format_datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.ontology import OntologyGraphResponse
from nfm_db.services.ontology_service import (
    HARD_MAX_NODES,
    CorpusNotFoundError,
    derive_ontology_graph,
)
from nfm_db.services.rate_limit import ontology_rate_limit

router = APIRouter(tags=["本体管理"])

# Safe slug — also the only form a staging ``source`` may take. Path-validated
# (422 on mismatch); no string interpolation into SQL downstream.
CORPUS_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"

# Derived (immutable) data: cache briefly. ETag is content-addressed (digest).
# `public` is safe because the derived corpus is scientific reference data with
# no auth and no PII. If embargoed/pre-publication corpora ever land here, switch
# to `private` and add an auth gate (T8 security review, MEDIUM-2).
_CACHE_CONTROL = "public, max-age=60"


@router.get(
    "/ontology/corpora/{corpus_id}/graph",
    response_model=OntologyGraphResponse,
    response_model_by_alias=True,
    summary="Versioned NVL graph for a corpus",
)
async def get_corpus_graph(
    response: Response,
    corpus_id: str = Path(
        ...,
        pattern=CORPUS_ID_PATTERN,
    ),
    max_nodes: int | None = Query(
        default=None,
        ge=1,
        le=HARD_MAX_NODES,
    ),
    cursor: str | None = Query(
        default=None,
    ),
    session: AsyncSession = Depends(get_db),
    _rate: None = Depends(ontology_rate_limit),
) -> OntologyGraphResponse:
    """返回指定语料库的版本化NVL图谱。

    Raises:
        404: the corpus resolves to no staging rows.
    """
    try:
        graph = await derive_ontology_graph(
            session,
            corpus_id,
            max_nodes=max_nodes,
            cursor=cursor,
        )
    except CorpusNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"corpus not found: {corpus_id}",
        ) from None

    response.headers["Cache-Control"] = _CACHE_CONTROL
    # source_digest is the stable corpus identity (NFM-227); for paginated
    # responses the request cursor is folded into the ETag so each page has a
    # distinct, cache-correct validator.
    etag_base = (
        graph.source_digest
        if cursor is None
        else f"{graph.source_digest}#{cursor}"
    )
    response.headers["ETag"] = f'"{etag_base}"'
    last_modified = graph._last_modified
    if last_modified is not None:
        # Postgres returns tz-aware values; SQLite (tests) is naive — coerce.
        if last_modified.tzinfo is None:
            last_modified = last_modified.replace(tzinfo=UTC)
        response.headers["Last-Modified"] = format_datetime(
            last_modified,
            usegmt=True,
        )
    return graph
