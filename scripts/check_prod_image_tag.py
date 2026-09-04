#!/usr/bin/env python3
"""NFM-4265 — guard against a stale PROD_IMAGE_TAG pinned in docker/.env.prod.

Incident class (NFM-4264, 2026-09-04): ``docker/.env.prod`` still held
``PROD_IMAGE_TAG=dce00e626…`` from the Sep-2 deploy. The sanctioned path
(scripts/deploy_prod.sh) exports its own ``DEPLOY_SHA`` tag, and the
rollback runbook passes the tag inline — a shell variable always
overrides the ``--env-file`` value. So the env-file tag is ONLY ever
inherited by ad-hoc host-side invocations::

    docker compose -f docker-compose.prod.yml --env-file docker/.env.prod \
        up -d --build …

which then silently re-tags/re-rolls prod services to the stale SHA while
building current-tree content (a nominal 34h/79-commit rollback with zero
audit trail).

Policy enforced here:

1. ``docker/.env.prod`` must NEVER pin a 40-hex SHA as PROD_IMAGE_TAG —
   not even the current one (it goes stale on the next deploy). The
   canonical value is ``latest`` (docker/.env.prod.example); removing the
   line entirely is equally valid (compose falls back to ``latest``).
2. The EFFECTIVE tag — shell env ``PROD_IMAGE_TAG`` > env-file value >
   ``latest`` — must be either a non-SHA tag or the expected deploying
   SHA (``--expected``, else ``git rev-parse <git-ref>``).

Exit codes (deploy_prod.sh reserves 71-74 for post-deploy-cutover-assert):
  0  pass
  2  effective tag is a SHA that does not match the expected/target SHA
  3  docker/.env.prod pins a 40-hex SHA (the NFM-4264 landmine)
  4  usage/config error (missing env-file, unresolvable git ref)
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_TAG_MISMATCH = 2
EXIT_ENVFILE_SHA_PIN = 3
EXIT_CONFIG_ERROR = 4

SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
DEFAULT_TAG = "latest"
ENV_FILE_KEY = "PROD_IMAGE_TAG"


def is_sha(tag: str) -> bool:
    return bool(SHA_RE.match(tag.strip()))


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a compose env-file into a flat dict (comments/blanks skipped)."""
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def resolve_git_sha(repo: Path, ref: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refuse a compose invocation whose effective PROD_IMAGE_TAG "
        "is a stale SHA or whose env-file pins one (NFM-4264/NFM-4265)."
    )
    parser.add_argument(
        "--env-file",
        default="docker/.env.prod",
        help="compose env-file to inspect (default: docker/.env.prod)",
    )
    parser.add_argument(
        "--expected",
        help="deploying SHA the effective tag must match when it is a SHA "
        "(deploy_prod.sh passes DEPLOY_SHA)",
    )
    parser.add_argument(
        "--git-ref",
        default="origin/main",
        help="ref whose commit the effective SHA tag must match when "
        "--expected is absent (default: origin/main)",
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="git repo path for --git-ref resolution (default: cwd)",
    )
    args = parser.parse_args(argv)

    env_file = Path(args.env_file)
    if not env_file.is_file():
        print(f"CONFIG-ERROR: env-file not found: {env_file}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    envfile_tag = parse_env_file(env_file).get(ENV_FILE_KEY)
    shell_tag = os.environ.get(ENV_FILE_KEY)
    effective = shell_tag or envfile_tag or DEFAULT_TAG

    # Check 1 — the env-file landmine. Fail even when the shell overrides
    # it: the pin itself is the recurrence vector (NFM-4264).
    if envfile_tag and is_sha(envfile_tag):
        print(
            f"ENV-FILE-SHA-PIN: {env_file} pins {ENV_FILE_KEY}={envfile_tag[:12]}… "
            f"(a 40-hex SHA). Any host-side `docker compose --env-file {env_file} "
            "up -d --build` silently inherits and re-tags prod to this stale SHA "
            "(NFM-4264, 2026-09-04). Fix: set PROD_IMAGE_TAG=latest in the env-file "
            "(or remove the line) — SHA deploys pass the tag inline "
            "(deploy_prod.sh / rollback runbook). "
            "Ref: docker/.env.prod.example, scripts/check_prod_image_tag.py",
            file=sys.stderr,
        )
        return EXIT_ENVFILE_SHA_PIN

    # Check 2 — effective tag must not be a foreign/stale SHA.
    target = args.expected or resolve_git_sha(Path(args.repo), args.git_ref)
    if target is None:
        print(
            f"CONFIG-ERROR: no --expected and could not resolve git ref "
            f"'{args.git_ref}' in {args.repo} — cannot validate the effective tag.",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR
    if is_sha(effective) and effective.lower() != target.lower():
        print(
            f"TAG-MISMATCH: effective {ENV_FILE_KEY}={effective[:12]}… is a SHA "
            f"that does not match the deploying SHA {target[:12]}…. A compose "
            "invocation with this tag would re-tag prod to a stale SHA while "
            "building current-tree content (NFM-4264 class). Pass the intended "
            "SHA inline (PROD_IMAGE_TAG=<sha> docker compose …), use --expected, "
            "or reset the env-file to latest.",
            file=sys.stderr,
        )
        return EXIT_TAG_MISMATCH

    source = "shell-env" if shell_tag else ("env-file" if envfile_tag else "default")
    print(
        f"OK: {ENV_FILE_KEY} effective={effective} (source: {source}) "
        f"matches target={target[:12]}… (env-file value: {envfile_tag or '<unset>'})"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
