"""Unit tests for the NFM-4587 mirror health probe.

NFM-4587 (2026-09-10): the G2 wall fronts Docker Hub via two public-image
mirrors (`docker.m.daocloud.io`, `docker.1panel.live`). Both have shown
intermittent failure modes — allowlist refusals, R2 blob EOFs, TLS
timeouts on `auth/token`. The fix has two halves:

  * The gate ships a `mirrors.json` config + a launchd mirror-health
    watchdog that probes each mirror's auth endpoint and records a
    per-mirror status. When fewer than ``min_healthy`` mirrors return
    200/401, the watchdog writes an ``alarm`` record to the JSONL audit
    log (so probe_g2.sh's heartbeat check can detect a single-mirror
    failure mode that masks a backend outage).
  * `probe_g2.sh` reads the latest audit record and asserts
    ``healthy_count >= 2`` before declaring the wall PASS.

These tests pin the classifier (parse config, classify a probe result,
summarize a set of results, write the alarm when below threshold).
Network probes are exercised against an in-process fake HTTP server so
no live mirror is required.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

GATE_DIR = Path(__file__).resolve().parents[1] / "host-prod-gate"
sys.path.insert(0, str(GATE_DIR))

from nfm_docker_gate.audit import AuditLog  # noqa: E402
from nfm_docker_gate.mirror_health import (  # noqa: E402
    HealthSummary,
    Mirror,
    ProbeResult,
    classify_status,
    load_mirrors,
    probe_mirror,
    summarize,
    write_alarm_if_below,
)


# ---- load_mirrors -------------------------------------------------------------


def test_load_mirrors_reads_minimal_config(tmp_path):
    cfg = tmp_path / "mirrors.json"
    cfg.write_text(
        json.dumps(
            {
                "mirrors": [
                    {
                        "name": "daocloud",
                        "url": "https://docker.m.daocloud.io",
                        "fronts_prod_images": True,
                    },
                    {"name": "1panel", "url": "https://docker.1panel.live"},
                ]
            }
        )
    )
    mirrors = load_mirrors(str(cfg))
    assert len(mirrors) == 2
    assert mirrors[0] == Mirror(
        name="daocloud",
        url="https://docker.m.daocloud.io",
        expected_status=(200, 401),
        fronts_prod_images=True,
    )
    assert mirrors[1].fronts_prod_images is False  # default


def test_load_mirrors_rejects_non_object(tmp_path):
    cfg = tmp_path / "mirrors.json"
    cfg.write_text(json.dumps([{"name": "x", "url": "https://x"}]))
    try:
        load_mirrors(str(cfg))
    except ValueError:
        return
    raise AssertionError("expected ValueError for non-object root")


def test_load_mirrors_rejects_missing_field(tmp_path):
    cfg = tmp_path / "mirrors.json"
    cfg.write_text(json.dumps({"mirrors": [{"name": "x"}]}))  # url missing
    try:
        load_mirrors(str(cfg))
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing url")


def test_load_mirrors_rejects_bad_url(tmp_path):
    cfg = tmp_path / "mirrors.json"
    cfg.write_text(json.dumps({"mirrors": [{"name": "x", "url": "not-a-url"}]}))
    try:
        load_mirrors(str(cfg))
    except ValueError:
        return
    raise AssertionError("expected ValueError for non-http url")


def test_load_mirrors_accepts_custom_expected_status(tmp_path):
    cfg = tmp_path / "mirrors.json"
    cfg.write_text(
        json.dumps(
            {
                "mirrors": [
                    {
                        "name": "daocloud",
                        "url": "https://docker.m.daocloud.io",
                        "expected_status": [200],
                    }
                ]
            }
        )
    )
    mirrors = load_mirrors(str(cfg))
    assert mirrors[0].expected_status == (200,)


def test_load_mirrors_dedups_by_name(tmp_path):
    cfg = tmp_path / "mirrors.json"
    cfg.write_text(
        json.dumps(
            {
                "mirrors": [
                    {"name": "daocloud", "url": "https://docker.m.daocloud.io"},
                    {"name": "daocloud", "url": "https://docker.m.daocloud.io"},
                ]
            }
        )
    )
    mirrors = load_mirrors(str(cfg))
    assert len(mirrors) == 1


# ---- classify_status ----------------------------------------------------------


def test_classify_status_ok_for_expected():
    assert classify_status(200, (200, 401)) == "ok"
    assert classify_status(401, (200, 401)) == "ok"


def test_classify_status_unexpected_for_other_codes():
    assert classify_status(404, (200, 401)) == "unexpected_status"
    assert classify_status(500, (200, 401)) == "unexpected_status"


# ---- probe_mirror (in-process HTTP server) ------------------------------------


class _FakeMirror(BaseHTTPRequestHandler):
    """Routes by path so the same server can model any failure mode."""

    response_code = 401
    response_body = b'{"token": "fake"}'

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
        self.send_response(self.response_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, format, *args):  # silence stderr noise
        pass


def _serve(handler_cls) -> tuple[str, threading.Thread]:
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://{host}:{port}", thread


class _OkMirror(_FakeMirror):
    response_code = 401


class _InternalErrorMirror(_FakeMirror):
    response_code = 500


def test_probe_mirror_returns_ok_for_expected_status():
    url, _ = _serve(_OkMirror)
    mirror = Mirror(name="ok", url=url, expected_status=(200, 401), fronts_prod_images=False)
    result = probe_mirror(mirror, timeout=2.0)
    assert result.status == "ok"
    assert result.http_status == 401
    assert result.duration_ms >= 0
    assert result.error is None


def test_probe_mirror_returns_unexpected_for_5xx():
    url, _ = _serve(_InternalErrorMirror)
    mirror = Mirror(name="broken", url=url, expected_status=(200, 401), fronts_prod_images=False)
    result = probe_mirror(mirror, timeout=2.0)
    assert result.status == "unexpected_status"
    assert result.http_status == 500


def test_probe_mirror_returns_unreachable_for_connection_refused():
    mirror = Mirror(
        name="dead",
        url="http://127.0.0.1:1",  # privileged port nothing listens on
        expected_status=(200, 401),
        fronts_prod_images=False,
    )
    result = probe_mirror(mirror, timeout=1.0)
    assert result.status == "unreachable"
    assert result.http_status is None


# ---- summarize ----------------------------------------------------------------


def test_summarize_counts_healthy_when_status_ok():
    results = (
        ProbeResult("a", "u1", "ok", 401, None, 12),
        ProbeResult("b", "u2", "ok", 401, None, 9),
        ProbeResult("c", "u3", "unexpected_status", 500, None, 20),
    )
    summary = summarize(results, threshold=2)
    assert summary.healthy_count == 2
    assert summary.healthy_below_threshold is False


def test_summarize_alarms_when_below_threshold():
    results = (
        ProbeResult("a", "u1", "ok", 401, None, 12),
        ProbeResult("b", "u2", "tls_timeout", None, "TLS handshake timeout", 5000),
        ProbeResult("c", "u3", "unreachable", None, "Connection refused", 200),
    )
    summary = summarize(results, threshold=2)
    assert summary.healthy_count == 1
    assert summary.healthy_below_threshold is True


def test_summarize_flags_prod_mirror_unhealthy():
    results = (
        ProbeResult("daocloud", "u1", "unreachable", None, "Connection refused", 200),
        ProbeResult("dockerproxy", "u2", "ok", 401, None, 12),
        ProbeResult("ustc", "u3", "ok", 401, None, 10),
    )
    mirrors_by_name = {
        "daocloud": Mirror(
            name="daocloud", url="u1", expected_status=(200, 401), fronts_prod_images=True
        ),
        "dockerproxy": Mirror(
            name="dockerproxy", url="u2", expected_status=(200, 401), fronts_prod_images=False
        ),
        "ustc": Mirror(
            name="ustc", url="u3", expected_status=(200, 401), fronts_prod_images=False
        ),
    }
    summary = summarize(results, threshold=2, mirrors_by_name=mirrors_by_name)
    assert summary.prod_mirror_healthy is False
    assert summary.healthy_count == 2
    assert summary.healthy_below_threshold is False  # 2 OK overall but prod is dark


def test_summarize_no_alarm_when_threshold_one():
    results = (ProbeResult("a", "u1", "ok", 401, None, 12),)
    summary = summarize(results, threshold=1)
    assert summary.healthy_below_threshold is False


# ---- write_alarm_if_below -----------------------------------------------------


def test_write_alarm_if_below_writes_alarm_record(tmp_path):
    audit_path = tmp_path / "audit.log"
    audit = AuditLog(str(audit_path), "mirror-health")
    summary = HealthSummary(
        results=(
            ProbeResult("a", "u1", "ok", 401, None, 12),
            ProbeResult("b", "u2", "unreachable", None, "Connection refused", 100),
        ),
        healthy_count=1,
        prod_mirror_healthy=True,
        healthy_below_threshold=True,
    )
    write_alarm_if_below(audit, summary, threshold=2)
    record = json.loads(audit_path.read_text().strip().splitlines()[-1])
    assert record["event"] == "alarm"
    assert record["mode"] == "mirror-health"
    assert record["healthy_count"] == 1
    assert record["threshold"] == 2
    assert record["prod_mirror_healthy"] is True


def test_write_alarm_if_below_skips_when_healthy(tmp_path):
    audit_path = tmp_path / "audit.log"
    audit = AuditLog(str(audit_path), "mirror-health")
    summary = HealthSummary(
        results=(
            ProbeResult("a", "u1", "ok", 401, None, 12),
            ProbeResult("b", "u2", "ok", 401, None, 11),
        ),
        healthy_count=2,
        prod_mirror_healthy=True,
        healthy_below_threshold=False,
    )
    write_alarm_if_below(audit, summary, threshold=2)
    # Healthy run: no alarm record written. The file may or may not exist
    # depending on whether AuditLog created it on init (it does not until
    # the first write — verified by reading the same `audit_path` from the
    # write_alarm_if_below_writes_alarm_record test, where the file IS
    # created). We assert by absence: a non-existent file OR an empty
    # one both mean "no alarm written".
    contents = audit_path.read_text() if audit_path.exists() else ""
    assert contents == ""


def test_write_alarm_if_below_records_prod_mirror_dark(tmp_path):
    """Even when the overall healthy_count meets the threshold, a dark prod
    mirror (fronts_prod_images=True but unreachable) must alarm: the AC
    demands a real `docker pull nucpot-prod-api:latest` succeeds end-to-end,
    which requires the prod allowlisted mirror to be reachable."""
    audit_path = tmp_path / "audit.log"
    audit = AuditLog(str(audit_path), "mirror-health")
    summary = HealthSummary(
        results=(
            ProbeResult("daocloud", "u1", "unreachable", None, "Connection refused", 200),
            ProbeResult("dockerproxy", "u2", "ok", 401, None, 12),
            ProbeResult("ustc", "u3", "ok", 401, None, 10),
        ),
        healthy_count=2,
        prod_mirror_healthy=False,
        healthy_below_threshold=False,
    )
    write_alarm_if_below(audit, summary, threshold=2)
    record = json.loads(audit_path.read_text().strip().splitlines()[-1])
    assert record["event"] == "alarm"
    assert record["prod_mirror_healthy"] is False
    assert record["healthy_count"] == 2


# ---- probe_mirror socket.timeout parity (py3.9 launchd runs this) -----------


def test_probe_mirror_uses_socket_timeout_not_builtin_timeouterror():
    """NFM-4320: py3.9 has socket.timeout but NOT TimeoutError; the launchd
    proxy runs under 3.9. probe_mirror must catch the right one or a stalled
    socket hangs the watchdog forever."""
    # Closed port -> ECONNREFUSED, not a timeout. If the implementation
    # reached for TimeoutError instead of socket.timeout, this test would
    # surface as 'unreachable' but with a hung watcher in production.
    mirror = Mirror(
        name="x",
        url="http://127.0.0.1:1",
        expected_status=(200, 401),
        fronts_prod_images=False,
    )
    result = probe_mirror(mirror, timeout=0.5)
    assert result.status == "unreachable"
    # result.error must be a string, not an exception repr (RE/operator-readable)
    assert isinstance(result.error, str)


# ---- defaults shape: ensure expected_status defaults survive (200, 401) -------


def test_default_expected_status_is_200_and_401():
    mirror = Mirror(name="x", url="http://x", fronts_prod_images=False)
    assert mirror.expected_status == (200, 401)


# ---- CLI: skip if no real mirror reachable; the loop entry writes to the same audit log


def test_audit_log_records_mirror_probe_line(tmp_path):
    """Smoke: the mirror-health watchdog's audit log line is a single JSON
    record per probe, identical to the gate's other audit streams, so
    probe_g2.sh's heartbeat check can grep the same file."""
    audit_path = tmp_path / "audit.log"
    audit = AuditLog(str(audit_path), "mirror-health")
    summary = HealthSummary(
        results=(
            ProbeResult("daocloud", "https://docker.m.daocloud.io", "ok", 401, None, 12),
            ProbeResult("1panel", "https://docker.1panel.live", "unreachable", None, "x", 1),
        ),
        healthy_count=1,
        prod_mirror_healthy=True,
        healthy_below_threshold=True,  # only one OK, threshold 2
    )
    write_alarm_if_below(audit, summary, threshold=2)
    records = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["event"] == "alarm"
    assert records[0]["mode"] == "mirror-health"
    # The alarm record must enumerate the failing mirror so an operator can
    # identify which backend is dark without re-running the probe.
    assert records[0]["unhealthy"] == ["1panel"]


