#!/usr/bin/env python3
"""Deploy-epoch fencing token (NFM-5253 / NFM-4848 T1+T4+T5; NFM-5259 —
deploy_prod.sh enforces by default since 2026-10-05).

SHA is not monotonic across redeploys/rollbacks of the same commit, so the
deploy layer needs an ORDERED token binding each state transition to the
message that attests it. This helper owns that one primitive:

    EPOCH — a monotonically increasing integer at
    ``${NFM_G2_VAR_DIR:-/usr/local/var/nfm-g2}/prod-deploy.epoch``
    (fallback ``~/.nfmd/prod-deploy.epoch`` — the SAME preference chain the
    deploy lock uses, scripts/deploy_prod.sh NFM-4273 note), incremented
    under an exclusive fcntl lock so concurrent deploys mint distinct
    consecutive values (macOS lacks flock(1); the runlock in
    scripts/check_deploy_drift.py, NFM-4297 CR F8, is the in-tree
    precedent for fcntl-on-a-state-file).

Subcommands
    read           Print the current epoch (rc 1, no output when absent or
                   unparseable). Never mints.
    mint           Print the NEXT epoch (N+1), persisting it. Corruption
                   recovery: an unparseable epoch file re-mints from
                   manifest-epoch + 1 (manifest resolved through the same
                   env > G2-dir > ~/.nfmd chain); no readable manifest
                   means re-mint from 0.
    lock-acquire   The T4 lockfile CAS. Under the epoch
                   file's fcntl lock: mint the next epoch N+1, then decide
                   whether THIS run may take the deploy lock —
                   REFUSE iff the existing lock file is fresh (mtime age
                   <= --max-age) AND its pid is alive; otherwise
                   ACQUIRE, writing ``{"epoch": N+1, "pid", "deploy_sha",
                   "started"}``. The holder's epoch is logged for the SRE
                   week but never gates the refusal: the epoch baseline
                   includes mints from runs that never took the lock
                   (refused runs, record_rollback.sh), so a fresh lock
                   held by a live pid is an in-flight deploy regardless
                   of its epoch. Without --enforce a REFUSE decision is
                   logged and the lock is still taken (the shadow
                   semantics retained for the explicit
                   NFM_DEPLOY_LOCK_ENFORCE=0 emergency-disable;
                   deploy_prod.sh passes --enforce by default since the
                   NFM-5259 flip, 2026-10-05). With --enforce a REFUSE
                   exits 80 WITHOUT touching the holder's lock.

Output contract (lock-acquire, one per line, grep-friendly for SRE):
    EPOCH_MINTED=<int>
    LOCK_DECISION=<acquire|refuse|enforced-refuse>
    LOCK_REASON=<short token>
plus a single ``deploy_epoch_lock: {...}`` JSON line carrying the full
decision context (AC6 observability: minted epoch + lock decision in the
deploy log without new infrastructure).

Exit codes: 0 ok; 1 usage/state error; 80 enforced lock refusal.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
try:  # py3.11+; fallback for the py3.9 CommandLineTools interpreter on the host
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone as _tz
    UTC = _tz.utc
from pathlib import Path

EPOCH_FILENAME = "prod-deploy.epoch"
MANIFEST_FILENAME = "prod-deploy-manifest.json"
LOCK_REFUSE_EXIT = 80
CANONICAL_G2_DIR = Path("/usr/local/var/nfm-g2")

_INT_RE = re.compile(r"^[0-9]+$")


def resolve_epoch_path(env: dict[str, str] | None = None) -> Path:
    """env override > canonical G2 dir (when present) > ~/.nfmd.

    Mirrors deploy_prod.sh's NFM_DEPLOY_LOCK chain exactly so the deploy
    body (writer, nfmdeploy) and the drift cron (reader, lwj04) always
    agree on ONE copy (NFM-4273).
    """
    environ = env if env is not None else os.environ
    override = environ.get("NFM_DEPLOY_EPOCH")
    if override:
        return Path(override)
    g2 = environ.get("NFM_G2_VAR_DIR") or str(CANONICAL_G2_DIR)
    if Path(g2).is_dir():
        return Path(g2) / EPOCH_FILENAME
    return Path.home() / ".nfmd" / EPOCH_FILENAME


def _resolve_manifest_path(environ: dict[str, str]) -> Path | None:
    """Manifest for corruption recovery: env override > G2 dir > ~/.nfmd."""
    override = environ.get("NFM_DEPLOY_MANIFEST")
    if override:
        return Path(override)
    g2 = environ.get("NFM_G2_VAR_DIR") or str(CANONICAL_G2_DIR)
    if Path(g2).is_dir():
        return Path(g2) / MANIFEST_FILENAME
    return Path.home() / ".nfmd" / MANIFEST_FILENAME


def read_epoch(path: Path | None = None) -> int | None:
    """Current epoch, or None when absent/unparseable (never mints)."""
    target = path or resolve_epoch_path()
    try:
        raw = target.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(raw) if _INT_RE.match(raw) else None


def _manifest_recovery_epoch(environ: dict[str, str]) -> int:
    """Corruption recovery baseline: manifest-epoch + 1, else 0."""
    manifest_path = _resolve_manifest_path(environ)
    if manifest_path is None:
        return 0
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        value = manifest.get("deploy_epoch")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return 0


class _EpochFileLock:
    """Exclusive fcntl lock ON THE EPOCH FILE itself — the one serialization
    point for mint + lock-acquire (the "single primitive" ruling: there is
    no second mutex). The fd must outlive the critical section, hence the
    explicit release()."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: object | None = None

    def __enter__(self) -> _EpochFileLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # "a+" never truncates: a concurrent holder's value stays readable.
        file = open(self._path, "a+")  # closed in __exit__
        try:
            fcntl.flock(file, fcntl.LOCK_EX)
        except BaseException:
            file.close()
            raise
        self._file = file
        return self

    def __exit__(self, *_exc: object) -> None:
        file = self._file
        self._file = None
        if file is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(file, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                file.close()

    def read(self) -> int | None:
        file = self._file
        assert file is not None, "read outside the with-block"
        os.lseek(file.fileno(), 0, os.SEEK_SET)
        raw = os.read(file.fileno(), 64).decode("utf-8", errors="replace").strip()
        return int(raw) if _INT_RE.match(raw) else None

    def write(self, value: int) -> None:
        # In-place truncate+write under the lock. NEVER os.replace here: the
        # flock binds to the inode, and a rename would hand the next opener
        # a fresh unlocked inode, silently breaking mutual exclusion. Readers
        # that do not lock may observe a torn window; read_epoch() treats
        # that as None (shadow-mode logging only).
        file = self._file
        assert file is not None, "write outside the with-block"
        fd = file.fileno()
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, f"{value}\n".encode())
        os.fsync(fd)


