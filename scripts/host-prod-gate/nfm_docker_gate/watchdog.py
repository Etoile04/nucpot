"""Socket-perms watchdog for the NFM-4270 gate (run as root, launchd).

Docker Desktop recreates ``~/.docker/run/docker.sock`` on restart/update
with default ownership (user:staff, mode 755+). The G2 wall depends on
that socket being group ``prod-deploy`` with the owner WRITE bit
stripped (mode 060) — verified on-host: with mode 060 the socket's owner
gets EACCES on connect while group members connect fine.

This watchdog polls the socket and re-asserts the desired owner group +
mode whenever it drifts, appending a ``drift`` record to the gate log so
the re-lock itself is attributable evidence.

Optionally (--assert-context user:context) it also re-asserts the desktop
user's docker CLI currentContext: Docker Desktop can flip it back to its
own context on restart, which would point ``docker`` straight at the
locked raw socket instead of the ro gate.
"""

from __future__ import annotations

import argparse
import grp
import os
import stat
import subprocess
import time

from .audit import AuditLog

_DOCKER = "/usr/local/bin/docker"
_SUDO = "/usr/bin/sudo"


def check_once(path: str, group: str, mode: int, audit: AuditLog) -> bool:
    """Re-assert perms; returns True if a drift was repaired."""
    try:
        group_id = grp.getgrnam(group).gr_gid
    except KeyError:
        raise SystemExit(f"nfm-g2 watchdog: group {group!r} does not exist") from None
    try:
        info = os.stat(path)
    except FileNotFoundError:
        audit.write("drift", {"known": False}, socket=path, detail="socket missing (daemon down?)")
        return False
    current_gid = info.st_gid
    current_mode = stat.S_IMODE(info.st_mode)
    if current_gid == group_id and current_mode == mode:
        return False
    os.chown(path, -1, group_id)
    os.chmod(path, mode)
    audit.write(
        "drift",
        {"known": False},
        socket=path,
        detail=f"re-asserted group {group} mode {oct(mode)} "
        f"(was gid={current_gid} mode={oct(current_mode)})",
    )
    return True


def _run_as(user: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_SUDO, "-H", "-u", user, _DOCKER, *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


def assert_context(user: str, context: str, audit: AuditLog) -> None:
    """Keep the desktop user's docker CLI pointed at the ro gate.

    Never raises: a broken docker CLI or missing home just logs a drift
    record; the watchdog must stay alive for the socket lock.
    """
    try:
        probe = _run_as(user, ["context", "inspect", context])
        if probe.returncode != 0:
            _run_as(
                user,
                [
                    "context",
                    "create",
                    context,
                    "--docker",
                    "host=unix:///var/run/nfm-g2/docker-ro.sock",
                ],
            )
            audit.write(
                "drift", {"known": False}, detail=f"recreated docker context {context!r} for {user}"
            )
        show = _run_as(user, ["context", "show"])
        current = show.stdout.strip()
        if current != context:
            _run_as(user, ["context", "use", context])
            audit.write(
                "drift",
                {"known": False},
                detail=f"re-asserted docker context {context!r} for {user} (was {current!r})",
            )
    except (OSError, subprocess.SubprocessError) as error:
        audit.write("drift", {"known": False}, detail=f"context assert failed for {user}: {error}")


def _parse_context(spec: str) -> tuple[str, str] | None:
    user, sep, context = spec.partition(":")
    if not sep or not user or not context:
        raise SystemExit("nfm-g2 watchdog: --assert-context expects user:context")
    return user, context


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="NFM-4270 socket perms watchdog")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--group", default="prod-deploy")
    parser.add_argument("--mode", type=lambda value: int(value, 8), default=0o060)
    parser.add_argument("--log", required=True)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument(
        "--assert-context",
        default=None,
        help="user:context — keep user's docker CLI on the ro gate context",
    )
    args = parser.parse_args(argv)

    context_spec = _parse_context(args.assert_context) if args.assert_context else None
    audit = AuditLog(args.log, "watchdog")
    audit.write(
        "startup",
        {"known": False},
        socket=args.socket,
        group=args.group,
        mode=oct(args.mode),
        interval=args.interval,
        assert_context=args.assert_context,
    )
    while True:
        try:
            check_once(args.socket, args.group, args.mode, audit)
        except OSError as error:
            audit.write("drift", {"known": False}, socket=args.socket, detail=f"error: {error}")
        if context_spec:
            assert_context(context_spec[0], context_spec[1], audit)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
