"""Snapshot-diff core for V1 vs V2 extraction prompt paths.

Builds both prompt paths against the same ontology input, then
computes three layers of diff:

1. **Full prompt bytes** — byte-for-byte equality and (if diverged)
   line counts and section-level deltas via ``difflib``.
2. **Categories block** — the list of property categories the prompt
   declares as the enumeration. Structural diff: keys (added /
   removed), values (changed), ordering.
3. **Standard names block** — the list of canonical property names
   the prompt tells the LLM to prefer. Same structural diff.

Also reports **per-fixture coverage**: for each golden-set sample the
extraction is supposed to handle, whether its declared
``property_category`` appears in V1's enum-driven list and in V2's
ontology-driven list. This is the regression signal NFM-3531-C will
re-evaluate after the swap.

The module is pure Python — no LLM calls, no DB. Both prompt paths
are deterministic functions of their inputs, so the baseline is
reproducible across branches and CI runs.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nfm_db.services.extraction_prompt import build_ontology_extraction_prompt
from tests.extraction.v1_prompt_legacy import (
    build_extraction_system_prompt_v1,
    list_v1_category_values,
    list_v1_standard_names,
)

# Repository-root resolution — pytest may run from apps/api/ or repo root,
# so resolve via this file rather than cwd.
# File path: <repo>/apps/api/tests/extraction/snapshot_diff.py
# parents[4] is the repo root (parents[3] is the apps/ subdirectory).
REPO_ROOT: Path = Path(__file__).resolve().parents[4]
GOLDEN_DIR: Path = REPO_ROOT / "apps" / "api" / "tests" / "fixtures" / "golden"
ONTOLOGY_FIXTURE: Path = (
    REPO_ROOT
    / "apps"
    / "api"
    / "tests"
    / "fixtures"
    / "extraction"
    / "test_ontology_version.json"
)


# ---------------------------------------------------------------------------
# Minimal OntologyVersion stub
# ---------------------------------------------------------------------------


class _OntologyVersionStub:
    """Bare-minimum OntologyVersion stand-in.

    The production ``build_ontology_extraction_prompt`` only reads
    ``ontology_data`` off the object (TYPE_CHECKING-typed). This stub
    satisfies that single attribute, so we can drive the V2 path from
    a JSON fixture without needing a DB or ORM session.
    """

    def __init__(self, ontology_data: dict[str, Any]) -> None:
        self.ontology_data = ontology_data


# ---------------------------------------------------------------------------
# Diff data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ListDiff:
    """Structural diff between two ordered lists of strings."""

    only_in_left: tuple[str, ...] = ()
    only_in_right: tuple[str, ...] = ()
    in_both: tuple[str, ...] = ()
    ordering_changed: bool = False
    left_ordered: tuple[str, ...] = ()
    right_ordered: tuple[str, ...] = ()

    @property
    def is_identical(self) -> bool:
        return (
            not self.only_in_left
            and not self.only_in_right
            and not self.ordering_changed
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "only_in_v1": list(self.only_in_left),
            "only_in_v2": list(self.only_in_right),
            "in_both": list(self.in_both),
            "ordering_changed": self.ordering_changed,
            "v1_order": list(self.left_ordered),
            "v2_order": list(self.right_ordered),
        }


@dataclass(frozen=True)
class SectionDiff:
    """Diff for a named block within the prompt."""

    name: str
    left_bytes: int
    right_bytes: int
    left_text: str
    right_text: str
    list_diff: ListDiff | None = None
    identical: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "v1_bytes": self.left_bytes,
            "v2_bytes": self.right_bytes,
            "identical": self.identical,
        }
        if self.list_diff is not None:
            out["list_diff"] = self.list_diff.to_dict()
        return out


@dataclass(frozen=True)
class FixtureCoverage:
    """Whether a golden-set fixture's declared property_category
    is covered by each prompt path's category list."""

    fixture_id: str
    property_category: str
    covered_by_v1: bool
    covered_by_v2: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "property_category": self.property_category,
            "covered_by_v1": self.covered_by_v1,
            "covered_by_v2": self.covered_by_v2,
        }


