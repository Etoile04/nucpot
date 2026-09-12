"""Mirror health probe for the NFM-4587 G2 mirror-allowlist expansion.

The G2 wall fronts Docker Hub via a configurable list of public-image
mirrors (``mirrors.json``). The mirrors fail intermittently at three
distinct points measured 2026-09-10 on wenjiedeMac-Studio:

  1. allowlist refusal (image not on the mirror's allowlist)
  2. R2 backend EOF on specific blobs (intermittent, SHA-scoped)
  3. TLS handshake timeout on ``/auth/token``

The first is a config gap (handled by adding fallback mirrors in
``daemon.json``). The second and third are real network failures that
look identical from the client side: a single mirror going dark masks
backend outages, and ``docker pull nucpot-prod-api:latest`` deadlocks
until a human notices the build stuck.

This module is the gate-side half of the fix:

  * ``load_mirrors(path)`` reads the JSON config the host installer
    writes to ``/usr/local/lib/nfm-g2/mirrors.json``.
  * ``probe_mirror(mirror, timeout)`` opens a TLS connection to the
    mirror's ``/v2/`` (Docker registry API v2 root — a canonical, stable
    endpoint that returns 401 for unauthenticated requests on every
    Docker registry; ``/auth/token`` was tried but the handshake times
    out on the same flaky paths, masking the very failure we want to
    detect). Classifies the result into ``ok`` / ``unexpected_status`` /
    ``tls_timeout`` / ``unreachable``.
  * ``summarize(results, threshold)`` aggregates a probe run: total
    healthy count, whether ANY prod allowlisted mirror is dark, and a
    boolean ``healthy_below_threshold`` that drives the alarm writer.
  * ``write_alarm_if_below`` appends an ``alarm`` JSONL record to the
    mirror-health audit log when below threshold OR when the prod
    mirror is dark (the AC demands a real ``docker pull
    nucpot-prod-api:latest`` succeeds, which requires the prod allowlist
    mirror to be reachable — even with healthy fallbacks, a dark prod
    mirror fails the deploy path).
  * ``latest_verdict(path)`` is the heartbeat reader used by probe_g2.sh
    to verify ≥2 mirrors returning 200/401. It reads the newest record
    across BOTH verdict events — ``alarm`` (tripped) and ``recovery``
    (cleared, NFM-4805) — so the standing verdict reflects current
    fleet state instead of latching the last alarm forever.

The launchd loop wrapper (``entries/start-mirror-health.sh``) polls on
a fixed interval, runs a probe + summarize + write per tick, and the
audit log is the operator-visible evidence of single-mirror failure
modes. The companion daemon.json change (adding fallback mirrors) is
the operator's job — ``host_setup.sh`` writes both the gate config and
the ``~/.docker/daemon.json`` registry-mirrors list at install time.

``main()`` is the launchd entry point: it loops ``probe + summarize +
write_alarm_if_below`` on a fixed interval, logging every transition
(startup, drift-class results) so a restart is forensically traceable.

Py3.9-clean (the watchdog runs under ``/usr/bin/python3`` on the
production host): no PEP 604 unions, no ``datetime.UTC``, catch
``socket.timeout`` not ``TimeoutError``.
"""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .audit import AuditLog

# Default probe path: Docker registry API v2 root. Returns 401 unauth
# on every working Docker registry; works for all the public mirrors
# (daocloud, 1panel, dockerproxy, ustc). /auth/token was tried but the
# handshake times out on the same flaky paths — using /v2/ keeps the
# probe orthogonal to the failure mode it monitors.
_DEFAULT_PROBE_PATH = "/v2/"
# Auth sub-resource on Docker registry v2; used as a fallback if /v2/
# returns something unexpected (some mirrors wrap 401s differently).
_DEFAULT_AUTH_PATH = "/auth"

# Mirrors that "look healthy" from this probe's perspective. Anything
# else (timeout, refused, 5xx) is a real fault the operator should see.
_HEALTHY_STATUSES = (200, 401)

# Default status set the mirror probe will accept as "healthy". The
# config can override per-mirror when a mirror fronts a known custom
# status (rare; the AC requires 401/200 across the board).
_DEFAULT_EXPECTED = (200, 401)


