"""Extraction-method provenance vocabulary and helpers (NFM-2247).

Every item surfaced in the literature-detail ``extraction_results`` array
carries a provenance value describing *how it was produced* — distinct from the
``source_page`` / ``source_paragraph`` / ``source_doi`` fields, which describe
*where in the document* it came from.

Three tokens are defined, matching the contract the frontend
``ProvenanceBadge`` already accepts:

``llm``
    Produced by the LLM extraction pipeline (``kg_re``, ``entity_linker``).
``manual``
    Authored or corrected by a human through the review endpoints.
``mineru``
    Produced by the MinerU / VLM figure-and-plot extraction pipeline.

Several tokens can apply to one item: an LLM-extracted value later corrected by
a reviewer is ``llm,manual``. The persisted column stores the canonical
comma-joined string; the API emits the parsed list. Precedence
(``manual`` > ``mineru`` > ``llm``) is applied client-side, so the server only
needs to keep the set stable and diffable.

An absent or unrecognised value means *unknown* and is emitted as an empty
list. That is deliberate: it is the documented backfill for rows written before
this column existed, and the badge renders it as ``来源未知`` rather than
guessing. Provenance is never inferred from ``confidence`` or ``review_status``.
"""

from __future__ import annotations

from collections.abc import Iterable

PROVENANCE_LLM = "llm"
PROVENANCE_MANUAL = "manual"
PROVENANCE_MINERU = "mineru"

#: Canonical ordering. ``parse_provenance`` sorts against this so the same set
#: of tokens always serialises identically regardless of write order.
KNOWN_PROVENANCE: tuple[str, ...] = (
    PROVENANCE_LLM,
    PROVENANCE_MANUAL,
    PROVENANCE_MINERU,
)

_SEPARATOR = ","


def parse_provenance(raw: str | Iterable[str] | None) -> list[str]:
    """Normalise a persisted provenance value into a list of known tokens.

    Accepts the comma-joined string form used in the database, an already-split
    iterable, or ``None``. Matching is case-insensitive and whitespace
    tolerant. Unknown tokens are dropped — the badge cannot render them, and
    forwarding them would make the wire contract unverifiable.

    Args:
        raw: Persisted value, e.g. ``"llm,manual"``, ``["llm"]``, or ``None``.

    Returns:
        Known tokens in canonical order. Empty when the value is absent or
        holds nothing recognisable, which the client reads as unknown.
    """
    if raw is None:
        return []

    parts = raw.split(_SEPARATOR) if isinstance(raw, str) else raw
    found = {
        token
        for token in (str(part).strip().lower() for part in parts)
        if token in KNOWN_PROVENANCE
    }
    return [token for token in KNOWN_PROVENANCE if token in found]


def add_provenance(raw: str | Iterable[str] | None, token: str) -> str:
    """Return ``raw`` with ``token`` added, as a canonical comma-joined string.

    Idempotent: adding a token that is already present is a no-op, so a row
    corrected twice does not accumulate duplicates.

    Args:
        raw: Existing persisted value, or ``None`` for a fresh row.
        token: One of :data:`KNOWN_PROVENANCE`.

    Returns:
        The canonical comma-joined value to persist.

    Raises:
        ValueError: If ``token`` is not a known provenance token. Callers pass
            module constants, so this only fires on a programming error.
    """
    normalised = token.strip().lower()
    if normalised not in KNOWN_PROVENANCE:
        raise ValueError(
            f"unknown provenance token {token!r}; expected one of {KNOWN_PROVENANCE}"
        )
    return _SEPARATOR.join(parse_provenance([*parse_provenance(raw), normalised]))
