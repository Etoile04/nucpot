#!/usr/bin/env python3
"""nfm-registration-gap-gate.py — dual-signal registration-gap gate (NFM-5880/NFM-5881).

Fires `launchctl kickstart -k system/com.cloudflare.cloudflared-nucpot` ONLY when
BOTH signals agree (design note: NFM-5880 comment 410386c2, AC1):

  Signal 1  sentinel full-edge episode window is OPEN:
            `open=1` in /Users/lwj04/.nfm-site-monitor.s1.state with file mtime
            fresher than S1_STALE_SECONDS. Missing/empty/corrupt/stale => no-fire
            (conservative). This is the "user-visible full-edge outage in progress"
            evidence (sentinel streak-1 four-conjunct rule, NFM-5667).

  Signal 2  the connector has NOT proven edge liveness within N_MIN_SECONDS:
            the last newline-terminated `Registered tunnel connection` line in
            /Library/Logs/cloudflared-nucpot.log is older than N=600s (rolling
            staleness — NOT "no registration since win_first"; the e7 flap-up
            registration at 2026-11-06T18:27:24Z while still dark is why). When the
            scanned tail holds no registration line at all, the tail's own time
            span decides: span >= N => fire-supporting (`none_span`), else
            inconclusive => no-fire (`none_short`) — rotation/truncation safe.

Caps: <= 3 fires per rolling 3600s, then exponential backoff 30m -> 60m -> 120m
(hard cap 4h). State persists in /var/run/nfm-watchdogd/reg-gap-gate-state.json so
backoff survives watchdogd restarts (watchdogd is StartInterval=60: process per
tick; state reloads every tick). Both-signals-clean tick resets everything.

DRY_RUN default: /usr/local/lib/nfm-watchdog/reg-gap-gate.mode absent or anything
other than exactly `LIVE` => log-only. Rollback = flip that one file (no plist
unload, no tunnel restart). The launchctl call is structurally unreachable unless
mode == LIVE AND the gate fired.

Log contract: exactly ONE machine-greppable line per watchdogd tick on stdout
(the bash wrapper redirects it into /var/log/nfm-watchdogd.log):

  [<ISO-Z>] watchdogd: reg-gap-gate: decision=<FIRE|NOFIRE|BLOCKED> mode=<DRY_RUN|LIVE> \
s1=<s1_open|s1_closed|s1_missing|s1_empty|s1_corrupt|s1_stale> \
reg=<age_seconds|none_span|none_short|log_missing> reason=<token> \
fires_1h=<n> backoff_s=<n>

reason tokens: both_signals, s1_clean, reg_fresh, s1_missing, s1_empty, s1_corrupt,
s1_stale, reg_none_short, log_missing, cap_reached, backoff_active, fire_ok,
fire_failed, internal_error.

Bounded failure: every path emits exactly one decision line and exits 0 — a gate
fault can never break the watchdogd tick (NFM-5747 AC5 precedent).

Python 3.9-compatible, stdlib-only: runs under /usr/bin/python3 as root.
"""
import json
import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

# --- pinned host paths (absolute — watchdogd runs as root, $HOME=/var/root) ---
S1_STATE_PATH = "/Users/lwj04/.nfm-site-monitor.s1.state"
CONNECTOR_LOG_PATH = "/Library/Logs/cloudflared-nucpot.log"
MODE_FLAG_PATH = "/usr/local/lib/nfm-watchdog/reg-gap-gate.mode"
STATE_PATH = "/var/run/nfm-watchdogd/reg-gap-gate-state.json"

# --- the action: fixed literal service, never interpolated ---
LAUNCHCTL = "/bin/launchctl"
TARGET_SERVICE = "system/com.cloudflare.cloudflared-nucpot"
FORBIDDEN_SERVICE_MARKER = "cloudflared-verify"  # out of scope (parent constraint)

# --- tunables (design note §2–§4) ---
N_MIN_SECONDS = 600          # N = 10 minutes (confirmed; == sentinel S1_EPISODE_GREEN_GAP)
S1_STALE_SECONDS = 900       # ~3 missed sentinel ticks (tick ~5m21s) => sentinel dead
CAP_WINDOW_SECONDS = 3600    # rolling hour for the fire cap
CAP_MAX_FIRES = 3
BACKOFF_BASE_SECONDS = 1800  # 30m; ladder 1800*2^(level-1)
BACKOFF_MAX_SECONDS = 14400  # 4h hard cap
TAIL_BYTES = 262144          # scan only the last 256KiB of the connector log

LOG_TAG = "reg-gap-gate: "

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


# --- Signal 1 ---------------------------------------------------------------

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


# --- Signal 2 ---------------------------------------------------------------

