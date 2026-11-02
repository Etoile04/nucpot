"""Tests for the NFM-5826 openclaw sqlite-cache guard.

Incident (NFM-5826, filed 2026-11-02): ``~/.openclaw`` regrew 18→30GB in
the two days after the 10-31 partial clear (NFM-5793 CEO adjudication),
driving df to 89% / 49Gi free with ~6 days to the <5Gi CRITICAL floor.
Attribution, first-hand: the producer is openclaw's own background
auto-update (``update.auto.enabled: true`` — upstream documented default
is ``false``), which every ~2h candidate check stages a FULL copy of the
live agent sqlite tree into ``tmp/openclaw-update-canary-*`` (measured
mid-copy at ~34GB/h input, ~4.5GB per attempt, self-cleaning on the
success path). Persistent mass is residue the updater and migration
tooling never rotates: per-upgrade ``pre-startup-migration-*.bak`` copies
(one full live-DB copy per agent per upgrade generation), June
``pre-dedup``/``migrated``/``bak``/``tmp-*`` memory one-offs, ``doctor-
cron`` state baks, stale gateway logs, and stale update-run-handoff /
oc-br / plugin-capture / npm-generation / DockerDesktopUpdates dirs.

The fix has two halves:

1. Pace the producer: restore ``update.auto.enabled`` to its documented
   default (false) in ``~/.openclaw/openclaw.json`` — updates stay
   available foreground (`openclaw update`) and via ``checkOnStart``
   hints; the 2-hourly background canary snapshot churn stops. (Config
   flip applied live, one reversible boolean — not under test here.)

2. This guard: a pattern-scoped retention pass over ``~/.openclaw`` that
   rotates exactly the enumerated residue classes (never workspace data,
   never live DBs) — ``scripts/host/openclaw-cache-guard/
   openclaw_cache_guard.sh``, installed as a daily user LaunchAgent
   (04:50, offset from the g2 04:20 docker cleanup). Dry-run is the
   default; only the plist passes ``--apply``. Mirrors the NFM-4802
   ``com.nfm.g2.cleanup-daily`` house pattern, including "install must
   not prune" (RunAtLoad false).

Hermetic: every test builds a synthetic ``.openclaw`` tree in tmp_path
with backdated mtimes (os.utime) and drives the guard with an injected
``--now`` epoch — no sleeps, no real ~/.openclaw reads. ``--root`` is
refused unless the basename is ``.openclaw`` or ``OCGUARD_TEST_ROOT_OK=1``
is set, so the same flag that makes this testable cannot point a prod
run at the wrong tree.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
from pathlib import Path

import pytest

GUARD_DIR = Path(__file__).resolve().parents[1] / "host" / "openclaw-cache-guard"
GUARD = GUARD_DIR / "openclaw_cache_guard.sh"
INSTALLER = Path(__file__).resolve().parents[1] / "install_openclaw_cache_guard.sh"
PLIST_TEMPLATE = GUARD_DIR / "ai.nfmd.openclaw-cache-guard.plist.template"

NOW = 1_800_000_000  # fixed epoch; all ages are expressed relative to this
HOUR = 3600
DAY = 86400

CANARY_AGE_MIN = 180  # default: 3h — one 2h update cycle + margin
ONEOFF_AGE_DAYS = 14
GEN_AGE_DAYS = 14
BACKUP_AGE_DAYS = 30


# ---- syntax / packaging -----------------------------------------------------------


def test_bash_syntax():
    result = subprocess.run(["/bin/bash", "-n", str(GUARD)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_guard_is_executable_in_repo():
    assert GUARD.stat().st_mode & 0o111, "openclaw_cache_guard.sh lost its exec bit"


def test_installer_bash_syntax():
    result = subprocess.run(["/bin/bash", "-n", str(INSTALLER)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_plist_template_rendered_parses_and_never_prunes_on_load():
    """The house "install must not prune" rule: RunAtLoad must be false and
    KeepAlive absent — installing the agent must never itself be a prune
    trigger; the only prune path is the daily 04:50 calendar beat with
    --apply."""
    rendered = _render_plist(tmp_render_dir())
    assert "__HOME__" not in rendered
    plist = plistlib.loads(rendered.encode())
    assert plist["Label"] == "ai.nfmd.openclaw-cache-guard"
    assert plist.get("RunAtLoad") is False
    assert "KeepAlive" not in plist
    args = plist["ProgramArguments"]
    assert "--apply" in args, "installed agent must run with --apply (dry-run default would make the beat a no-op)"
    assert str(plist["StartCalendarInterval"]["Hour"]) in ("4",)
    assert str(plist["StartCalendarInterval"]["Minute"]) in ("50",)


# ---- harness ----------------------------------------------------------------------


def tmp_render_dir() -> Path:
    import tempfile

    return Path(tempfile.mkdtemp(prefix="ocguard-render-"))


def _render_plist(out_dir: Path) -> str:
    result = subprocess.run(
        ["/bin/bash", str(INSTALLER), "--render-only", str(out_dir / "rendered.plist")],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": os.environ.get("HOME", str(Path.home()))},
    )
    assert result.returncode == 0, result.stderr
    return (out_dir / "rendered.plist").read_text()


def _touch(path: Path, age_s: int, size: int = 1024) -> Path:
    """Create a file backdated ``age_s`` before NOW."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (NOW - age_s, NOW - age_s))
    return path


