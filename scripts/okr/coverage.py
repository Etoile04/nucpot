"""Core-module coverage aggregator for KR-5 (NFM-2046).

Ingests one or more Cobertura ``coverage.xml`` reports, filters them down to
the core module set, and emits a single line-weighted coverage metric with a
per-package breakdown.

Why line-weighted and not file-count arithmetic: averaging per-file rates lets
a one-line fully covered helper cancel out a 500-line untested service. The
KR-5 target is about how much of the codebase is exercised, so every line gets
one vote.

Branch coverage is reported alongside as a secondary field. It never
participates in the >=80% line-rate target.

Usage:
    python -m scripts.okr.coverage apps/api/coverage.xml [apps/web/coverage.xml ...]
"""

# ruff: noqa: B905, SIM103
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, NamedTuple
from xml.etree import ElementTree

# ---------------------------------------------------------------------------
# Core-module filter
# ---------------------------------------------------------------------------

# Directory runs that are not part of the core module set. Matched as
# contiguous path *segments* anywhere in the path, so a report whose filenames
# are app-relative resolves the same way as one with absolute filenames.
_NON_CORE_DIR_RUNS: tuple[tuple[str, ...], ...] = (
    ("apps", "web", "src", "components", "ui"),  # generated shadcn primitives
    ("apps", "mcp-server"),
    ("apps", "nfm-md-runner"),
)

# Single segments that disqualify a path wherever they appear. These are
# compared segment-by-segment rather than by substring so that "dist" does not
# also knock out "redistribute.py".
_NON_CORE_SEGMENTS = frozenset({"__pycache__", ".next", "dist", "node_modules"})

# Migration *fixtures* are excluded; migration logic itself is core.
_MIGRATION_SEGMENTS = frozenset({"migrations", "alembic_migrations"})
_FIXTURE_SEGMENTS = frozenset({"fixtures", "tests"})

_CONDITION_COVERAGE = re.compile(r"\((\d+)/(\d+)\)")


def _segments(path: str) -> list[str]:
    """Split a path into non-empty segments, normalising separators."""
    return [seg for seg in path.replace("\\", "/").split("/") if seg]


def _contains_run(segments: list[str], run: tuple[str, ...]) -> bool:
    """True when ``run`` appears as a contiguous slice of ``segments``."""
    span = len(run)
    return any(tuple(segments[i : i + span]) == run for i in range(len(segments) - span + 1))


def is_core_path(path: str) -> bool:
    """Whether a covered file belongs to the core module set."""
    segments = _segments(path)

    if _NON_CORE_SEGMENTS.intersection(segments):
        return False

    if any(_contains_run(segments, run) for run in _NON_CORE_DIR_RUNS):
        return False

    seen = set(segments)
    if _MIGRATION_SEGMENTS & seen and _FIXTURE_SEGMENTS & seen:
        return False

    return True


# ---------------------------------------------------------------------------
# Cobertura parsing
# ---------------------------------------------------------------------------


class FileCoverage(NamedTuple):
    """Line and branch counts for a single covered file."""

    package: str
    filename: str
    path: str
    covered_lines: int
    total_lines: int
    covered_branches: int
    total_branches: int


def _join_source(source: str | None, filename: str) -> str:
    """Resolve a class filename against the report's <source> root."""
    if not source:
        return filename
    return f"{source.rstrip('/')}/{filename.lstrip('/')}"


def _branch_counts(line: ElementTree.Element) -> tuple[int, int]:
    """Extract (covered, total) conditions from a <line> element."""
    if line.get("branch") != "true":
        return 0, 0
    match = _CONDITION_COVERAGE.search(line.get("condition-coverage", ""))
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def parse_cobertura(xml_text: str) -> list[FileCoverage]:
    """Parse a Cobertura document into per-file coverage records.

    No filtering happens here — every class in the report is returned so that
    callers can decide what counts as core.
    """
    root = ElementTree.fromstring(xml_text)

    source_el = root.find("./sources/source")
    source = source_el.text.strip() if source_el is not None and source_el.text else None

    files: list[FileCoverage] = []
    for package in root.iter("package"):
        package_name = package.get("name", "")
        for class_el in package.iter("class"):
            filename = class_el.get("filename", "")
            covered_lines = 0
            total_lines = 0
            covered_branches = 0
            total_branches = 0

            for line in class_el.iter("line"):
                total_lines += 1
                if int(line.get("hits", "0")) > 0:
                    covered_lines += 1
                hit_conditions, all_conditions = _branch_counts(line)
                covered_branches += hit_conditions
                total_branches += all_conditions

            files.append(
                FileCoverage(
                    package=package_name,
                    filename=filename,
                    path=_join_source(source, filename),
                    covered_lines=covered_lines,
                    total_lines=total_lines,
                    covered_branches=covered_branches,
                    total_branches=total_branches,
                )
            )

    return files


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

