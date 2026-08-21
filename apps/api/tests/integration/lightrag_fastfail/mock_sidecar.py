"""LightRAG-shaped sidecar stub for the NFM-3427 fast-fail integration test.

Reproduces the two sidecar behaviours the four-layer timeout chain of
ADR-NFM-3404 depends on, without pulling in the real (multi-minute build,
Postgres-backed) LightRAG image:

``GET  /health``
    Liveness probe — always 200 (ADR §2.5 health-check contract).

``POST /query``
    Proxies to the upstream LLM at ``LLM_BINDING_HOST``. While the LLM is
    reachable this answers 200 immediately. Once the LLM container is stopped
    the proxy call fails, and the sidecar keeps retrying until its
    ``LLM_TIMEOUT`` budget is exhausted before answering ``500``. That retry
    loop is the point: it is *layer 1* of the chain, and it is what turns a
    dead LLM into a bounded 5xx instead of an open-ended stall.

``POST /__control__``
    Test-only hook. ``{"hang_s": N}`` makes subsequent ``/query`` calls accept
    the connection and then stall for ``N`` seconds without writing a
    response, simulating a sidecar that has stopped honouring its own
    ``LLM_TIMEOUT``. That is how the test exercises *layer 3* — the backend's
    ``NFM_LIGHTRAG_QUERY_TIMEOUT_S`` read budget — as the backstop.

Runs on stdlib only so the container needs no build step — the compose file
bind-mounts this file into a stock ``python:3.12-slim`` image.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_LLM_HOST = os.environ.get("LLM_BINDING_HOST", "http://mock-llm:9000")
_LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT", "8"))

# Gap between upstream retries while the LLM is down. Small enough that the
# sidecar reacts promptly once the budget expires, large enough not to spin.
_RETRY_GAP_S = 0.25

_state_lock = threading.Lock()
_hang_s = 0.0


def _call_upstream_until_budget_spent() -> tuple[bool, str]:
    """Retry the upstream LLM until it answers or ``LLM_TIMEOUT`` runs out.

    Returns ``(ok, detail)``. ``ok`` is False once the budget is spent, which
    the caller turns into a 500 — the fast-fail signal the backend converts
    into ``LightRAGClientError``.
    """
    deadline = time.monotonic() + _LLM_TIMEOUT_S
    last_error = "no attempt made"

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, f"LLM unreachable after {_LLM_TIMEOUT_S}s: {last_error}"

        request = urllib.request.Request(
            f"{_LLM_HOST}/v1/chat/completions",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=min(remaining, _LLM_TIMEOUT_S)
            ) as response:
                response.read()
            return True, "ok"
        except (urllib.error.URLError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(min(_RETRY_GAP_S, max(deadline - time.monotonic(), 0)))


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            self._send(200, {"status": "healthy"})
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))

        if self.path.startswith("/__control__"):
            self._handle_control(body)
        elif self.path.startswith("/query"):
            self._handle_query()
        else:
            self._send(404, {"detail": "not found"})

    def _handle_control(self, body: bytes) -> None:
        global _hang_s
        try:
            requested = float(json.loads(body or b"{}").get("hang_s", 0))
        except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
            self._send(400, {"detail": "hang_s must be a number"})
            return
        with _state_lock:
            _hang_s = requested
        self._send(200, {"hang_s": requested})

    def _handle_query(self) -> None:
        with _state_lock:
            hang = _hang_s

        if hang > 0:
            # Connection is accepted but no response is written: the backend's
            # read budget is the only thing that can end this request.
            time.sleep(hang)
            self._send(200, {"response": "answered after stall"})
            return

        ok, detail = _call_upstream_until_budget_spent()
        if ok:
            self._send(200, {"response": "stub answer", "references": []})
        else:
            self._send(500, {"detail": detail})

    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Silence per-request logging; the test asserts on timing, not logs."""


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8001"))), _Handler).serve_forever()