def _mkdir(path: Path, age_s: int, children: tuple = ()) -> Path:
    """Create a directory whose OWN mtime is backdated (set after the
    children exist — a dir's mtime moves when its direct children change)."""
    path.mkdir(parents=True, exist_ok=True)
    for name, cage, csize in children:
        _touch(path / name, age_s=cage, size=csize)
    os.utime(path, (NOW - age_s, NOW - age_s))
    return path


def _make_tree(base: Path) -> Path:
    """A synthetic ~/.openclaw covering every residue class plus the
    must-never-touch surfaces (live DBs, workspace data, fresh residue)."""
    root = base / ".openclaw"

    # update-transient dirs: one stale (leaked), one fresh (mid-cycle)
    _mkdir(root / "tmp/openclaw-update-canary-STALE", age_s=4 * HOUR,
           children=(("payload.bin", 4 * HOUR, 4096),))
    _mkdir(root / "tmp/openclaw-update-canary-FRESH", age_s=5 * 60)
    _mkdir(root / "tmp/openclaw-update-run-handoff-OLD", age_s=4 * HOUR)
    _mkdir(root / "tmp/openclaw-update-run-handoff-NEW", age_s=30 * 60)
    _mkdir(root / "tmp/oc-br-old", age_s=4 * HOUR)

    # per-upgrade migration baks: keep-newest-with-age-floor semantics
    agent = root / "agents/ontofuel-extractor/agent"
    _touch(agent / "openclaw-agent.sqlite", age_s=30 * 60)  # live DB — NEVER
    _touch(agent / "openclaw-agent.sqlite.pre-startup-migration-gen1.bak", age_s=20 * DAY)
    _touch(agent / "openclaw-agent.sqlite.pre-startup-migration-gen2.bak", age_s=16 * DAY)
    _touch(agent / "openclaw-agent.sqlite.pre-startup-migration-gen3.bak", age_s=5 * DAY)  # under floor

    # state dir: two classes, live db present
    state = root / "state"
    _touch(state / "openclaw.sqlite", age_s=30 * 60)  # live — NEVER
    _touch(state / "openclaw.sqlite.doctor-cron-old.bak", age_s=20 * DAY)
    _touch(state / "openclaw.sqlite.doctor-cron-new.bak", age_s=16 * DAY)
    _touch(state / "openclaw.sqlite.pre-startup-migration-solo.bak", age_s=20 * DAY)  # only one — kept

    # memory one-offs (June leak class) + live memory db + fresh one-off
    mem = root / "memory"
    _touch(mem / "ontofuel-extractor.sqlite", age_s=30 * 60)  # live — NEVER
    _touch(mem / "ontofuel-extractor.sqlite.pre-dedup.20260627", age_s=120 * DAY)
    _touch(mem / "main.sqlite.migrated", age_s=120 * DAY)
    _touch(mem / "main.sqlite.bak.20260621", age_s=120 * DAY)
    _touch(mem / "ontofuel-extractor.sqlite.tmp-aacad02c", age_s=120 * DAY)
    _touch(mem / "fresh.sqlite.pre-dedup.today", age_s=5 * DAY)  # under floor — kept

    # logs: big+stale → truncate; small+stale and big+fresh → keep
    logs = root / "logs"
    _touch(logs / "gateway.log", age_s=30 * DAY, size=200_000)
    _touch(logs / "gateway.err.log", age_s=30 * DAY, size=1_000)
    _touch(logs / "gateway.fresh.log", age_s=DAY, size=200_000)

    # generation dirs: plugin captures, npm generation projects, docker updates
    _mkdir(root / "tmp/plugin-captures/OLDUUID", age_s=20 * DAY)
    _mkdir(root / "tmp/plugin-captures/FRESHUUID", age_s=DAY)
    _mkdir(root / "npm/projects/openclaw-x__openclaw-generation__g-old", age_s=20 * DAY)
    _mkdir(root / "npm/projects/openclaw-x__openclaw-generation__g-new", age_s=DAY)
    _mkdir(root / "tmp/DockerDesktopUpdates/Docker-240920", age_s=20 * DAY)

    # versioned plugin backups
    _mkdir(root / "backups/openclaw-honcho-1.3.2-bak", age_s=40 * DAY)
    _mkdir(root / "backups/openclaw-honcho-1.4.0-bak", age_s=10 * DAY)

    # must-never-touch surfaces
    _touch(root / "workspace/data/fuel_swelling_wiki/raw.zip", age_s=120 * DAY, size=4096)
    return root


