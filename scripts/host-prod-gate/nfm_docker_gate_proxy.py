#!/usr/bin/env python3
"""NFM-4270 (ADR-013 G2) docker gate proxy — entry point.

Runs one filtering proxy in front of the real Docker daemon unix socket.

  ro mode   (default docker context for the desktop user):
      read-only frictionless; prod-scoped / daemon-wide / escape-hatch
      mutations denied with 403 + audit; non-prod mutations allowed +
      audited (staging, autovc, e2e share this daemon).

  full mode (deploy identity only; socket is group-gated):
      everything allowed; every mutation audited with peer identity.

Stdlib only — this file is copied to /usr/local/lib/nfm-g2/ by
scripts/host-prod-gate/host_setup.sh and run by launchd as root.

Usage:
  nfm_docker_gate_proxy.py --mode ro   --listen /var/run/nfm-g2/docker-ro.sock \
      --upstream "$HOME/.docker/run/docker.sock" --log /var/log/nfm-g2/gate.log
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nfm_docker_gate.audit import AuditLog
from nfm_docker_gate.policy import ScopeConfig
from nfm_docker_gate.proxy import DockerGateProxy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="NFM-4270 docker gate proxy")
    parser.add_argument("--mode", choices=("ro", "full"), default="ro")
    parser.add_argument("--listen", required=True, help="gate unix socket path")
    parser.add_argument("--upstream", required=True, help="real daemon unix socket path")
    parser.add_argument("--log", required=True, help="JSONL audit log path")
    parser.add_argument("--config", help="scope config JSON (prod name prefixes)")
    parser.add_argument(
        "--socket-mode",
        type=lambda value: int(value, 8),
        default=None,
        help="mode for the gate socket (default 666 ro / 660 full)",
    )
    parser.add_argument(
        "--socket-group",
        default=None,
        help="group owner for the gate socket (full mode: prod-deploy)",
    )
    args = parser.parse_args(argv)

    scope = ScopeConfig()
    if args.config:
        with open(args.config, encoding="utf-8") as handle:
            scope = ScopeConfig.from_dict(json.load(handle))

    socket_mode = args.socket_mode
    if socket_mode is None:
        socket_mode = 0o666 if args.mode == "ro" else 0o660

    audit = AuditLog(args.log, args.mode)
    proxy = DockerGateProxy(
        listen_path=args.listen,
        upstream_path=args.upstream,
        audit_log=audit,
        scope=scope,
        full_mode=(args.mode == "full"),
        socket_mode=socket_mode,
        socket_group=args.socket_group,
    )
    proxy.serve_forever()


if __name__ == "__main__":
    main()
