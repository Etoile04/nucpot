"""Shared NVL contract conformance checker (T2 harness).

The single source of truth for "what the versioned NFM-227 NVL contract
accepts." Run the SAME assertions against every provider:

* the canonical versioned fixture (``tests/fixtures/nvl_contract_sample.json``)
* the real vendored viewer artifact (element-only — it predates the envelope)
* the backend endpoint output (via TestClient in the conformance test)

Any provider failing = CI red. This is the drift firewall (NFM-266 invariant #2).

Element rules are sourced from the real artifact + ontofuel
``nvl_contract.schema.json``:
* nodes are OPEN (``additionalProperties: true``) but require ``id`` + ``type``
  with ``type ∈ {class, individual}``;
* relationships require ``id``/``from``/``to``/``type`` and every ``from``/``to``
  must reference an existing node id (referential integrity).
"""

from __future__ import annotations

import re
from typing import Any

_ALLOWED_NODE_TYPES = {"class", "individual"}
_SCHEMA_VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")
_SOURCE_DIGEST_RE = re.compile(r"^[a-f0-9]{16}$")

# Phase 2 record_ref stability firewall (NFM-267 §3.1). A relative URL has no
# scheme: it must not start with ``<scheme>://``. Transient/auth keys would make
# a deep link session-bound rather than shareable — they are forbidden. NOTE:
# the short transient key is matched as ``"timestamp"`` (not bare ``"ts"``) so
# legitimate names containing the letters "ts" (e.g. "stats", "nts",
# "constants") are not false-positived by substring search (NFM-282 follow-up).
_RECORD_REF_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_RECORD_REF_FORBIDDEN_SUBSTRINGS = (
    "session",
    "token",
    "sid",
    "auth",
    "timestamp",
    "expires",
)


class ContractViolationError(AssertionError):
    """Raised when a payload drifts from the versioned NVL contract."""


def assert_nvl_contract(
    payload: dict[str, Any],
    *,
    corpus_id: str | None = None,
    check_envelope: bool = True,
    check_stats_consistency: bool = True,
    check_record_ref: bool = True,
) -> None:
    """Assert ``payload`` conforms to the versioned NFM-227 NVL contract.

    Args:
        payload: decoded NVL graph document.
        corpus_id: if given, assert the envelope echoes it.
        check_envelope: assert envelope metadata (skip for the legacy artifact).
        check_stats_consistency: assert stats counts match nodes/relationships.
        check_record_ref: assert the Phase 2 record_ref deep-link invariant
            (NFM-267): material individuals carry a stable, shareable link and
            every present record_ref is well-formed. Opt-out for pre-record_ref
            artifacts (the vendored viewer artifact).
    """
    if not isinstance(payload, dict):
        raise ContractViolationError("payload must be a JSON object")

    if check_envelope:
        _assert_envelope(payload, corpus_id=corpus_id)

    nodes = payload.get("nodes")
    relationships = payload.get("relationships")
    if not isinstance(nodes, list):
        raise ContractViolationError("'nodes' must be a list")
    if not isinstance(relationships, list):
        raise ContractViolationError("'relationships' must be a list")

    node_ids = _assert_nodes(nodes)
    _assert_relationships(relationships, node_ids)

    if check_record_ref:
        _assert_record_ref(nodes, relationships)

    if check_envelope and check_stats_consistency:
        _assert_stats_consistency(payload, nodes, relationships)


def _assert_envelope(payload: dict[str, Any], *, corpus_id: str | None) -> None:
    for field in (
        "schema_version",
        "generated_at",
        "source_ontology",
        "source_digest",
        "stats",
        "nodes",
        "relationships",
    ):
        if field not in payload:
            raise ContractViolationError(f"envelope missing required field {field!r}")

    version = payload["schema_version"]
    if not isinstance(version, str) or not _SCHEMA_VERSION_RE.match(version):
        raise ContractViolationError(f"schema_version {version!r} is not a semver string")

    digest = payload["source_digest"]
    if not isinstance(digest, str) or not _SOURCE_DIGEST_RE.match(digest):
        raise ContractViolationError(
            f"source_digest {digest!r} must be 16 lowercase hex chars",
        )

    stats = payload["stats"]
    if not isinstance(stats, dict):
        raise ContractViolationError("'stats' must be an object")
    for field in ("nodes", "relationships", "classes", "individuals"):
        if not isinstance(stats.get(field), int) or stats[field] < 0:
            raise ContractViolationError(f"stats.{field} must be a non-negative int")

    if corpus_id is not None:
        echoed = payload.get("corpus_id")
        if echoed != corpus_id:
            raise ContractViolationError(
                f"corpus_id not echoed: expected {corpus_id!r}, got {echoed!r}",
            )


