"""Snapshot-diff harness entry point (NFM-3581).

Loads all `*.json` fixtures from `apps/api/tests/parity/golden/`, runs the
identical input through:

  - V1-hardcoded path: `build_v1_legacy_prompt(ontology_data)`
    (reconstructed pre-NFM-3258 behavior, see v1_legacy_prompt.py)
  - V2-ontology-only path: `build_ontology_extraction_prompt(ontology_version)`
    (current production path)

Then calls `compare_prompts()` to produce a `PromptDiff`, `classify_diff()` to
produce a verdict, and emits a markdown report at
`apps/api/tests/parity/reports/diff_report.md`.

Public entry points:
    run_diff() -> DiffReport
    run_diff_and_write_report(output_path) -> Path
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nfm_db.services.extraction_prompt import build_ontology_extraction_prompt
from tests.parity.comparator import compare_prompts
from tests.parity.diff_classifier import (
    Classification,
    PromptDiff,
    Severity,
    Status,
    classify_diff,
)
from tests.parity.v1_legacy_prompt import build_v1_legacy_prompt

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenInput:
    """One loaded golden input plus its rendered V1/V2 prompts."""

    name: str
    scenario: str
    scenario_notes: str
    ontology_data: dict[str, Any]
    v1_prompt: str
    v2_prompt: str
    diff: PromptDiff
    classification: Classification


@dataclass(frozen=True)
class DiffReport:
    """Top-level report aggregated across all golden inputs."""

    generated_at: str
    inputs: list[GoldenInput]
    summary: dict[str, int] = field(default_factory=dict)

    def status_counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Status}
        for inp in self.inputs:
            out[inp.classification.status.value] += 1
        return out

    def severity_counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for inp in self.inputs:
            out[inp.classification.severity.value] += 1
        return out


# ---------------------------------------------------------------------------
# Internal: load + run + classify one golden input
# ---------------------------------------------------------------------------


def _strip_harness_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop keys starting with `_` (harness-only metadata)."""
    return {k: v for k, v in payload.items() if not k.startswith("_")}


