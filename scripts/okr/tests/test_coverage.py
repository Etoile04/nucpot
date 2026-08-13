"""Tests for coverage.py — KR-5 core-module coverage aggregator (NFM-2046).

Functions under test:
- is_core_path(path: str) -> bool
- parse_cobertura(xml_text: str) -> list[FileCoverage]
- aggregate(files: list[FileCoverage]) -> dict
- build_report(xml_texts: list[str]) -> dict
- main(argv: list[str]) -> int

The five AC-5 cases are covered by:
- TestAggregate.test_weights_by_line_count_not_file_count  (aggregation math)
- TestIsCorePath.*                                          (filter excludes non-core)
- TestAggregate.test_skips_zero_line_stub_files             (stub skip)
- TestAggregate.test_reports_branch_rate_when_present       (branch passthrough)
- TestMain.test_exits_zero_and_emits_required_keys          (exit code 0)
"""

from __future__ import annotations

import json

from scripts.okr.coverage import (
    FileCoverage,
    aggregate,
    build_report,
    is_core_path,
    main,
    parse_cobertura,
)


# ---------------------------------------------------------------------------
# Synthetic Cobertura fixtures
# ---------------------------------------------------------------------------

def _cobertura(source: str, packages: str) -> str:
    """Wrap package XML fragments in a minimal Cobertura document."""
    return (
        '<?xml version="1.0" ?>\n'
        '<coverage version="7.14.1" line-rate="0.5" branch-rate="0">\n'
        f"  <sources><source>{source}</source></sources>\n"
        f"  <packages>{packages}</packages>\n"
        "</coverage>\n"
    )


def _lines(*specs: tuple[int, int]) -> str:
    """Build <line> elements from (number, hits) pairs."""
    return "".join(f'<line number="{n}" hits="{h}"/>' for n, h in specs)


# Package "small" holds one 1-line fully covered file.
# Package "big" holds one 4-line fully uncovered file.
# File-count arithmetic would report 50%; line-weighted reports 1/5 = 20%.
MIXED_XML = _cobertura(
    "/repo/apps/api/src",
    '<package name="small" line-rate="1" branch-rate="0"><classes>'
    '<class name="tiny.py" filename="pkg/tiny.py" line-rate="1" branch-rate="0">'
    f"<methods/><lines>{_lines((1, 3))}</lines>"
    "</class></classes></package>"
    '<package name="big" line-rate="0" branch-rate="0"><classes>'
    '<class name="huge.py" filename="pkg/huge.py" line-rate="0" branch-rate="0">'
    f"<methods/><lines>{_lines((1, 0), (2, 0), (3, 0), (4, 0))}</lines>"
    "</class></classes></package>",
)


class TestIsCorePath:
    """Non-core paths are filtered out before any counting happens."""

    def test_keeps_ordinary_source_file(self) -> None:
        assert is_core_path("/repo/apps/api/src/nfm_db/config.py") is True

    def test_excludes_shadcn_ui_components(self) -> None:
        assert is_core_path("/repo/apps/web/src/components/ui/button.tsx") is False

    def test_excludes_mcp_server(self) -> None:
        assert is_core_path("/repo/apps/mcp-server/src/index.ts") is False

    def test_excludes_nfm_md_runner(self) -> None:
        assert is_core_path("/repo/apps/nfm-md-runner/main.py") is False

    def test_excludes_build_and_cache_segments(self) -> None:
        assert is_core_path("apps/web/.next/static/chunk.js") is False
        assert is_core_path("apps/web/dist/bundle.js") is False
        assert is_core_path("scripts/__pycache__/thing.pyc") is False
        assert is_core_path("node_modules/lib/index.js") is False

    def test_excludes_migration_test_fixtures(self) -> None:
        assert is_core_path("apps/api/migrations/fixtures/seed_v1.py") is False
        assert is_core_path("apps/api/alembic_migrations/tests/test_upgrade.py") is False

    def test_keeps_migration_code_itself(self) -> None:
        """Only migration *fixtures* are excluded, not migration logic."""
        assert is_core_path("apps/api/migrations/0001_initial.py") is True

    def test_matches_whole_segments_not_substrings(self) -> None:
        """'dist' must not knock out 'redistribute.py' or a 'distributed' package."""
        assert is_core_path("apps/api/src/nfm_db/redistribute.py") is True
        assert is_core_path("apps/api/src/distributed/worker.py") is True


