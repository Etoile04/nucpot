"""Versioned NFM-227 NVL ontology contract (Phase 1 backend endpoint).

The envelope + element shapes are sourced verbatim from the ontofuel
``schemas/nvl_contract.schema.json`` (NFM-227 D2, merged to ``ontofuel-v0.1``)
— we do not invent a shape. The schema is ``additionalProperties: true`` at
every level, so the Phase 1 extensions (``corpus_id``, ``pagination``) and the
reserved Phase 2 ``record_ref`` slot (NFM-267) extend it without a breaking
bump.

Contract-as-firewall invariant (NFM-246 ADR): the viewer consumes this exact
element shape, so a Phase 1 data-source swap (static JSON → backend endpoint)
breaks zero viewer code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

# Phase 1 pinned contract version (NFM-227 initial value).
CONTRACT_SCHEMA_VERSION = "1.0"

# sha256 short digest: first 16 lowercase hex chars (per nvl_contract.schema.json).
_SOURCE_DIGEST_PATTERN = r"^[a-f0-9]{16}$"

# Controlled node type — drift firewall (schema pins to class|individual).
NodeType = Literal["class", "individual"]


class OntologyNode(BaseModel):
    """NVL node — element shape unchanged from the legacy viewer artifact.

    Phase 2 reserves ``record_ref`` as an optional deep-link slot (NFM-267).
    """

    id: str = Field(min_length=1)
    type: NodeType
    name: str | None = None
    label: str | None = None
    comment: str | None = None
    uri: str | None = None
    color: str | None = None
    size: float | None = None
    record_ref: str | None = None


class OntologyRelationship(BaseModel):
    """NVL relationship — ``from`` is a Python keyword, aliased on the wire."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1)
    from_: str = Field(min_length=1, alias="from")
    to: str = Field(min_length=1)
    type: str = Field(min_length=1)
    label: str | None = None
    comment: str | None = None
    record_ref: str | None = None


class OntologyStats(BaseModel):
    """NVL-derived counts (NFM-227)."""

    nodes: int = 0
    relationships: int = 0
    classes: int = 0
    individuals: int = 0


class OntologyPagination(BaseModel):
    """Cursor pagination metadata — only present when a corpus is chunked."""

    next_cursor: str | None = None
    total: int


class OntologyGraphResponse(BaseModel):
    """Versioned NFM-227 NVL graph envelope emitted by the ontology endpoint."""

    schema_version: str = Field(
        default=CONTRACT_SCHEMA_VERSION,
        pattern=r"^\d+\.\d+(\.\d+)?$",
    )
    corpus_id: str
    generated_at: datetime
    source_ontology: str = Field(min_length=1)
    source_digest: str = Field(pattern=_SOURCE_DIGEST_PATTERN)
    stats: OntologyStats
    nodes: list[OntologyNode] = Field(default_factory=list)
    relationships: list[OntologyRelationship] = Field(default_factory=list)
    pagination: OntologyPagination | None = None

    # Non-serialized provenance for HTTP caching headers (never on the wire).
    _last_modified: datetime | None = PrivateAttr(default=None)