def mint_epoch(path: Path | None = None, env: dict[str, str] | None = None) -> int:
    """Return the NEXT epoch (N+1) and persist it. Serialized by the epoch
    file's fcntl lock, so concurrent mints get distinct consecutive values."""
    target = path or resolve_epoch_path(env)
    environ = env if env is not None else os.environ
    with _EpochFileLock(target) as lock:
        current = lock.read()
        if current is None:
            # Absent (first mint) or corrupt: recover from manifest-epoch + 1.
            current = _manifest_recovery_epoch(environ)
        nxt = current + 1
        lock.write(nxt)
        return nxt


# ------------------------------------------------------------------ T4 CAS


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but is another user's — still live
    return True


def _read_lock(path: Path) -> dict | None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _write_lock(path: Path, payload: dict) -> None:
    """Atomic lock publish: tmp + os.replace in the same dir. Safe to rename
    here (unlike the epoch file): nothing flocks the lock file — the epoch
    file is the mutex and this runs inside its critical section."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".prod-deploy-lock.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def lock_acquire(
    lock_path: Path,
    deploy_sha: str,
    *,
    pid: int,
    max_age: int = 7200,
    enforce: bool = False,
    epoch_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str, dict]:
    """Mint + decide + (maybe) take the deploy lock, atomically.

    Returns (exit_code, decision, reason, context) where context carries
    the full observability payload. See module docstring for the decision
    rule and the shadow/enforce split."""
    environ = env if env is not None else os.environ
    epoch_target = epoch_path or resolve_epoch_path(environ)
    with _EpochFileLock(epoch_target) as lock:
        current = lock.read()
        if current is None:
            current = _manifest_recovery_epoch(environ)
        minted = current + 1
        lock.write(minted)

        existing = _read_lock(lock_path)
        existing_epoch = existing.get("epoch") if isinstance(existing, dict) else None
        existing_pid = existing.get("pid") if isinstance(existing, dict) else None
        fresh = False
        try:
            age = time.time() - lock_path.stat().st_mtime
            fresh = age <= max_age
        except OSError:
            fresh = False
        live = isinstance(existing_pid, int) and _pid_alive(existing_pid)
        context = {
            "epoch_minted": minted,
            "epoch_before": current,
            "lock_path": str(lock_path),
            "lock_fresh": fresh,
            "lock_pid": existing_pid,
            "lock_pid_alive": live if isinstance(existing_pid, int) else False,
            "lock_epoch": existing_epoch,
            "enforce": enforce,
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        }

        if fresh and live:
            decision = "enforced-refuse" if enforce else "refuse"
            reason = "fresh-lock-live-pid"
            context["decision"] = decision
            context["reason"] = reason
            if enforce:
                return LOCK_REFUSE_EXIT, decision, reason, context
            # Shadow mode: log the refusal, then take the lock exactly as
            # the pre-fencing blind overwrite did (today's behavior).
            _write_lock(
                lock_path,
                {
                    "epoch": minted,
                    "pid": pid,
                    "deploy_sha": deploy_sha,
                    "started": datetime.now(UTC).isoformat(timespec="seconds"),
                },
            )
            return 0, decision, reason, context

        reason = (
            "no-prior-lock"
            if existing is None
            else "stale-lock"
            if not fresh
            else "dead-pid"
        )
        decision = "acquire"
        context["decision"] = decision
        context["reason"] = reason
        _write_lock(
            lock_path,
            {
                "epoch": minted,
                "pid": pid,
                "deploy_sha": deploy_sha,
                "started": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        )
        return 0, decision, reason, context


# -------------------------------------------------------------------- CLI


def _parse_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise ValueError("must be >= 0")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy-epoch fencing token (NFM-5253).")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("read", help="print the current epoch (no mint)")
    sub.add_parser("mint", help="mint + print the next epoch (N+1)")

    lock_parser = sub.add_parser(
        "lock-acquire", help="mint + epoch-aware deploy-lock acquire (T4)"
    )
    lock_parser.add_argument("--lock", required=True, help="deploy lock file path")
    lock_parser.add_argument("--sha", required=True, help="SHA being deployed")
    lock_parser.add_argument(
        "--pid", type=_parse_int, default=None, help="holder pid (default: this process)"
    )
    lock_parser.add_argument(
        "--max-age",
        type=_parse_int,
        default=7200,
        help="lock freshness window seconds (drift checker parity; default 7200)",
    )
    lock_parser.add_argument(
        "--enforce",
        action="store_true",
        help="actually refuse on a fresh-lock/live-pid conflict (default: shadow log only)",
    )

    args = parser.parse_args(argv)

    if args.command == "read":
        value = read_epoch()
        if value is None:
            return 1
        print(value)
        return 0

    if args.command == "mint":
        print(mint_epoch())
        return 0

    code, decision, reason, context = lock_acquire(
        Path(args.lock),
        args.sha,
        pid=args.pid if args.pid is not None else os.getpid(),
        max_age=args.max_age,
        enforce=args.enforce,
    )
    print(f"EPOCH_MINTED={context['epoch_minted']}")
    print(f"LOCK_DECISION={decision}")
    print(f"LOCK_REASON={reason}")
    print(f"deploy_epoch_lock: {json.dumps(context, sort_keys=True)}")
    if code == LOCK_REFUSE_EXIT:
        print(
            "FATAL (NFM-5253): deploy lock refused — a fresh lock is held by live "
            f"pid {context['lock_pid']} ({args.lock}); another deploy "
            "is in flight. Refusing to overwrite it.",
            file=sys.stderr,
        )
    return code


if __name__ == "__main__":
    sys.exit(main())
