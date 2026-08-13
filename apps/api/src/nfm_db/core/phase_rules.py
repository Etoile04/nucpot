"""PhaseMapper — three-step inference for nuclear material phase identification.

Ported from v4 phase_rules.md into NFMD Python code.

Three-step inference:
  Step 1 — Direct mapping: raw_phase in alias table → return canonical
  Step 2 — Material inference: phase empty but material implies → infer
  Step 3 — Context clues: extract phase keywords from context text

If all three steps fail → return None (never guess).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class _MappingEntry:
    """A single canonical phase with its aliases."""

    canonical: str
    category: str
    aliases: frozenset[str]

    @staticmethod
    def from_dict(data: dict[str, Any]) -> _MappingEntry:
        return _MappingEntry(
            canonical=data["canonical"],
            category=data.get("category", "unknown"),
            aliases=frozenset(data.get("aliases", [])),
        )


@dataclass(frozen=True)
class _LaTeXRule:
    """A single LaTeX→plain-text replacement pattern."""

    pattern: str
    replacement: str


@dataclass(frozen=True)
class PhaseMapper:
    """Infers canonical phase names using three-step reasoning.

    Usage::

        mapper = PhaseMapper.from_config("path/to/phase_mapping.json")
        result = mapper.infer_phase("α-Zr", "Zircaloy-4", "matrix measured at 300K")
        assert result == "alpha"
    """

    _alias_to_canonical: dict[str, str] = field(default_factory=dict)
    _canonical_set: frozenset[str] = field(default_factory=frozenset)
    _not_phase: frozenset[str] = field(default_factory=frozenset)
    _latex_rules: tuple[_LaTeXRule, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @staticmethod
    def from_config(path: Path) -> PhaseMapper:
        """Load phase mapping rules from a JSON config file.

        Args:
            path: Path to the JSON config file.

        Returns:
            A frozen PhaseMapper instance.

        Raises:
            FileNotFoundError: If the config file does not exist.
        """
        if not path.exists():
            raise FileNotFoundError(f"Phase mapping config not found: {path}")

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        return PhaseMapper.from_dict(data)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> PhaseMapper:
        """Build a PhaseMapper from a raw dict (for testing / inline config).

        Args:
            data: Dict with keys 'mappings', 'laTeX_patterns', 'not_phase'.

        Returns:
            A frozen PhaseMapper instance.
        """
        alias_to_canonical: dict[str, str] = {}
        canonical_set: set[str] = set()

        for entry_data in data.get("mappings", []):
            entry = _MappingEntry.from_dict(entry_data)
            canonical_set.add(entry.canonical)
            # Register canonical → itself (case-insensitive)
            alias_to_canonical[entry.canonical.lower()] = entry.canonical
            for alias in entry.aliases:
                alias_to_canonical[alias.lower()] = entry.canonical

        latex_rules = tuple(
            _LaTeXRule(pattern=r["pattern"], replacement=r["replacement"])
            for r in data.get("laTeX_patterns", [])
        )

        not_phase = frozenset(item.lower() for item in data.get("not_phase", []))

        return PhaseMapper(
            _alias_to_canonical=dict(alias_to_canonical),
            _canonical_set=frozenset(canonical_set),
            _not_phase=not_phase,
            _latex_rules=latex_rules,
        )

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def canonical_phases(self) -> frozenset[str]:
        """The set of all canonical phase names in the mapping."""
        return self._canonical_set

    # ------------------------------------------------------------------
    # Core: infer_phase (three-step)
    # ------------------------------------------------------------------

    def infer_phase(
        self,
        raw_phase: str | None,
        material: str | None,
        context: str | None,
    ) -> str | None:
        """Infer a canonical phase using three-step reasoning.

        Step 1 — Direct mapping: If raw_phase matches a known alias,
                 return the canonical name immediately.
        Step 2 — Material inference: If phase is empty but material
                 implies a phase, return the inferred result.
                 (Currently returns None — no universal material→phase rule.)
        Step 3 — Context clues: Scan context text for known phase
                 keywords and return the first match.

        If all three steps fail, return None.

        Args:
            raw_phase: The phase string as reported in the source.
            material: The material name (e.g. "Zircaloy-4").
            context: Free-text context (paragraph, table caption, etc.)

        Returns:
            Canonical phase name, or None if inference is impossible.
        """
        # --- Step 1: Direct mapping ---
        step1 = self._step1_direct(raw_phase)
        if step1 is not None:
            return step1

        # --- Step 2: Material inference ---
        step2 = self._step2_material(material)
        if step2 is not None:
            return step2

        # --- Step 3: Context clues ---
        step3 = self._step3_context(context)
        if step3 is not None:
            return step3

        return None

    # ------------------------------------------------------------------
    # Step 1: Direct alias → canonical mapping
    # ------------------------------------------------------------------

    def _step1_direct(self, raw_phase: str | None) -> str | None:
        """Look up raw_phase in the alias→canonical table.

        Applies LaTeX cleaning before lookup.
        """
        if not raw_phase or not raw_phase.strip():
            return None

        cleaned = self._clean_latex(raw_phase.strip())
        lower = cleaned.lower()

        # Reject known non-phase terms
        if lower in self._not_phase:
            return None

        return self._alias_to_canonical.get(lower)

    # ------------------------------------------------------------------
    # Step 2: Material-based inference
    # ------------------------------------------------------------------

    def _step2_material(self, material: str | None) -> str | None:
        """Infer phase from material name alone.

        Per v4 rules, material name alone does NOT determine phase
        (e.g. "Zircaloy-4" has no inherent phase). This step always
        returns None to enforce the "don't guess" rule.
        """
        return None

    # ------------------------------------------------------------------
    # Step 3: Context clue extraction
    # ------------------------------------------------------------------

    def _step3_context(self, context: str | None) -> str | None:
        """Scan context text for the first known phase keyword.

        Searches each alias against the context. Returns the canonical
        name for the first match found. Longer aliases are checked first
        to avoid partial matches (e.g. "delta-hydride" before "delta").
        """
        if not context or not context.strip():
            return None

        # Sort aliases by length descending — longest match wins
        sorted_aliases = sorted(
            self._alias_to_canonical.keys(),
            key=len,
            reverse=True,
        )

        lower_context = context.lower()
        for alias in sorted_aliases:
            if alias in lower_context:
                return self._alias_to_canonical[alias]

        return None

    # ------------------------------------------------------------------
    # LaTeX cleaning
    # ------------------------------------------------------------------

    def _clean_latex(self, text: str) -> str:
        """Replace known LaTeX patterns with plain-text equivalents.

        Pattern strings are treated as literal text (regex special chars
        like ``$`` are escaped) because they represent exact LaTeX
        markup, not regex patterns.

        Also normalizes Unicode subscripts to ASCII (e.g. ₄ → 4).
        """
        result = text
        for rule in self._latex_rules:
            # Escape the pattern so $, {, } etc. are literal
            literal_pattern = re.escape(rule.pattern)
            result = re.sub(literal_pattern, rule.replacement, result)

        # Unicode subscript digits → ASCII
        _sub_map: dict[str, str] = {
            "₀": "0",
            "₁": "1",
            "₂": "2",
            "₃": "3",
            "₄": "4",
            "₅": "5",
            "₆": "6",
            "₇": "7",
            "₈": "8",
            "₉": "9",
        }
        result = result.translate(str.maketrans(_sub_map))  # type: ignore[arg-type]

        return result
