"""Upstream LLM stub for the NFM-3427 fast-fail integration test.

Stands in for whatever ``LLM_BINDING_HOST`` points at in
``docker-compose.lightrag.yml``. It answers an OpenAI-shaped
``POST /v1/chat/completions`` immediately, which is all the test needs: the
scenario under test is what happens when this container is *stopped*, not
what it returns while alive.

Runs on stdlib only so the container needs no build step — the compose file
bind-mounts this file into a stock ``python:3.12-slim`` image.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_RESPONSE = json.dumps(
    {"choices": [{"message": {"role": "assistant", "content": "stub answer"}}]}
).encode()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._send(_RESPONSE)

    def do_GET(self) -> None:
        self._send(b'{"status":"ok"}')

    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Silence per-request logging; the test asserts on timing, not logs."""


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "9000"))), _Handler).serve_forever()
