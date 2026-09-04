#!/usr/bin/env python3
"""``--selftest`` mode for check_deploy_drift.py (NFM-4272).

Fabricates a divergence against a fixture manifest + fake docker shim and
verifies issue-filing END-TO-END (create, then dedupe/comment-append)
against an in-process stub Paperclip server — the AC-G4b.3 "test target".
Touches NOTHING real: fixture state lives in a temp dir, the API is a
127.0.0.1 ephemeral socket, and the fake docker shim shadows PATH only for
the duration of the run.

Lives in its own module (imported lazily from check_deploy_drift.main) to
keep the checker itself focused; importing it from the checker at module
level would be circular (the selftest drives the checker's own main()).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from check_deploy_drift import DEFAULT_COMPOSE_PROJECT, SRE_MONITOR_AGENT_ID, TITLE_PREFIX, main

# ---------------------------------------------------------------- selftest


SELFTEST_SERVICES = {
    "api": "a" * 64,
    "web": "b" * 64,
    "db": "d" * 64,
}


def _selftest_container(service: str, image_id: str) -> dict:
    return {
        "Id": f"cid-{service}",
        "Name": f"/{DEFAULT_COMPOSE_PROJECT}-{service}",
        "Config": {
            "Image": f"nucpot-prod-{service}:{image_id[:7]}",
            "Labels": {
                "com.docker.compose.project": DEFAULT_COMPOSE_PROJECT,
                "com.docker.compose.service": service,
            },
        },
        "Image": f"sha256:{image_id}",
        "RepoDigests": [],
    }


class _SelftestStubHandler(BaseHTTPRequestHandler):
    """Stub Paperclip for --selftest (the AC 'test target')."""

    def log_message(self, *args):  # silence
        pass

    def _path(self) -> str:
        return urlparse(self.path).path  # strip ?limit=… query strings

    def _send(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = json.loads(self.rfile.read(length).decode() or "{}")
        path = self._path()
        self.server.journal.append({"method": "POST", "path": path, "body": raw})
        if path.endswith("/issues"):
            self.server.seq += 1
            issue = {
                "id": f"st-uuid-{self.server.seq}",
                "identifier": f"NFM-ST{self.server.seq}",
                "status": "todo",
                **raw,
            }
            self.server.issues[issue["id"]] = issue
            self._send(issue, 201)
        elif path.endswith("/comments"):
            self._send({"ok": True})
        else:
            self._send({"error": "unsupported"}, 404)

    def do_GET(self):
        path = self._path()
        self.server.journal.append({"method": "GET", "path": path, "body": None})
        if path.endswith("/issues"):
            self._send(list(self.server.issues.values()))
        else:
            parts = path.strip("/").split("/")
            if len(parts) == 3:
                issue = self.server.issues.get(parts[2])
                self._send(issue if issue else {"error": "nf"}, 200 if issue else 404)
            else:
                self._send({"error": "unsupported"}, 404)


def run_selftest() -> int:
    """Fabricate a divergence against a fixture manifest + fake docker and
    verify filing end-to-end (create, then dedupe-comment) against the
    in-process stub server. Touches NO real state: everything lives in a
    temp dir and a 127.0.0.1 ephemeral socket."""
    work = Path(tempfile.mkdtemp(prefix="nfm-drift-selftest-"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SelftestStubHandler)
    server.journal = []  # type: ignore[attr-defined]
    server.issues = {}  # type: ignore[attr-defined]
    server.seq = 0  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    old_path = os.environ.get("PATH", "")
    checks: list[tuple[str, bool]] = []
    try:
        # Fixture: manifest recorded from a clean deploy…
        containers = {s: _selftest_container(s, i) for s, i in SELFTEST_SERVICES.items()}
        manifest = {
            "deploy_sha": "selftest0000000000000000000000000000000000000",
            "image_tags": {s: c["Config"]["Image"] for s, c in containers.items()},
            "image_digests": {s: f"sha256:{i}" for s, i in SELFTEST_SERVICES.items()},
            "service_containers": {s: c["Name"].lstrip("/") for s, c in containers.items()},
            "timestamp": "2026-09-04T00:00:00+00:00",
            "actor": "selftest",
        }
        manifest_path = work / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        # …then an out-of-band rebuild of api (the NFM-4264 signal).
        tampered = dict(containers)
        tampered["api"] = _selftest_container("api", "f" * 64)
        state_path = work / "docker-state.json"
        state_path.write_text(json.dumps({"containers": tampered}), encoding="utf-8")
        bin_dir = work / "bin"
        bin_dir.mkdir()
        shim = bin_dir / "docker"
        shim.write_text('#!/bin/sh\nexec python3 "$FAKE_DOCKER_SHIM" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
        # Delegate to the tests' fake-docker shape via a tiny inline shim.
        shim.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "state = json.load(open(os.environ['FAKE_DOCKER_STATE']))\n"
            "args = sys.argv[1:]\n"
            "if args[:1] == ['ps']:\n"
            "    for name, c in sorted(state['containers'].items()):\n"
            "        if c['Config']['Labels']['com.docker.compose.project'] == "
            f"'{DEFAULT_COMPOSE_PROJECT}':\n"
            "            print(name)\n"
            "    sys.exit(0)\n"
            "if args[:1] == ['inspect']:\n"
            "    print(json.dumps([state['containers'][args[1]]]))\n"
            "    sys.exit(0)\n"
            "sys.exit(64)\n",
            encoding="utf-8",
        )
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{old_path}"
        os.environ["FAKE_DOCKER_STATE"] = str(state_path)

        argv = [
            "--manifest",
            str(manifest_path),
            "--state",
            str(work / "drift-state.json"),
            "--lock",
            str(work / "no-such.lock"),
            "--recheck-seconds",
            "0",
            "--paperclip-url",
            f"http://127.0.0.1:{server.server_address[1]}",
            "--paperclip-key",
            "selftest-key",
            "--company-id",
            "selftest-company",
        ]
        rc1 = main(argv)
        creates = [
            r
            for r in server.journal  # type: ignore[attr-defined]
            if r["method"] == "POST" and r["path"].endswith("/issues")
        ]
        payload = creates[0]["body"] if creates else {}
        body = str(payload.get("description", ""))
        checks += [
            ("run 1 exits 1 (drift filed)", rc1 == 1),
            ("exactly one issue created", len(creates) == 1),
            ("assigned to SRE Monitor", payload.get("assigneeAgentId") == SRE_MONITOR_AGENT_ID),
            (
                "title has [DEPLOY-DRIFT] prefix + service",
                str(payload.get("title", "")).startswith(TITLE_PREFIX)
                and "api" in str(payload.get("title", "")),
            ),
            (
                "body names expected vs actual digest",
                f"sha256:{'a' * 64}" in body and f"sha256:{'f' * 64}" in body,
            ),
            (
                "body carries manifest actor + first-seen + signature",
                "selftest" in body and "first-seen:" in body and "signature: " in body,
            ),
        ]
        rc2 = main(argv)
        creates2 = [
            r
            for r in server.journal  # type: ignore[attr-defined]
            if r["method"] == "POST" and r["path"].endswith("/issues")
        ]
        comments = [
            r
            for r in server.journal  # type: ignore[attr-defined]
            if r["method"] == "POST" and r["path"].endswith("/comments")
        ]
        checks += [
            ("run 2 exits 1 (drift persists)", rc2 == 1),
            ("dedupe: no second issue", len(creates2) == 1),
            (
                "dedupe: comment appended",
                len(comments) == 1 and "still diverged" in str(comments[0]["body"].get("body", "")),
            ),
        ]
    finally:
        os.environ["PATH"] = old_path
        os.environ.pop("FAKE_DOCKER_STATE", None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        import shutil

        shutil.rmtree(work, ignore_errors=True)

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
    if failed:
        print(f"SELFTEST FAIL — {len(failed)} check(s) failed.")
        return 1
    print(
        "SELFTEST PASS — fabricated divergence detected, filed to SRE Monitor, "
        "and deduped end-to-end against the stub target."
    )
    return 0
