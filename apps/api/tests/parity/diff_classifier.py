"""Diff classifier for the V1/V2 snapshot diff harness (NFM-3581).

Consumes a structured `PromptDiff` (V1 vs V2 prompt outputs) and returns a
verdict classifying the diff as PASS / WARN / FAIL with severity COSMETIC /
NON_COSMETIC / BLOCKING.

Rules (in priority order):
1. V2 missed an ontology-defined canonical property (V2 rendered < ontology) ⇒ FAIL + BLOCKING
2. Retry-count regression (V2 retries > V1 retries by ≥1) ⇒ FAIL + BLOCKING
3. V1-only property names (hardcoded Chinese aliases dropped by V2) ⇒ WARN + NON_COSMETIC
4. V2-only property names (new canonical English names V2 added) ⇒ WARN + NON_COSMETIC
5. Category swap (any category in only-V1 or only-V2) ⇒ WARN + NON_COSMETIC
6. Comment text delta that survives whitespace normalization ⇒ WARN + NON_COSMETIC
7. Comment text delta that is whitespace/punctuation only ⇒ PASS + COSMETIC
8. Identical (no deltas) ⇒ PASS + NONE

The classifier is intentionally pure and deterministic — no I/O, no logging,
no hidden state. Same input always yields the same `Classification`.

Important: the V1 path renders Chinese alias names from `property_mapping.json`,
while V2 renders canonical English names from the ontology's
`entity_types[].required_properties`. Naming-convention deltas (Rule 3 + 4) are
**expected post-NFM-3258 behavior** and are classified WARN, not BLOCKING.
A property is only BLOCKING if V2 omits something the ontology actually defines
(Rule 1: `properties_in_ontology - V2_rendered != ∅`).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum


class Status(Enum):
    """Bucket for the per-input verdict."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class Severity(Enum):
    """Severity annotation explaining why the verdict was assigned."""

    NONE = "NONE"
    COSMETIC = "COSMETIC"
    NON_COSMETIC = "NON_COSMETIC"
    BLOCKING = "BLOCKING"


@dataclass(frozen=True)
class PromptDiff:
    """Structured diff between V1 and V2 prompt outputs.

    All set fields hold token names (categories or properties) extracted from
    the rendered prompts. `comment_diff_lines` carries the unified-diff body
    (lines starting with '-' or '+') for the comment / prose section only
    (not the dynamic ontology blocks).

    `properties_in_ontology` is the union of properties defined by the input
    ontology (property_categories[].standard_properties + entity_types[].required_properties).
    It is used to distinguish "V2 correctly dropped a hardcoded V1 property
    that the ontology never defined" (non-cosmetic, expected) from "V2
    dropped a property that was both hardcoded AND in the ontology"
    (blocking, indicates a bug in V2's ontology-sourcing code).
    """

    categories_only_in_v1: set[str]
    categories_only_in_v2: set[str]
    categories_shared: set[str]

    properties_only_in_v1: set[str]
    properties_only_in_v2: set[str]
    properties_shared: set[str]

    properties_in_ontology: set[str]

    comment_diff_lines: list[str]

    retry_count_v1: int
    retry_count_v2: int

    prompt_length_v1: int
    prompt_length_v2: int


@dataclass(frozen=True)
class Classification:
    """Per-input verdict — drives the diff report row."""

    status: Status
    severity: Severity
    deltas: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_cosmetic_comment_diff(comment_diff_lines: Sequence[str]) -> bool:
    """True if every +/- pair differs only in whitespace or punctuation.

    We define "cosmetic" as: stripping trailing whitespace and all
    non-word, non-CJK punctuation makes the + and - lines identical for
    every +/- pair. This catches trivial whitespace/quote/bracket changes
    that don't change semantics.
    """

    def _normalize(line: str) -> str:
        # Drop the leading diff marker
        body = line[1:] if line and line[0] in "-+" else line
        # Strip all whitespace and ASCII punctuation except CJK characters
        return re.sub(r"[\s\.\,\;\:\?\!\(\)\[\]\{\}\"\'\`\-\\\/]", "", body)

    minus_lines = [ln for ln in comment_diff_lines if ln.startswith("-")]
    plus_lines = [ln for ln in comment_diff_lines if ln.startswith("+")]

    if not minus_lines and not plus_lines:
        return True

    # Pair them up by index; if counts differ, treat as non-cosmetic
    if len(minus_lines) != len(plus_lines):
        return False

    for minus, plus in zip(minus_lines, plus_lines, strict=True):
        if _normalize(minus) != _normalize(plus):
            return False

    return True


def _summarize_property_drops(diff: PromptDiff) -> list[str]:
    """Return human-readable bullets for properties V2 is missing."""
    return [f"property lost in V2: {p}" for p in sorted(diff.properties_only_in_v1)]


