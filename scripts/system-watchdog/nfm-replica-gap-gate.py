#!/usr/bin/env python3
"""nfm-replica-gap-gate.py — replica registration-gap gate (NFM-5966, D2-G3).

The NFM-5880 dual-signal gate extended to the z203 HA replica connector
(parent NFM-5963). Fires a restart of `cloudflared-nucpot-replica.service` on
z203 (over tailscale SSH) ONLY when BOTH signals agree:

  Signal 1  sentinel full-edge episode window is OPEN — IDENTICAL to the
            primary gate: `open=1` in /Users/lwj04/.nfm-site-monitor.s1.state
            with mtime fresher than S1_STALE_SECONDS. The site is full-edge
            dark; this is the only window in which a replica restart is
            meaningful (the replica exists to carry traffic while the primary
            is dark — NFM-5965: a replica presenting the MAIN token joins the
            same edge config).

  Signal 2  the REPLICA connector has NOT proven edge liveness within
            N_MIN_SECONDS: the last newline-terminated
            `Registered tunnel connection` line in the z203 replica log
            (/var/log/cloudflared-nucpot-replica.log) is older than N=600s
            (rolling staleness, same rationale as the primary gate — a flap-up
            registration while still dark is why "since win_first" was
            rejected there). The tail is read over SSH in one round trip.
            When the tail holds no registration line at all, the tail's own
            time span decides (span >= N => `none_span`, else `none_short`).
            SSH path failure (`ssh_fail`) or an unreadable/absent log
            (`log_missing`) is NOFIRE — the gate never restarts blind: the
            absence of a replica verdict must not produce an action (the
            sentinel lane carries replica-dark visibility; this gate only
            acts on positive staleness evidence).

Action inheritance (CPO dispatch, NFM-5966): restart caps/backoff are the
primary gate's config, unchanged — <= 3 fires per rolling 3600s, then
backoff 30m -> 60m -> 120m (4h hard cap), reset only on a fully clean tick.
State persists across watchdogd restarts (watchdogd is StartInterval=60:
process per tick; state reloads every tick).

DRY_RUN default: /usr/local/lib/nfm-watchdog/replica-gap-gate.mode absent or
anything other than exactly `LIVE` => log-only. Rollback = flip or delete that
one file (no plist unload, no watchdogd restart, no tunnel/SSH change), or
remove watchdogd section 4 — sections 1-3 are independent.

SSH budget: the probe is skipped entirely when Signal 1 is closed AND the
persisted state is already clean (no fires in the rolling window, backoff
expired, level 0) — ~1440 SSH spawns/day would otherwise ride every tick for
no decision value. A dirty state (cap/backoff memory from a previous episode)
still probes so the reset conjunct can observe replica freshness. When
Signal 1 is open (a real full-edge episode) every tick probes.

Process identity: watchdogd (root) runs this module AS THE CONSOLE USER via
`launchctl asuser` + `sudo -u` (the section-1 `open -a Docker` pattern,
wedge-proven NFM-5343) so the SSH probe uses the operator's z203 identity —
the same key/known_hosts the site-monitor lane probe uses. One dependency
surface (user SSH + tailscale), honestly shared with the lane probe. State
therefore lives in the user home, not /var/run (survives watchdogd restarts
the same way). The mode file is world-readable (644), so the user context
can read it.

Unit scope: the gate targets EXACTLY cloudflared-nucpot-replica.service. The
verify tunnels (Mac com.cloudflare.cloudflared-verify; z203 soak variant
cloudflared-verify-replica.service, NFM-5965 artifact 2) are out of scope:
the pinned action literal is equality-checked at import and the verify-replica
name is asserted absent — an import-time or test failure means someone tried
to widen the action.

Log contract: exactly ONE machine-greppable line per watchdogd tick on stdout
(the wrapper redirects it into /var/log/nfm-watchdogd.log):

  [<ISO-Z>] watchdogd: rep-gap-gate-replica: decision=<FIRE|NOFIRE|BLOCKED> \
mode=<DRY_RUN|LIVE> s1=<s1_open|s1_closed|s1_missing|s1_empty|s1_corrupt|s1_stale> \
rep=<age_seconds|none_span|none_short|log_missing|ssh_fail|n/a> reason=<token> \
fires_1h=<n> backoff_s=<n>

reason tokens: both_signals, s1_clean, reg_fresh, s1_missing, s1_empty,
s1_corrupt, s1_stale, reg_none_short, log_missing, ssh_fail, cap_reached,
backoff_active, fire_ok, fire_failed, internal_error.

Bounded failure: every path emits exactly one decision line and exits 0 — a
gate fault can never break the watchdogd tick (NFM-5747 AC5 precedent).

Python 3.9-compatible, stdlib-only: runs under /usr/bin/python3.
"""
import json
import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