def _assert_nodes(nodes: list[Any]) -> set[str]:
    node_ids: set[str] = set()
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ContractViolationError(f"node[{idx}] must be an object")
        nid = node.get("id")
        if not isinstance(nid, str) or not nid:
            raise ContractViolationError(f"node[{idx}] missing non-empty 'id'")
        ntype = node.get("type")
        if ntype not in _ALLOWED_NODE_TYPES:
            raise ContractViolationError(
                f"node[{idx}] ({nid}) type {ntype!r} not in {sorted(_ALLOWED_NODE_TYPES)}",
            )
        if nid in node_ids:
            raise ContractViolationError(f"duplicate node id {nid!r}")
        node_ids.add(nid)
    return node_ids


def _assert_relationships(relationships: list[Any], node_ids: set[str]) -> None:
    rel_ids: set[str] = set()
    for idx, rel in enumerate(relationships):
        if not isinstance(rel, dict):
            raise ContractViolationError(f"relationship[{idx}] must be an object")
        for field in ("id", "from", "to", "type"):
            val = rel.get(field)
            if not isinstance(val, str) or not val:
                raise ContractViolationError(
                    f"relationship[{idx}] missing non-empty {field!r}",
                )
        rid = rel["id"]
        if rid in rel_ids:
            raise ContractViolationError(f"duplicate relationship id {rid!r}")
        rel_ids.add(rid)
        # Referential integrity — the viewer assumes edges point at real nodes.
        for endpoint in ("from", "to"):
            if rel[endpoint] not in node_ids:
                raise ContractViolationError(
                    f"relationship[{idx}] {endpoint} {rel[endpoint]!r} "
                    "does not reference a known node id",
                )


def _assert_valid_record_ref(value: Any, where: str) -> None:
    """Phase 2 stability rules for a single record_ref (NFM-267 §3.1)."""
    if not isinstance(value, str) or not value:
        raise ContractViolationError(
            f"{where} record_ref must be a non-empty string",
        )
    if _RECORD_REF_SCHEME_RE.match(value):
        raise ContractViolationError(
            f"{where} record_ref {value!r} must be relative (no scheme/host)",
        )
    if not value.startswith("/"):
        raise ContractViolationError(
            f"{where} record_ref {value!r} must be a root-relative path (start with '/')",
        )
    lowered = value.lower()
    for key in _RECORD_REF_FORBIDDEN_SUBSTRINGS:
        if key in lowered:
            raise ContractViolationError(
                f"{where} record_ref {value!r} must not carry transient/auth key {key!r}",
            )


def _assert_record_ref(nodes: list[Any], relationships: list[Any]) -> None:
    """Phase 2 deep-link firewall (NFM-267 §3.1 / §3.2).

    Material-individual nodes MUST carry a stable, shareable record_ref deep link.
    Any record_ref that IS present (on any node or relationship) must conform to
    the stability rules — non-empty, relative, free of transient/auth keys — so a
    link can never expire with a session. Class nodes omit it (no single record).
    """
    for node in nodes:
        ntype = node.get("type")
        nid = node.get("id")
        ref = node.get("record_ref")
        if ntype == "individual":
            if ref is None:
                raise ContractViolationError(
                    f"individual node {nid!r} must carry a record_ref deep link",
                )
            _assert_valid_record_ref(ref, f"node {nid!r}")
        elif ref is not None:
            _assert_valid_record_ref(ref, f"node {nid!r}")
    for rel in relationships:
        ref = rel.get("record_ref")
        if ref is not None:
            _assert_valid_record_ref(ref, f"relationship {rel.get('id')!r}")


def _assert_stats_consistency(
    payload: dict[str, Any],
    nodes: list[Any],
    relationships: list[Any],
) -> None:
    stats = payload["stats"]
    expected = {
        "nodes": len(nodes),
        "relationships": len(relationships),
        "classes": sum(1 for n in nodes if n.get("type") == "class"),
        "individuals": sum(1 for n in nodes if n.get("type") == "individual"),
    }
    for field, value in expected.items():
        if stats[field] != value:
            raise ContractViolationError(
                f"stats.{field}={stats[field]} inconsistent with payload ({value})",
            )