def _summarize_property_adds(diff: PromptDiff) -> list[str]:
    """Return human-readable bullets for properties V2 added."""
    return [f"property added in V2: {p}" for p in sorted(diff.properties_only_in_v2)]


def _summarize_category_changes(diff: PromptDiff) -> list[str]:
    """Return human-readable bullets for category drift."""
    out: list[str] = []
    for cat in sorted(diff.categories_only_in_v1):
        out.append(f"category removed in V2: {cat}")
    for cat in sorted(diff.categories_only_in_v2):
        out.append(f"category added in V2: {cat}")
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_diff(diff: PromptDiff) -> Classification:
    """Apply the classification rules in priority order and return the verdict.

    The classifier is pure: same input always yields the same Classification.

    Blocking-vs-informational rule for property drops:
    A property V1 had but V2 lacks is BLOCKING only if that property is ALSO
    defined in the input ontology's canonical set (properties_in_ontology =
    entity_types[].required_properties). Properties that V1 had from
    property_mapping.json but the ontology never defined are intentionally
    dropped by V2 — that is the C1 fix, not a regression.
    """
    deltas: list[str] = []
    notes: list[str] = []

    # --- Rule 1: V2 missed an ontology-defined canonical property (BLOCKING)
    # A real coverage regression: the ontology's required_properties set
    # contains a name that V2's rendered prompt does NOT contain.
    v2_rendered = diff.properties_only_in_v2 | diff.properties_shared
    missing_from_v2 = diff.properties_in_ontology - v2_rendered
    if missing_from_v2:
        deltas.extend(
            f"property defined in ontology but missing from V2 prompt: {p}"
            for p in sorted(missing_from_v2)
        )
        return Classification(
            status=Status.FAIL,
            severity=Severity.BLOCKING,
            deltas=deltas,
            notes=[
                "V2's rendered standard-names block omits one or more properties "
                "that the ontology's entity_types[].required_properties set "
                "defines. This is a real coverage regression — V2's "
                "ontology-sourcing code failed to emit these names."
            ],
        )

    # --- Rule 2: Retry regression (BLOCKING / FAIL) ------------------------
    if diff.retry_count_v2 > diff.retry_count_v1 + 1:
        deltas.append(
            f"retry count regressed: V1={diff.retry_count_v1}, V2={diff.retry_count_v2}"
        )
        return Classification(
            status=Status.FAIL,
            severity=Severity.BLOCKING,
            deltas=deltas,
            notes=[
                "V2 needs ≥2 more retries than V1 — likely degraded prompt clarity."
            ],
        )

    # --- Rule 3: V1-only property names dropped by V2 (WARN) ---------------
    # V1 had hardcoded Chinese alias names from property_mapping.json.
    # V2 dropped them in favor of the ontology's canonical English names.
    # This is the C1 fix and is EXPECTED.
    if diff.properties_only_in_v1:
        sample = sorted(diff.properties_only_in_v1)[:5]
        n = len(diff.properties_only_in_v1)
        deltas.append(
            f"V1 hardcoded {n} Chinese-alias property name(s) V2 dropped "
            f"(e.g., {', '.join(f'`{p}`' for p in sample)}{'...' if n > 5 else ''}). "
            "Expected post-NFM-3258: V2 sources canonical English names from the "
            "ontology rather than the hardcoded Chinese alias table."
        )

    # --- Rule 4: V2-only property names added (WARN) ----------------------
    # V2 added canonical English names from the ontology that V1 never had.
    # This is also part of the C1 fix and is EXPECTED.
    if diff.properties_only_in_v2:
        sample = sorted(diff.properties_only_in_v2)[:5]
        n = len(diff.properties_only_in_v2)
        deltas.append(
            f"V2 added {n} canonical English property name(s) not in V1's hardcoded "
            f"list (e.g., {', '.join(f'`{p}`' for p in sample)}{'...' if n > 5 else ''})."
        )

    # --- Rule 5: Category drift (NON_COSMETIC / WARN) ----------------------
    if diff.categories_only_in_v1 or diff.categories_only_in_v2:
        deltas.extend(_summarize_category_changes(diff))

    # --- Rule 6/7: Comment text delta --------------------------------------
    if diff.comment_diff_lines:
        if _is_cosmetic_comment_diff(diff.comment_diff_lines):
            notes.append("comment text differs only in whitespace/punctuation (cosmetic)")
            if not deltas:
                return Classification(
                    status=Status.PASS,
                    severity=Severity.COSMETIC,
                    deltas=[],
                    notes=notes,
                )
        else:
            notes.append("comment text differs semantically (non-cosmetic)")
            if not deltas:
                deltas.append("comment text differs between V1 and V2")

    # --- Rule 8: Final status ---------------------------------------------
    if deltas:
        return Classification(
            status=Status.WARN,
            severity=Severity.NON_COSMETIC,
            deltas=deltas,
            notes=notes,
        )

    return Classification(status=Status.PASS, severity=Severity.NONE, notes=notes)