# --- pinned host paths (absolute; the module runs as the console user) -------
S1_STATE_PATH = "/Users/lwj04/.nfm-site-monitor.s1.state"
MODE_FLAG_PATH = "/usr/local/lib/nfm-watchdog/replica-gap-gate.mode"
STATE_PATH = "/Users/lwj04/.nfm-replica-gate-state.json"

# --- z203 replica (pinned; shared names with D2-G2, NFM-5965) ----------------
SSH_BIN = "/usr/bin/ssh"
SSH_TARGET = "z203"  # ~/.ssh/config alias -> 100.70.30.21 (tailscale, NFM-5964)
REMOTE_LOG_PATH = "/var/log/cloudflared-nucpot-replica.log"
SSH_CONNECT_TIMEOUT = 8      # TCP+auth budget
SSH_CMD_TIMEOUT = 15         # whole-probe wall budget (subprocess kill)
SSH_TAIL_BYTES = 262144      # scan only the last 256KiB of the replica log

# --- the action: fixed literal service, never interpolated -------------------
TARGET_UNIT = "cloudflared-nucpot-replica.service"
FORBIDDEN_UNIT_MARKERS = ("cloudflared-verify",)  # verify + verify-replica soak unit


def _scope_guard() -> None:
    """Import-time unit-scope assertion (verify tunnels stay out of scope)."""
    if TARGET_UNIT != "cloudflared-nucpot-replica.service":
        raise RuntimeError("replica gate target unit repinned — review required")
    for marker in FORBIDDEN_UNIT_MARKERS:
        if marker in TARGET_UNIT:
            raise RuntimeError("verify-tunnel marker in replica gate target — out of scope")


_scope_guard()


def remote_action_command() -> List[str]:
    """The LIVE action, built only from pinned literals (no interpolation)."""
    return [
        SSH_BIN,
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=%d" % SSH_CONNECT_TIMEOUT,
        "-o", "StrictHostKeyChecking=accept-new",
        SSH_TARGET,
        "sudo -n systemctl restart %s" % TARGET_UNIT,
    ]


# --- tunables — INHERITED from the primary gate (NFM-5880), unchanged --------
N_MIN_SECONDS = 600          # == primary gate N; == sentinel S1_EPISODE_GREEN_GAP
S1_STALE_SECONDS = 900       # == primary gate sentinel-freshness bound
CAP_WINDOW_SECONDS = 3600    # == primary gate rolling hour
CAP_MAX_FIRES = 3            # == primary gate fire cap
BACKOFF_BASE_SECONDS = 1800  # == primary gate 30m ladder base
BACKOFF_MAX_SECONDS = 14400  # == primary gate 4h hard cap

LOG_TAG = "rep-gap-gate-replica: "

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) ")
_REG_SUBSTR = "Registered tunnel connection"


def parse_iso_z(ts: str) -> Optional[int]:
    """RFC3339 `YYYY-MM-DDTHH:MM:SSZ` -> epoch seconds; None on any mismatch."""
    try:
        import calendar
        t = time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        return int(calendar.timegm(t))
    except (ValueError, OverflowError):
        return None


# --- Signal 1 (identical file and semantics as the primary gate) -------------

def read_kv_state(path: str) -> Optional[Dict[str, str]]:
    """Flat key=value file, last occurrence wins (matches sentinel `tail -1`).

    None => file missing/unreadable; {} => file exists but holds no key=value
    lines (empty or wholly unparseable).
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return None
    out: Dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key:
            out[key] = val.strip()
    return out


def eval_signal1(now: int, path: str = S1_STATE_PATH) -> Tuple[bool, str]:
    """True iff the full-edge episode window is open AND the state is fresh."""
    data = read_kv_state(path)
    if data is None:
        return False, "s1_missing"
    if not data:
        return False, "s1_empty"
    open_v = data.get("open")
    if open_v not in ("0", "1"):
        return False, "s1_corrupt"
    if open_v == "0":
        return False, "s1_closed"
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return False, "s1_missing"
    if now - mtime > S1_STALE_SECONDS:
        return False, "s1_stale"
    return True, "s1_open"


# --- Signal 2 (replica log tail over SSH) -------------------------------------

def scan_replica_log(raw: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Scan newline-terminated log lines -> (last_reg_epoch, first_ts, last_ts).

    A trailing fragment without \\n is ignored (in-flight write). Unparseable
    timestamps are skipped. Returns (None, first, last) when no registration
    line matched, (None, None, None) when no line carried a parseable stamp.
    """
    text = raw
    if not text.endswith("\n"):
        cut = text.rfind("\n")
        text = text[: cut + 1] if cut != -1 else ""
    last_reg: Optional[int] = None
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None
    for line in text.split("\n"):
        m = _TS_RE.match(line)
        if not m:
            continue
        ep = parse_iso_z(m.group(1))
        if ep is None:
            continue
        if first_ts is None:
            first_ts = ep
        last_ts = ep
        if _REG_SUBSTR in line:
            last_reg = ep
    return last_reg, first_ts, last_ts


