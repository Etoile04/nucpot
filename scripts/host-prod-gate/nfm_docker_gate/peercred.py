"""Peer identity recovery for AF_UNIX sockets (NFM-4270 AC-G2.6).

Every denied request must be attributable: who connected, when, doing
what. On macOS the connected peer's pid is available via
``getsockopt(SOL_LOCAL, LOCAL_PEERPID)`` and its uid/gid via
``LOCAL_PEERCRED`` (struct xucred: version, uid, gid, ngroups...). On
Linux ``SO_PEERCRED`` returns struct ucred (pid, uid, gid). Both were
verified empirically on this host (2026-09-04): LOCAL_PEERPID returned
the exact client pid; LOCAL_PEERCRED returned uid 501/gid 20.

Pure-stdlib, best-effort: any failure degrades to {"known": false}
rather than blocking the gate decision.
"""

from __future__ import annotations

import pwd
import socket
import subprocess
from typing import Any, Optional

# macOS: /usr/include/sys/un.h — SOL_LOCAL is 0 on Darwin.
_SOL_LOCAL = 0
_LOCAL_PEERCRED = 0x001  # struct xucred
_LOCAL_PEERPID = 0x002  # pid_t

# Linux: SOL_SOCKET/SO_PEERCRED → struct ucred {pid, uid, gid}.
_SOL_SOCKET = 1
_SO_PEERCRED = 17


def _macos_creds(conn: socket.socket) -> Optional[dict[str, int]]:
    pid: Optional[int] = None
    uid: Optional[int] = None
    try:
        raw = conn.getsockopt(_SOL_LOCAL, _LOCAL_PEERPID, 4)
        pid = int.from_bytes(raw, "little")
    except OSError:
        pass
    try:
        raw = conn.getsockopt(_SOL_LOCAL, _LOCAL_PEERCRED, 16)
        uid = int.from_bytes(raw[4:8], "little")
    except OSError:
        pass
    if pid is None and uid is None:
        return None
    return {"pid": pid or 0, "uid": uid or 0}


def _linux_creds(conn: socket.socket) -> Optional[dict[str, int]]:
    try:
        raw = conn.getsockopt(_SOL_SOCKET, _SO_PEERCRED, 12)
    except OSError:
        return None
    pid, uid, _gid = (int.from_bytes(raw[i : i + 4], "little") for i in (0, 4, 8))
    return {"pid": pid, "uid": uid}


def _username(uid: int) -> Optional[str]:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return None


def _command(pid: int) -> Optional[str]:
    if pid <= 0:
        return None
    try:
        out = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    cmd = out.stdout.strip()
    return cmd[:200] if cmd else None


def peer_identity(conn: socket.socket) -> dict[str, Any]:
    """Best-effort identity dict for the process on the other end."""
    creds = None
    try:
        if hasattr(socket, "AF_UNIX") and conn.family == socket.AF_UNIX:
            import sys

            creds = _linux_creds(conn) if sys.platform.startswith("linux") else _macos_creds(conn)
    except OSError:
        creds = None

    if not creds:
        return {"known": False, "pid": None, "uid": None, "user": None, "cmd": None}

    uid = creds.get("uid") or 0
    pid = creds.get("pid") or 0
    return {
        "known": True,
        "pid": pid,
        "uid": uid,
        "user": _username(uid),
        "cmd": _command(pid),
    }
