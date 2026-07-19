"""Materials Project API client with local JSON caching.

Wraps the ``mp-api`` (Materials Project API v2) to fetch DFT-calculated
properties for alloy compositions, converting results to the DFT export
format defined in §3 of the export specification.

Caches results locally as JSON files keyed by SHA256 hash of the
composition dictionary (sorted elements).  DFT data is immutable per
material, so no TTL is applied.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from nfm_db.ml.feature_engineering import calculate_lattice_distortion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — Materials Project defaults
# ---------------------------------------------------------------------------

DEFAULT_CUTOFF_ENERGY: float = 500.0
DEFAULT_KPOINT_DENSITY: str = "MP-standard"
DEFAULT_CODE: str = "VASP"

# Backoff schedule for rate-limit retries (seconds)
_BACKOFF_DELAYS: List[float] = [1.0, 2.0, 4.0, 8.0, 30.0]
_MAX_RETRIES: int = len(_BACKOFF_DELAYS)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MPEntry:
    """Immutable record for a single Materials Project database entry."""

    material_id: str
    composition: Dict[str, float]
    formation_energy_per_atom: float
    formation_energy_uncertainty: Optional[float]
    lattice_constants: Dict[str, float]
    lattice_type: Optional[str]
    functional: str
    band_gap: Optional[float]
    is_gap_direct: Optional[bool]
    e_above_hull: Optional[float]


@dataclass(frozen=True)
class SupplementaryRecord:
    """DFT export record matching §3 of the export specification.

    Produced by converting :class:`MPEntry` instances into the flat CSV/JSONL
    format expected by the ML training pipeline.
    """

    element_system: str
    composition: str
    phase: str
    functional: str
    formation_energy: float
    formation_energy_uncertainty: Optional[float]
    cohesive_energy: Optional[float]
    lattice_constant_a: float
    lattice_constant_b: Optional[float]
    lattice_constant_c: Optional[float]
    lattice_distortion: float
    source_id: str
    cutoff_energy: float
    kpoint_density: str
    code: str


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def composition_cache_key(composition: Dict[str, float]) -> str:
    """Generate a deterministic SHA256 hex digest for a composition dict.

    Elements are sorted before hashing so that ``{"U":70,"Zr":30}`` and
    ``{"Zr":30,"U":70}`` produce the same key.

    Args:
        composition: Element symbol to atomic-percent mapping.

    Returns:
        64-character lowercase hex string.
    """
    canonical = json.dumps(composition, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cache_file_path(cache_dir: str, key: str) -> str:
    """Return the absolute path for a cache file given the directory and key."""
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"{key}.json")


def write_cache_entry(
    cache_dir: str,
    composition: Dict[str, float],
    entries: List[MPEntry],
) -> None:
    """Write MPEntry list to the JSON file cache.

    Args:
        cache_dir: Directory for cache files.
        composition: Composition used as the cache key.
        entries: List of MPEntry objects to cache.
    """
    key = composition_cache_key(composition)
    path = _cache_file_path(cache_dir, key)

    data = [
        {
            "material_id": e.material_id,
            "composition": e.composition,
            "formation_energy_per_atom": e.formation_energy_per_atom,
            "formation_energy_uncertainty": e.formation_energy_uncertainty,
            "lattice_constants": e.lattice_constants,
            "lattice_type": e.lattice_type,
            "functional": e.functional,
            "band_gap": e.band_gap,
            "is_gap_direct": e.is_gap_direct,
            "e_above_hull": e.e_above_hull,
        }
        for e in entries
    ]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def read_cache_entry(
    cache_dir: str,
    composition: Dict[str, float],
) -> Optional[List[MPEntry]]:
    """Read cached MPEntry list for a given composition.

    Args:
        cache_dir: Directory for cache files.
        composition: Composition used as the cache key.

    Returns:
        List of MPEntry objects, or ``None`` if no cache hit.
    """
    key = composition_cache_key(composition)
    path = _cache_file_path(cache_dir, key)

    if not os.path.isfile(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return [
            MPEntry(
                material_id=item["material_id"],
                composition=item["composition"],
                formation_energy_per_atom=item["formation_energy_per_atom"],
                formation_energy_uncertainty=item.get("formation_energy_uncertainty"),
                lattice_constants=item["lattice_constants"],
                lattice_type=item.get("lattice_type"),
                functional=item["functional"],
                band_gap=item.get("band_gap"),
                is_gap_direct=item.get("is_gap_direct"),
                e_above_hull=item.get("e_above_hull"),
            )
            for item in data
        ]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Corrupt cache file %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Transformation
# ---------------------------------------------------------------------------


def mp_entry_to_supplementary_record(entry: MPEntry) -> SupplementaryRecord:
    """Convert an MPEntry into a SupplementaryRecord matching DFT export spec §3.

    Computes:
    - ``element_system``: sorted, hyphen-joined element symbols
    - ``composition``: JSON-encoded at.% string
    - ``phase``: from lattice_type (uppercase), defaults to "Unknown"
    - ``lattice_distortion``: via feature_engineering.calculate_lattice_distortion
    - ``source_id``: ``SUPPL-MP-{material_id}``

    Args:
        entry: A Materials Project entry.

    Returns:
        SupplementaryRecord matching the DFT export format.
    """
    elements = sorted(entry.composition.keys())
    element_system = "-".join(elements)
    composition_json = json.dumps(entry.composition, sort_keys=True)
    phase = (entry.lattice_type or "Unknown").upper()

    distortion = calculate_lattice_distortion(entry.composition)

    return SupplementaryRecord(
        element_system=element_system,
        composition=composition_json,
        phase=phase,
        functional=entry.functional,
        formation_energy=entry.formation_energy_per_atom,
        formation_energy_uncertainty=entry.formation_energy_uncertainty,
        cohesive_energy=None,
        lattice_constant_a=entry.lattice_constants.get("a", 0.0),
        lattice_constant_b=entry.lattice_constants.get("b"),
        lattice_constant_c=entry.lattice_constants.get("c"),
        lattice_distortion=distortion,
        source_id=f"SUPPL-MP-{entry.material_id}",
        cutoff_energy=DEFAULT_CUTOFF_ENERGY,
        kpoint_density=DEFAULT_KPOINT_DENSITY,
        code=DEFAULT_CODE,
    )


# ---------------------------------------------------------------------------
# API query functions
# ---------------------------------------------------------------------------


def _validate_api_key(api_key: str) -> None:
    """Raise ValueError if API key is missing or empty.

    Args:
        api_key: Materials Project API key.

    Raises:
        ValueError: If the key is falsy.
    """
    if not api_key or not api_key.strip():
        raise ValueError(
            "MP_API_KEY is required but was not provided. "
            "Get your API key from https://materialsproject.org/open "
            "and set it as the MP_API_KEY environment variable."
        )


def _parse_summary_to_entries(
    summaries: List[Any],
) -> List[MPEntry]:
    """Convert raw MP summary docs to MPEntry list.

    Args:
        summaries: Raw summary documents from MPRester.materials.summary.

    Returns:
        List of MPEntry objects.
    """
    entries: List[MPEntry] = []

    for doc in summaries:
        try:
            lattice = doc.structure.lattice if doc.structure else None
            abc = list(lattice.abc) if lattice and hasattr(lattice, "abc") else [0.0]

            lattice_constants: Dict[str, float] = {}
            if len(abc) >= 1:
                lattice_constants["a"] = abc[0]
            if len(abc) >= 2:
                lattice_constants["b"] = abc[1]
            if len(abc) >= 3:
                lattice_constants["c"] = abc[2]

            comp_reduced = (
                doc.composition_reduced
                if hasattr(doc, "composition_reduced")
                else {}
            )
            composition_at = {
                el: frac * 100.0 for el, frac in comp_reduced.items()
            }

            entry = MPEntry(
                material_id=doc.material_id,
                composition=composition_at,
                formation_energy_per_atom=doc.formation_energy_per_atom,
                formation_energy_uncertainty=getattr(
                    doc, "formation_energy_per_atom_uncertainty", None
                ),
                lattice_constants=lattice_constants,
                lattice_type=(
                    getattr(lattice, "type", None) if lattice else None
                ),
                functional="PBE",
                band_gap=doc.band_gap,
                is_gap_direct=(
                    doc.is_gap_direct
                    if hasattr(doc, "is_gap_direct")
                    else None
                ),
                e_above_hull=doc.e_above_hull,
            )
            entries.append(entry)

        except (AttributeError, TypeError) as exc:
            logger.warning("Failed to parse MP summary document: %s", exc)

    return entries


def _parse_entry_doc_to_mp_entry(doc: Any) -> MPEntry:
    """Convert a single MP entry doc to MPEntry.

    Args:
        doc: Raw entry document from MPRester.materials.get_entry_by_id.

    Returns:
        A single MPEntry.
    """
    lattice = doc.structure.lattice if doc.structure else None
    abc = list(lattice.abc) if lattice and hasattr(lattice, "abc") else [0.0]

    lattice_constants: Dict[str, float] = {}
    if len(abc) >= 1:
        lattice_constants["a"] = abc[0]
    if len(abc) >= 2:
        lattice_constants["b"] = abc[1]
    if len(abc) >= 3:
        lattice_constants["c"] = abc[2]

    comp_reduced = (
        doc.composition_reduced if hasattr(doc, "composition_reduced") else {}
    )
    composition_at = {el: frac * 100.0 for el, frac in comp_reduced.items()}

    return MPEntry(
        material_id=doc.material_id,
        composition=composition_at,
        formation_energy_per_atom=doc.formation_energy_per_atom,
        formation_energy_uncertainty=getattr(
            doc, "formation_energy_per_atom_uncertainty", None
        ),
        lattice_constants=lattice_constants,
        lattice_type=getattr(lattice, "type", None) if lattice else None,
        functional="PBE",
        band_gap=doc.band_gap,
        is_gap_direct=doc.is_gap_direct if hasattr(doc, "is_gap_direct") else None,
        e_above_hull=doc.e_above_hull,
    )


def fetch_entries_by_composition(
    composition: Dict[str, float],
    api_key: str,
) -> List[MPEntry]:
    """Query the Materials Project API for entries matching a composition.

    Uses nearest-neighbor search via ``MPRester.materials.summary``.
    Implements exponential backoff on rate-limit errors (429).

    Args:
        composition: Element symbol to at.% mapping.
        api_key: Materials Project API key.

    Returns:
        List of matching MPEntry objects. Empty list if no matches.

    Raises:
        ValueError: If api_key is missing.
    """
    _validate_api_key(api_key)

    chemsys = "-".join(sorted(composition.keys()))

    from mp_api.client import MPRester

    for attempt in range(_MAX_RETRIES):
        try:
            mpr = MPRester(api_key)
            docs = mpr.materials.summary(
                chemsys=chemsys,
                fields=[
                    "material_id",
                    "composition_reduced",
                    "formation_energy_per_atom",
                    "formation_energy_per_atom_uncertainty",
                    "structure",
                    "band_gap",
                    "is_gap_direct",
                    "e_above_hull",
                ],
            )
            return _parse_summary_to_entries(docs)

        except Exception as exc:
            is_rate_limit = (
                hasattr(exc, "response")
                and getattr(exc.response, "status_code", None) == 429
            )
            if is_rate_limit and attempt < _MAX_RETRIES - 1:
                delay = _BACKOFF_DELAYS[attempt]
                logger.warning(
                    "MP API rate limited. Retrying in %.1fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                time.sleep(delay)
                continue

            logger.error("MP API query failed after %d attempts: %s", attempt + 1, exc)
            return []

    return []


def fetch_by_material_id(
    material_id: str,
    api_key: str,
) -> MPEntry:
    """Look up a single material by its Materials Project ID.

    Args:
        material_id: MP material ID (e.g. ``"mp-1234"``).
        api_key: Materials Project API key.

    Returns:
        A single MPEntry.

    Raises:
        ValueError: If api_key is missing.
        RuntimeError: If the material is not found.
    """
    _validate_api_key(api_key)

    from mp_api.client import MPRester

    mpr = MPRester(api_key)
    doc = mpr.materials.get_entry_by_id(material_id)
    return _parse_entry_doc_to_mp_entry(doc)


# ---------------------------------------------------------------------------
# Batch query with caching
# ---------------------------------------------------------------------------


def batch_query(
    compositions: List[Dict[str, float]],
    api_key: str,
    cache_dir: str,
) -> List[SupplementaryRecord]:
    """Batch-query the MP API for multiple compositions with caching.

    For each composition:
    1. Check the local JSON cache — return cached results if present.
    2. If cache miss, query the MP API.
    3. Write API results to cache for future lookups.
    4. Convert all MPEntry results to SupplementaryRecord format.

    Args:
        compositions: List of element-to-at.% dicts.
        api_key: Materials Project API key (may be empty for cache-only).
        cache_dir: Path to the cache directory.

    Returns:
        List of SupplementaryRecord objects across all compositions.
    """
    if not compositions:
        return []

    if not api_key or not api_key.strip():
        logger.warning(
            "MP_API_KEY not set — returning empty results. "
            "Set the key to enable Materials Project queries."
        )
        return []

    records: List[SupplementaryRecord] = []

    for composition in compositions:
        cached = read_cache_entry(cache_dir, composition)
        if cached is not None:
            logger.debug("Cache hit for composition: %s", composition)
            entries = cached
        else:
            logger.info("Cache miss for composition: %s — querying MP API", composition)
            entries = fetch_entries_by_composition(composition, api_key)
            if entries:
                write_cache_entry(cache_dir, composition, entries)

        for entry in entries:
            record = mp_entry_to_supplementary_record(entry)
            records.append(record)

    return records
