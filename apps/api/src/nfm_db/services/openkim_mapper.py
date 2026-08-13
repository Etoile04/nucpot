"""Pure functions mapping OpenKIM model records → potential schemas.

OpenKIM models are identified by KIM IDs of the form ``MO_<digits>_<version>``.
Long names follow the convention::

    <Prefix>_<Authors>_<Year>_<Elements>__MO_<digits>_<version>

e.g. ``EAM_Dynamo_ErcolessiAdams_1994_Al__MO_123629422045_006``.

All IDs are mapped to a deterministic UUID via
``uuid5(NAMESPACE_URL, "openkim:"+kim_id)`` so the detail route stays
UUID-typed with zero router change.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from nfm_db.schemas.potential import PotentialDetail, PotentialSummary

OPENKIM_NAMESPACE_PREFIX = "openkim:"

# Match the trailing KIM ID portion (__MO_<digits>_<version>) of a long name.
_KIM_ID_RE = re.compile(r"(MO_\d+_\d+)$")
# Long-name convention: ..._<year>_<elements>__MO_<digits>_<version>
_LONG_NAME_RE = re.compile(
    r"^(?P<prefix>[A-Za-z0-9]+)"  # potential type prefix
    r"(?:_[A-Za-z0-9]+)*?"  # author/variant segments (non-greedy)
    r"_(?P<year>\d{4})"
    r"_?(?P<elements>[A-Z][a-z]*(?:[A-Z][a-z]*)*)"
    r"__MO_(?P<digits>\d+_\d+)$"
)


def openkim_potential_id(kim_id: str) -> uuid.UUID:
    """Deterministic UUID for an OpenKIM KIM ID."""
    return uuid.uuid5(uuid.NAMESPACE_URL, OPENKIM_NAMESPACE_PREFIX + kim_id)


def extract_kim_id(value: str) -> str | None:
    """Extract a canonical KIM ID (``MO_<digits>_<version>``) from any string.

    Accepts both short KIM IDs and full long names.
    Returns None if no KIM ID can be found.
    """
    if not isinstance(value, str):
        return None
    m = _KIM_ID_RE.search(value)
    return m.group(1) if m else None


def _parse_long_name(long_name: str) -> tuple[str, list[str]]:
    """Return (potential_type, elements) parsed from a KIM long name.

    Falls back to ("unknown", []) when parsing fails.
    """
    m = _LONG_NAME_RE.match(long_name)
    if m:
        prefix = m.group("prefix").lower()
        raw_elements = m.group("elements")
        elements = re.findall(r"[A-Z][a-z]*", raw_elements)
        return prefix, elements
    # Fallback: just use the first segment as type
    prefix = long_name.split("_", 1)[0].lower() if "_" in long_name else "unknown"
    return prefix, []


def map_openkim_summary(value: str) -> PotentialSummary:
    """Map a KIM ID (short or long name) → a PotentialSummary.

    Raises ValueError when no KIM ID can be extracted.
    """
    kim_id = extract_kim_id(value)
    if kim_id is None:
        raise ValueError(f"cannot extract KIM ID from {value!r}")

    # Try to enrich from a long name; otherwise unknown.
    if value.startswith("MO_"):
        potential_type: str = "unknown"
        elements: list[str] = []
    else:
        potential_type, elements = _parse_long_name(value)

    return PotentialSummary(
        id=openkim_potential_id(kim_id),
        name=kim_id,
        display_name=value if value != kim_id else None,
        type=potential_type,
        elements=elements,
        provider="openkim",
    )


def map_openkim_model(model: dict[str, Any]) -> PotentialDetail:
    """Map an OpenKIM model-detail record → a PotentialDetail.

    Tolerates missing fields (defaults applied).  Raises ValueError only
    when no KIM ID can be determined (so callers can skip + log).

    Expected input shape (see ``tests/fixtures/openkim/model_detail_sample.json``)::

        {
            "kim_id": "MO_123629422045_006",
            "long_name": "EAM_Dynamo_..._Al__MO_123629422045_006",
            "title": "...",
            "authors": ["F. Ercolessi", "J. B. Adams"],
            "doi": "10.xxxx/xxxx",
            "description": "...",
            "species": ["Al"],
            "potential_type": "eam",
        }
    """
    kim_id = extract_kim_id(model.get("kim_id", "")) or extract_kim_id(
        model.get("long_name", "")
    )
    if kim_id is None:
        raise ValueError("OpenKIM model record has no usable KIM ID")

    long_name = model.get("long_name") or kim_id
    potential_type = (model.get("potential_type") or _parse_long_name(long_name)[0]).lower()
    species = model.get("species") or _parse_long_name(long_name)[1]
    authors = model.get("authors") or []
    developers = [{"name": a} for a in authors if a]

    return PotentialDetail(
        id=openkim_potential_id(kim_id),
        name=kim_id,
        display_name=model.get("title") or long_name,
        type=potential_type,
        elements=list(species),
        description=model.get("description"),
        provider="openkim",
        source=OPENKIM_NAMESPACE_PREFIX + kim_id,
        source_doi=model.get("doi"),
        developers=developers,
    )
