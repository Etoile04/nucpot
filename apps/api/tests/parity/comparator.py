"""Prompt diff comparator (NFM-3581).

Extracts the structured comparison surface from two rendered prompts:

  - Categories mentioned (under "## Property Categories" heading)
  - Standard property names mentioned (under "## Standard Property Names" heading)
  - Comment / prose text (everything OUTSIDE the dynamic blocks)
  - Retry counts (reported by the caller — both paths use a single attempt
    here since neither actually invokes the LLM; the harness reports 1/1 by
    default and lets callers override via `compare_prompts(..., retry_v1=,
    retry_v2=)`)
  - Properties defined in the input ontology (so the classifier can
    distinguish V2 dropping hardcoded-but-not-ontology properties from V2
    dropping ontology-defined properties)

Output is a `PromptDiff` (see diff_classifier.py). The comparator itself is
deterministic and pure — no I/O, no logging, no global state.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

from tests.parity.diff_classifier import PromptDiff

# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------

# Sections we treat as "comment text" — everything between these two markers
# in the prompt is the static prose + dynamic blocks. The dynamic blocks
# (Property Categories, Standard Property Names, 本体定义) are extracted
# separately so they're not compared as free-text diff lines.
_CATEGORIES_HEADING = "## Property Categories"
_STANDARD_NAMES_HEADING = "## Standard Property Names"
_FIELDS_HEADING = "## 字段规则"
_ONTOLOGY_HEADING = "## 本体定义"


@dataclass(frozen=True)
class _ExtractedSections:
    categories: set[str]
    properties: set[str]
    comment_text: str


def _extract_section_block(text: str, start_marker: str, end_marker: str | None) -> str:
    """Return the text between start_marker and end_marker (or EOF)."""
    start = text.find(start_marker)
    if start == -1:
        return ""
    start += len(start_marker)
    if end_marker is None:
        return text[start:]
    end = text.find(end_marker, start)
    if end == -1:
        return text[start:]
    return text[start:end]


def _extract_bulleted_items(block: str) -> set[str]:
    """Extract bullet items (`- ...`) from a block. Skips the empty placeholder."""
    items: set[str] = set()
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            # Strip the legacy [核心] / [支持] tag V1 added (post-NFM-3258 ontology
            # builders don't emit it). Stripping here lets us compare key sets
            # rather than cosmetic suffixes.
            value = re.sub(r"\s*\[(核心|支持)\]\s*$", "", value)
            if value:
                items.add(value)
    return items


def _extract_sections(prompt: str) -> _ExtractedSections:
    """Pull categories, property names, and comment text out of a prompt."""

    # Categories block: between "## Property Categories" and the next "##"
    categories_block = _extract_section_block(prompt, _CATEGORIES_HEADING, _STANDARD_NAMES_HEADING)
    categories = _extract_bulleted_items(categories_block)

    # Standard property names block: between heading and "## 字段规则"
    properties_block = _extract_section_block(prompt, _STANDARD_NAMES_HEADING, _FIELDS_HEADING)
    properties = _extract_bulleted_items(properties_block)

    # Comment text = prompt minus dynamic blocks (ontology + categories + standard names).
    # The ontology block only exists in V2 — V1 strips it entirely. Removing it
    # from both sides lets us diff the prose fairly.
    def _strip_block(text: str, marker: str, end_marker: str) -> str:
        idx = text.find(marker)
        if idx == -1:
            return text
        end = text.find(end_marker, idx)
        if end == -1:
            return text[:idx]
        return text[:idx] + text[end:]

    comment_text = _strip_block(prompt, _ONTOLOGY_HEADING, "## ")
    comment_text = _strip_block(comment_text, _CATEGORIES_HEADING, "## ")
    comment_text = _strip_block(comment_text, _STANDARD_NAMES_HEADING, "## ")

    return _ExtractedSections(
        categories=categories,
        properties=properties,
        comment_text=comment_text.strip(),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _properties_defined_in_ontology(ontology_data: dict[str, Any] | None) -> set[str]:
    """Return the set of properties defined by the input ontology that V2 must render.

    Mirrors the names V2's `build_ontology_extraction_prompt` actually emits:
    `entity_types[].required_properties` (the canonical English set).

    Why NOT include `property_categories[].standard_properties`:
        The Chinese aliases under `property_categories[].standard_properties` are
        informational only. V2 does NOT render them — V2 renders the canonical
        English names from `entity_types[].required_properties`. Including the
        Chinese aliases would create a false-positive coverage check: V1's
        hardcoded list (which is all Chinese) would overlap with those aliases
        and the classifier would falsely flag every Chinese-named V1 property
        as "defined in ontology".
    """
    if not ontology_data:
        return set()
    names: set[str] = set()
    for et in ontology_data.get("entity_types", []) or []:
        for prop in et.get("required_properties", []) or []:
            if prop:
                names.add(prop)
    return names


def compare_prompts(
    v1_prompt: str,
    v2_prompt: str,
    retry_count_v1: int = 1,
    retry_count_v2: int = 1,
    ontology_data: dict[str, Any] | None = None,
) -> PromptDiff:
    """Produce a structured diff between two rendered prompts.

    Args:
        v1_prompt: Full text from `build_v1_legacy_prompt(ontology_data)`.
        v2_prompt: Full text from `build_ontology_extraction_prompt(ontology_version)`.
        retry_count_v1: How many retries the V1 path needed (default 1, since
            the harness does not invoke an LLM).
        retry_count_v2: Same for V2.
        ontology_data: Input ontology payload (used to populate
            `properties_in_ontology` for the classifier). Pass `None` if the
            properties_in_ontology field is not relevant.

    Returns:
        PromptDiff ready for `classify_diff()`.
    """
    v1 = _extract_sections(v1_prompt)
    v2 = _extract_sections(v2_prompt)

    return PromptDiff(
        categories_only_in_v1=v1.categories - v2.categories,
        categories_only_in_v2=v2.categories - v1.categories,
        categories_shared=v1.categories & v2.categories,
        properties_only_in_v1=v1.properties - v2.properties,
        properties_only_in_v2=v2.properties - v1.properties,
        properties_shared=v1.properties & v2.properties,
        properties_in_ontology=_properties_defined_in_ontology(ontology_data),
        comment_diff_lines=list(
            difflib.unified_diff(
                v1.comment_text.splitlines(),
                v2.comment_text.splitlines(),
                lineterm="",
                n=0,
            )
        ),
        retry_count_v1=retry_count_v1,
        retry_count_v2=retry_count_v2,
        prompt_length_v1=len(v1_prompt),
        prompt_length_v2=len(v2_prompt),
    )