def _run(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(GUARD), "--root", str(root), "--now", str(NOW), *extra],
        capture_output=True,
        text=True,
        env={**os.environ, "OCGUARD_TEST_ROOT_OK": "1"},
    )


def _out(cp: subprocess.CompletedProcess) -> str:
    return cp.stdout + cp.stderr


# ---- root safety ------------------------------------------------------------------


def test_refuses_root_not_named_openclaw(tmp_path: Path):
    victim = tmp_path / "not-openclaw"
    victim.mkdir()
    (victim / "keepme").write_bytes(b"x")
    cp = subprocess.run(
        ["/bin/bash", str(GUARD), "--root", str(victim), "--now", str(NOW), "--apply"],
        capture_output=True,
        text=True,
        env={**os.environ, "OCGUARD_TEST_ROOT_OK": ""},
    )
    assert cp.returncode == 2, _out(cp)
    assert (victim / "keepme").exists(), "guard touched a tree not named .openclaw"


def test_refuses_filesystem_root(tmp_path: Path):
    cp = subprocess.run(
        ["/bin/bash", str(GUARD), "--root", "/", "--now", str(NOW), "--apply"],
        capture_output=True,
        text=True,
        env={**os.environ, "OCGUARD_TEST_ROOT_OK": "1"},  # even the test override must not save "/"
    )
    assert cp.returncode == 2, _out(cp)


# ---- dry-run default --------------------------------------------------------------


def test_dry_run_is_default_and_deletes_nothing(tmp_path: Path):
    root = _make_tree(tmp_path)
    before = sorted(str(p) for p in root.rglob("*"))
    cp = _run(root)
    assert cp.returncode == 0, _out(cp)
    assert "[plan]" in _out(cp), "dry-run should narrate its plan"
    assert "[prune]" not in _out(cp), "dry-run must not claim prunes"
    after = sorted(str(p) for p in root.rglob("*"))
    assert before == after, "dry-run must not mutate the tree"
    assert (root / "tmp/openclaw-update-canary-STALE").exists()


# ---- update-transient dirs --------------------------------------------------------


def test_stale_canary_pruned_fresh_canary_kept(tmp_path: Path):
    root = _make_tree(tmp_path)
    cp = _run(root, "--apply")
    assert cp.returncode == 0, _out(cp)
    assert not (root / "tmp/openclaw-update-canary-STALE").exists()
    assert (root / "tmp/openclaw-update-canary-FRESH").exists()


def test_stale_handoff_and_ocbr_pruned_fresh_handoff_kept(tmp_path: Path):
    root = _make_tree(tmp_path)
    cp = _run(root, "--apply")
    assert cp.returncode == 0, _out(cp)
    assert not (root / "tmp/openclaw-update-run-handoff-OLD").exists()
    assert (root / "tmp/openclaw-update-run-handoff-NEW").exists()
    assert not (root / "tmp/oc-br-old").exists()


# ---- migration baks (keep-newest-with-age-floor) ----------------------------------


def test_agent_migration_baks_keep_newest_within_floor(tmp_path: Path):
    root = _make_tree(tmp_path)
    cp = _run(root, "--apply")
    assert cp.returncode == 0, _out(cp)
    agent = root / "agents/ontofuel-extractor/agent"
    # gen1 (20d) is superseded AND over the floor → gone
    assert not (agent / "openclaw-agent.sqlite.pre-startup-migration-gen1.bak").exists()
    # gen2 (16d): newest bak over the floor → kept (rollback copy)
    assert (agent / "openclaw-agent.sqlite.pre-startup-migration-gen2.bak").exists()
    # gen3 (5d): under the 14d floor → never touched
    assert (agent / "openclaw-agent.sqlite.pre-startup-migration-gen3.bak").exists()
    # live DB never touched
    assert (agent / "openclaw-agent.sqlite").exists()


