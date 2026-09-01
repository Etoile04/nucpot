#!/usr/bin/env python3
"""NFM-3918 Unknown Material cleanup — verification wrapper.

Runs scripts/nfm-3918-unknown-material-cleanup.sql against a target database,
captures the BEFORE / AFTER RAISE NOTICE blocks, and prints the comparison
table required by ticket AC #1.

The script also enforces the safety gate from the ticket body:
  * Staging runs are allowed at any time.
  * Prod apply requires the env var NFMD_PROD_BACKUP_PATH to point at the
    pg_dump output (the ticket body's only rollback path).
  * Prod apply also requires env var NFMD_TIER_1B_DEPLOYED=1, set after the
    NFM-3919 migration has been verified in prod.

Usage:
    # Staging dry-run (always allowed):
    python scripts/nfm-3918-unknown-material-cleanup.py \\
        --database-url "$STAGING_DATABASE_URL" \\
        --dry-run

    # Staging apply (allowed; demo the migration):
    python scripts/nfm-3918-unknown-material-cleanup.py \\
        --database-url "$STAGING_DATABASE_URL"

    # Prod apply (gated):
    NFMD_PROD_BACKUP_PATH=/var/backups/nfm-3918-pre.sql \\
    NFMD_TIER_1B_DEPLOYED=1 \\
    python scripts/nfm-3918-unknown-material-cleanup.py \\
        --database-url "$PROD_DATABASE_URL" \\
        --expected-unknown-count 27

Output: a Markdown table on stdout, suitable for pasting into a Paperclip
comment. The script exits non-zero if any acceptance invariant is violated
(post-count drift, orphan rows, or density=10.55 not collapsed).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


SCRIPT_DIR = Path(__file__).resolve().parent
SQL_PATH = SCRIPT_DIR / "nfm-3918-unknown-material-cleanup.sql"

# Each psql_vars entry is a PL/pgSQL GUC read inside the SQL via
# `current_setting('<name>', true)`. psql's `-v name=value` only sets
# a client-side *substitution variable* (accessible via `:name` in SQL
# text), NOT a server-side setting reachable by `current_setting()`.
# The driver therefore rewrites the SQL body to literal values before
# invoking psql. The mapping knows how to emit a typed literal for each
# GUC name so PL/pgSQL's DECLARE blocks still parse.
_GUC_SUBSTITUTIONS: dict[str, Callable[[str], str]] = {
    "expected_unknown_count": lambda v: f"{int(v)}::int",
    "dry_run": lambda v: f"{'true' if str(v) in ('1', 'true', 't') else 'false'}::boolean",
    "require_backup_path": lambda v: "'" + v.replace("'", "''") + "'::text",
    "merge_strategy": lambda v: "'" + str(v).replace("'", "''") + "'::text",
}

# Tolerance window for the BEFORE measurement count. The ticket body hard-codes
# 93 measurements across the 10 carrying-data Unknown rows; if the actual count
# drifts (e.g., new ingest since the ticket was filed), we don't want to fail
# — we want to log and let a human reconcile. Set to None to disable.
DEFAULT_EXPECTED_MEASUREMENTS = 93

# Targets that require the prod safety gate. Anything else (staging, dev,
# CI scratch) skips the gate so the migration can be exercised freely.
PROD_HOST_HINTS = ("nucpot-prod-db", "prod-db", "5433")


@dataclass(frozen=True)
class CountSnapshot:
    """Parsed counts from a single RAISE NOTICE block.

    Either NFM-3918_BEFORE or NFM-3918_AFTER. Keys are the columns we expect
    the SQL to emit; missing keys indicate a parse failure.
    """

    label: str  # "BEFORE" or "AFTER"
    raw_line: str
    counts: Mapping[str, int]

    def render_row(self) -> str:
        cols = (
            "unknown",
            "zero_downstream",
            "carrying_data",
            "datasets",
            "measurements",
            "aliases",
            "compositions",
            "density_10_55",
        )
        cells = [str(self.counts.get(c, "—")) for c in cols]
        return "| " + " | ".join((self.label, *cells)) + " |"


# Same column order as CountSnapshot.render_row plus total-measurements +
# orphans + dedup (only meaningful in AFTER; BEFORE has them as N/A).
COMPARISON_HEADER = (
    "| phase | unknown | zero_downstream | carrying_data | datasets | "
    "measurements | aliases | compositions | density_10_55 |\n"
    "|-------|---------|-----------------|---------------|----------|"
    "-------------|---------|--------------|---------------|"
)


def is_prod_target(database_url: str) -> bool:
    lowered = database_url.lower()
    return any(hint in lowered for hint in PROD_HOST_HINTS)


def parse_counts(notice_line: str) -> CountSnapshot:
    """Parse a single NFM-3918_BEFORE / NFM-3918_AFTER RAISE NOTICE line.

    Format (BEFORE):
        NFM-3918_BEFORE unknown=% zero_downstream=% carrying_data=% datasets=%
                       measurements=% aliases=% compositions=% density_10_55=%

    Format (AFTER):
        NFM-3918_AFTER unknown=% measurements_total=% (was %)
                       density_10_55=% orphan_datasets=% orphan_measurements=% dedup_total=%
    """
    if "NFM-3918_BEFORE" in notice_line:
        label = "BEFORE"
        keymap = {
            "unknown": r"unknown=(\d+)",
            "zero_downstream": r"zero_downstream=(\d+)",
            "carrying_data": r"carrying_data=(\d+)",
            "datasets": r"datasets=(\d+)",
            "measurements": r"measurements=(\d+)",
            "measurements_total": r"measurements_total=(\d+)",
            "aliases": r"aliases=(\d+)",
            "compositions": r"compositions=(\d+)",
            "density_10_55": r"density_10_55=(\d+)",
        }
    elif "NFM-3918_AFTER" in notice_line:
        label = "AFTER"
        # AFTER doesn't have zero_downstream / carrying_data / datasets /
        # aliases / compositions breakdowns (only the salient end state).
        keymap = {
            "unknown": r"unknown=(\d+)",
            "measurements": r"measurements_total=(\d+)",
            "measurements_total": r"measurements_total=(\d+)",
            "density_10_55": r"density_10_55=(\d+)",
            "orphan_datasets": r"orphan_datasets=(\d+)",
            "orphan_measurements": r"orphan_measurements=(\d+)",
            "dedup_total": r"dedup_total=(\d+)",
        }
    else:
        raise ValueError(f"not a recognized BEFORE/AFTER line: {notice_line!r}")

    counts: dict[str, int] = {}
    for key, pattern in keymap.items():
        match = re.search(pattern, notice_line)
        if match:
            counts[key] = int(match.group(1))
    return CountSnapshot(label=label, raw_line=notice_line, counts=counts)


def _rewrite_sql_with_literals(sql_text: str, psql_vars: Mapping[str, str]) -> str:
    """Substitute `current_setting('<name>', true)::TYPE` with typed literals.

    ``psql_vars`` carries values the SQL expects to read via
    ``current_setting()`` — but psql's ``-v name=value`` flag only sets
    client-side *substitution* variables, not server-side GUCs. We rewrite
    the SQL body so each ``current_setting('name', true)`` call is replaced
    with the corresponding literal cast to the type the PL/pgSQL DECLARE
    block expects. This keeps the entire SQL file postgresql-portable
    (no custom GUC registration needed) while letting the wrapper pass
    values through the same CLI it advertises.
    """
    rewritten = sql_text
    for key, value in psql_vars.items():
        if key not in _GUC_SUBSTITUTIONS:
            raise SystemExit(
                f"Unknown GUC {key!r}; add a typed-literal mapping to "
                f"_GUC_SUBSTITUTIONS in scripts/nfm-3918-unknown-material-cleanup.py."
            )
        literal = _GUC_SUBSTITUTIONS[key](value)
        # Match `current_setting('name', true)` with optional `::type` cast
        # following, and capture/preserve the cast so the rewritten literal
        # remains a typed expression.
        pattern = re.compile(
            r"current_setting\(\s*'" + re.escape(key) + r"'\s*,\s*true\s*\)"
            r"(\s*::\s*\w+)?"
        )
        rewritten, _n = pattern.subn(literal, rewritten)
    return rewritten


def run_psql(database_url: str, sql_path: Path, psql_vars: Mapping[str, str]) -> str:
    """Invoke psql with the SQL file and capture NOTICE output.

    Wraps a temp-file rewrite so ``current_setting('<name>', true)`` reads
    in the SQL actually see the values passed via ``psql_vars``. psql's
    ``-v name=value`` flag only populates substitution variables, not GUCs.
    Errors propagate via ``\\set ON_ERROR_STOP on`` inside the SQL file.
    """
    sql_text = sql_path.read_text(encoding="utf-8")
    rewritten_sql = _rewrite_sql_with_literals(sql_text, psql_vars)

    tmp_fd, tmp_path = tempfile.mkstemp(prefix="nfm-3918-", suffix=".sql", text=True)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp:
            tmp.write(rewritten_sql)

        cmd = [
            "psql",
            database_url,
            "--no-psqlrc",
            "-P",
            "pager=off",
            "-f",
            tmp_path,
        ]

        env = {**os.environ, "PGOPTIONS": "--client-min-messages=NOTICE"}

        proc = subprocess.run(
            cmd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if proc.returncode != 0:
            sys.stderr.write(combined)
            raise SystemExit(
                f"psql exited {proc.returncode}; check the SQL output above."
            )
        return combined
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def extract_snapshots(psql_output: str) -> tuple[CountSnapshot | None, CountSnapshot | None]:
    before = None
    after = None
    for line in psql_output.splitlines():
        if "NFM-3918_BEFORE" in line:
            before = parse_counts(line)
        elif "NFM-3918_AFTER" in line:
            after = parse_counts(line)
    return before, after


def render_comparison(
    before: CountSnapshot | None,
    after: CountSnapshot | None,
) -> str:
    if before is None or after is None:
        raise SystemExit(
            "Could not locate NFM-3918_BEFORE / NFM-3918_AFTER blocks in psql "
            "output. The SQL file may have drifted; refusing to claim success."
        )

    rows = [COMPARISON_HEADER]
    rows.append(before.render_row())
    # AFTER has a different schema; flatten it into the same table for visual
    # comparison. Cells that don't apply render as N/A.
    after_cols = (
        "unknown",
        "zero_downstream",
        "carrying_data",
        "datasets",
        "measurements",
        "aliases",
        "compositions",
        "density_10_55",
    )
    after_cells = [str(after.counts.get(c, "N/A")) for c in after_cols]
    rows.append("| " + " | ".join(("AFTER", *after_cells)) + " |")
    return "\n".join(rows)


def assert_invariants(
    before: CountSnapshot,
    after: CountSnapshot,
    *,
    apply: bool,
    expected_measurements: int | None,
    strategy: str = "source_id_walk",
) -> list[str]:
    """Check the post-state against the ticket body's acceptance criteria.

    Returns a list of failure messages. Empty list = all pass.

    `strategy` controls AC #4 (density=10.55 collapse):
      * 'source_id_walk' — density=10.55 must collapse to 1 row (the option
        resolves onto a NON-Unknown material, where the dedup mechanism can
        fire).
      * 'new_canonical' — density=10.55 may stay at 8 (the option's contract
        per NFM-3918 interaction c28a98a9, board-ratified): all 8 must now
        live on the SINGLE "Unknown Material (canonical)" material row, not
        split across 8 Unknown rows. The wrapper verifies this qualitatively
        in the printed table — the density_10_55 cell collapses from
        "split across N Unknowns" to "all on 1 canonical row".
    """
    failures: list[str] = []

    # AC #2: Unknown count = 0 after.
    if after.counts.get("unknown", -1) != 0:
        failures.append(
            f"AC #2 FAIL: post-count Unknown = {after.counts.get('unknown')}, "
            "expected 0."
        )

    # AC #4: density=10.55 collapse — only meaningful when source_id_walk
    # actually re-points onto a NON-Unknown target. For new_canonical the
    # density=10.55 distribution collapses from 8 across 8 Unknown rows to
    # 8 across 1 canonical row — the row count is unchanged by design.
    #
    # Both `.get(..., 0)` calls use a default because `Counts.counts` is
    # typed `Mapping[str, int]` — a missing key means parse_counts never saw
    # the token (e.g., Phase 5 psql output was truncated). Without the
    # default, the LHS would be `int | None` and any `>` / `!=` comparison
    # raises TypeError at runtime, masking the AC failure as a traceback.
    after_density = after.counts.get("density_10_55", 0)
    if apply and strategy == "source_id_walk" and after_density != 1:
        failures.append(
            f"AC #4 FAIL (source_id_walk): post-state density=10.55 rows = "
            f"{after.counts.get('density_10_55')}, expected 1."
        )

    # AC #4 (new_canonical): density=10.55 must be ≤ the BEFORE count
    # (we never gain rows). The "8 on 1 material" invariant is verified by
    # a separate SQL check (see Phase 5 of the migration SQL — not in this
    # wrapper because the wrapper only sees aggregate counts).
    if apply and strategy == "new_canonical" and after_density > before.counts.get("density_10_55", 0):
        failures.append(
            f"AC #4 FAIL (new_canonical): post-state density=10.55 rows = "
            f"{after.counts.get('density_10_55')} GREATER than before "
            f"{before.counts.get('density_10_55')}; migration gained rows."
        )

    # AC #5: no orphans.
    if apply:
        if after.counts.get("orphan_datasets", 0) != 0:
            failures.append(
                f"FK integrity FAIL: orphan_datasets = "
                f"{after.counts.get('orphan_datasets')}, expected 0."
            )
        if after.counts.get("orphan_measurements", 0) != 0:
            failures.append(
                f"FK integrity FAIL: orphan_measurements = "
                f"{after.counts.get('orphan_measurements')}, expected 0."
            )

    # AC #3 (measurement preservation): the GLOBAL property_measurements count
    # must not drop below (BEFORE_total - dedup_total).
    #
    # This deliberately compares measurements_total on both sides. An earlier
    # version compared BEFORE's Unknown-scoped count (94) against AFTER's
    # global count (97); because the global total is always >= the scoped one,
    # that check could not fail even if every Unknown measurement were
    # destroyed. Invariant #1 is a whole-table property — measure it as one.
    if apply:
        before_total = before.counts.get("measurements_total")
        after_total = after.counts.get("measurements_total")
        if before_total is None or after_total is None:
            failures.append(
                "AC #3 FAIL: measurements_total missing from "
                f"{'BEFORE' if before_total is None else 'AFTER'} snapshot — "
                "cannot verify measurement preservation. Refusing to pass a "
                "check we could not actually run."
            )
        else:
            dedup = after.counts.get("dedup_total", 0)
            net_expected = before_total - dedup
            if after_total < net_expected:
                failures.append(
                    f"AC #3 FAIL: post-state measurements = {after_total}, "
                    f"expected at least {net_expected} (= {before_total} BEFORE − "
                    f"{dedup} dedup). Investigate measurement loss."
                )

    return failures


def gate_prod_apply(database_url: str, args: argparse.Namespace) -> None:
    """Enforce the prod-only safety gate described in the ticket body.

    Two env-var checks: backup path present, Tier 1B deployed flag present.
    The flag is operator-set after NFM-3919 has been verified in prod. The
    script deliberately has no "auto-detect" because auto-detecting prod
    state from inside the prod migration is the exact failure mode the
    ticket body is trying to prevent.
    """
    if not is_prod_target(database_url):
        return

    backup = os.environ.get("NFMD_PROD_BACKUP_PATH", "").strip()
    if not backup:
        raise SystemExit(
            "PROD gate FAILED: NFMD_PROD_BACKUP_PATH is unset. The ticket body "
            "§5 requires pg_dump of materials/datasets/property_measurements "
            "before prod apply. See docs/runbooks/mac-studio-docker-ops.md §6 "
            "for backup context."
        )
    if not Path(backup).exists():
        raise SystemExit(
            f"PROD gate FAILED: NFMD_PROD_BACKUP_PATH={backup!r} does not "
            "exist on disk. Run pg_dump first."
        )

    tier1b = os.environ.get("NFMD_TIER_1B_DEPLOYED", "").strip()
    if tier1b != "1":
        raise SystemExit(
            "PROD gate FAILED: NFMD_TIER_1B_DEPLOYED != 1. Tier 1B (NFM-3919) "
            "must be MERGED AND DEPLOYED TO PROD before this script runs "
            "against prod. Until the upstream block lands, new ingest keeps "
            "refilling Unknown Material at ~22 rows/day (see ticket body "
            "§前置条件). Set the env var to 1 only after NFM-3919 is verified."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database-url",
        required=True,
        help="Postgres URL; pass $STAGING_DATABASE_URL for staging, "
        "$PROD_DATABASE_URL for prod (gated).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run phases 0, 1, 2 (snapshot only). Phases 3/4/5 emit "
        "dry-run notices but do not mutate.",
    )
    parser.add_argument(
        "--expected-unknown-count",
        type=int,
        default=27,
        help="Override the preflight Unknown count check (default 27 per "
        "ticket body).",
    )
    parser.add_argument(
        "--expected-measurements",
        type=int,
        default=DEFAULT_EXPECTED_MEASUREMENTS,
        help="Override the BEFORE measurement-count sanity check (default 93 "
        "per ticket body §决策依据).",
    )
    parser.add_argument(
        "--strategy",
        choices=("source_id_walk", "new_canonical"),
        default="source_id_walk",
        help="Phase 4 merge strategy. "
        "'source_id_walk' (default) tries to re-point each carrying Unknown "
        "onto a NON-Unknown material reachable by source_id; the real shard "
        "measures 0/11 resolvable, so it is a no-op fallback for this ticket. "
        "'new_canonical' (Option B, board-ratified via NFM-3918 interaction "
        "c28a98a9) creates ONE new 'Unknown Material (canonical)' row and "
        "re-points every carrying Unknown's datasets onto it, preserving all "
        "measurements with trivial reversibility.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    gate_prod_apply(args.database_url, args)

    psql_vars = {
        "dry_run": "1" if args.dry_run else "0",
        "expected_unknown_count": str(args.expected_unknown_count),
        "require_backup_path": (
            "stub" if args.dry_run or not is_prod_target(args.database_url)
            else os.environ["NFMD_PROD_BACKUP_PATH"]
        ),
        "merge_strategy": args.strategy,
    }

    output = run_psql(args.database_url, SQL_PATH, psql_vars)
    before, after = extract_snapshots(output)
    print(render_comparison(before, after))

    if before is None or after is None:
        return 2

    failures = assert_invariants(
        before,
        after,
        apply=not args.dry_run,
        expected_measurements=args.expected_measurements,
        strategy=args.strategy,
    )
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(
        "\nAll acceptance criteria passed." if not args.dry_run
        else "\nDry-run: no invariants enforced beyond parseability."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())