def _load_golden_inputs(golden_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Load and sort all `*.json` fixtures in golden_dir."""
    if not golden_dir.is_dir():
        raise FileNotFoundError(f"Golden fixtures directory not found: {golden_dir}")
    files = sorted(golden_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"No JSON fixtures found in {golden_dir}")
    return [(path, json.loads(path.read_text(encoding="utf-8"))) for path in files]


def _fake_ontology_version(ontology_data: dict[str, Any]) -> Any:
    """Build a duck-typed OntologyVersion so V2's build_ontology_extraction_prompt accepts it.

    Mirrors `_FakeOntologyVersion` from apps/api/tests/test_extraction_prompt.py.
    Avoids a real DB round-trip in the harness.
    """
    from dataclasses import dataclass as _dc
    from dataclasses import field as _df

    @_dc(frozen=True)
    class _FakeOV:
        ontology_data: dict[str, Any] | None = _df(default_factory=dict)

    return _FakeOV(ontology_data=ontology_data)


def _process_one(path: Path, payload: dict[str, Any]) -> GoldenInput:
    """Run both prompt builders on one fixture and produce a GoldenInput."""
    scenario = payload.get("_scenario", "unknown")
    notes = payload.get("_scenario_notes", "")
    ontology_data = _strip_harness_metadata(payload)

    v1_prompt = build_v1_legacy_prompt(ontology_data)
    v2_prompt = build_ontology_extraction_prompt(_fake_ontology_version(ontology_data))
    diff = compare_prompts(v1_prompt, v2_prompt, ontology_data=ontology_data)
    classification = classify_diff(diff)

    return GoldenInput(
        name=path.stem,
        scenario=scenario,
        scenario_notes=notes,
        ontology_data=ontology_data,
        v1_prompt=v1_prompt,
        v2_prompt=v2_prompt,
        diff=diff,
        classification=classification,
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _truncate(text: str, limit: int = 60) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _render_report(report: DiffReport) -> str:
    """Render the DiffReport as a markdown document."""
    status_counts = report.status_counts()
    severity_counts = report.severity_counts()

    lines: list[str] = []
    lines.append("# V1-hardcoded vs V2-ontology-only — Snapshot Diff Report")
    lines.append("")
    lines.append(f"_Generated: {report.generated_at}_")
    lines.append(f"_Inputs: {len(report.inputs)} golden fixture(s)_")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|--------|-------|")
    for s in Status:
        lines.append(f"| {s.value} | {status_counts[s.value]} |")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("|----------|-------|")
    for s in Severity:
        lines.append(f"| {s.value} | {severity_counts[s.value]} |")
    lines.append("")

    # Per-input detail table
    lines.append("## Per-Input Verdict")
    lines.append("")
    lines.append(
        "| Input | Scenario | Status | Severity | "
        "Categories Δ | Properties Δ | Comment Δ lines | V1 len | V2 len |"
    )
    lines.append(
        "|-------|----------|--------|----------|"
        "--------------|---------------|-------------------|--------|--------|"
    )
    for inp in report.inputs:
        cat_delta = (
            f"+{len(inp.diff.categories_only_in_v2)}"
            f"/-{len(inp.diff.categories_only_in_v1)}"
        )
        prop_delta = (
            f"+{len(inp.diff.properties_only_in_v2)}"
            f"/-{len(inp.diff.properties_only_in_v1)}"
        )
        comment_delta = len(inp.diff.comment_diff_lines)
        lines.append(
            f"| `{inp.name}` "
            f"| {inp.scenario} "
            f"| **{inp.classification.status.value}** "
            f"| {inp.classification.severity.value} "
            f"| {cat_delta} "
            f"| {prop_delta} "
            f"| {comment_delta} "
            f"| {inp.diff.prompt_length_v1} "
            f"| {inp.diff.prompt_length_v2} |"
        )
    lines.append("")

    # Per-input detail blocks
    lines.append("## Per-Input Detail")
    lines.append("")
    for inp in report.inputs:
        lines.append(f"### `{inp.name}` — {inp.scenario}")
        lines.append("")
        if inp.scenario_notes:
            lines.append(f"**Scenario:** {inp.scenario_notes}")
            lines.append("")
        lines.append(
            f"**Verdict:** {inp.classification.status.value} "
            f"({inp.classification.severity.value})"
        )
        lines.append("")

        # Deltas
        deltas = inp.classification.deltas
        if deltas:
            lines.append("**Deltas:**")
            lines.append("")
            for d in deltas:
                lines.append(f"- {d}")
            lines.append("")
        else:
            lines.append("**Deltas:** _(none)_")
            lines.append("")

        # Notes
        notes = inp.classification.notes
        if notes:
            lines.append("**Notes:**")
            lines.append("")
            for n in notes:
                lines.append(f"- {n}")
            lines.append("")

        # Set listings (top 10)
        if inp.diff.categories_only_in_v1:
            sample = sorted(inp.diff.categories_only_in_v1)[:10]
            lines.append(
                f"**Categories only in V1 ({len(inp.diff.categories_only_in_v1)}):** "
                + ", ".join(f"`{_truncate(c, 40)}`" for c in sample)
                + ("…" if len(inp.diff.categories_only_in_v1) > 10 else "")
            )
            lines.append("")
        if inp.diff.categories_only_in_v2:
            sample = sorted(inp.diff.categories_only_in_v2)[:10]
            lines.append(
                f"**Categories only in V2 ({len(inp.diff.categories_only_in_v2)}):** "
                + ", ".join(f"`{_truncate(c, 40)}`" for c in sample)
                + ("…" if len(inp.diff.categories_only_in_v2) > 10 else "")
            )
            lines.append("")
        if inp.diff.properties_only_in_v1:
            sample = sorted(inp.diff.properties_only_in_v1)[:10]
            lines.append(
                f"**Properties only in V1 ({len(inp.diff.properties_only_in_v1)}):** "
                + ", ".join(f"`{_truncate(p, 40)}`" for p in sample)
                + ("…" if len(inp.diff.properties_only_in_v1) > 10 else "")
            )
            lines.append("")
        if inp.diff.properties_only_in_v2:
            sample = sorted(inp.diff.properties_only_in_v2)[:10]
            lines.append(
                f"**Properties only in V2 ({len(inp.diff.properties_only_in_v2)}):** "
                + ", ".join(f"`{_truncate(p, 40)}`" for p in sample)
                + ("…" if len(inp.diff.properties_only_in_v2) > 10 else "")
            )
            lines.append("")

    lines.append("## Methodology")
    lines.append("")
    lines.append(
        "Each golden input is fed to **two** prompt builders using the same "
        "`ontology_data` payload:"
    )
    lines.append("")
    lines.append(
        "1. **V1-hardcoded** — `tests.parity.v1_legacy_prompt.build_v1_legacy_prompt()`. "
        "Reconstructs the pre-NFM-3258 behavior: 11 fixed `PropertyCategory` enum "
        "values + the hardcoded `STANDARD_PROPERTIES` mapping from "
        "`property_mapping.json`. Ignores `ontology_data` entirely (V1 never read it)."
    )
    lines.append("")
    lines.append(
        "2. **V2-ontology-only** — `nfm_db.services.extraction_prompt.build_ontology_extraction_prompt()`. "
        "The current production path; sources categories and property names from "
        "`ontology_data` (`property_categories` 0.2.0+ schema, plus "
        "`entity_types[].required_properties`)."
    )
    lines.append("")
    lines.append(
        "The comparator (`tests.parity.comparator`) extracts the **key set** of "
        "categories and standard property names from each rendered prompt, "
        "computes set differences, and emits a unified diff of the static prose "
        "blocks (everything outside the dynamic ontology/categories/names blocks)."
    )
    lines.append("")
    lines.append(
        "The classifier (`tests.parity.diff_classifier`) applies the rules in "
        "the module docstring and emits a PASS / WARN / FAIL verdict with "
        "severity COSMETIC / NON_COSMETIC / BLOCKING."
    )
    lines.append("")
    lines.append("## How to run")
    lines.append("")
    lines.append("```bash")
    lines.append("# Unit tests for the classifier:")
    lines.append("pytest apps/api/tests/parity/test_diff_classifier.py -v")
    lines.append("")
    lines.append("# Regenerate this report:")
    lines.append("python -m tests.parity.harness")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def run_diff(golden_dir: Path | None = None) -> DiffReport:
    """Run V1/V2 diff on every golden fixture and return an aggregated report."""
    if golden_dir is None:
        # apps/api/tests/parity/harness.py → apps/api/tests/parity/golden
        golden_dir = Path(__file__).resolve().parent / "golden"

    fixtures = _load_golden_inputs(golden_dir)
    inputs = [_process_one(path, payload) for path, payload in fixtures]

    return DiffReport(
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        inputs=inputs,
    )


def run_diff_and_write_report(
    output_path: Path | None = None,
    golden_dir: Path | None = None,
) -> Path:
    """Run diff and write the markdown report to disk. Returns the output path."""
    if output_path is None:
        output_path = Path(__file__).resolve().parent / "reports" / "diff_report.md"

    report = run_diff(golden_dir=golden_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_report(report), encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    output = run_diff_and_write_report()
    print(f"Wrote diff report to {output}", file=sys.stdout)
    # Print a one-line summary for CI logs
    report = run_diff()
    counts = report.status_counts()
    summary = ", ".join(f"{s.value}={counts[s.value]}" for s in Status)
    print(f"Summary: {summary}", file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