def test_state_baks_keep_newest_per_class_solo_kept(tmp_path: Path):
    root = _make_tree(tmp_path)
    cp = _run(root, "--apply")
    assert cp.returncode == 0, _out(cp)
    state = root / "state"
    assert not (state / "openclaw.sqlite.doctor-cron-old.bak").exists()
    assert (state / "openclaw.sqlite.doctor-cron-new.bak").exists()
    assert (state / "openclaw.sqlite.pre-startup-migration-solo.bak").exists(), "a class with a single member must not self-delete"
    assert (state / "openclaw.sqlite").exists()


# ---- memory one-offs --------------------------------------------------------------


def test_memory_oneoff_residue_pruned_live_db_untouched(tmp_path: Path):
    root = _make_tree(tmp_path)
    cp = _run(root, "--apply")
    assert cp.returncode == 0, _out(cp)
    mem = root / "memory"
    assert not (mem / "ontofuel-extractor.sqlite.pre-dedup.20260627").exists()
    assert not (mem / "main.sqlite.migrated").exists()
    assert not (mem / "main.sqlite.bak.20260621").exists()
    assert not (mem / "ontofuel-extractor.sqlite.tmp-aacad02c").exists()
    assert (mem / "ontofuel-extractor.sqlite").exists(), "live memory db deleted"
    assert (mem / "fresh.sqlite.pre-dedup.today").exists(), "one-off under the age floor was deleted"


# ---- logs -------------------------------------------------------------------------


def test_logs_truncated_only_when_big_and_stale(tmp_path: Path):
    root = _make_tree(tmp_path)
    cp = _run(root, "--apply", "--log-max-bytes", "100000")
    assert cp.returncode == 0, _out(cp)
    logs = root / "logs"
    assert (logs / "gateway.log").stat().st_size == 0, "big stale log must be truncated to 0, not deleted"
    assert (logs / "gateway.log").exists()
    assert (logs / "gateway.err.log").stat().st_size == 1_000, "small log must be kept"
    assert (logs / "gateway.fresh.log").stat().st_size == 200_000, "fresh log must be kept"


# ---- generation dirs --------------------------------------------------------------


def test_generation_dirs_age_gated(tmp_path: Path):
    root = _make_tree(tmp_path)
    cp = _run(root, "--apply")
    assert cp.returncode == 0, _out(cp)
    assert not (root / "tmp/plugin-captures/OLDUUID").exists()
    assert (root / "tmp/plugin-captures/FRESHUUID").exists()
    assert not (root / "npm/projects/openclaw-x__openclaw-generation__g-old").exists()
    assert (root / "npm/projects/openclaw-x__openclaw-generation__g-new").exists()
    assert not (root / "tmp/DockerDesktopUpdates/Docker-240920").exists()


def test_versioned_backups_age_gated(tmp_path: Path):
    root = _make_tree(tmp_path)
    cp = _run(root, "--apply")
    assert cp.returncode == 0, _out(cp)
    assert not (root / "backups/openclaw-honcho-1.3.2-bak").exists()
    assert (root / "backups/openclaw-honcho-1.4.0-bak").exists()


# ---- hard never -------------------------------------------------------------------


def test_workspace_and_live_dbs_survive(tmp_path: Path):
    root = _make_tree(tmp_path)
    cp = _run(root, "--apply")
    assert cp.returncode == 0, _out(cp)
    assert (root / "workspace/data/fuel_swelling_wiki/raw.zip").exists()


# ---- accounting -------------------------------------------------------------------


def test_apply_reports_reclaimed_bytes(tmp_path: Path):
    root = _make_tree(tmp_path)
    cp = _run(root, "--apply")
    assert cp.returncode == 0, _out(cp)
    match = re.search(r"reclaimed=(\d+)", _out(cp))
    assert match, f"no reclaimed= summary line in: {_out(cp)}"
    assert int(match.group(1)) > 0


def test_test_root_mode_does_not_invoke_df(tmp_path: Path):
    root = _make_tree(tmp_path)
    cp = _run(root, "--apply")
    assert cp.returncode == 0, _out(cp)
    assert "Filesystem" not in _out(cp), "--root mode must stay df-free (fixture trees are not volumes)"
