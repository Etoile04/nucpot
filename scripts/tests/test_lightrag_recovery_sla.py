"""Tests for scripts/lightrag_recovery_sla.py (NFM-5339).

NFM-5202 selected branch H3 at gate close (2026-10-07): the lightrag
watchdog is the mitigation-of-record and the AC5-successor success bar
is a recovery SLA — 100% auto-recovery, MTTR < 60s per wedge. This
module is the READ-ONLY measurement layer over
/var/log/nfm-g2/lightrag-watchdog.log: it never writes, never touches
the watchdog's kill path, probes, or cadence.

Watchdog log grammar (start-lightrag-watchdog.sh, NFM-4804/4816/4887):

    <UTC ts> probe=clean|skip|suppressed|WEDGE ...
    <UTC ts> host=healthy|runner-absent|wedge-suspect|stop-recovered|
             stop-failed|term|verified|verify-failed|probe-error ...
    <UTC ts> action=restart-lightrag rc=N
    <UTC ts> action=lightrag-reprocess rc=N
    <UTC ts> action=lightrag-reprocess skipped reason=restart-failed rc=N

One wedge EPISODE = one ``probe=WEDGE`` line plus the host=/action=
lines that follow it, up to the next ``probe=`` line or EOF (d2=/e1=
lifecycle lines from other stages are not episode content).

Classification (NFM-5202 taxonomy, cross-checked against the wedge of
record 2026-09-24T04:00:10Z which the parent labels "host-level" while
its own host stage reported healthy — the class is the watchdog's
action attribution, not the host-probe outcome):

  - class=host             WEDGE line action contains host-unwedge
                           (post-NFM-4887: host remediation stage ran)
  - class=lightrag-internal pre-NFM-4887 action=restart-lightrag only

MTTR of record = detection -> last successful recovery action
(lightrag-reprocess rc=0 when present — NFM-4816 — else restart
rc=0). Manual/open episodes carry no log-derivable completion and are
excluded from MTTR stats while still counting in the auto-rate
denominator (no silently-dropped rows).

Window filtering is a PLAIN-timestamp compare on the fixed-format UTC
prefix — the NFM-5202 pre-registered method (a bracket-split filter
silently read 0 rows and masked a RED); minute-granularity --since
values are padded to seconds so ``2026-09-22T14:01`` matches
``...T14:01:00Z`` onward.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import lightrag_recovery_sla as mod  # noqa: E402


def _write_log(tmp_path: Path, lines: list[str]) -> Path:
    log = tmp_path / "lightrag-watchdog.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log


def _run(log: Path, *args: str, capsys):
    rc = mod.main(["--log", str(log), *args])
    out = capsys.readouterr().out
    return rc, out


# ---------------------------------------------------------------------------
# Real log shapes (verbatim lines from /var/log/nfm-g2/lightrag-watchdog.log)
# ---------------------------------------------------------------------------

WEDGE_OF_RECORD = [
    "2026-09-24T03:55:09Z probe=clean container=nucpot-prod-lightrag boot=2026-09-23T14:58:43.994727171Z reason=normal-drain",
    "2026-09-24T04:00:10Z probe=WEDGE container=nucpot-prod-lightrag boot=2026-09-23T14:58:43.994727171Z action=host-unwedge+restart-lightrag",
    "2026-09-24T04:00:11Z host=healthy model=qwen3.5:4b-nvfp4 probe=generate",
    "2026-09-24T04:00:14Z action=restart-lightrag rc=0",
    "2026-09-24T04:00:25Z action=lightrag-reprocess rc=0",
    "2026-09-24T04:05:26Z probe=clean container=nucpot-prod-lightrag boot=2026-09-24T04:00:14.430151972Z reason=no-pipeline-stop",
]

HOST_TERM_EPISODE = [
    "2026-09-17T03:51:33Z probe=WEDGE container=nucpot-prod-lightrag boot=2026-09-16T07:02:06.566377627Z action=host-unwedge+restart-lightrag",
    "2026-09-17T03:52:18Z host=wedge-suspect model=qwen3.5:4b-nvfp4 pid=94871 cpu= 71.6",
    "2026-09-17T03:52:36Z host=stop-failed model=qwen3.5:4b-nvfp4 pid=94871 (pid alive after ollama stop — wedge signature)",
    "2026-09-17T03:52:38Z host=term pid=94871 rc=0",
    "2026-09-17T03:52:39Z host=verified model=qwen3.5:4b-nvfp4 probe=generate",
    "2026-09-17T03:52:41Z action=restart-lightrag rc=0",
    "2026-09-17T03:52:52Z action=lightrag-reprocess rc=0",
    "2026-09-17T03:57:42Z probe=clean container=nucpot-prod-lightrag boot=2026-09-17T03:52:41.097541718Z reason=no-pipeline-stop",
]

OLD_FORMAT_EPISODE = [
    "2026-09-13T00:41:37Z probe=WEDGE container=nucpot-prod-lightrag boot=2026-09-12T22:41:42.602700673Z action=restart-lightrag",
    "2026-09-13T00:41:38Z action=restart-lightrag rc=0",
    "2026-09-13T00:46:39Z probe=clean container=nucpot-prod-lightrag boot=2026-09-13T00:41:38.650269295Z reason=no-pipeline-stop",
]

VERIFY_FAILED_EPISODE = [
    "2026-10-03T04:12:16Z probe=WEDGE container=nucpot-prod-lightrag boot=2026-10-02T04:38:33.042836094Z action=host-unwedge+restart-lightrag",
    "2026-10-03T04:13:06Z host=wedge-suspect model=qwen3.5:4b-nvfp4 pid=46970 cpu= 61.3",
    "2026-10-03T04:13:52Z host=stop-failed model=qwen3.5:4b-nvfp4 pid=46970 (pid alive after ollama stop — wedge signature)",
    "2026-10-03T04:13:54Z host=term pid=46970 rc=0",
    "2026-10-03T04:14:09Z host=verify-failed model=qwen3.5:4b-nvfp4 probe=generate (operator attention)",
    "2026-10-03T04:14:12Z action=restart-lightrag rc=0",
    "2026-10-03T04:14:24Z action=lightrag-reprocess rc=0",
    "2026-10-03T04:19:25Z probe=clean container=nucpot-prod-lightrag boot=2026-10-03T04:14:12Z reason=no-pipeline-stop",
]


# ---------------------------------------------------------------------------
# Episode parsing
# ---------------------------------------------------------------------------


class TestParseEpisodes:
    def test_wedge_of_record_shape(self, tmp_path):
        log = _write_log(tmp_path, WEDGE_OF_RECORD)
        episodes, stats = mod.parse_log(log.read_text().splitlines())
        assert len(episodes) == 1
        ep = episodes[0]
        assert ep.detected_at == "2026-09-24T04:00:10"
        assert ep.cls == "host"
        assert ep.host_stage == "healthy"
        assert ep.mode == "watchdog-auto"
        assert ep.restart_rc == 0
        assert ep.reprocess_rc == 0
        assert ep.mttr_restart_s == 4
        assert ep.mttr_s == 15  # detection 04:00:10 -> reprocess rc=0 04:00:25
        assert stats["lines_total"] == 6
        assert stats["lines_unparsed"] == 0

    def test_host_term_episode_stage_summary(self, tmp_path):
        log = _write_log(tmp_path, HOST_TERM_EPISODE)
        episodes, _ = mod.parse_log(log.read_text().splitlines())
        ep = episodes[0]
        assert ep.cls == "host"
        assert ep.host_stage == "suspect->term+verified"
        assert ep.mode == "watchdog-auto"
        assert ep.mttr_restart_s == 68
        assert ep.mttr_s == 79

    def test_verify_failed_visible_in_stage(self, tmp_path):
        log = _write_log(tmp_path, VERIFY_FAILED_EPISODE)
        episodes, _ = mod.parse_log(log.read_text().splitlines())
        assert episodes[0].host_stage == "suspect->term+verify-failed"
        # host verify failed, but the container recovery still completed
        assert episodes[0].mode == "watchdog-auto"

    def test_old_format_is_lightrag_internal(self, tmp_path):
        log = _write_log(tmp_path, OLD_FORMAT_EPISODE)
        episodes, _ = mod.parse_log(log.read_text().splitlines())
        ep = episodes[0]
        assert ep.cls == "lightrag-internal"
        assert ep.host_stage == "not-run(pre-4887)"
        assert ep.mode == "watchdog-auto"
        # pre-NFM-4816: no reprocess step; MTTR of record = restart rc=0
        assert ep.reprocess_rc is None
        assert ep.mttr_s == 1

    def test_manual_episode_no_watchdog_action(self, tmp_path):
        lines = [
            "2026-09-20T04:00:00Z probe=WEDGE container=nucpot-prod-lightrag boot=2026-09-19T07:12:08.217627003Z action=host-unwedge+restart-lightrag",
            # operator restarted by hand; next watchdog probe sees a fresh boot
            "2026-09-20T04:35:00Z probe=clean container=nucpot-prod-lightrag boot=2026-09-20T04:20:00Z reason=no-pipeline-stop",
        ]
        log = _write_log(tmp_path, lines)
        episodes, _ = mod.parse_log(log.read_text().splitlines())
        ep = episodes[0]
        assert ep.mode == "manual"
        assert ep.mttr_s is None
        assert ep.sla_pass is False

    def test_open_episode_at_log_end(self, tmp_path):
        lines = [
            "2026-10-07T04:00:00Z probe=clean container=nucpot-prod-lightrag boot=2026-10-07T03:00:00Z reason=normal-drain",
            "2026-10-07T04:05:00Z probe=WEDGE container=nucpot-prod-lightrag boot=2026-10-07T03:00:00Z action=host-unwedge+restart-lightrag",
        ]
        log = _write_log(tmp_path, lines)
        episodes, _ = mod.parse_log(log.read_text().splitlines())
        assert episodes[0].mode == "open"
        assert episodes[0].mttr_s is None

    def test_reprocess_failure_after_restart_is_auto_failed(self, tmp_path):
        lines = [
            "2026-09-21T04:00:00Z probe=WEDGE container=nucpot-prod-lightrag boot=2026-09-21T03:36:41Z action=host-unwedge+restart-lightrag",
            "2026-09-21T04:00:04Z action=restart-lightrag rc=0",
            "2026-09-21T04:00:15Z action=lightrag-reprocess rc=1",
            "2026-09-21T04:05:26Z probe=clean container=nucpot-prod-lightrag boot=2026-09-21T04:00:04Z reason=no-pipeline-stop",
        ]
        log = _write_log(tmp_path, lines)
        episodes, _ = mod.parse_log(log.read_text().splitlines())
        ep = episodes[0]
        # restart succeeded but the NFM-4816 re-enqueue ran and failed:
        # service is not whole, so no MTTR of record and no auto credit
        assert ep.mode == "auto-failed"
        assert ep.mttr_restart_s == 4
        assert ep.mttr_s is None
        assert ep.sla_pass is False

    def test_repeat_wedge_detection_is_not_manual(self, tmp_path):
        lines = [
            "2026-09-20T04:00:00Z probe=WEDGE container=nucpot-prod-lightrag boot=b action=host-unwedge+restart-lightrag",
            # watchdog re-detected the still-wedged container: no operator
            # action, no logged recovery for the first episode
            "2026-09-20T04:01:00Z probe=WEDGE container=nucpot-prod-lightrag boot=b action=host-unwedge+restart-lightrag",
            "2026-09-20T04:01:05Z action=restart-lightrag rc=0",
            "2026-09-20T04:01:16Z action=lightrag-reprocess rc=0",
            "2026-09-20T04:06:17Z probe=clean container=nucpot-prod-lightrag boot=b reason=no-pipeline-stop",
        ]
        log = _write_log(tmp_path, lines)
        episodes, _ = mod.parse_log(log.read_text().splitlines())
        assert episodes[0].mode == "repeat-wedge"
        assert episodes[0].mttr_s is None
        assert episodes[1].mode == "watchdog-auto"
        assert episodes[1].mttr_s == 16

    def test_auto_failed_restart(self, tmp_path):
        lines = [
            "2026-09-21T04:00:00Z probe=WEDGE container=nucpot-prod-lightrag boot=2026-09-21T03:36:41Z action=host-unwedge+restart-lightrag",
            "2026-09-21T04:00:20Z host=healthy model=qwen3.5:4b-nvfp4 probe=generate",
            "2026-09-21T04:00:25Z action=restart-lightrag rc=1",
            "2026-09-21T04:00:25Z action=lightrag-reprocess skipped reason=restart-failed rc=1",
            "2026-09-21T04:05:26Z probe=clean container=nucpot-prod-lightrag boot=2026-09-21T03:36:41Z reason=no-pipeline-stop",
        ]
        log = _write_log(tmp_path, lines)
        episodes, _ = mod.parse_log(log.read_text().splitlines())
        ep = episodes[0]
        assert ep.mode == "auto-failed"
        assert ep.mttr_s is None
        assert ep.sla_pass is False

    def test_malformed_lines_counted_not_fabricated(self, tmp_path):
        lines = [
            "not-a-timestamped-line",
            "",
            "2026-09-24T04:00:10Z probe=WEDGE container=nucpot-prod-lightrag boot=x action=host-unwedge+restart-lightrag",
            "2026-09-24T04:00:14Z action=restart-lightrag rc=0",
            "2026-09-24T04:00:25Z action=lightrag-reprocess rc=0",
            "garbage line two",
        ]
        log = _write_log(tmp_path, lines)
        episodes, stats = mod.parse_log(log.read_text().splitlines())
        assert stats["lines_total"] == 6
        assert stats["lines_unparsed"] == 3
        assert len(episodes) == 1  # no fabricated rows

    def test_d2_e1_lines_are_not_episode_content(self, tmp_path):
        lines = [
            "2026-10-05T03:25:00Z e1=window-enter bounds=205-300 tick=60s lifetime=600s busy_guard=off",
            "2026-10-05T03:30:00Z d2=recycled window=1 phase=preburst model=qwen3.5:4b-nvfp4 pid=123",
            "2026-10-05T03:59:19Z probe=WEDGE container=nucpot-prod-lightrag boot=2026-10-05T03:00:00Z action=host-unwedge+restart-lightrag",
            "2026-10-05T03:59:46Z host=healthy model=qwen3.5:4b-nvfp4 probe=generate",
            "2026-10-05T03:59:50Z action=restart-lightrag rc=0",
            "2026-10-05T04:00:01Z action=lightrag-reprocess rc=0",
            "2026-10-05T04:00:30Z d2=skip window=1 reason=under-max-lifetime age_lt=600s model=qwen3.5:4b-nvfp4 pid=456",
            "2026-10-05T04:05:31Z probe=clean container=nucpot-prod-lightrag boot=2026-10-05T04:00:01Z reason=no-pipeline-stop",
        ]
        log = _write_log(tmp_path, lines)
        episodes, stats = mod.parse_log(log.read_text().splitlines())
        assert len(episodes) == 1
        assert episodes[0].mttr_s == 42
        assert stats["lines_unparsed"] == 0  # d2=/e1= are recognized, not errors


# ---------------------------------------------------------------------------
# Window filtering (the NFM-5202 plain-timestamp-compare trap)
# ---------------------------------------------------------------------------


class TestWindowFilter:
    @pytest.fixture()
    def three_day_log(self, tmp_path):
        lines = OLD_FORMAT_EPISODE + WEDGE_OF_RECORD + VERIFY_FAILED_EPISODE
        return _write_log(tmp_path, lines)  # episodes: 09-13, 09-24, 10-03

    def test_since_filters_episodes(self, tmp_path, three_day_log, capsys):
        rc, out = _run(three_day_log, "--since", "2026-09-22T14:01", capsys=capsys)
        assert rc == 0
        assert "2026-09-24T04:00:10" in out
        assert "2026-10-03T04:12:16" in out
        assert "2026-09-13T00:41:37" not in out
        assert "wedge episodes in window: 2" in out

    def test_minute_granularity_since_matches_inclusive_boundary(self, tmp_path, capsys):
        lines = [
            "2026-09-22T14:00:59Z probe=WEDGE container=c boot=b action=host-unwedge+restart-lightrag",
            "2026-09-22T14:01:00Z action=restart-lightrag rc=0",
            "2026-09-22T14:01:01Z probe=WEDGE container=c boot=b action=host-unwedge+restart-lightrag",
            "2026-09-22T14:01:30Z action=restart-lightrag rc=0",
        ]
        log = _write_log(tmp_path, lines)
        rc, out = _run(log, "--since", "2026-09-22T14:01", capsys=capsys)
        assert rc == 0
        assert "14:00:59" not in out
        assert "14:01:01" in out  # padded to :00 — 14:01:00 boundary would match too
        assert "wedge episodes in window: 1" in out

    def test_full_seconds_since_form_equivalent(self, tmp_path, three_day_log, capsys):
        _, out_a = _run(three_day_log, "--since", "2026-09-22T14:01", capsys=capsys)
        _, out_b = _run(three_day_log, "--since", "2026-09-22T14:01:00Z", capsys=capsys)
        assert out_a == out_b

    def test_zero_match_window_is_loud_not_silent(self, tmp_path, three_day_log, capsys):
        rc, out = _run(three_day_log, "--since", "2026-11-01T00:00", capsys=capsys)
        assert rc == 0
        assert "wedge episodes in window: 0" in out
        assert "2026-11-01T00:00" in out  # the window itself is echoed

    def test_no_since_means_full_log(self, tmp_path, three_day_log, capsys):
        rc, out = _run(three_day_log, capsys=capsys)
        assert rc == 0
        assert "wedge episodes in window: 3" in out

    def test_bad_since_exits_nonzero(self, tmp_path, three_day_log, capsys):
        rc, _ = _run(three_day_log, "--since", "yesterday-ish", capsys=capsys)
        assert rc == 2

    def test_missing_log_exits_nonzero(self, tmp_path, capsys):
        rc = mod.main(["--log", str(tmp_path / "nope.log")])
        assert rc == 2


# ---------------------------------------------------------------------------
# Aggregates and report
# ---------------------------------------------------------------------------


class TestAggregates:
    def _multi_episode_log(self, tmp_path):
        # 4 auto wedges, MTTRs (detection->reprocess) 33/30/15/79
        # -> mean 39.2s, nearest-rank p95 79s, max 79s
        lines = []
        for day, (detect, restart, reprocess) in {
            "2026-09-22": ("04:01:46", "04:02:08", "04:02:19"),
            "2026-09-23": ("04:00:00", "04:00:15", "04:00:30"),
            "2026-09-24": ("04:00:10", "04:00:14", "04:00:25"),
            "2026-09-25": ("04:00:00", "04:01:08", "04:01:19"),
        }.items():
            lines.append(
                f"{day}T{detect}Z probe=WEDGE container=nucpot-prod-lightrag boot=b action=host-unwedge+restart-lightrag"
            )
            lines.append(f"{day}T{restart}Z action=restart-lightrag rc=0")
            lines.append(f"{day}T{reprocess}Z action=lightrag-reprocess rc=0")
        return _write_log(tmp_path, lines)

    def test_aggregate_line_and_p95_nearest_rank(self, tmp_path, capsys):
        log = self._multi_episode_log(tmp_path)
        rc, out = _run(log, capsys=capsys)
        assert rc == 0
        # MTTRs: 09-22=33, 09-23=30, 09-24=15, 09-25=79 -> sorted [15,30,33,79]
        # nearest-rank p95: ceil(0.95*4)=4 -> index 3 -> 79
        assert "mttr mean / p95 / max   : 39.2s / 79s / 79s" in out
        assert "auto-recovery rate      : 4/4 (100.0%)" in out
        # one wedge (79s) exceeds 60s -> SLA FAIL even at 100% auto
        assert "SLA (100% auto, <60s/MTTR): FAIL" in out

    def test_manual_counts_against_auto_rate(self, tmp_path, capsys):
        lines = [
            *WEDGE_OF_RECORD,
            "2026-09-25T04:00:00Z probe=WEDGE container=c boot=b action=host-unwedge+restart-lightrag",
            "2026-09-25T04:35:00Z probe=clean container=c boot=2026-09-25T04:20:00Z reason=no-pipeline-stop",
        ]
        log = _write_log(tmp_path, lines)
        rc, out = _run(log, capsys=capsys)
        assert rc == 0
        assert "auto-recovery rate      : 1/2 (50.0%)" in out
        assert "manual-or-open          : 1" in out

    def test_reprocess_failure_fails_sla_not_masks_it(self, tmp_path, capsys):
        lines = [
            *WEDGE_OF_RECORD,
            "2026-09-25T04:00:00Z probe=WEDGE container=c boot=b action=host-unwedge+restart-lightrag",
            "2026-09-25T04:00:04Z action=restart-lightrag rc=0",
            "2026-09-25T04:00:15Z action=lightrag-reprocess rc=1",
            "2026-09-25T04:05:26Z probe=clean container=c boot=b reason=no-pipeline-stop",
        ]
        log = _write_log(tmp_path, lines)
        rc, out = _run(log, capsys=capsys)
        assert rc == 0
        assert "auto-recovery rate      : 1/2 (50.0%)" in out
        assert "auto-failed             : 1" in out
        assert "mttr stats over watchdog-completed recoveries: n=1" in out
        assert "SLA (100% auto, <60s/MTTR): FAIL" in out

    def test_repeat_wedge_counted_against_auto_rate(self, tmp_path, capsys):
        lines = [
            *WEDGE_OF_RECORD,
            "2026-09-25T04:00:00Z probe=WEDGE container=c boot=b action=host-unwedge+restart-lightrag",
            "2026-09-25T04:01:00Z probe=WEDGE container=c boot=b action=host-unwedge+restart-lightrag",
            "2026-09-25T04:01:05Z action=restart-lightrag rc=0",
            "2026-09-25T04:01:16Z action=lightrag-reprocess rc=0",
            "2026-09-25T04:06:17Z probe=clean container=c boot=b reason=no-pipeline-stop",
        ]
        log = _write_log(tmp_path, lines)
        rc, out = _run(log, capsys=capsys)
        assert rc == 0
        assert "repeat-wedge            : 1" in out
        assert "auto-recovery rate      : 2/3 (66.7%)" in out

    def test_by_class_breakdown(self, tmp_path, capsys):
        lines = OLD_FORMAT_EPISODE + WEDGE_OF_RECORD
        log = _write_log(tmp_path, lines)
        rc, out = _run(log, capsys=capsys)
        assert rc == 0
        assert "by class: host=1 (auto 100.0%), lightrag-internal=1 (auto 100.0%)" in out


class TestReportContract:
    def test_deterministic_output(self, tmp_path, capsys):
        log = _write_log(tmp_path, OLD_FORMAT_EPISODE + WEDGE_OF_RECORD)
        _, out_a = _run(log, capsys=capsys)
        _, out_b = _run(log, capsys=capsys)
        assert out_a == out_b  # same log snapshot -> byte-identical report

    def test_no_wallclock_in_report(self, tmp_path, capsys):
        log = _write_log(tmp_path, WEDGE_OF_RECORD)
        _, out = _run(log, capsys=capsys)
        assert "2026-10-07T" not in out  # generated-at would break determinism
        assert "NFM-5339" in out

    def test_json_output(self, tmp_path, capsys):
        log = _write_log(tmp_path, WEDGE_OF_RECORD)
        rc = mod.main(["--log", str(log), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["wedges_total"] == 1
        assert payload["auto_recovery_rate"] == 1.0
        assert payload["episodes"][0]["detected_at"] == "2026-09-24T04:00:10"
        assert payload["episodes"][0]["mttr_s"] == 15

    def test_out_file_writes_report(self, tmp_path, capsys):
        log = _write_log(tmp_path, WEDGE_OF_RECORD)
        out_file = tmp_path / "report.txt"
        rc = mod.main(["--log", str(log), "--out", str(out_file)])
        assert rc == 0
        assert "2026-09-24T04:00:10" in out_file.read_text()

    def test_default_log_path_env_override(self, tmp_path, monkeypatch, capsys):
        log = _write_log(tmp_path, WEDGE_OF_RECORD)
        monkeypatch.setenv("NFM_LIGHTRAG_WATCHDOG_LOG", str(log))
        rc = mod.main([])
        out = capsys.readouterr().out
        assert rc == 0
        assert "wedge episodes in window: 1" in out

    def test_tool_is_read_only(self, tmp_path, capsys):
        log = _write_log(tmp_path, WEDGE_OF_RECORD)
        before = log.read_bytes()
        _run(log, capsys=capsys)
        _run(log, "--json", capsys=capsys)
        assert log.read_bytes() == before  # hard constraint: read-only over logs