def scan_connector_log(raw: bytes) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Scan newline-terminated log lines -> (last_reg_epoch, first_ts, last_ts).

    `raw` must start on a line boundary (read_log_tail guarantees it). A
    trailing fragment without \\n is ignored (in-flight write). Unparseable
    timestamps are skipped. Returns (None, first, last) when no registration
    line matched, (None, None, None) when no line carried a parseable stamp.
    """
    text = raw.decode("utf-8", errors="replace")
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


def read_log_tail(path: str) -> Optional[bytes]:
    """Last TAIL_BYTES of the log, aligned to a line boundary; None if missing.

    A tail slice starts mid-line: the leading fragment (up to the first \\n)
    is discarded so a boundary-split line is never half-matched. Files smaller
    than the tail window start at a line boundary and are used whole.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > TAIL_BYTES:
                f.seek(size - TAIL_BYTES)
                buf = f.read()
                nl = buf.find(b"\n")
                return buf[nl + 1:] if nl != -1 else b""
            return f.read()
    except OSError:
        return None


def eval_signal2(now: int, path: str = CONNECTOR_LOG_PATH) -> Tuple[bool, str]:
    """True iff the connector has NOT proven registration within N seconds."""
    raw = read_log_tail(path)
    if raw is None:
        return False, "log_missing"
    last_reg, first_ts, last_ts = scan_connector_log(raw)
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


# --- mode flag (DRY_RUN default) --------------------------------------------

def read_mode(path: str = MODE_FLAG_PATH) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return "LIVE" if f.read().strip() == "LIVE" else "DRY_RUN"
    except OSError:
        return "DRY_RUN"


# --- persisted cap/backoff state -------------------------------------------

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


# --- decision core (pure — unit-test surface) -------------------------------

def evaluate(
    now: int,
    mode: str,
    s1_ok: bool,
    s1_token: str,
    s2_ok: bool,
    reg_field: str,
    state: Dict[str, object],
) -> Tuple[str, str, Dict[str, object], int, int]:
    """Returns (decision, reason, new_state, fires_in_window, backoff_remaining).

    decision: FIRE (gate satisfied; executed iff mode == LIVE), NOFIRE
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
        # connector proven fresh (reg_field is an integer age < N). A stale
        # registration (digit age >= N) or inconclusive evidence
        # (log_missing / none_short) never resets — design note §4.5.
        if (
            s1_token == "s1_closed"
            and reg_field.isdigit()
            and int(reg_field) < N_MIN_SECONDS
        ):
            new_state = {"fires": [], "backoff_level": 0, "backoff_until": 0}
            return "NOFIRE", "s1_clean", new_state, 0, 0
        reason = s1_token
        if s1_ok:
            # signal 1 open, signal 2 clean or inconclusive
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


# --- action (LIVE only) ------------------------------------------------------

def execute_action(executor=None) -> bool:
    """kickstart the connector. `executor` is injectable for tests."""
    if executor is None:
        executor = _default_executor
    return bool(executor(LAUNCHCTL, TARGET_SERVICE))


def _default_executor(launchctl: str, service: str) -> bool:
    try:
        completed = subprocess.run(
            [launchctl, "kickstart", "-k", service],
            capture_output=True,
            timeout=10,
        )
        return completed.returncode == 0
    except Exception:
        return False


# --- decision line -----------------------------------------------------------

def emit(
    now: int,
    decision: str,
    mode: str,
    s1_token: str,
    reg_field: str,
    reason: str,
    fires_in_window: int,
    backoff_remaining: int,
) -> str:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    return (
        "[{ts}] watchdogd: {tag}decision={decision} mode={mode} s1={s1} reg={reg} "
        "reason={reason} fires_1h={fires} backoff_s={backoff}".format(
            ts=ts,
            tag=LOG_TAG,
            decision=decision,
            mode=mode,
            s1=s1_token,
            reg=reg_field,
            reason=reason,
            fires=fires_in_window,
            backoff=backoff_remaining,
        )
    )


def main() -> int:
    now = int(time.time())
    try:
        mode = read_mode()
        s1_ok, s1_token = eval_signal1(now)
        s2_ok, reg_field = eval_signal2(now)
        state = load_state()
        decision, reason, new_state, fires_n, backoff_rem = evaluate(
            now, mode, s1_ok, s1_token, s2_ok, reg_field, state
        )
        save_state(new_state)
        if decision == "FIRE" and mode == "LIVE":
            if execute_action():
                reason = "fire_ok"
            else:
                reason = "fire_failed"
            backoff_rem = 0
        line = emit(now, decision, mode, s1_token, reg_field, reason, fires_n, backoff_rem)
    except Exception:  # bounded failure: one line, exit 0, always
        line = emit(now, "NOFIRE", "DRY_RUN", "s1_missing", "log_missing", "internal_error", 0, 0)
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
