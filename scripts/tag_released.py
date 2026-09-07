#!/usr/bin/env python3
"""Release anchor reconciliation — ADR-015 §2 part 2 (NFM-4452).

CI half (production-deployment.yml ``tag-released`` job) pushes a lightweight
``released/<utcdate>-<sha8>`` tag after deploy + smoke pass. This script is
the HOST-SIDE fallback: it reads the G4a deploy manifest (the system of
record for "what is live") and pushes the anchor tag when CI could not
(GFW egress flake killed the push, pre-ADR-015 deploy, manual deploy). It is
safe to run on every cron tick.

Policy:

1. The live manifest's ``deploy_sha`` must be an ancestor of origin/main
   (ref[0]). A manifest describing a SHA that is not on main means a
   rollback / detached deploy — the operator anchors it manually or
   explicitly; automation stays out of the way.
2. Never move or delete an existing tag. If the candidate name exists and
   already points at the live SHA, that is success (idempotent). If it
   points anywhere else, that is a hard alarm (exit 4) — it would mean two
   different deploys claimed the same date+sha8 name, which should be
   impossible unless something forged tags.
3. Before pushing, verify the remote does not already carry the tag pointing
   elsewhere (same alarm, exit 4).
4. Push failures (proxy down, GitHub unreachable) exit 3 and the next cron
   tick retries — the manifest survives regardless.

Exit codes: 0 ok/already-anchored; 2 manifest missing/invalid; 3 live SHA
not an ancestor of origin/main (a rollback happened — anchor skipped and
the condition IS worth surfacing on the cron channel, so it is a non-zero
exit on purpose); 4 tag-name conflict; 5 push failed (next tick retries);
6 usage/config error.

Usage::

    python3 scripts/tag_released.py                 # reconcile, then exit
    python3 scripts/tag_released.py --dry-run       # print intended action
    python3 scripts/tag_released.py --manifest PATH # override manifest path
    python3 scripts/tag_released.py --remote ORIGIN # override remote name

Manifest path resolution mirrors check_deploy_drift.py / record_deploy_manifest.py:
``--manifest`` > ``NFM_DEPLOY_MANIFEST`` > canonical G4 dir
``/usr/local/var/nfm-g2/prod-deploy-manifest.json`` > ``~/.nfmd/prod-deploy-manifest.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

EXIT_OK = 0
EXIT_MANIFEST = 2
EXIT_NOT_ON_MAIN = 3
EXIT_TAG_CONFLICT = 4
EXIT_PUSH_FAILED = 5
EXIT_CONFIG = 6

CANONICAL_G4 = Path("/usr/local/var/nfm-g2/prod-deploy-manifest.json")
HOME_NFMD = Path.home() / ".nfmd" / "prod-deploy-manifest.json"


def resolve_manifest(path_override: str | None) -> Path:
    if path_override:
        return Path(path_override)
    env = os.environ.get("NFM_DEPLOY_MANIFEST", "").strip()
    if env:
        return Path(env)
    if CANONICAL_G4.exists():
        return CANONICAL_G4
    return HOME_NFMD


def load_deploy_sha(manifest_path: Path) -> str:
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"exit {EXIT_MANIFEST}: cannot read manifest {manifest_path}: {exc}")
        sys.exit(EXIT_MANIFEST)
    try:
        manifest = json.loads(raw)
        sha = str(manifest["deploy_sha"]).strip().lower()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"exit {EXIT_MANIFEST}: manifest {manifest_path} invalid: {exc}")
        sys.exit(EXIT_MANIFEST)
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        print(f"exit {EXIT_MANIFEST}: deploy_sha {sha!r} is not a 40-hex SHA")
        sys.exit(EXIT_MANIFEST)
    return sha


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile released/* anchor tags from the G4a deploy manifest (ADR-015 §2, NFM-4452).")
    parser.add_argument("--manifest", help="override deploy manifest path")
    parser.add_argument("--remote", default="origin", help="git remote to fetch/push (default: origin)")
    parser.add_argument("--dry-run", action="store_true", help="print the intended action, change nothing")
    args = parser.parse_args()

    # NOTE: deliberately NO "must be on main" check. The canonical host
    # checkout spends most of its life on agent ticket branches; tag
    # correctness is guaranteed by the manifest + the origin/main ancestry
    # check below, not by where HEAD happens to point.
    manifest_path = resolve_manifest(args.manifest)
    deploy_sha = load_deploy_sha(manifest_path)

    remote_ok = subprocess.run(
        ["git", "remote", "get-url", args.remote], capture_output=True, text=True
    )
    if remote_ok.returncode != 0:
        print(f"exit {EXIT_CONFIG}: no '{args.remote}' remote here — run in the nucpot checkout")
        sys.exit(EXIT_CONFIG)

    fetch = subprocess.run(
        ["git", "fetch", "--quiet", args.remote, "main", "refs/tags/released/*:refs/tags/released/*"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if fetch.returncode != 0:
        print(f"exit {EXIT_PUSH_FAILED}: git fetch failed: {fetch.stderr.strip()[:300]}")
        sys.exit(EXIT_PUSH_FAILED)

    # Policy 1: live SHA must be an ancestor of origin/main.
    anc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", deploy_sha, f"{args.remote}/main"],
        capture_output=True,
        text=True,
    )
    if anc.returncode != 0:
        print(
            f"exit {EXIT_NOT_ON_MAIN}: live {deploy_sha[:8]} is NOT an ancestor of "
            f"{args.remote}/main (rollback or detached deploy?) — anchor skipped, handle manually"
        )
        sys.exit(EXIT_NOT_ON_MAIN)

    date_part = datetime.now(UTC).strftime("%Y.%m%d")
    candidate = f"released/{date_part}-{deploy_sha[:8]}"

    # Policy 2: local ref already claimed?
    local = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{candidate}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if local.returncode == 0 and local.stdout.strip() != deploy_sha:
        print(
            f"exit {EXIT_TAG_CONFLICT}: local tag {candidate} points at "
            f"{local.stdout.strip()[:8]}, manifest says {deploy_sha[:8]} — forge or clock skew, investigate"
        )
        sys.exit(EXIT_TAG_CONFLICT)

    # Policy 3: remote already carries it?
    ls = subprocess.run(
        ["git", "ls-remote", args.remote, f"refs/tags/{candidate}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if ls.returncode != 0:
        print(f"exit {EXIT_PUSH_FAILED}: ls-remote failed: {ls.stderr.strip()[:200]}")
        sys.exit(EXIT_PUSH_FAILED)
    remote_line = ls.stdout.strip()
    if remote_line:
        remote_sha = remote_line.split()[0]
        if remote_sha != deploy_sha:
            print(
                f"exit {EXIT_TAG_CONFLICT}: remote tag {candidate} points at "
                f"{remote_sha[:8]}, manifest says {deploy_sha[:8]} — investigate before touching tags"
            )
            sys.exit(EXIT_TAG_CONFLICT)
        print(f"already anchored: {candidate} -> {deploy_sha[:8]} (remote)")
        sys.exit(EXIT_OK)

    if args.dry_run:
        print(f"dry-run: would tag {candidate} -> {deploy_sha[:8]} and push to {args.remote}")
        sys.exit(EXIT_OK)

    tag = subprocess.run(["git", "tag", candidate, deploy_sha], capture_output=True, text=True)
    if tag.returncode != 0:
        print(f"exit {EXIT_TAG_CONFLICT}: git tag failed: {tag.stderr.strip()[:200]}")
        sys.exit(EXIT_TAG_CONFLICT)

    push = subprocess.run(
        ["git", "push", args.remote, f"refs/tags/{candidate}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if push.returncode != 0:
        print(f"exit {EXIT_PUSH_FAILED}: push failed (cron will retry): {push.stderr.strip()[:300]}")
        sys.exit(EXIT_PUSH_FAILED)

    print(f"anchored: {candidate} -> {deploy_sha[:8]} (manifest {manifest_path})")
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