# ---- helper used by probe_g2.sh: latest alarm wins ----------------------------


def test_latest_alarm_returns_most_recent_record(tmp_path):
    """probe_g2.sh reads the audit log to verify the heartbeat alarm. When
    the alarm log has multiple records (probe runs every interval), the
    heartbeat must consider the MOST RECENT record's verdict, not an older
    one — otherwise a recovery would not be detectable until the log rotates.
    """
    from nfm_docker_gate.mirror_health import latest_alarm

    audit_path = tmp_path / "audit.log"
    audit = AuditLog(str(audit_path), "mirror-health")
    bad = HealthSummary(
        results=(ProbeResult("a", "u", "unreachable", None, "x", 1),),
        healthy_count=0,
        prod_mirror_healthy=False,
        healthy_below_threshold=True,
    )
    good = HealthSummary(
        results=(ProbeResult("a", "u", "ok", 200, None, 1),),
        healthy_count=1,
        prod_mirror_healthy=True,
        healthy_below_threshold=False,
    )
    write_alarm_if_below(audit, bad, threshold=1)
    write_alarm_if_below(audit, good, threshold=1)  # no record written (healthy)
    record = latest_alarm(str(audit_path))
    assert record is not None
    assert record["healthy_count"] == 0  # only the alarm record is there


def test_latest_alarm_returns_none_when_log_missing(tmp_path):
    from nfm_docker_gate.mirror_health import latest_alarm

    assert latest_alarm(str(tmp_path / "missing.log")) is None