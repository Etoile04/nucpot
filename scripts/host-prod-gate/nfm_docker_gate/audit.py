"""JSONL audit log for the NFM-4270 docker gate (AC-G2.6 fail-loud).

One JSON object per line, appended under a lock (the proxy is threaded).
Denials carry the peer identity recovered from the socket; allowances of
mutations carry it too — the 2026-09-04 NFM-4264 incident cost ~6h of
attribution precisely because nothing recorded who did what.
"""

from __future__ import annotations

import datetime
import json
import os
import threading
from typing import Any

try:  # py3.11+ fast path; fall back for the py3.9 launchd interpreter
    from datetime import UTC
except ImportError:  # pragma: no cover — py<3.11
    from datetime import timezone as _tz

    UTC = _tz.utc


class AuditLog:
    def __init__(self, path: str, mode: str) -> None:
        self._path = path
        self._mode = mode
        self._lock = threading.Lock()
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def write(self, event: str, identity: dict[str, Any] | None, **fields: Any) -> None:
        record = {
            "ts": datetime.datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": event,  # "allow" | "deny" | "startup" | "drift"
            "mode": self._mode,
            "identity": identity or {"known": False},
        }
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            directory = os.path.dirname(self._path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._count += 1