@dataclass(frozen=True)
class SnapshotReport:
    """Aggregate baseline diff for one ontology input."""

    ontology_fixture_path: str
    v1_prompt: str
    v2_prompt: str
    prompt_identical: bool
    unified_diff: str
    categories_section: SectionDiff
    standard_names_section: SectionDiff
    fixtures_covered_total: int
    fixtures_covered_by_v1: int
    fixtures_covered_by_v2: int
    fixtures_diverged: tuple[FixtureCoverage, ...] = ()

    @property
    def fixtures_diverged_count(self) -> int:
        return sum(
            1 for f in self.fixtures_diverged if f.covered_by_v1 != f.covered_by_v2
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ontology_fixture_path": self.ontology_fixture_path,
            "prompt_identical": self.prompt_identical,
            "v1_prompt_bytes": len(self.v1_prompt.encode("utf-8")),
            "v2_prompt_bytes": len(self.v2_prompt.encode("utf-8")),
            "sections": [
                self.categories_section.to_dict(),
                self.standard_names_section.to_dict(),
            ],
            "unified_diff_excerpt": self.unified_diff[:2000],
            "fixtures_covered_total": self.fixtures_covered_total,
            "fixtures_covered_by_v1": self.fixtures_covered_by_v1,
            "fixtures_covered_by_v2": self.fixtures_covered_by_v2,
            "fixtures_diverged_count": self.fixtures_diverged_count,
            "fixtures_diverged": [f.to_dict() for f in self.fixtures_diverged],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _list_diff(left: list[str], right: list[str]) -> ListDiff:
    """Compute structural diff between two ordered lists of strings.

    Treats the lists as *ordered*: ordering_changed is True when both
    sides share the same elements but in a different sequence.
    """
    left_t = tuple(left)
    right_t = tuple(right)
    only_left = tuple(x for x in left_t if x not in right_t)
    only_right = tuple(x for x in right_t if x not in left_t)
    both = tuple(x for x in left_t if x in right_t)
    ordering_changed = (
        set(left_t) == set(right_t) and left_t != right_t
    )
    return ListDiff(
        only_in_left=only_left,
        only_in_right=only_right,
        in_both=both,
        ordering_changed=ordering_changed,
        left_ordered=left_t,
        right_ordered=right_t,
    )


def _unified_diff(left: str, right: str, *, label_left: str, label_right: str) -> str:
    """Return a unified diff between two strings (line-oriented)."""
    return "\n".join(
        difflib.unified_diff(
            left.splitlines(keepends=True),
            right.splitlines(keepends=True),
            fromfile=label_left,
            tofile=label_right,
            n=2,
        )
    )


def _extract_block(prompt: str, header_regex: str) -> str:
    """Slice a block from a prompt by header regex.

    Returns the matched header plus every line that belongs to the
    section: blanks, the prompt's preamble text (e.g. V2's
    ``优先使用以下标准名称:`` line under ``## Standard Property Names``),
    and ``- <bullet>`` entries. The block ends at the next ``## ...``
    section header (start of the next section) or at end-of-input.

    NFM-3535 HIGH-1: the previous heuristic terminated on the first
    non-bullet, non-blank line, which prematurely cut V2's standard
    names block to a 37-byte header because V2 emits a ``优先使用以下
    标准名称:`` preamble before its bullets. The new heuristic terminates
    only on the next ``## ...`` header so preamble lines and the entire
    bullet list are preserved.
    """
    pattern = re.compile(header_regex, re.MULTILINE)
    match = pattern.search(prompt)
    if not match:
        return ""
    start = match.start()
    lines = prompt[start:].splitlines(keepends=True)
    block_lines: list[str] = []
    for idx, line in enumerate(lines):
        if idx == 0:
            # Header line itself; always include and keep going.
            block_lines.append(line)
            continue
        stripped = line.strip()
        if stripped.startswith("## "):
            # Start of the next section — stop without including this line.
            break
        block_lines.append(line)
    return "".join(block_lines).rstrip("\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_v1_prompt() -> str:
    """Build the V1 (hardcoded STANDARD_PROPERTIES) prompt."""
    return build_extraction_system_prompt_v1()


def run_v2_prompt(ontology_data: dict[str, Any]) -> str:
    """Build the V2 (ontology-only) prompt from the given ontology dict."""
    return build_ontology_extraction_prompt(_OntologyVersionStub(ontology_data))


def _categories_block_v2(prompt: str) -> str:
    """Slice the categories block from a V2 prompt."""
    return _extract_block(prompt, r"^## Property Categories \(property_category\)$")


def _standard_names_block_v2(prompt: str) -> str:
    """Slice the standard-names block from a V2 prompt."""
    return _extract_block(prompt, r"^## Standard Property Names \(property\)$")


def _category_value_lines(block: str) -> list[str]:
    """Pull the list of category values out of a categories block.

    V1 bullets are "- 密度 [核心]" / "- 其他性能 [支持]".
    V2 bullets are "- 密度" or "- 密度: 密度, 比热容, ..." /
    "- 密度: 密度, 比热容, … (+N)". We keep only the first token
    (the category value) so the structural diff compares category
    names only, not the inline standard-properties preview.
    """
    values: list[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line.startswith("- "):
            continue
        body = line[2:]
        name = body.split(":", 1)[0].split()[0]
        values.append(name)
    return values


def _standard_name_value_lines(block: str) -> list[str]:
    """Pull the list of standard-name values out of a standard-names block.

    Both V1 and V2 use bullets "- <name>" so this is identical for both.
    """
    values: list[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line.startswith("- "):
            continue
        values.append(line[2:])
    return values


def load_golden_fixtures(golden_dir: Path = GOLDEN_DIR) -> list[dict[str, Any]]:
    """Load every JSON fixture under ``apps/api/tests/fixtures/golden/``."""
    fixtures: list[dict[str, Any]] = []
    for path in sorted(golden_dir.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["_fixture_path"] = str(path)
        fixtures.append(data)
    return fixtures


def fixture_property_categories(fixtures: Iterable[dict[str, Any]]) -> list[str]:
    """Return the unique property_category values declared by the fixtures.

    These represent the category coverage the prompt must faithfully
    enumerate so the LLM has somewhere to put each extraction record.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for fixture in fixtures:
        cat = fixture.get("property_category")
        if cat and cat not in seen:
            seen.add(cat)
            ordered.append(cat)
    return ordered


def build_snapshot_report(
    ontology_fixture_path: Path = ONTOLOGY_FIXTURE,
    golden_dir: Path = GOLDEN_DIR,
) -> SnapshotReport:
    """Run the V1 + V2 prompt builders and produce a SnapshotReport.

    Loads the canonical ontology fixture, builds both prompts, slices
    out the two dynamic blocks (categories, standard-names), computes
    structural + textual diffs, then walks the golden set to score
    per-fixture category coverage.
    """
    with open(ontology_fixture_path, encoding="utf-8") as f:
        ontology_data = json.load(f)

    v1_prompt = run_v1_prompt()
    v2_prompt = run_v2_prompt(ontology_data)

    prompt_identical = v1_prompt == v2_prompt
    unified = (
        ""
        if prompt_identical
        else _unified_diff(
            v1_prompt, v2_prompt, label_left="v1_prompt", label_right="v2_prompt"
        )
    )

    # Categories block — V1 has no in-prompt header for this block, so
    # we synthesize it from list_v1_category_values() for an apples-to-
    # apples comparison against the V2 block that _build_ontology_
    # categories_block produced.
    v1_categories_block = (
        "## Property Categories (property_category)\n\n"
        + "\n".join(f"- {c} [核心]" for c in list_v1_category_values()[:9])
        + "\n"
        + "\n".join(f"- {c} [支持]" for c in list_v1_category_values()[9:])
    )
    v2_categories_block = _categories_block_v2(v2_prompt)

    v1_cat_names = list_v1_category_values()
    v2_cat_names = _category_value_lines(v2_categories_block)
    categories_list_diff = _list_diff(v1_cat_names, v2_cat_names)

    categories_section = SectionDiff(
        name="categories_block",
        left_bytes=len(v1_categories_block.encode("utf-8")),
        right_bytes=len(v2_categories_block.encode("utf-8")),
        left_text=v1_categories_block,
        right_text=v2_categories_block,
        list_diff=categories_list_diff,
        identical=(
            v1_categories_block == v2_categories_block
            and categories_list_diff.is_identical
        ),
    )

    # Standard names block.
    v1_standard_names_block = (
        "## Standard Property Names (property)\n"
        "优先使用以下标准名称:\n\n"
        + "\n".join(f"- {n}" for n in list_v1_standard_names())
    )
    v2_standard_names_block = _standard_names_block_v2(v2_prompt)

    v1_std_names = list_v1_standard_names()
    v2_std_names = _standard_name_value_lines(v2_standard_names_block)
    standard_list_diff = _list_diff(v1_std_names, v2_std_names)

    standard_names_section = SectionDiff(
        name="standard_names_block",
        left_bytes=len(v1_standard_names_block.encode("utf-8")),
        right_bytes=len(v2_standard_names_block.encode("utf-8")),
        left_text=v1_standard_names_block,
        right_text=v2_standard_names_block,
        list_diff=standard_list_diff,
        identical=(
            v1_standard_names_block == v2_standard_names_block
            and standard_list_diff.is_identical
        ),
    )

    # Golden-set fixture coverage.
    fixtures = load_golden_fixtures(golden_dir)
    categories_in_fixtures = fixture_property_categories(fixtures)

    v1_set = set(v1_cat_names)
    v2_set = set(v2_cat_names)
    covered_total = len(categories_in_fixtures)
    covered_v1 = sum(1 for c in categories_in_fixtures if c in v1_set)
    covered_v2 = sum(1 for c in categories_in_fixtures if c in v2_set)
    diverged = tuple(
        FixtureCoverage(
            fixture_id=cat,
            property_category=cat,
            covered_by_v1=cat in v1_set,
            covered_by_v2=cat in v2_set,
        )
        for cat in categories_in_fixtures
        if (cat in v1_set) != (cat in v2_set)
    )

    return SnapshotReport(
        ontology_fixture_path=str(ontology_fixture_path),
        v1_prompt=v1_prompt,
        v2_prompt=v2_prompt,
        prompt_identical=prompt_identical,
        unified_diff=unified,
        categories_section=categories_section,
        standard_names_section=standard_names_section,
        fixtures_covered_total=covered_total,
        fixtures_covered_by_v1=covered_v1,
        fixtures_covered_by_v2=covered_v2,
        fixtures_diverged=diverged,
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_baseline_markdown(report: SnapshotReport) -> str:
    """Render a SnapshotReport as a human-readable Markdown baseline."""
    lines: list[str] = []
    lines.append("# NFM-3531 V1 vs V2 extraction-prompt baseline")
    lines.append("")
    lines.append(
        "Captured **before** [NFM-3531-C](/NFM/issues/NFM-3531) replaces the V2 "
        "prompt assembly path. Re-run `pytest apps/api/tests/extraction/test_snapshot_diff.py` "
        "on the integrated branch to detect precision/recall regression."
    )
    lines.append("")
    lines.append("## Inputs")
    lines.append("")
    lines.append(f"- Ontology fixture: `{report.ontology_fixture_path}`")
    lines.append("- Golden set: `apps/api/tests/fixtures/golden/` (13 fixtures)")
    lines.append(f"- V1 prompt bytes: `{len(report.v1_prompt.encode('utf-8'))}`")
    lines.append(f"- V2 prompt bytes: `{len(report.v2_prompt.encode('utf-8'))}`")
    lines.append(f"- Full-prompt byte equality: **`{report.prompt_identical}`**")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    identical_count = sum(
        1 for s in (report.categories_section, report.standard_names_section) if s.identical
    )
    lines.append(
        f"- **Identical sections**: {identical_count} / 2 "
        f"({identical_count / 2:.0%})"
    )
    pct_v2 = report.fixtures_covered_by_v2 / max(1, report.fixtures_covered_total)
    lines.append(
        f"- **Fixtures covered**: {report.fixtures_covered_by_v2} / "
        f"{report.fixtures_covered_total} "
        f"({pct_v2:.0%})"
    )
    cd = report.categories_section.list_diff
    lines.append(
        f"- **Categories diverged vs V1**: "
        f"{len(cd.only_in_left)} only-in-V1, "
        f"{len(cd.only_in_right)} only-in-V2, "
        f"ordering_changed=`{cd.ordering_changed}`"
    )
    sd = report.standard_names_section.list_diff
    lines.append(
        f"- **Standard names diverged vs V1**: "
        f"{len(sd.only_in_left)} only-in-V1, "
        f"{len(sd.only_in_right)} only-in-V2, "
        f"ordering_changed=`{sd.ordering_changed}`"
    )
    lines.append(f"- **Fixtures with category coverage divergence**: {report.fixtures_diverged_count}")
    lines.append("")

    # Categories block detail
    lines.append("## Categories block")
    lines.append("")
    lines.append(f"- Identical: **`{report.categories_section.identical}`**")
    lines.append(f"- V1 size: `{report.categories_section.left_bytes}` bytes")
    lines.append(f"- V2 size: `{report.categories_section.right_bytes}` bytes")
    lines.append("")
    if cd.only_in_left:
        lines.append("### Categories present in V1 only")
        lines.append("")
        for c in cd.only_in_left:
            lines.append(f"- {c}")
        lines.append("")
    if cd.only_in_right:
        lines.append("### Categories present in V2 only")
        lines.append("")
        for c in cd.only_in_right:
            lines.append(f"- {c}")
        lines.append("")
    if cd.ordering_changed:
        lines.append("### Ordering note")
        lines.append("")
        lines.append("V1 and V2 contain the same category names but in a different order.")
        lines.append("")

    # Standard names block detail
    lines.append("## Standard property names block")
    lines.append("")
    lines.append(f"- Identical: **`{report.standard_names_section.identical}`**")
    lines.append(f"- V1 size: `{report.standard_names_section.left_bytes}` bytes")
    lines.append(f"- V2 size: `{report.standard_names_section.right_bytes}` bytes")
    lines.append("")
    if sd.only_in_left:
        lines.append("### Names present in V1 only")
        lines.append("")
        for n in sd.only_in_left[:50]:
            lines.append(f"- {n}")
        if len(sd.only_in_left) > 50:
            lines.append(f"- … (+{len(sd.only_in_left) - 50} more)")
        lines.append("")
    if sd.only_in_right:
        lines.append("### Names present in V2 only")
        lines.append("")
        for n in sd.only_in_right[:50]:
            lines.append(f"- {n}")
        if len(sd.only_in_right) > 50:
            lines.append(f"- … (+{len(sd.only_in_right) - 50} more)")
        lines.append("")
    if sd.ordering_changed:
        lines.append("### Ordering note")
        lines.append("")
        lines.append("V1 and V2 contain the same names but in a different order.")
        lines.append("")

    # Golden set coverage
    lines.append("## Golden-set category coverage")
    lines.append("")
    lines.append(
        f"Of the {report.fixtures_covered_total} unique `property_category` values "
        f"declared across the 13 golden-set fixtures:"
    )
    lines.append("")
    lines.append(f"- V1 enum-driven coverage: **{report.fixtures_covered_by_v1} / {report.fixtures_covered_total}**")
    lines.append(f"- V2 ontology-driven coverage: **{report.fixtures_covered_by_v2} / {report.fixtures_covered_total}**")
    lines.append(f"- Coverage divergence: **{report.fixtures_diverged_count}** category(ies)")
    lines.append("")
    if report.fixtures_diverged:
        lines.append("### Diverged categories")
        lines.append("")
        lines.append("| Category | V1 | V2 |")
        lines.append("|---|---|---|")
        for f in report.fixtures_diverged:
            lines.append(
                f"| {f.property_category} | "
                f"{'✓' if f.covered_by_v1 else '✗'} | "
                f"{'✓' if f.covered_by_v2 else '✗'} |"
            )
        lines.append("")
    else:
        lines.append("No fixture categories diverge between V1 and V2 coverage.")
        lines.append("")

    # Per-delta classification
    lines.append("## Delta classification")
    lines.append("")
    lines.append(
        "Categories of delta, with recommended disposition. Anything in the "
        "**unacceptable** bucket must be investigated before NFM-3531-C merges "
        "into `main`."
    )
    lines.append("")
    lines.append("| Bucket | Definition | Disposition |")
    lines.append("|---|---|---|")
    lines.append(
        "| identical | V1 and V2 emit the same bytes for that section. | None — safe to swap. |"
    )
    lines.append(
        "| acceptable-extra (V2 only) | V2 surfaces a category that V1's static enum never had. "
        "Likely an ontology-specific category the LLM benefits from. | Acceptable if it represents "
        "an actual material property class the golden set covers. |"
    )
    lines.append(
        "| acceptable-drop (V1 only) | V2 dropped a V1 enum entry because the ontology does not "
        "model it. | Acceptable only if the dropped category is unrepresented in the golden set; "
        "otherwise flag for LE. |"
    )
    lines.append(
        "| ordering-only | Same names, different order. | Cosmetic — does not affect extraction. |"
    )
    lines.append(
        "| unacceptable | A category the golden set needs is in one path but not the other. "
        "Will cause precision/recall regression. | Block NFM-3531-C until fixed. |"
    )
    lines.append("")

    # Concrete verdict
    lines.append("## Verdict for NFM-3531-C merge gate")
    lines.append("")
    if report.fixtures_diverged_count == 0 and not report.unified_diff.strip():
        verdict = (
            "**PASS** — V1 and V2 prompts are byte-identical given the canonical "
            "ontology fixture, and every golden-set category is covered by both "
            "paths. NFM-3531-C may merge without further investigation."
        )
    elif report.fixtures_diverged_count == 0:
        verdict = (
            f"**PASS-WITH-NOTES** — golden-set coverage matches ({report.fixtures_covered_by_v2}"
            f"/{report.fixtures_covered_total}), but the prompts diverge in "
            "formatting/ordering. NFM-3531-C may merge; document the divergence in "
            "the PR description."
        )
    else:
        verdict = (
            f"**FAIL** — {report.fixtures_diverged_count} golden-set category(ies) "
            "have different coverage between V1 and V2. LE must investigate before "
            "NFM-3531-C merges."
        )
    lines.append(verdict)
    lines.append("")

    # Diff excerpt
    if not report.prompt_identical and report.unified_diff:
        excerpt = report.unified_diff.splitlines()
        head = "\n".join(excerpt[:60])
        lines.append("## Unified diff (excerpt, first 60 lines)")
        lines.append("")
        lines.append("```diff")
        lines.append(head)
        if len(excerpt) > 60:
            lines.append(f"… (+{len(excerpt) - 60} more lines)")
        lines.append("```")
        lines.append("")

    lines.append("## Reproducing this baseline")
    lines.append("")
    lines.append("```bash")
    lines.append("# From the repo root:")
    lines.append("pytest apps/api/tests/extraction/test_snapshot_diff.py -v")
    lines.append("")
    lines.append("# Or via the standalone CLI:")
    lines.append("python apps/api/tests/extraction/run_snapshot_diff.py \\")
    lines.append("    --output docs/verification/NFM-3531-v1-v2-baseline.md")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)