class TestParseCobertura:
    """Cobertura XML is turned into per-file line/branch counts."""

    def test_extracts_per_file_line_counts(self) -> None:
        files = parse_cobertura(MIXED_XML)
        by_name = {f.filename: f for f in files}
        assert by_name["pkg/tiny.py"].covered_lines == 1
        assert by_name["pkg/tiny.py"].total_lines == 1
        assert by_name["pkg/huge.py"].covered_lines == 0
        assert by_name["pkg/huge.py"].total_lines == 4

    def test_records_package_name(self) -> None:
        packages = {f.package for f in parse_cobertura(MIXED_XML)}
        assert packages == {"small", "big"}

    def test_resolves_filename_against_source_root(self) -> None:
        """Filenames are relative to <source>; joining is what makes the
        non-core filters match app-relative reports such as apps/web."""
        xml = _cobertura(
            "/repo/apps/web",
            '<package name="ui" line-rate="1" branch-rate="0"><classes>'
            '<class name="button.tsx" filename="src/components/ui/button.tsx" '
            'line-rate="1" branch-rate="0">'
            f"<methods/><lines>{_lines((1, 1))}</lines>"
            "</class></classes></package>",
        )
        assert parse_cobertura(xml)[0].path == "/repo/apps/web/src/components/ui/button.tsx"

    def test_parses_branch_conditions(self) -> None:
        xml = _cobertura(
            "/repo",
            '<package name="b" line-rate="1" branch-rate="0.5"><classes>'
            '<class name="b.py" filename="b.py" line-rate="1" branch-rate="0.5">'
            "<methods/><lines>"
            '<line number="1" hits="1" branch="true" condition-coverage="50% (1/2)"/>'
            '<line number="2" hits="1"/>'
            "</lines></class></classes></package>",
        )
        parsed = parse_cobertura(xml)[0]
        assert parsed.covered_branches == 1
        assert parsed.total_branches == 2


class TestAggregate:
    """Aggregation math, filtering, and stub handling."""

    def test_weights_by_line_count_not_file_count(self) -> None:
        """A 1-line 100%-covered file must not mask a 4-line 0%-covered file.

        File-count arithmetic would give (1.0 + 0.0) / 2 = 0.5.
        Line-weighted gives 1 / 5 = 0.2.
        """
        report = aggregate(parse_cobertura(MIXED_XML))
        assert report["covered_lines"] == 1
        assert report["total_lines"] == 5
        assert report["line_rate"] == 0.2

    def test_per_package_breakdown_covers_every_core_package(self) -> None:
        report = aggregate(parse_cobertura(MIXED_XML))
        assert set(report["per_package"]) == {"small", "big"}
        assert report["per_package"]["small"]["line_rate"] == 1.0
        assert report["per_package"]["big"]["line_rate"] == 0.0
        assert report["per_package"]["big"]["total_lines"] == 4

    def test_filter_excludes_non_core_paths_from_totals(self) -> None:
        files = [
            FileCoverage("core", "a.py", "/repo/apps/api/src/a.py", 5, 10, 0, 0),
            FileCoverage(
                "ui",
                "button.tsx",
                "/repo/apps/web/src/components/ui/button.tsx",
                0,
                90,
                0,
                0,
            ),
        ]
        report = aggregate(files)
        assert report["total_lines"] == 10
        assert report["line_rate"] == 0.5
        assert "ui" not in report["per_package"]

    def test_skips_zero_line_stub_files(self) -> None:
        """An n/a stub has no measurable lines: it is skipped, not scored 0."""
        files = [
            FileCoverage("core", "a.py", "/repo/a.py", 3, 4, 0, 0),
            FileCoverage("core", "stub.py", "/repo/stub.py", 0, 0, 0, 0),
        ]
        report = aggregate(files)
        assert report["total_lines"] == 4
        assert report["line_rate"] == 0.75
        assert report["skipped_stubs"] == ["/repo/stub.py"]

    def test_stub_package_absent_when_it_has_only_stubs(self) -> None:
        files = [FileCoverage("empty", "__init__.py", "/repo/__init__.py", 0, 0, 0, 0)]
        report = aggregate(files)
        assert report["per_package"] == {}
        assert report["total_lines"] == 0
        assert report["line_rate"] == 0.0

    def test_reports_branch_rate_when_present(self) -> None:
        files = [FileCoverage("core", "a.py", "/repo/a.py", 4, 4, 3, 6)]
        report = aggregate(files)
        assert report["branch_rate"] == 0.5
        assert report["per_package"]["core"]["branch_rate"] == 0.5

    def test_omits_branch_rate_when_no_branch_data(self) -> None:
        files = [FileCoverage("core", "a.py", "/repo/a.py", 4, 4, 0, 0)]
        report = aggregate(files)
        assert report["branch_rate"] is None
        assert "branch_rate" not in report["per_package"]["core"]

    def test_branch_rate_never_gates_the_line_metric(self) -> None:
        """Line rate is computed from lines alone, regardless of branch data."""
        files = [FileCoverage("core", "a.py", "/repo/a.py", 9, 10, 0, 8)]
        report = aggregate(files)
        assert report["line_rate"] == 0.9