@dataclass(frozen=True)
class Mirror:
    """A single registry mirror the gate fronts Docker Hub through.

    fronts_prod_images is the bridge between the mirror config and the
    prod-image-pull path. When the only mirror that fronts nucpot-prod-*
    goes dark, every fallback mirror (which DOES NOT front prod images)
    can still return 200/401 — the AC's "≥2 mirrors return 200/401"
    passes, but the AC's "real docker pull of nucpot-prod-api:latest
    succeeds" fails. So ``fronts_prod_images=True`` mirrors gate the
    ``prod_mirror_healthy`` flag on the summary, and a dark prod mirror
    alone trips the alarm even when the global healthy count is fine.
    """

    name: str
    url: str  # e.g. "https://docker.m.daocloud.io" — no trailing slash
    expected_status: tuple[int, ...] = _DEFAULT_EXPECTED
    fronts_prod_images: bool = False
    probe_path: str = _DEFAULT_PROBE_PATH


@dataclass(frozen=True)
class ProbeResult:
    """One mirror's verdict for a single probe tick."""

    name: str
    url: str
    status: str  # "ok" | "unexpected_status" | "tls_timeout" | "unreachable"
    http_status: Optional[int]
    error: Optional[str]
    duration_ms: int


@dataclass(frozen=True)
class HealthSummary:
    results: tuple[ProbeResult, ...]
    healthy_count: int
    prod_mirror_healthy: bool
    healthy_below_threshold: bool
    # Names of every mirror that did NOT land in "ok" — operator-readable
    # so an alarm record explains itself without a re-probe.
    unhealthy: tuple[str, ...] = field(default_factory=tuple)


def load_mirrors(path: str) -> tuple[Mirror, ...]:
    """Parse ``mirrors.json`` into a deduplicated tuple of Mirror.

    The schema is shaped like the gate's existing ``config.json``:
    ``{"mirrors": [{"name": ..., "url": ..., "expected_status": [...],
    "fronts_prod_images": bool}, ...]}``. Every entry must have a name
    and an http(s) URL; ``expected_status`` defaults to (200, 401);
    ``fronts_prod_images`` defaults to False; ``probe_path`` defaults
    to ``/v2/``. Duplicate names collapse to the first occurrence (the
    install path writes the canonical list, so duplicates here mean a
    botched edit — first one wins).
    """
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"mirrors config root must be an object, got {type(raw).__name__}")
    entries = raw.get("mirrors")
    if not isinstance(entries, list) or not entries:
        raise ValueError("mirrors config must define a non-empty 'mirrors' array")
    seen: dict[str, Mirror] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"mirror entry must be an object, got {type(entry).__name__}")
        name = entry.get("name")
        url = entry.get("url")
        if not isinstance(name, str) or not name:
            raise ValueError("mirror entry missing non-empty 'name'")
        if not isinstance(url, str) or not url:
            raise ValueError(f"mirror {name!r} missing non-empty 'url'")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"mirror {name!r} url must be http(s)://host, got {url!r}")
        expected = entry.get("expected_status", list(_DEFAULT_EXPECTED))
        if not isinstance(expected, list) or not all(
            isinstance(value, int) for value in expected
        ):
            raise ValueError(
                f"mirror {name!r} expected_status must be a list of integers, got {expected!r}"
            )
        fronts_prod = entry.get("fronts_prod_images", False)
        if not isinstance(fronts_prod, bool):
            raise ValueError(
                f"mirror {name!r} fronts_prod_images must be boolean, got {fronts_prod!r}"
            )
        probe_path = entry.get("probe_path", _DEFAULT_PROBE_PATH)
        if not isinstance(probe_path, str) or not probe_path.startswith("/"):
            raise ValueError(f"mirror {name!r} probe_path must be an absolute path string")
        seen.setdefault(
            name,
            Mirror(
                name=name,
                url=url.rstrip("/"),
                expected_status=tuple(expected),
                fronts_prod_images=fronts_prod,
                probe_path=probe_path,
            ),
        )
    return tuple(seen.values())


def classify_status(http_status: Optional[int], expected: tuple[int, ...]) -> str:
    """Map a probe response into the four-bucket status enum."""
    if http_status is None:
        return "unreachable"
    if http_status in expected:
        return "ok"
    return "unexpected_status"


