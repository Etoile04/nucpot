"""Tests for the NFM-5566 SIP-tolerant deploy-home chown helper.

Incident (2026-10-17 03:07Z apply, NFM-5553 host apply follow-up): once
macOS daemons had first run as nfmdeploy, SIP-protected paths appeared
inside /var/lib/nfmdeploy (containermanagerd / Mail container metadata).
host_setup.sh §2's bare ``chown -R nfmdeploy:staff /var/lib/nfmdeploy``
failed EPERM on those entries — as root — and under ``set -euo pipefail``
aborted the installer BEFORE §4's gate-file installs (mirrors.json +
nfm_docker_gate/mirror_health.py). The live mirror-health module stayed on
the old ``all()`` quorum and G2.7 false-reded within 4 minutes (03:11:29Z,
healthy_count 4, prod_mirror_healthy false). The 10-16 first run had
passed only because the SIP paths did not exist yet.

The fix: scripts/host-prod-gate/chown-deploy-home.sh — a per-entry chown
that skips (counted, reported) SIP-protected entries so the installer
ALWAYS reaches the gate-file installs, while staying fatal when NOTHING is
chownable (systemic misconfig, not SIP noise).

Hermetic: a fake ``chown`` (records argv, fails per configurable
predicate) is wired via the helper's NFM_G2_CHOWN_BIN env hook; the tree
walk itself uses the real find (BSD + GNU both speak -print0), with
NFM_G2_FIND_BIN overridden only for the degenerate-walk guard.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

GATE_DIR = Path(__file__).resolve().parents[1] / "host-prod-gate"
HELPER = GATE_DIR / "chown-deploy-home.sh"
HOST_SETUP = GATE_DIR / "host_setup.sh"

OWNER = "nfmdeploy:staff"


# ---- syntax ----------------------------------------------------------------------


def test_bash_syntax():
    result = subprocess.run(["/bin/bash", "-n", str(HELPER)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_helper_is_executable_in_repo():
    assert HELPER.stat().st_mode & 0o111, "chown-deploy-home.sh lost its exec bit"


# ---- harness ---------------------------------------------------------------------


def _walk_entries(root: Path) -> set[str]:
    """The exact entry set ``find <root> -print0`` (no -L) emits: the root
    itself plus every dir, file, and symlink, never descending into
    symlinked dirs (Projects/nucpot points OUT of the tree)."""
    entries = {str(root)}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        entries.add(dirpath)
        entries.update(os.path.join(dirpath, name) for name in dirnames)
        entries.update(os.path.join(dirpath, name) for name in filenames)
    return entries


class ChownHarness:
    """Runs the helper over a DEPLOY_HOME skeleton with a fake chown."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.bin_dir = tmp_path / "fakebin"
        self.bin_dir.mkdir()
        self.calls = tmp_path / "chown-calls.txt"
        # §2 skeleton: what host_setup.sh creates right before the chown —
        # these must ALWAYS be chownable on a healthy host.
        self.home = tmp_path / "deploy-home"
        (self.home / ".docker" / "cli-plugins").mkdir(parents=True)
        (self.home / "Projects").mkdir()
        (self.home / ".docker" / "config.json").write_text("{}\n")
        # §2 also symlinks the REAL repo (operator's home) into the deploy
        # home — the target lives outside the walked tree, and chowning
        # through it would hand the whole checkout to the deploy identity.
        self.outside_repo = tmp_path / "outside-repo"
        (self.outside_repo / ".git").mkdir(parents=True)
        (self.outside_repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (self.home / "Projects" / "nucpot").symlink_to(self.outside_repo)
        self.fail_substr: str | None = None
        self.fail_all = False
        self._write_chown()

    def _write(self, name: str, body: str) -> None:
        target = self.bin_dir / name
        target.write_text(f"#!/bin/bash\n{body}\n")
        target.chmod(0o755)

    def _write_chown(self) -> None:
        guard = f'case "$3" in *"{self.fail_substr}"*) exit 1 ;; esac\n' if self.fail_substr else ""
        if self.fail_all:
            guard = "exit 1\n"
        # argv is: -h <owner> <path> — recording all three pins the
        # no-dereference (-h) contract alongside coverage. The guard runs
        # BEFORE the record line, so the calls file holds SUCCESSES only
        # (a failed chown is a skip, visible via the helper's WARNING).
        self._write(
            "chown",
            f'{guard}printf \'%s\\t%s\\t%s\\n\' "$1" "$2" "$3" >> {self.calls}\nexit 0\n',
        )

    def add_sip_entries(self) -> None:
        """The 2026-10-17 stand-ins: SIP-protected containermanagerd / Mail
        paths that appeared under DEPLOY_HOME after daemons first ran as
        nfmdeploy. Every path component containing 'sip-guarded' EPERMs."""
        mail = self.home / "Library" / "Containers" / "com.apple.mail" / "Data"
        mail.mkdir(parents=True)
        (mail / "sip-guarded").write_text("protected\n")
        cm = self.home / "Library" / "containermanagerd" / "sip-guarded-bundle"
        cm.mkdir(parents=True)
        (cm / "inner").write_text("protected\n")

    def run(
        self, args: list[str] | None = None, env_extra: dict | None = None
    ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["NFM_G2_CHOWN_BIN"] = str(self.bin_dir / "chown")
        if env_extra:
            env.update(env_extra)
        # args=None → the canonical invocation; args=[] genuinely passes no
        # arguments (usage-error shape).
        argv = ["/bin/bash", str(HELPER), *((OWNER, str(self.home)) if args is None else args)]
        return subprocess.run(argv, capture_output=True, text=True, env=env, timeout=20)

    def chowned(self) -> dict[str, tuple[str, str]]:
        """path -> (flag, owner) for every chown the helper attempted."""
        if not self.calls.exists():
            return {}
        rows: dict[str, tuple[str, str]] = {}
        for line in self.calls.read_text().splitlines():
            flag, owner, path = line.split("\t")
            rows[path] = (flag, owner)
        return rows


@pytest.fixture()
def chown_h(tmp_path):
    return ChownHarness(tmp_path)


# ---- the regression: EPERM must not abort the walk --------------------------------


def test_sip_eperm_entries_are_skipped_and_rest_chowned(chown_h):
    """THE incident: SIP-protected entries EPERM (even as root). The old
    bare `chown -R` aborted under set -e and the installer never reached
    the §4 gate-file installs — the helper must exit 0, chown everything
    else (including the home root itself), and report the skips."""
    chown_h.add_sip_entries()
    chown_h.fail_substr = "sip-guarded"
    chown_h._write_chown()
    result = chown_h.run()
    assert result.returncode == 0, result.stderr
    chowned = chown_h.chowned()
    # everything except the SIP stand-ins is chowned, with the right owner
    walk = _walk_entries(chown_h.home)
    sip = {p for p in walk if "sip-guarded" in p}
    assert set(chowned) == walk - sip
    assert all(flag == "-h" and owner == OWNER for flag, owner in chowned.values())
    assert str(chown_h.home) in chowned, "the home root itself must be chowned"
    # the skip is counted and reported, not silent — the count itself comes
    # from the fixture tree, never hardcoded (three stand-in paths match the
    # predicate: the mail file, the containermanagerd bundle dir, and the
    # file inside it), so the assertion cannot rot with tmp-path numbering
    # (CR: the literal "2/" form passed only where basetemp contained "2/")
    assert f"WARNING: {len(sip)}/{len(walk)} entries" in result.stdout


def test_clean_tree_chowns_every_entry_no_warning(chown_h):
    """Positive control: no SIP entries (the 10-16 first-run shape) — the
    full tree is chowned and the helper stays quiet."""
    result = chown_h.run()
    assert result.returncode == 0, result.stderr
    assert set(chown_h.chowned()) == _walk_entries(chown_h.home)
    assert "WARNING" not in result.stdout


def test_symlink_targets_outside_tree_are_never_chowned(chown_h):
    """DEPLOY_HOME/Projects/nucpot is a symlink to the operator's real
    checkout. The walk must not descend into it and every chown must pass
    -h — chowning through the link would re-own the whole repo tree."""
    chown_h.add_sip_entries()
    chown_h.fail_substr = "sip-guarded"
    chown_h._write_chown()
    result = chown_h.run()
    assert result.returncode == 0, result.stderr
    chowned = chown_h.chowned()
    link = str(chown_h.home / "Projects" / "nucpot")
    assert link in chowned, "the symlink itself must be chowned (lchown)"
    for path in chowned:
        assert not path.startswith(str(chown_h.outside_repo)), (
            f"chown escaped the walked tree into the real checkout: {path}"
        )
    assert chowned[link][0] == "-h", "symlink chown must not dereference"


def test_total_chown_failure_is_fatal(chown_h):
    """Fail-closed boundary: a bad owner spec (or equally broken host)
    fails EVERY entry — that is systemic, not SIP, and must abort the
    installer rather than silently ship an un-chowned deploy home."""
    chown_h.fail_all = True
    chown_h._write_chown()
    result = chown_h.run()
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert "systemic" in result.stderr


def test_degenerate_walk_output_is_fatal(chown_h, tmp_path):
    """A walk that emits nothing (find broken / root unreadable) must not
    pass as a successful no-op chown."""
    fake_find = tmp_path / "fake-find"
    fake_find.write_text("#!/bin/bash\nexit 0\n")
    fake_find.chmod(0o755)
    result = chown_h.run(env_extra={"NFM_G2_FIND_BIN": str(fake_find)})
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert "no entries" in result.stderr


# ---- argument contract ------------------------------------------------------------


def test_usage_errors_exit_64(chown_h):
    assert chown_h.run(args=[]).returncode == 64
    assert chown_h.run(args=[OWNER]).returncode == 64
    assert chown_h.run(args=[OWNER, str(chown_h.home), "extra"]).returncode == 64


def test_missing_root_dir_exits_66(chown_h):
    result = chown_h.run(args=[OWNER, str(chown_h.tmp / "no-such-dir")])
    assert result.returncode == 66
    assert "not a directory" in result.stderr


# ---- host_setup wiring (source inspection — host_setup needs root) -----------------


def test_host_setup_uses_helper_and_drops_bare_recursive_chown():
    """The §2 call site must delegate to the helper — a bare recursive
    chown over DEPLOY_HOME is exactly the 2026-10-17 abort, so any
    regression to that shape must fail here."""
    text = HOST_SETUP.read_text(encoding="utf-8")
    invoke = 'bash "${SRC}/chown-deploy-home.sh" "${DEPLOY_USER}:staff" "${DEPLOY_HOME}"'
    assert invoke in text, "host_setup.sh does not invoke chown-deploy-home.sh at §2"
    # the bare chown -R over DEPLOY_HOME is gone (match a COMMAND at line
    # start, as the original L147 was — not prose about it)
    assert not re.search(r"^chown -R ", text, re.MULTILINE), (
        "host_setup.sh still contains a bare chown -R command"
    )
    # and the delegation must land BEFORE the §4 gate-file installs — the
    # incident was dying en route to them (mirrors.json + mirror_health.py)
    assert text.index(invoke) < text.index('"${SRC}/mirrors.json"'), (
        "chown delegation must precede the §4 gate-file installs"
    )