class TestBuildReport:
    """Multiple XML inputs merge into one metric."""

    def test_merges_line_counts_across_reports(self) -> None:
        second = _cobertura(
            "/repo/apps/web",
            '<package name="small" line-rate="0" branch-rate="0"><classes>'
            '<class name="more.ts" filename="src/more.ts" line-rate="0" branch-rate="0">'
            f"<methods/><lines>{_lines((1, 0), (2, 0), (3, 0), (4, 0), (5, 0))}</lines>"
            "</class></classes></package>",
        )
        report = build_report([MIXED_XML, second])
        assert report["total_lines"] == 10
        assert report["covered_lines"] == 1
        assert report["per_package"]["small"]["total_lines"] == 6


class TestMain:
    """CLI contract: exit 0 and a JSON object on stdout."""

    def _write(self, tmp_path, name: str, xml: str) -> str:
        path = tmp_path / name
        path.write_text(xml, encoding="utf-8")
        return str(path)

    def test_exits_zero_and_emits_required_keys(self, tmp_path, capsys) -> None:
        path = self._write(tmp_path, "coverage.xml", MIXED_XML)

        exit_code = main([path])

        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        for key in ("covered_lines", "total_lines", "line_rate", "per_package"):
            assert key in payload
        assert payload["line_rate"] == 0.2

    def test_accepts_multiple_xml_arguments(self, tmp_path, capsys) -> None:
        a = self._write(tmp_path, "a.xml", MIXED_XML)
        b = self._write(tmp_path, "b.xml", MIXED_XML)

        assert main([a, b]) == 0
        assert json.loads(capsys.readouterr().out)["total_lines"] == 10

    def test_logs_skipped_stub_to_stderr(self, tmp_path, capsys) -> None:
        xml = _cobertura(
            "/repo",
            '<package name="core" line-rate="1" branch-rate="0"><classes>'
            '<class name="__init__.py" filename="__init__.py" line-rate="1" '
            'branch-rate="0"><methods/><lines/></class>'
            '<class name="real.py" filename="real.py" line-rate="1" branch-rate="0">'
            f"<methods/><lines>{_lines((1, 1))}</lines>"
            "</class></classes></package>",
        )
        path = self._write(tmp_path, "coverage.xml", xml)

        assert main([path]) == 0
        captured = capsys.readouterr()
        assert "__init__.py" in captured.err
        assert json.loads(captured.out)["total_lines"] == 1

    def test_returns_nonzero_for_missing_file(self, tmp_path, capsys) -> None:
        assert main([str(tmp_path / "nope.xml")]) == 2
        assert "nope.xml" in capsys.readouterr().err

    def test_returns_nonzero_for_malformed_xml(self, tmp_path, capsys) -> None:
        path = self._write(tmp_path, "bad.xml", "<coverage><not-closed>")
        assert main([path]) == 2
        assert "bad.xml" in capsys.readouterr().err