def probe_mirror(mirror: Mirror, *, timeout: float = 5.0) -> ProbeResult:
    """Probe one mirror's configured probe path; return a ProbeResult.

    Uses stdlib only: ``socket.create_connection`` for the TCP connect,
    ``ssl.create_default_context`` for the TLS handshake. We do NOT
    send a fully-formed HTTP request — the TLS handshake is enough to
    catch the timeout class measured on ``m.daocloud.io/auth/token``
    (the failure mode the AC names), and the ``/v2/`` 401 path that
    Docker registries always return requires a Host header to elicit
    a status code. We instead open the connection, send a minimal
    ``GET /v2/ HTTP/1.0\r\nHost: <host>\r\n\r\n`` request, read until
    we see the status line, and classify. ``socket.timeout`` is the
    right catch — py3.9 launchd has no TimeoutError alias (NFM-4320).
    """
    parsed = urllib.parse.urlparse(mirror.url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = mirror.probe_path
    started = time.monotonic()
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)
        if parsed.scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
        request = (
            f"GET {path} HTTP/1.0\r\nHost: {host}\r\nUser-Agent: nfm-g2-mirror-health\r\n"
            "Connection: close\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        # Read until the status line is complete. A working registry
        # returns "HTTP/1.1 401 Unauthorized" within a few hundred bytes;
        # a broken mirror either times out (TLS handshake failure) or
        # closes mid-stream (EOF / R2 backend). 1 KiB is plenty for the
        # status line and headers; we deliberately do NOT parse the body.
        chunks = []
        while b"\r\n" not in b"".join(chunks) and sum(len(c) for c in chunks) < 1024:
            chunk = sock.recv(512)
            if not chunk:
                break
            chunks.append(chunk)
        head = b"".join(chunks)
        elapsed = int((time.monotonic() - started) * 1000)
        status = _parse_status(head)
        if status is None:
            return ProbeResult(
                mirror.name,
                mirror.url,
                "unreachable",
                None,
                f"no status line received in {elapsed}ms (EOF or non-HTTP response)",
                elapsed,
            )
        return ProbeResult(
            mirror.name,
            mirror.url,
            classify_status(status, mirror.expected_status),
            status,
            None,
            elapsed,
        )
    except socket.timeout as error:
        elapsed = int((time.monotonic() - started) * 1000)
        return ProbeResult(
            mirror.name,
            mirror.url,
            "tls_timeout",
            None,
            f"TLS handshake timed out after {elapsed}ms: {error}",
            elapsed,
        )
    except (OSError, ssl.SSLError) as error:
        elapsed = int((time.monotonic() - started) * 1000)
        return ProbeResult(
            mirror.name,
            mirror.url,
            "unreachable",
            None,
            f"{type(error).__name__}: {error}",
            elapsed,
        )
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _parse_status(head: bytes) -> Optional[int]:
    """Extract the HTTP status code from a partial response head."""
    if not head:
        return None
    line = head.split(b"\r\n", 1)[0]
    parts = line.split(b" ", 2)
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def summarize(
    results: Iterable[ProbeResult],
    *,
    threshold: int = 2,
    mirrors_by_name: Optional[dict[str, Mirror]] = None,
) -> HealthSummary:
    """Aggregate probe results into the alarm-gating summary.

    ``mirrors_by_name`` is optional; when supplied, the summary also
    tracks whether any ``fronts_prod_images=True`` mirror returned
    ``ok``. With it omitted, ``prod_mirror_healthy`` defaults to True
    (no prod-mirror-specific verdict is possible), which preserves
    back-compat with callers that only have raw results.
    """
    results_tuple = tuple(results)
    healthy = tuple(r for r in results_tuple if r.status == "ok")
    unhealthy_names = tuple(r.name for r in results_tuple if r.status != "ok")
    prod_healthy = True
    if mirrors_by_name is not None:
        # A prod allowlisted mirror is healthy iff its probe result is
        # "ok". If we DIDN'T get a result for a prod mirror at all
        # (results filtered out upstream), treat it as unhealthy — the
        # probe loop must enumerate every configured mirror, so a
        # missing result is itself a fault.
        prod_results = {
            name: result for name, result in ((r.name, r) for r in results_tuple)
            if mirrors_by_name.get(name, Mirror(name=name, url="")).fronts_prod_images
        }
        prod_healthy = bool(prod_results) and all(
            result.status == "ok" for result in prod_results.values()
        )
    return HealthSummary(
        results=results_tuple,
        healthy_count=len(healthy),
        prod_mirror_healthy=prod_healthy,
        healthy_below_threshold=len(healthy) < threshold,
        unhealthy=unhealthy_names,
    )


def write_alarm_if_below(audit, summary: HealthSummary, *, threshold: int) -> None:
    """Append an ``alarm`` JSONL record when AC conditions are violated.

    Two conditions trip the alarm:
      1. ``summary.healthy_count < threshold`` (default AC: ≥2 mirrors)
      2. ``summary.prod_mirror_healthy is False`` (the prod allowlist
         mirror is dark; pulls of nucpot-prod-* will deadlock even when
         fallback mirrors look healthy).

    The audit record carries every input the operator needs to triage
    without re-running the probe: per-mirror status names, the global
    healthy count, the threshold, and the prod-mirror flag.
    """
    if not summary.healthy_below_threshold and summary.prod_mirror_healthy:
        return
    unhealthy = [
        {
            "name": result.name,
            "status": result.status,
            "http_status": result.http_status,
            "error": result.error,
            "duration_ms": result.duration_ms,
        }
        for result in summary.results
        if result.status != "ok"
    ]
    audit.write(
        "alarm",
        None,
        healthy_count=summary.healthy_count,
        threshold=threshold,
        prod_mirror_healthy=summary.prod_mirror_healthy,
        unhealthy=[result.name for result in summary.results if result.status != "ok"],
        unhealthy_detail=unhealthy,
    )


def _tripped(summary: HealthSummary) -> bool:
    """The alarm condition for one tick: below threshold OR prod mirror dark."""
    return summary.healthy_below_threshold or not summary.prod_mirror_healthy


def write_recovery(
    audit,
    summary: HealthSummary,
    *,
    threshold: int,
    first_tick: bool = False,
) -> None:
    """Append a ``recovery`` JSONL record when an alarmed state clears.

    NFM-4805: healthy ticks used to write nothing, so the most recent
    ``alarm`` record stayed the standing heartbeat verdict forever —
    probe_g2.sh's G2.7 latched red across mirror recoveries AND across
    watchdog restarts into a healthy fleet (an ``alarm`` record by
    construction always fails the probe's asserts, so once any alarm
    existed the check could never pass again). Writing a ``recovery``
    verdict on the alarmed→healthy transition — and on the first tick
    after startup when that tick is healthy — lets the heartbeat reader
    reflect current fleet state instead of stale history.

    ``first_tick=True`` marks the post-startup verdict that clears any
    pre-restart alarm records in the same log.
    """
    audit.write(
        "recovery",
        None,
        healthy_count=summary.healthy_count,
        threshold=threshold,
        prod_mirror_healthy=summary.prod_mirror_healthy,
        first_tick=first_tick,
    )


def _latest_event(path: str, events: tuple[str, ...]) -> Optional[dict]:
    """Scan the JSONL log newest-first for the first record in ``events``."""
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("event") in events:
            return record
    return None


# Verdict events: the records that carry a standing fleet verdict. The
# watchdog writes ``alarm`` while tripped and ``recovery`` when the alarm
# clears (or on a healthy first tick after startup); everything else
# (``startup``, ``drift``) is lifecycle noise, not a verdict.
_VERDICT_EVENTS = ("alarm", "recovery")


def latest_alarm(path: str) -> Optional[dict]:
    """Read the most-recent ``alarm`` JSONL record from the mirror-health log.

    Returns ``None`` if the file is missing or has no alarm records.
    Operator-facing triage helper: shows the last time the fleet was
    below threshold — NOT the standing verdict (see ``latest_verdict``).
    """
    return _latest_event(path, ("alarm",))


def latest_verdict(path: str) -> Optional[dict]:
    """Read the most-recent verdict record (``alarm`` OR ``recovery``).

    This is the heartbeat reader used by ``probe_g2.sh`` G2.7. A
    ``recovery`` record newer than the last ``alarm`` means the fleet
    is currently healthy; an ``alarm`` newer than the last ``recovery``
    means it is currently tripped. Returns ``None`` when the file is
    missing or holds no verdict records at all — the watchdog has been
    healthy on every tick since install, which the probe accepts as a
    pass.
    """
    return _latest_event(path, _VERDICT_EVENTS)


# AC threshold: probe_g2.sh and write_alarm_if_below share this default.
# Keep the literal here so a future tightening (e.g. "≥3 mirrors") only
# has to edit one place.
DEFAULT_THRESHOLD = 2
DEFAULT_PROBE_TIMEOUT = 5.0


def _run_once(
    mirrors: tuple[Mirror, ...],
    audit,
    *,
    threshold: int,
    timeout: float,
    previous_tripped: Optional[bool] = None,
) -> HealthSummary:
    """Probe + summarize + write the tick's verdict record.

    ``previous_tripped`` is the previous tick's alarm state (``None``
    before the first tick after startup). A tripped tick writes an
    ``alarm`` record every tick (operator-visible persistence of an
    ongoing condition). A healthy tick writes a ``recovery`` record only
    on the alarmed→healthy transition or on the first tick after
    startup — so steady-state health stays quiet, but the NEWEST record
    in the log always carries the current verdict for
    ``latest_verdict``/probe_g2.sh instead of latching the last alarm.
    """
    results = tuple(probe_mirror(mirror, timeout=timeout) for mirror in mirrors)
    mirrors_by_name = {mirror.name: mirror for mirror in mirrors}
    summary = summarize(results, threshold=threshold, mirrors_by_name=mirrors_by_name)
    if _tripped(summary):
        write_alarm_if_below(audit, summary, threshold=threshold)
    elif previous_tripped is None or previous_tripped:
        write_recovery(
            audit, summary, threshold=threshold, first_tick=previous_tripped is None
        )
    return summary


def main(argv: Optional[list[str]] = None) -> None:
    """Launchd entry: loop probe + summarize + write on a fixed interval.

    Mirrors ``nfm_docker_gate.watchdog.main``'s shape (argparse + AuditLog
    + interval sleep + crash-safe per-tick). Never raises: a malformed
    mirrors.json or a totally unreachable mirror fleet still logs the
    startup record so the operator can see the watchdog tried to run.
    """
    parser = argparse.ArgumentParser(description="NFM-4587 registry-mirror health watchdog")
    parser.add_argument("--config", required=True, help="mirrors.json path")
    parser.add_argument("--log", required=True, help="mirror-health audit log path")
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="seconds between probes (default 30s; <60s keeps worst-case AC latency <90s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_PROBE_TIMEOUT,
        help="per-mirror probe timeout in seconds",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help="minimum healthy mirrors required to suppress alarm (AC default: 2)",
    )
    args = parser.parse_args(argv)

    audit = AuditLog(args.log, "mirror-health")
    try:
        mirrors = load_mirrors(args.config)
    except (OSError, ValueError) as error:
        audit.write("startup", None, ok=False, detail=f"config load failed: {error}")
        print(f"nfm-g2 mirror-health: config load failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    audit.write(
        "startup",
        None,
        ok=True,
        config=args.config,
        mirrors=[mirror.name for mirror in mirrors],
        interval=args.interval,
        threshold=args.threshold,
    )
    # Alarm-state carry across ticks: None until the first tick completes,
    # then the tick's _tripped() verdict. A watchdog restart resets this to
    # None, so the first post-startup tick always writes a verdict record
    # (alarm if tripped, recovery if healthy) — clearing any stale
    # pre-restart alarm the probe would otherwise latch onto (NFM-4805).
    previous_tripped = None
    while True:
        try:
            summary = _run_once(
                mirrors,
                audit,
                threshold=args.threshold,
                timeout=args.timeout,
                previous_tripped=previous_tripped,
            )
            previous_tripped = _tripped(summary)
        except (OSError, ValueError) as error:
            audit.write("drift", {"known": False}, detail=f"probe tick failed: {error}")
        time.sleep(args.interval)


if __name__ == "__main__":  # pragma: no cover — exercised via launchd
    main()