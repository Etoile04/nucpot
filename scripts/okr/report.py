"""5-KR weekly report aggregator (NFM-2041 B2).

Collapses all five company key results into a single repeatable command.
Reproduces the CEO hand-computed values on the reference window.

Usage:
    python scripts/okr/report.py --since 2026-07-20 --until 2026-07-26
    python scripts/okr/report.py --since 2026-07-20 --until 2026-07-26 --json
    python scripts/okr/report.py --since 2026-07-20 --until 2026-07-26 --baseline
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.okr import PaperclipFetchError, fetch_all_issues
from scripts.okr.commit_efficiency import (
    calculate_metrics,
    enrich_commits_with_refs,
    fetch_issue_statuses,
    parse_git_log,
    run_git_log,
)
from scripts.okr.coverage import build_report as build_kr5_report
from scripts.okr.coverage_kr3 import build_report as build_kr3_report

logger = logging.getLogger(__name__)

_DATE_FORMAT = "%Y-%m-%d"
_NO_DATA_BASELINE = "no_data_baseline"

# Human-readable labels for table output.
_KR_LABELS: dict[str, str] = {
    "KR-1": "Commit Efficiency",
    "KR-2": "Structural Waste Rate",
    "KR-3": "Deploy First-Pass Success",
    "KR-4": "Avg Lead Time",
    "KR-5": "Test Coverage",
}


# ---------------------------------------------------------------------------
# KR-4: Average Lead Time
# ---------------------------------------------------------------------------


def compute_lead_time(
    issues: list[dict[str, Any]],
    since: str,
    until: str,
) -> float | None:
    """Compute mean lead time (created -> updated) for done issues in window.

    Filters issues whose ``updatedAt`` falls within ``[since, until]``.
    Lead time = ``updatedAt - createdAt`` in fractional days.

    Returns ``None`` when no matching issues exist.
    """
    since_d = datetime.strptime(since, _DATE_FORMAT).date()
    until_d = datetime.strptime(until, _DATE_FORMAT).date()

    lead_times: list[float] = []
    for issue in issues:
        updated_str = issue.get("updatedAt", "")
        created_str = issue.get("createdAt", "")
        if not updated_str or not created_str:
            continue

        try:
            updated_d = datetime.strptime(updated_str[:10], _DATE_FORMAT).date()
            created_d = datetime.strptime(created_str[:10], _DATE_FORMAT).date()
        except ValueError:
            continue

        if updated_d < since_d or updated_d > until_d:
            continue

        delta = (updated_d - created_d).total_seconds() / 86400.0
        lead_times.append(delta)

    if not lead_times:
        return None

    return round(sum(lead_times) / len(lead_times), 2)


# ---------------------------------------------------------------------------
# KR-3: Deploy First-Pass Success
# ---------------------------------------------------------------------------


def compute_kr3(
    since: str,
    until: str,
    deploy_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Compute KR-3 from deploy event JSONL.

    Returns a KR entry dict with value or ``no_data_baseline`` status.
    When ``deploy_path`` is None the underlying ``_resolve_path`` fallback
    selects the default JSONL location.
    """
    # _resolve_path handles None by falling back to the default path.
    # Import here to keep this function pure (no module-level side effects).
    from scripts.okr.coverage_kr3 import _resolve_path

    resolved_path = _resolve_path(deploy_path)
    report = build_kr3_report(
        path=resolved_path,
        since=since,
        until=until,
    )
    value = report.get("value")
    if value is not None:
        return {
            "key": "deploy_first_pass_success",
            "value": value,
            "unit": "ratio",
        }
    return {
        "key": "deploy_first_pass_success",
        "value": None,
        "status": _NO_DATA_BASELINE,
    }


# ---------------------------------------------------------------------------
# KR-5: Test Coverage
# ---------------------------------------------------------------------------


