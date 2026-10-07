#!/usr/bin/env python3
"""NFM-5339 — read-only recovery-SLA instrumentation for the LightRAG
watchdog (NFM-5202 branch H3: watchdog = mitigation-of-record; the
AC5-successor success bar is a recovery SLA — 100% auto-recovery,
MTTR < 60s per wedge).

One command, deterministic output, zero writes to system state or the
watchdog log — this tool only READS
/var/log/nfm-g2/lightrag-watchdog.log (the watchdog's own decision
record, NFM-4804). It never touches the watchdog's kill path, probes,
or cadence; byte-diffing the host watchdog scripts/plists before vs
after any run of this tool is empty by construction.

Episode grammar (see scripts/host-prod-gate/entries/start-lightrag-watchdog.sh):

    <ts> probe=WEDGE  ... action=host-unwedge+restart-lightrag   # detection
    <ts> host=healthy|wedge-suspect|stop-failed|stop-recovered|
          term|verified|verify-failed|runner-absent|probe-error  # host stage
    <ts> action=restart-lightrag rc=N                            # container restart
    <ts> action=lightrag-reprocess rc=N                          # NFM-4816 re-enqueue
    <ts> action=lightrag-reprocess skipped reason=restart-failed rc=N

One wedge episode = one ``probe=WEDGE`` line plus the host=/action=
lines that follow, terminated by the next ``probe=`` line (manual
recovery or a repeat detection) or EOF (open). d2=/e1= lifecycle
lines belong to the D-2/E-1 stages, not to an episode.

Class taxonomy (matches NFM-5202's labeling of the wedge of record
2026-09-24T04:00:10Z as host-level): ``host`` = the WEDGE line's
action names the NFM-4887 host-unwedge stage (post-4887 fires always
carry it); ``lightrag-internal`` = the pre-4887 restart-only action.
The host_stage column carries what the host stage actually found
(healthy at probe vs confirmed runner wedge -> SIGTERM).

MTTR of record = detection -> last successful recovery action:
``lightrag-reprocess rc=0`` when present (the NFM-4816 step that
re-enqueues stranded docs — service is whole when it completes), else
``restart-lightrag rc=0`` (pre-4816 episodes with no reprocess line at
all). A reprocess attempt that ran and failed (rc!=0, not the skipped
path) leaves no recovery of record: the episode is auto-failed and
carries no MTTR. Manual/open episodes have no log-derivable completion
timestamp: excluded from MTTR statistics, still counted in the
auto-recovery-rate denominator — no fabricated rows, no
silently-dropped rows.

Window filtering is a plain-timestamp compare on the fixed-format UTC
prefix (the NFM-5202 pre-registered method — a bracket-split filter
silently read 0 rows and masked a RED). Minute-granularity --since
values are padded to seconds; a window that matches nothing is echoed
loudly with a zero count, never silently.

Usage:
    python3 scripts/lightrag_recovery_sla.py [--since 2026-09-22T14:01]
                                             [--log PATH] [--json] [--out FILE]

Exit codes: 0 report produced; 2 bad arguments / unreadable log.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ISSUE = "NFM-5339"
PARENT = "NFM-5202"
DEFAULT_LOG = "/var/log/nfm-g2/lightrag-watchdog.log"
LOG_ENV = "NFM_LIGHTRAG_WATCHDOG_LOG"  # same knob the watchdog itself honors
SLA_MTTR_S = 60
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z (.*)$")
SINCE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(:\d{2})?$")
TS_LEN = 19  # len("YYYY-MM-DDTHH:MM:SS")


def _ts(line: str):
    """Return (timestamp_19char, rest) for a log line, else None."""
    m = TS_RE.match(line)
    return (m.group(1), m.group(2)) if m else None


def _epoch(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")


def normalize_since(raw: str) -> str:
    """Pad a caller-supplied window start to 19 plain-timestamp chars.

    ``2026-09-22T14:01`` and ``2026-09-22T14:01:00Z`` are the same
    window; anything else is an argument error, not a silent zero-row
    read.
    """
    m = SINCE_RE.match(raw.rstrip("Zz"))
    if not m:
        raise ValueError(f"--since must be YYYY-MM-DDTHH:MM[:SS] (UTC), got: {raw!r}")
    return m.group(1) + (m.group(2) or ":00")


@dataclass
class Episode:
    line_no: int
    detected_at: str
    action: str
    host_tokens: list[str] = field(default_factory=list)
    restart_at: str | None = None
    restart_rc: int | None = None
    reprocess_at: str | None = None
    reprocess_rc: int | None = None
    reprocess_skipped: bool = False
    terminated_by: str = "eof"  # 'probe-wedge' | 'probe-other' | 'eof'

    # --- derived ----------------------------------------------------------
    @property
    def cls(self) -> str:
        if "host-unwedge" in self.action:
            return "host"
        if self.action == "restart-lightrag":
            return "lightrag-internal"
        return "unknown"

    @property
    def host_stage(self) -> str:
        t = self.host_tokens
        if not t:
            return "not-run(pre-4887)"
        if "wedge-suspect" in t:
            if "term" in t:
                base = "suspect->term"
            elif "stop-recovered" in t:
                base = "suspect->stop-recovered"
            else:
                base = "suspect"
        elif "healthy" in t:
            base = "healthy"
        elif "runner-absent" in t:
            base = "runner-absent"
        elif "probe-error" in t:
            base = "probe-error"
        else:
            base = t[0]
        if "verify-failed" in t:
            return base + "+verify-failed"
        if "verified" in t:
            return base + "+verified"
        return base

    @property
    def mode(self) -> str:
        if self.restart_rc == 0:
            if (
                self.reprocess_at is not None
                and not self.reprocess_skipped
                and self.reprocess_rc != 0
            ):
                return "auto-failed"
            return "watchdog-auto"
        if self.restart_rc is not None:
            return "auto-failed"
        if self.terminated_by == "probe-wedge":
            return "repeat-wedge"
        return "manual" if self.terminated_by == "probe-other" else "open"

    def _delta(self, done: str | None) -> int | None:
        if done is None:
            return None
        return int((_epoch(done) - _epoch(self.detected_at)).total_seconds())

    @property
    def mttr_restart_s(self) -> int | None:
        return self._delta(self.restart_at) if self.restart_rc == 0 else None

    @property
    def mttr_s(self) -> int | None:
        """Detection -> last successful recovery action (of record)."""
        if self.reprocess_at is not None:
            return self._delta(self.reprocess_at) if self.reprocess_rc == 0 else None
        return self.mttr_restart_s

    @property
    def sla_pass(self) -> bool | None:
        if self.mode != "watchdog-auto" or self.mttr_s is None:
            return False
        return self.mttr_s < SLA_MTTR_S


def parse_log(lines: list[str]):
    """Parse watchdog log lines into episodes + scan stats (pure)."""
    episodes: list[Episode] = []
    stats = {"lines_total": len(lines), "lines_unparsed": 0}
    current: Episode | None = None

    def close(terminated_by: str) -> None:
        nonlocal current
        if current is not None:
            current.terminated_by = terminated_by
            episodes.append(current)
            current = None

    for idx, line in enumerate(lines, start=1):
        parsed = _ts(line)
        if parsed is None:
            stats["lines_unparsed"] += 1
            continue
        ts, rest = parsed
        if rest.startswith("probe="):
            if rest.startswith("probe=WEDGE"):
                close("probe-wedge")  # repeat detection: prior episode unrecovered
                m_action = re.search(r"action=(\S+)", rest)
                current = Episode(
                    line_no=idx,
                    detected_at=ts,
                    action=m_action.group(1) if m_action else "",
                )
            else:
                close("probe-other")
        elif current is not None and rest.startswith("host="):
            current.host_tokens.append(rest[len("host=") :].split()[0])
        elif current is not None and rest.startswith("action="):
            m_rc = re.search(r"rc=(\d+)", rest)
            rc = int(m_rc.group(1)) if m_rc else None
            if rest.startswith("action=restart-lightrag"):
                if current.restart_at is None:
                    current.restart_at, current.restart_rc = ts, rc
            elif rest.startswith("action=lightrag-reprocess") and (current.reprocess_at is None):
                current.reprocess_at = ts
                current.reprocess_rc = rc
                current.reprocess_skipped = "skipped" in rest
        # d2=/e1=/anything-else with a valid timestamp: recognized, not
        # episode content — ignored by design.
    close("eof")
    return episodes, stats


def _p95_nearest_rank(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def aggregate(episodes: list[Episode]) -> dict:
    total = len(episodes)
    auto = sum(1 for e in episodes if e.mode == "watchdog-auto")
    auto_failed = sum(1 for e in episodes if e.mode == "auto-failed")
    manual_open = sum(1 for e in episodes if e.mode in ("manual", "open"))
    repeat_wedge = sum(1 for e in episodes if e.mode == "repeat-wedge")
    mttrs = [e.mttr_s for e in episodes if e.mttr_s is not None]
    by_class: dict[str, dict] = {}
    for e in episodes:
        c = by_class.setdefault(e.cls, {"total": 0, "auto": 0})
        c["total"] += 1
        c["auto"] += int(e.mode == "watchdog-auto")
    sla = None
    if total:
        sla = auto == total and bool(mttrs) and max(mttrs) < SLA_MTTR_S
    return {
        "total": total,
        "watchdog_auto": auto,
        "auto_failed": auto_failed,
        "manual_or_open": manual_open,
        "repeat_wedge": repeat_wedge,
        "auto_rate": (auto / total) if total else None,
        "mttr_n": len(mttrs),
        "mttr_mean": (sum(mttrs) / len(mttrs)) if mttrs else None,
        "mttr_p95": _p95_nearest_rank(mttrs),
        "mttr_max": max(mttrs) if mttrs else None,
        "sla_pass": sla,
        "by_class": by_class,
    }


def _fmt_rate(num: int, den: int) -> str:
    if den == 0:
        return "n/a (0 wedges)"
    return f"{num}/{den} ({100.0 * num / den:.1f}%)"


def _row(ep: Episode, n: int) -> str:
    sla = {True: "PASS", False: "FAIL", None: "n-a"}[ep.sla_pass]
    return (
        f"{n:<2} {ep.detected_at}Z  "
        f"{ep.cls:<17} {ep.host_stage:<30} {ep.mode:<13} "
        f"rc={_rc(ep.restart_rc):>3}     rc={_rc(ep.reprocess_rc):>3}     "
        f"{_num(ep.mttr_restart_s):>13}  {_num(ep.mttr_s):>5}  {sla}"
    )


def _rc(rc: int | None) -> str:
    return "-" if rc is None else str(rc)


def _num(v: int | None) -> str:
    return "-" if v is None else str(v)


def build_report(episodes: list[Episode], stats: dict, log_path: str, since: str | None) -> str:
    agg = aggregate(episodes)
    out: list[str] = [
        f"{ISSUE} lightrag recovery-SLA report ({PARENT} H3 — watchdog = mitigation-of-record)",
        f"log: {log_path}",
        "window: " + (f"since {since} (detection >= T0, inclusive)" if since else "(start of log)"),
        f"lines scanned: {stats['lines_total']} (unparsed: {stats['lines_unparsed']})",
        f"wedge episodes in window: {agg['total']}",
        "",
        "per-wedge:",
        (
            f"{'#':<2} {'detected_at':<20} {'class':<17} {'host_stage':<30} "
            f"{'mode':<13} restart reprocess mttr_restart_s  mttr_s  sla"
        ),
    ]
    for n, ep in enumerate(episodes, start=1):
        out.append(_row(ep, n))
    out += [
        "",
        f"aggregate (wedges: {agg['total']}; "
        f"mttr stats over watchdog-completed recoveries: n={agg['mttr_n']})",
        f"  {'auto-recovery rate':<24}: {_fmt_rate(agg['watchdog_auto'], agg['total'])}",
        f"  {'manual-or-open':<24}: {agg['manual_or_open']}",
        f"  {'repeat-wedge':<24}: {agg['repeat_wedge']}",
        f"  {'auto-failed':<24}: {agg['auto_failed']}",
        f"  {'mttr mean / p95 / max':<24}: "
        + (
            f"{agg['mttr_mean']:.1f}s / {agg['mttr_p95']}s / {agg['mttr_max']}s"
            f"   (p95 = nearest-rank)"
            if agg["mttr_n"]
            else "n/a (no completed recoveries)"
        ),
        f"  SLA (100% auto, <{SLA_MTTR_S}s/MTTR): "
        + (
            "n/a (no wedges in window)"
            if agg["total"] == 0
            else ("PASS" if agg["sla_pass"] else "FAIL")
        ),
        "by class: "
        + ", ".join(
            f"{cls}={c['total']} (auto {100.0 * c['auto'] / c['total']:.1f}%)"
            for cls, c in sorted(agg["by_class"].items())
        )
        + ("" if agg["by_class"] else "none"),
        "",
        "notes: MTTR of record = detection -> last successful recovery action "
        "(lightrag-reprocess rc=0 per NFM-4816; restart-lightrag rc=0 for "
        "pre-4816 episodes). a reprocess attempt that ran and failed "
        "(rc!=0, not skipped) is auto-failed with no MTTR of record. "
        "manual/open episodes carry no log-derivable completion: excluded "
        "from MTTR stats, counted in the auto-rate denominator. "
        "repeat-wedge = superseded by a repeat WEDGE detection with no "
        "logged recovery. class=host means the watchdog ran its NFM-4887 "
        "host-unwedge stage (action=host-unwedge+restart-lightrag); "
        "host_stage carries the host-probe outcome.",
    ]
    return "\n".join(out) + "\n"


def _episode_json(ep: Episode) -> dict:
    return {
        "line_no": ep.line_no,
        "detected_at": ep.detected_at,
        "class": ep.cls,
        "host_stage": ep.host_stage,
        "host_tokens": list(ep.host_tokens),
        "action": ep.action,
        "mode": ep.mode,
        "restart_at": ep.restart_at,
        "restart_rc": ep.restart_rc,
        "reprocess_at": ep.reprocess_at,
        "reprocess_rc": ep.reprocess_rc,
        "reprocess_skipped": ep.reprocess_skipped,
        "mttr_restart_s": ep.mttr_restart_s,
        "mttr_s": ep.mttr_s,
        "sla_pass": ep.sla_pass,
    }


def build_json(episodes: list[Episode], stats: dict, log_path: str, since: str | None) -> str:
    agg = aggregate(episodes)
    payload = {
        "issue": ISSUE,
        "parent": PARENT,
        "log": log_path,
        "window_since": since,
        "sla_target": {"auto_recovery": 1.0, "mttr_max_s": SLA_MTTR_S},
        "lines_scanned": stats["lines_total"],
        "lines_unparsed": stats["lines_unparsed"],
        "wedges_total": agg["total"],
        "watchdog_auto": agg["watchdog_auto"],
        "auto_failed": agg["auto_failed"],
        "manual_or_open": agg["manual_or_open"],
        "repeat_wedge": agg["repeat_wedge"],
        "auto_recovery_rate": agg["auto_rate"],
        "mttr": {
            "n": agg["mttr_n"],
            "mean_s": agg["mttr_mean"],
            "p95_s": agg["mttr_p95"],
            "max_s": agg["mttr_max"],
            "method": "nearest-rank",
        },
        "sla_pass": agg["sla_pass"],
        "by_class": agg["by_class"],
        "episodes": [_episode_json(ep) for ep in episodes],
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def _default_log() -> str:
    return os.environ.get(LOG_ENV) or DEFAULT_LOG


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lightrag_recovery_sla",
        description=(
            "Read-only recovery-SLA report over the lightrag watchdog log "
            f"({ISSUE}, {PARENT} H3). Never writes to system state or the "
            "watchdog log (the report itself goes to stdout or --out)."
        ),
    )
    parser.add_argument(
        "--since",
        help="window start, UTC plain timestamp YYYY-MM-DDTHH:MM[:SS] "
        "(inclusive; minute form padded to :00)",
    )
    parser.add_argument(
        "--log",
        default=None,
        help=f"watchdog log path (default: ${LOG_ENV} or {DEFAULT_LOG})",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--out", help="write the text report to this path")
    args = parser.parse_args(argv)

    try:
        since = normalize_since(args.since) if args.since else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    log_path = args.log or _default_log()
    try:
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        print(f"error: cannot read log {log_path}: {exc}", file=sys.stderr)
        return 2

    episodes, stats = parse_log(lines)
    if since is not None:
        # Plain-timestamp compare (NFM-5202 pre-registered filter): both
        # sides are fixed-format 19-char UTC strings, so lexicographic ==
        # chronological — no bracket-split, no silent zero-row masking.
        episodes = [e for e in episodes if e.detected_at >= since]

    if args.json:
        output = build_json(episodes, stats, log_path, since)
    else:
        output = build_report(episodes, stats, log_path, since)

    if args.out:
        try:
            Path(args.out).write_text(output, encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