def ssh_probe_replica_log() -> Tuple[Optional[str], str]:
    """One SSH round trip: tail the replica log. -> (tail_text, token).

    token: `ok` (rc 0 — tail_text may be empty: an existing but empty log),
    `log_missing` (remote tail failed on the file — absent/unreadable),
    `ssh_fail` (transport/auth/timeout — no replica verdict available).
    """
    cmd = [
        SSH_BIN,
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=%d" % SSH_CONNECT_TIMEOUT,
        "-o", "StrictHostKeyChecking=accept-new",
        SSH_TARGET,
        "tail -c %d %s" % (SSH_TAIL_BYTES, REMOTE_LOG_PATH),
    ]
    try:
        completed = subprocess.run(cmd, capture_output=True, timeout=SSH_CMD_TIMEOUT)
    except Exception:
        return None, "ssh_fail"
    if completed.returncode == 0:
        return completed.stdout.decode("utf-8", errors="replace"), "ok"
    err = completed.stderr.decode("utf-8", errors="replace")
    if "No such file" in err or "Permission denied" in err:
        return None, "log_missing"
    return None, "ssh_fail"


def eval_signal2(now: int, probe=None) -> Tuple[bool, str]:
    """True iff the replica has NOT proven registration within N seconds.

    `probe` is injectable for tests; default is the SSH probe. ssh_fail and
    log_missing are conservative NOFIRE evidence (the gate never acts blind).
    """
    if probe is None:
        raw, token = ssh_probe_replica_log()
        if token != "ok":
            return False, token
    else:
        raw = probe()
        if raw is None:
            return False, "ssh_fail"
    last_reg, first_ts, last_ts = scan_replica_log(raw or "")
    if last_reg is not None:
        age = now - last_reg
        if age < 0:
            age = 0  # clock skew: a stamp ahead of the host clock is definitionally fresh
        return (age >= N_MIN_SECONDS), str(age)
    if first_ts is not None and last_ts is not None:
        if last_ts - first_ts >= N_MIN_SECONDS:
            return True, "none_span"
        return False, "none_short"
    return False, "log_missing"


# --- mode flag (DRY_RUN default) ----------------------------------------------

def read_mode(path: str = MODE_FLAG_PATH) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return "LIVE" if f.read().strip() == "LIVE" else "DRY_RUN"
    except OSError:
        return "DRY_RUN"


# --- persisted cap/backoff state ----------------------------------------------

def load_state(path: str = STATE_PATH) -> Dict[str, object]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def save_state(state: Dict[str, object], path: str = STATE_PATH) -> bool:
    """Atomic tmp+rename; best-effort — a failed save only loses cap memory."""
    tmp = path + ".tmp"
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def state_is_clean(state: Dict[str, object], now: int) -> bool:
    """True when no fire memory needs reset evidence (SSH-skip precondition)."""
    fires_raw = state.get("fires")
    fires: List[int] = [e for e in fires_raw if isinstance(e, int)] if isinstance(fires_raw, list) else []
    live_fires = [e for e in fires if 0 <= now - e < CAP_WINDOW_SECONDS]
    backoff_until = state.get("backoff_until")
    backoff_until = backoff_until if isinstance(backoff_until, int) else 0
    backoff_level = state.get("backoff_level")
    backoff_level = backoff_level if isinstance(backoff_level, int) and backoff_level >= 0 else 0
    return not live_fires and backoff_until <= now and backoff_level == 0


# --- decision core (pure — unit-test surface) ---------------------------------