_RATE_PRECISION = 6


def _rate(covered: int, total: int) -> float:
    """Coverage ratio, or 0.0 when there is nothing to measure."""
    if total == 0:
        return 0.0
    return round(covered / total, _RATE_PRECISION)


class _Totals:
    """Mutable accumulator used only inside :func:`aggregate`."""

    def __init__(self) -> None:
        self.covered_lines = 0
        self.total_lines = 0
        self.covered_branches = 0
        self.total_branches = 0

    def add(self, file: FileCoverage) -> None:
        self.covered_lines += file.covered_lines
        self.total_lines += file.total_lines
        self.covered_branches += file.covered_branches
        self.total_branches += file.total_branches

    def as_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "covered_lines": self.covered_lines,
            "total_lines": self.total_lines,
            "line_rate": _rate(self.covered_lines, self.total_lines),
        }
        if self.total_branches:
            entry["branch_rate"] = _rate(self.covered_branches, self.total_branches)
        return entry


def aggregate(files: list[FileCoverage]) -> dict[str, Any]:
    """Fold per-file records into the KR-5 metric.

    Non-core paths are dropped. Files with no measurable lines are n/a stubs:
    they are recorded in ``skipped_stubs`` rather than counted as zero, so an
    empty ``__init__.py`` cannot drag the rate down.
    """
    overall = _Totals()
    per_package: dict[str, _Totals] = {}
    skipped_stubs: list[str] = []

    for file in files:
        if not is_core_path(file.path):
            continue
        if file.total_lines == 0:
            skipped_stubs.append(file.path)
            continue

        overall.add(file)
        per_package.setdefault(file.package, _Totals()).add(file)

    report = overall.as_dict()
    report["branch_rate"] = (
        _rate(overall.covered_branches, overall.total_branches) if overall.total_branches else None
    )
    report["covered_branches"] = overall.covered_branches
    report["total_branches"] = overall.total_branches
    report["skipped_stubs"] = skipped_stubs
    report["per_package"] = {name: totals.as_dict() for name, totals in sorted(per_package.items())}
    return report


def build_report(xml_texts: list[str]) -> dict[str, Any]:
    """Parse and merge several Cobertura reports into one metric."""
    files: list[FileCoverage] = []
    for xml_text in xml_texts:
        files.extend(parse_cobertura(xml_text))
    return aggregate(files)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_EXIT_OK = 0
_EXIT_INPUT_ERROR = 2


def _read_reports(paths: list[str]) -> list[str]:
    """Read each XML file, raising ValueError naming the offending path."""
    texts: list[str] = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as handle:
                texts.append(handle.read())
        except OSError as exc:
            raise ValueError(f"cannot read coverage report {path}: {exc}") from exc
    return texts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.okr.coverage",
        description="Aggregate core-module line coverage from Cobertura reports.",
    )
    parser.add_argument(
        "xml_paths",
        nargs="+",
        metavar="coverage.xml",
        help="One or more Cobertura XML reports to merge.",
    )
    args = parser.parse_args(argv)

    try:
        texts = _read_reports(args.xml_paths)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return _EXIT_INPUT_ERROR

    parsed: list[FileCoverage] = []
    for path, text in zip(args.xml_paths, texts):
        try:
            parsed.extend(parse_cobertura(text))
        except ElementTree.ParseError as exc:
            print(f"malformed coverage report {path}: {exc}", file=sys.stderr)
            return _EXIT_INPUT_ERROR

    report = aggregate(parsed)

    for stub in report["skipped_stubs"]:
        print(f"skipping n/a stub (no measurable lines): {stub}", file=sys.stderr)

    print(json.dumps(report, indent=2))
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