def compute_kr5(
    coverage_xml_paths: list[str] | None,
) -> dict[str, Any]:
    """Compute KR-5 from Cobertura coverage XML files.

    Returns a KR entry dict with value or ``no_data_baseline`` status.
    """
    if not coverage_xml_paths:
        return {
            "key": "test_coverage",
            "value": None,
            "status": _NO_DATA_BASELINE,
        }

    xml_texts: list[str] = []
    for path in coverage_xml_paths:
        try:
            xml_texts.append(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("Cannot read coverage XML %s: %s", path, exc)

    if not xml_texts:
        return {
            "key": "test_coverage",
            "value": None,
            "status": _NO_DATA_BASELINE,
        }

    report = build_kr5_report(xml_texts)
    line_rate = report.get("line_rate")

    if line_rate is not None:
        return {
            "key": "test_coverage",
            "value": line_rate,
            "unit": "ratio",
        }

    return {
        "key": "test_coverage",
        "value": None,
        "status": _NO_DATA_BASELINE,
    }


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def build_kr_report(
    period_start: str,
    period_end: str,
    kr1_value: float | None,
    kr2_value: float | None,
    kr3_entry: dict[str, Any],
    kr4_value: float | None,
    kr5_entry: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the 5-KR report per ADR-REPEATABLE-1."""
    krs: dict[str, dict[str, Any]] = {
        "KR-1": {
            "key": "commit_efficiency",
            "value": kr1_value,
            "unit": "ratio",
        },
        "KR-2": {
            "key": "structural_waste_rate",
            "value": kr2_value,
            "unit": "ratio",
        },
        "KR-3": kr3_entry,
        "KR-4": {
            "key": "avg_lead_time",
            "value": kr4_value,
            "unit": "days",
        },
        "KR-5": kr5_entry,
    }

    return {
        "period": {"start": period_start, "end": period_end},
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "krs": krs,
    }


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def format_table(report: dict[str, Any]) -> str:
    """Format report as a human-readable table."""
    lines: list[str] = [
        f"Period: {report['period']['start']} → {report['period']['end']}",
        f"Generated: {report['generated_at']}",
        "",
        f"{'KR':<6} {'Metric':<30} {'Value':<12}",
        "-" * 50,
    ]

    for kr_id in ["KR-1", "KR-2", "KR-3", "KR-4", "KR-5"]:
        entry = report["krs"][kr_id]
        label = _KR_LABELS.get(kr_id, kr_id)
        value = entry.get("value")
        status = entry.get("status")
        unit = entry.get("unit", "")

        if status == _NO_DATA_BASELINE:
            lines.append(f"{kr_id:<6} {label:<30} [no data]")
        elif value is not None:
            if unit == "ratio":
                lines.append(f"{kr_id:<6} {label:<30} {value:.3f}")
            elif unit == "days":
                lines.append(f"{kr_id:<6} {label:<30} {value:.2f} d")
            else:
                lines.append(f"{kr_id:<6} {label:<30} {value}")
        else:
            lines.append(f"{kr_id:<6} {label:<30} [no data]")

    return "\n".join(lines)


def format_baseline(report: dict[str, Any], goal_id: str | None) -> dict[str, Any]:
    """Wrap report in a Lark OKR import envelope."""
    return {
        "type": "okr_baseline",
        "goal_id": goal_id or "company-okr",
        "period": report["period"],
        "generated_at": report["generated_at"],
        "key_results": report["krs"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _validate_date(value: str, arg_name: str) -> str:
    """Validate that a date string matches YYYY-MM-DD format."""
    try:
        datetime.strptime(value, _DATE_FORMAT)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{arg_name} must be {_DATE_FORMAT}, got: {value}"
        ) from exc
    return value


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="5-KR weekly report aggregator.",
    )
    parser.add_argument(
        "--since",
        required=True,
        type=lambda v: _validate_date(v, "--since"),
        help="Period start (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--until",
        required=True,
        type=lambda v: _validate_date(v, "--until"),
        help="Period end (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--goal",
        default=None,
        help="Goal ID for --baseline mode",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help="Output raw JSON",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        default=False,
        help="Emit Lark OKR import JSON",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help=(
            "Paperclip API base URL. Required — set $PAPERCLIP_API_URL "
            "or pass --api-url explicitly (NFM-2442/NFM-2444)."
        ),
    )
    parser.add_argument(
        "--company-id",
        default=None,
        help="Company UUID (or set PAPERCLIP_COMPANY_ID)",
    )
    parser.add_argument(
        "--coverage-xml",
        nargs="*",
        default=None,
        metavar="coverage.xml",
        help="Coverage XML files for KR-5",
    )
    parser.add_argument(
        "--rev",
        default="origin/main",
        help="Git revision basis for KR-1/KR-2",
    )
    parser.add_argument(
        "--deploy-events-path",
        default=None,
        help="Path to deploy-event JSONL for KR-3",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()

    company_id = args.company_id or os.environ.get("PAPERCLIP_COMPANY_ID", "")
    if not company_id:
        build_arg_parser().error(
            "--company-id is required (or set PAPERCLIP_COMPANY_ID env var)"
        )

    api_url = args.api_url or os.environ.get("PAPERCLIP_API_URL")
    if not api_url:
        build_arg_parser().error(
            "--api-url is required (or set PAPERCLIP_API_URL env var). "
            "The script must not guess a host — a wrong URL silently "
            "produces plausible-looking but false KR values (NFM-2442)."
        )

    api_key = os.environ.get("PAPERCLIP_API_KEY", "")

    # KR-1 & KR-2: commit efficiency + waste rate.
    #
    # If the Paperclip fetch fails (auth, network, malformed response),
    # we deliberately degrade KR-1 to None — i.e. "[no data]" — instead
    # of letting the metric render as 0.000. A fetch failure must NEVER
    # produce a numeric KR value, because a reader cannot distinguish
    # "we measured zero" from "the measurement never ran" (NFM-2443
    # AC3). KR-2 follows the same path so the two related metrics stay
    # consistent.
    raw_log = run_git_log(args.since, args.until, rev=args.rev)
    commits = parse_git_log(raw_log)
    enriched = enrich_commits_with_refs(commits)

    all_refs = sorted({
        ref for c in enriched for ref in c["issue_refs"]
    })

    kr1_value: float | None
    kr2_value: float | None
    try:
        statuses = fetch_issue_statuses(
            all_refs, api_url, company_id, api_key=api_key,
        )
        metrics = calculate_metrics(enriched, statuses)
        kr1_value = metrics["metrics"]["commitEfficiency"]
        kr2_value = metrics["metrics"]["structuralWasteRate"]
    except PaperclipFetchError as exc:
        logger.warning(
            "Paperclip fetch failed; reporting KR-1/KR-2 as [no data]: %s",
            exc,
        )
        kr1_value = None
        kr2_value = None

    # KR-3: deploy success rate (local file, no Paperclip dep)
    kr3_entry = compute_kr3(args.since, args.until, deploy_path=args.deploy_events_path)

    # KR-4: lead time — same fetch-failure semantics as KR-1.
    kr4_value: float | None
    try:
        done_issues = fetch_all_issues(
            api_url, company_id, {"status": "done"}, api_key=api_key,
        )
        kr4_value = compute_lead_time(done_issues, args.since, args.until)
    except PaperclipFetchError as exc:
        logger.warning(
            "Paperclip fetch failed; reporting KR-4 as [no data]: %s",
            exc,
        )
        kr4_value = None

    # KR-5: test coverage (local XML, no Paperclip dep)
    kr5_entry = compute_kr5(args.coverage_xml)

    report = build_kr_report(
        args.since,
        args.until,
        kr1_value,
        kr2_value,
        kr3_entry,
        kr4_value,
        kr5_entry,
    )

    if args.json_output:
        print(json.dumps(report, indent=2))
    elif args.baseline:
        envelope = format_baseline(report, args.goal)
        print(json.dumps(envelope, indent=2))
    else:
        print(format_table(report))


if __name__ == "__main__":
    main()