def evaluate(
    now: int,
    mode: str,
    s1_ok: bool,
    s1_token: str,
    s2_ok: bool,
    rep_field: str,
    state: Dict[str, object],
) -> Tuple[str, str, Dict[str, object], int, int]:
    """Returns (decision, reason, new_state, fires_in_window, backoff_remaining).

    Same shape and semantics as the primary gate's evaluate (inherited
    config): FIRE (gate satisfied; executed iff mode == LIVE), NOFIRE
    (either signal clean or evidence missing — conservative), BLOCKED
    (gate satisfied but cap/backoff refuses).
    """
    fires_raw = state.get("fires")
    fires: List[int] = [e for e in fires_raw if isinstance(e, int)] if isinstance(fires_raw, list) else []
    fires = [e for e in fires if 0 <= now - e < CAP_WINDOW_SECONDS]
    backoff_until = state.get("backoff_until")
    backoff_until = backoff_until if isinstance(backoff_until, int) else 0
    backoff_level = state.get("backoff_level")
    backoff_level = backoff_level if isinstance(backoff_level, int) and backoff_level >= 0 else 0

    if not (s1_ok and s2_ok):
        # Reset only on a FULLY clean tick: sentinel window closed AND the
        # replica proven fresh (rep_field is an integer age < N). A stale
        # replica (digit age >= N) or inconclusive evidence (ssh_fail /
        # log_missing / none_short) never resets — primary-gate parity
        # (design note NFM-5880 comment 410386c2 §4.5).
        if (
            s1_token == "s1_closed"
            and rep_field.isdigit()
            and int(rep_field) < N_MIN_SECONDS
        ):
            new_state = {"fires": [], "backoff_level": 0, "backoff_until": 0}
            return "NOFIRE", "s1_clean", new_state, 0, 0
        reason = s1_token
        if s1_ok:
            reason = "reg_fresh"
        elif s1_token == "s1_closed":
            reason = "s1_clean"
        new_state = {"fires": fires, "backoff_level": backoff_level, "backoff_until": backoff_until}
        return "NOFIRE", reason, new_state, len(fires), max(0, backoff_until - now)

    # Both signals fire-supporting.
    if len(fires) >= CAP_MAX_FIRES:
        backoff_level = min(backoff_level + 1, 4)  # ladder saturates at 1800*2^3 = 4h
        backoff_until = now + min(BACKOFF_BASE_SECONDS * (2 ** (backoff_level - 1)), BACKOFF_MAX_SECONDS)
        new_state = {"fires": fires, "backoff_level": backoff_level, "backoff_until": backoff_until}
        return "BLOCKED", "cap_reached", new_state, len(fires), backoff_until - now
    if now < backoff_until:
        new_state = {"fires": fires, "backoff_level": backoff_level, "backoff_until": backoff_until}
        return "BLOCKED", "backoff_active", new_state, len(fires), backoff_until - now

    fires = fires + [now]
    new_state = {"fires": fires, "backoff_level": backoff_level, "backoff_until": backoff_until}
    return "FIRE", "both_signals", new_state, len(fires), 0


# --- action (LIVE only) --------------------------------------------------------

def execute_action(executor=None) -> bool:
    """Restart the replica connector on z203. `executor` is injectable for tests."""
    if executor is None:
        executor = _default_executor
    return bool(executor(remote_action_command()))


def _default_executor(cmd: List[str]) -> bool:
    try:
        completed = subprocess.run(cmd, capture_output=True, timeout=SSH_CMD_TIMEOUT + 5)
        return completed.returncode == 0
    except Exception:
        return False


# --- decision line --------------------------------------------------------------

def emit(
    now: int,
    decision: str,
    mode: str,
    s1_token: str,
    rep_field: str,
    reason: str,
    fires_in_window: int,
    backoff_remaining: int,
) -> str:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    return (
        "[{ts}] watchdogd: {tag}decision={decision} mode={mode} s1={s1} rep={rep} "
        "reason={reason} fires_1h={fires} backoff_s={backoff}".format(
            ts=ts,
            tag=LOG_TAG,
            decision=decision,
            mode=mode,
            s1=s1_token,
            rep=rep_field,
            reason=reason,
            fires=fires_in_window,
            backoff=backoff_remaining,
        )
    )


def main() -> int:
    now = int(time.time())
    rep_field = "n/a"
    try:
        mode = read_mode()
        s1_ok, s1_token = eval_signal1(now)
        state = load_state()
        # SSH budget: probe only when a decision actually needs replica
        # evidence — Signal 1 open (a live episode), or a dirty state whose
        # reset conjunct needs freshness. Closed + clean skips the probe
        # (~1440 SSH spawns/day saved) with rep=n/a on the decision line.
        if s1_ok or not state_is_clean(state, now):
            s2_ok, rep_field = eval_signal2(now)
        else:
            s2_ok = False
            rep_field = "n/a"
        decision, reason, new_state, fires_n, backoff_rem = evaluate(
            now, mode, s1_ok, s1_token, s2_ok, rep_field, state
        )
        save_state(new_state)
        if decision == "FIRE" and mode == "LIVE":
            if execute_action():
                reason = "fire_ok"
            else:
                reason = "fire_failed"
            backoff_rem = 0
        line = emit(now, decision, mode, s1_token, rep_field, reason, fires_n, backoff_rem)
    except Exception:  # bounded failure: one line, exit 0, always
        line = emit(now, "NOFIRE", "DRY_RUN", "s1_missing", "log_missing", "internal_error", 0, 0)
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
