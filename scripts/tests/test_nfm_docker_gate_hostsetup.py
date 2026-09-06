"""Tests for the NFM-4270 host-install layer (AC-G2.4 / AC-G2.5).

The G2 wall's strength reduces to invariants of small text files:
  * the sudoers fragment grants ONLY command-enumerated entries,
  * the launchd plists point at the installed root-owned scripts,
  * the entry scripts validate their arguments before touching docker,
  * host_setup.sh / probe_g2.sh are at least syntactically sound bash.

Entry-script behavior is exercised hermetically: a fake `id` (reports
nfmdeploy) and a fake `docker` (records argv) are prepended to PATH.
"""

from __future__ import annotations

import os
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

import pytest

GATE_DIR = Path(__file__).resolve().parents[1] / "host-prod-gate"
sys.path.insert(0, str(GATE_DIR))

from nfm_docker_gate.watchdog import _parse_context, assert_context  # noqa: E402

ENTRIES = sorted((GATE_DIR / "entries").glob("*.sh"))
ALL_BASH = [*ENTRIES, GATE_DIR / "host_setup.sh", GATE_DIR / "probe_g2.sh"]

SANCTIONED = [
    "run-deploy.sh",
    "run-pre-deploy-assert.sh",
    "run-recovery.sh",
    "run-worker-inspect.sh",
    "run-sql.sh",
    "run-record-manifest.sh",  # NFM-4273: G4a manifest record via gate entry
    "run-cleanup.sh",  # NFM-4357: sanctioned image retention cleanup
]

# Hermetic sha satisfying HEAD==DEPLOY_SHA in entry tests.
_SHA = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"


# ---- syntax -------------------------------------------------------------------


@pytest.mark.parametrize("script", ALL_BASH, ids=lambda p: p.name)
def test_bash_syntax(script):
    result = subprocess.run(["/bin/bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_all_sanctioned_entries_exist():
    names = {p.name for p in ENTRIES}
    for entry in SANCTIONED:
        assert entry in names
    # the launchd start wrappers ship too
    assert {"start-proxy.sh", "start-watchdog.sh"} <= names


# ---- sudoers fragment (AC-G2.4) ------------------------------------------------


def _sudoers_lines():
    text = (GATE_DIR / "sudoers.d" / "nfm-prod-deploy").read_text(encoding="utf-8")
    return [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]


def test_sudoers_every_grant_is_an_enumerated_g2_entry():
    grants = [line for line in _sudoers_lines() if "NOPASSWD" in line]
    assert len(grants) == len(SANCTIONED)
    for line in grants:
        assert line.startswith("%admin ALL=(nfmdeploy) NOPASSWD: /usr/local/lib/nfm-g2/")
        command = line.rsplit(":", 1)[1].strip()
        assert command in {f"/usr/local/lib/nfm-g2/{name}" for name in SANCTIONED}


def test_sudoers_no_wildcards_or_blanket_all():
    grants = [line for line in _sudoers_lines() if "NOPASSWD" in line]
    assert grants, "expected enumerated sudo grants"
    for line in _sudoers_lines():
        assert "*" not in line
    for line in grants:
        # CR F10: the old `endswith(" ALL") or startswith("%admin")` check
        # was vacuous (every grant line starts with %admin). Pin the real
        # properties: run-as exactly the deploy identity, never (ALL);
        # command field is one absolute entry-script path.
        assert "NOPASSWD: ALL" not in line, line
        assert "(nfmdeploy)" in line, line
        assert "(ALL)" not in line, line
        assert not line.rstrip().endswith(" ALL"), line
        assert line.rstrip().split()[-1].startswith("/usr/local/lib/nfm-g2/"), line


def test_sudoers_defaults_are_command_scoped():
    defaults = [line for line in _sudoers_lines() if line.startswith("Defaults")]
    assert defaults, "expected at least one env_keep Default"
    for line in defaults:
        assert line.startswith("Defaults!/usr/local/lib/nfm-g2/")
        assert "env_keep" in line


# ---- launchd plists --------------------------------------------------------------


@pytest.mark.parametrize(
    "plist,expected_args",
    [
        ("com.nfm.g2.docker-ro.plist", ["/usr/local/lib/nfm-g2/start-proxy.sh", "ro"]),
        ("com.nfm.g2.docker-full.plist", ["/usr/local/lib/nfm-g2/start-proxy.sh", "full"]),
        ("com.nfm.g2.socket-watchdog.plist", ["/usr/local/lib/nfm-g2/start-watchdog.sh"]),
    ],
)
def test_plists_launch_installed_root_scripts(plist, expected_args):
    data = (GATE_DIR / "launchd" / plist).read_bytes()
    doc = plistlib.loads(data)
    assert doc["Label"] == plist.removesuffix(".plist")
    assert doc["ProgramArguments"][1:] == expected_args
    assert doc["RunAtLoad"] is True
    assert doc["KeepAlive"] is True
    assert doc["ProgramArguments"][0] == "/bin/bash"


# ---- entry scripts: identity + argument validation (hermetic) --------------------


class EntryHarness:
    """Runs entry scripts with a fake deploy identity and fake docker."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.bin_dir = tmp_path / "fakebin"
        self.bin_dir.mkdir()
        self.repo = tmp_path / "fakerepo"
        (self.repo / "tools" / "pre-deploy-assert-smoke").mkdir(parents=True)
        self.docker_calls = tmp_path / "docker-calls.txt"
        self.entry_lock = tmp_path / "entry.lock"
        self._write_executable("id", 'printf "nfmdeploy\\n"')
        self._write_executable(
            "docker",
            f"printf '%s\\n' \"$*\" >> {self.docker_calls}\nexit 0\n",
        )
        # Default fake git (NFM-4297 CR F7): satisfies the entries' SHA
        # binding (merge-base reachable) + HEAD==DEPLOY_SHA (rev-parse)
        # unless a test overrides it.
        self._write_executable(
            "git",
            'if [ "$1" = "merge-base" ]; then exit 0; fi\n'
            f'if [ "$1" = "rev-parse" ]; then printf "%s\\n" "{_SHA}"; exit 0; fi\n'
            "exit 1\n",
        )
        # stub of the real assert.sh — records argv, exits 0
        stub = self.repo / "tools" / "pre-deploy-assert-smoke" / "assert.sh"
        stub.write_text(
            f"#!/bin/bash\nprintf 'assert %s\\n' \"$*\" >> {self.docker_calls}\nexit 0\n"
        )
        stub.chmod(0o755)

    def _write_executable(self, name: str, body: str) -> None:
        target = self.bin_dir / name
        target.write_text(f"#!/bin/bash\n{body}\n")
        target.chmod(0o755)

    def run(
        self, script: str, *args: str, env_extra: dict | None = None
    ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = f"{self.bin_dir}:{env['PATH']}"
        env.setdefault("NFM_G2_REPO", str(self.repo))  # test hook; env_reset kills it in prod
        # NFM-4297 CR F7: entries pin interpreters/lock to absolute paths
        # via NFM_G2_* hooks. Wire any fake present in fakebin to its hook
        # so fakes stay exercised; env_extra (applied last) overrides.
        for tool, hook in (
            ("git", "NFM_G2_GIT_BIN"),
            ("python3", "NFM_G2_PYTHON_BIN"),
            ("bash", "NFM_G2_BASH_BIN"),
            ("id", "NFM_G2_ID_BIN"),
        ):
            fake = self.bin_dir / tool
            if fake.exists():
                env.setdefault(hook, str(fake))
        env.setdefault("NFM_G2_ENTRY_LOCK", str(self.entry_lock))
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["/bin/bash", str(GATE_DIR / "entries" / script), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )

    def docker_argv(self) -> list[list[str]]:
        if not self.docker_calls.exists():
            return []
        return [line.split() for line in self.docker_calls.read_text().splitlines()]


@pytest.fixture()
def entry(tmp_path):
    return EntryHarness(tmp_path)


@pytest.mark.parametrize("script", SANCTIONED)
def test_entry_refuses_wrong_identity(script):
    """Without the fake id (real test user), every entry exits 77."""
    result = subprocess.run(
        ["/bin/bash", str(GATE_DIR / "entries" / script)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 77
    assert "nfmdeploy" in result.stderr


def test_run_deploy_requires_deploy_sha(entry):
    result = entry.run("run-deploy.sh")
    assert result.returncode == 1
    assert "DEPLOY_SHA not provided" in result.stderr


def test_run_recovery_rejects_unknown_service(entry):
    result = entry.run("run-recovery.sh", "restart", "evil-svc")
    assert result.returncode == 64


def test_run_recovery_rejects_rollback_non_sha(entry):
    assert entry.run("run-recovery.sh", "rollback", "--tag", "../etc/passwd").returncode == 64
    assert entry.run("run-recovery.sh", "rollback", "--tag", "short").returncode == 64


def test_run_recovery_restart_api_invokes_docker_restart(entry):
    result = entry.run("run-recovery.sh", "restart", "api")
    assert result.returncode == 0, result.stderr
    assert entry.docker_argv()[-1][-2:] == ["restart", "nucpot-prod-api"]


def test_run_sql_success_path_reads_repo_file(entry):
    (entry.repo / "apps" / "api" / "sql").mkdir(parents=True)
    sql = entry.repo / "apps" / "api" / "sql" / "001_x.sql"
    sql.write_text("SELECT 1;\n")
    result = entry.run(
        "run-sql.sh",
        "--db-name",
        "nfm_db",
        "apps/api/sql/001_x.sql",
        env_extra={"NFM_G2_REPO": str(entry.repo)},
    )
    assert result.returncode == 0, result.stderr
    recorded = entry.docker_calls.read_text()
    assert "exec -i nucpot-prod-db psql -U nfm -d nfm_db" in recorded


def test_run_sql_missing_file_fails_loudly(entry):
    result = entry.run("run-sql.sh", "nope/missing.sql", env_extra={"NFM_G2_REPO": str(entry.repo)})
    assert result.returncode == 66


def test_run_recovery_restart_db_allowed(entry):
    assert entry.run("run-recovery.sh", "restart", "db").returncode == 0


def test_run_pre_deploy_assert_rejects_foreign_image(entry):
    assert (
        entry.run(
            "run-pre-deploy-assert.sh",
            "--image",
            "alpine:latest",
            "--db-container",
            "nucpot-prod-db",
        ).returncode
        == 64
    )


def test_run_pre_deploy_assert_rejects_foreign_db_container(entry):
    assert (
        entry.run(
            "run-pre-deploy-assert.sh",
            "--image",
            "nucpot-prod-api:candidate-abc",
            "--db-container",
            "nucpot-staging-db",
        ).returncode
        == 64
    )


def test_run_pre_deploy_assert_rejects_shell_metacharacters(entry):
    assert (
        entry.run(
            "run-pre-deploy-assert.sh",
            "--image",
            "nucpot-prod-api:candidate-$(id)",
            "--db-container",
            "nucpot-prod-db",
        ).returncode
        == 64
    )


def test_run_pre_deploy_assert_accepts_candidate_tag(entry):
    result = entry.run(
        "run-pre-deploy-assert.sh",
        "--image",
        "nucpot-prod-api:candidate-abc1234",
        "--db-container",
        "nucpot-prod-db",
        "--db-user",
        "nfm",
        "--db-name",
        "nfm_db",
        "--distinct-exit",
        "64",
    )
    assert result.returncode == 0, result.stderr
    recorded = entry.docker_calls.read_text()
    assert "--image nucpot-prod-api:candidate-abc1234" in recorded
    assert "--db-container nucpot-prod-db" in recorded


def test_run_worker_inspect_takes_no_arguments(entry):
    assert entry.run("run-worker-inspect.sh", "--anything").returncode == 64


def test_run_sql_rejects_absolute_and_traversal_paths(entry):
    assert entry.run("run-sql.sh", "/etc/passwd.sql").returncode == 64
    assert entry.run("run-sql.sh", "../../etc/passwd.sql").returncode == 64
    assert entry.run("run-sql.sh", "docs/x.txt").returncode == 64


def test_run_sql_rejects_bad_db_names(entry):
    assert entry.run("run-sql.sh", "--db-name", "x; rm -rf /", "a.sql").returncode == 64


# ---- run-record-manifest.sh (NFM-4273: G4a via the gate) --------------------------


class RecorderSpy:
    """Stubs python3 for run-record-manifest.sh: records env + argv.

    The entry's flock probe (``python3 -c 'import fcntl; …'``) must reach the
    REAL python3 — fd 9 has to be genuinely flocked or every lock test is a
    no-op — so ``-c`` invocations pass through untouched."""

    def __init__(self, harness):
        self.calls = harness.repo / "recorder-calls.log"
        harness._write_executable(
            "python3",
            'if [ "$1" = "-c" ]; then exec /usr/bin/python3 "$@"; fi\n'
            f'printf "%s\\n" "$*" >> {self.calls}\n'
            f'printf "manifest=%s world=%s\\n" "$NFM_DEPLOY_MANIFEST" '
            f'"$NFM_DEPLOY_MANIFEST_WORLD_READABLE" >> {self.calls}\nexit 0\n',
        )


def test_run_record_manifest_rejects_non_hex_sha(entry):
    assert (
        entry.run(
            "run-record-manifest.sh", "--deploy-sha", "zz-not-hex", "--actor", "gh-runner:lwj04"
        ).returncode
        == 64
    )


def test_run_record_manifest_rejects_short_and_oversized_sha(entry):
    assert (
        entry.run(
            "run-record-manifest.sh", "--deploy-sha", "abc123", "--actor", "gh-runner:lwj04"
        ).returncode
        == 64
    )
    assert (
        entry.run(
            "run-record-manifest.sh", "--deploy-sha", "a" * 41, "--actor", "gh-runner:lwj04"
        ).returncode
        == 64
    )


def test_run_record_manifest_rejects_shell_metacharacters_in_actor(entry):
    assert (
        entry.run(
            "run-record-manifest.sh", "--deploy-sha", "a" * 7, "--actor", "gh-runner:$(id)"
        ).returncode
        == 64
    )
    assert (
        entry.run(
            "run-record-manifest.sh", "--deploy-sha", "a" * 7, "--actor", "path:user; rm -rf /"
        ).returncode
        == 64
    )
    assert (
        entry.run("run-record-manifest.sh", "--deploy-sha", "a" * 7, "--actor", "").returncode == 64
    )


def test_run_record_manifest_requires_both_args(entry):
    assert entry.run("run-record-manifest.sh").returncode == 64
    assert entry.run("run-record-manifest.sh", "--deploy-sha", "a" * 7).returncode == 64
    assert entry.run("run-record-manifest.sh", "--actor", "gh-runner:x").returncode == 64


def test_run_record_manifest_invokes_recorder_at_canonical_path(entry):
    """The exec'd recorder must see the canonical G4 manifest path + the
    world-readable flag, with sha/actor forwarded verbatim (NFM-4273)."""
    RecorderSpy(entry)
    result = entry.run(
        "run-record-manifest.sh",
        "--deploy-sha",
        "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
        "--actor",
        "gh-runner:lwj04",
    )
    assert result.returncode == 0, result.stderr
    log = (entry.repo / "recorder-calls.log").read_text()
    assert "--deploy-sha 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b" in log
    assert "--actor gh-runner:lwj04" in log
    assert "manifest=/usr/local/var/nfm-g2/prod-deploy-manifest.json" in log
    assert "world=1" in log


def test_run_record_manifest_accepts_short_sha(entry):
    """git short sha (7 hex) is the documented minimum — must pass through."""
    RecorderSpy(entry)
    result = entry.run(
        "run-record-manifest.sh", "--deploy-sha", "1a2b3c4", "--actor", "deploy_prod.sh:lwj04"
    )
    assert result.returncode == 0, result.stderr


# ---- NFM-4297 (CR F7 hardening): lock, SHA binding, pinned interpreters --------


def test_run_deploy_refuses_sha_unreachable_from_main(entry):
    """CR F7 SHA binding: HEAD==DEPLOY_SHA alone qualifies ANY local commit
    (or rewritten history) — the deploy must also require DEPLOY_SHA to be
    reachable from origin/main. Fake git refuses the ancestry → deploy body
    never execs."""
    entry._write_executable(
        "git",
        f'if [ "$1" = "rev-parse" ]; then printf "%s\\n" "{_SHA}"; exit 0; fi\n'
        "exit 1\n",  # merge-base --is-ancestor → NOT reachable
    )
    entry._write_executable(
        "bash",
        f"printf 'body ran\\n' >> {entry.docker_calls}\nexit 0\n",
    )
    result = entry.run("run-deploy.sh", env_extra={"DEPLOY_SHA": _SHA})
    assert result.returncode == 1
    assert "not reachable from origin/main" in result.stderr
    assert not entry.docker_calls.exists() or "body ran" not in entry.docker_calls.read_text()


def test_run_record_manifest_refuses_unreachable_sha(entry):
    """CR F7 manifest-poisoning guard: a recorded baseline matching rogue
    live state would SILENCE the drift alarm — the recorder entry must
    refuse a --deploy-sha that is not reachable from origin/main."""
    RecorderSpy(entry)
    entry._write_executable(
        "git",
        'if [ "$1" = "merge-base" ]; then exit 1; fi\nexit 0\n',
    )
    result = entry.run("run-record-manifest.sh", "--deploy-sha", _SHA, "--actor", "gh-runner:lwj04")
    assert result.returncode == 1
    assert "not reachable from origin/main" in result.stderr
    assert not (entry.repo / "recorder-calls.log").exists()


def test_entry_lock_excludes_overlapping_record(entry):
    """CR F7 entry lock: an exclusive flock shared by run-deploy.sh and
    run-record-manifest.sh serializes them — while held, the record entry
    stands down (75) without touching anything; released, it proceeds."""
    import fcntl

    RecorderSpy(entry)
    lock = entry.entry_lock
    lock.parent.mkdir(parents=True, exist_ok=True)
    # the hold must span the entry subprocess — no context manager
    holder = open(lock, "a+")  # noqa: SIM115
    try:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = entry.run(
            "run-record-manifest.sh", "--deploy-sha", _SHA, "--actor", "gh-runner:lwj04"
        )
        assert result.returncode == 75
        assert "another gated entry holds" in result.stderr
        assert not (entry.repo / "recorder-calls.log").exists()
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()

    result = entry.run("run-record-manifest.sh", "--deploy-sha", _SHA, "--actor", "gh-runner:lwj04")
    assert result.returncode == 0, result.stderr
    assert (entry.repo / "recorder-calls.log").exists()


def test_run_deploy_pinned_bash_ignores_path_shim(entry, tmp_path):
    """CR F7 pinned interpreters + PATH sanitize: a caller-controlled PATH
    must never select a binary that runs as nfmdeploy (macOS sudo has no
    secure_path). A PATH bash shim must NOT run; the pinned bash must — and
    it must see a TRUSTED-only PATH."""
    pinned = tmp_path / "pinned-bash"
    pinned.write_text(
        f'#!/bin/bash\nprintf "PINNED path=[%s]\\n" "$PATH" >> {entry.docker_calls}\nexit 0\n'
    )
    pinned.chmod(0o755)
    # the PATH shim would win under PATH resolution (fakebin is PATH-first)
    entry._write_executable("bash", f"printf 'PATHSHIM ran\\n' >> {entry.docker_calls}\nexit 0\n")
    result = entry.run(
        "run-deploy.sh",
        env_extra={"DEPLOY_SHA": _SHA, "NFM_G2_BASH_BIN": str(pinned)},
    )
    assert result.returncode == 0, result.stderr
    log = entry.docker_calls.read_text()
    assert "PATHSHIM" not in log
    assert "PINNED" in log
    # the deploy body's PATH is the trusted list only — no caller dirs
    body_path = log.split("path=[", 1)[1].split("]", 1)[0]
    assert str(entry.bin_dir) not in body_path
    assert body_path.startswith("/usr/local/bin:")


def test_record_entry_pinned_python_ignores_path_shim(entry, tmp_path):
    """CR F7 pinned interpreters, record side: the recorder must exec via
    the pinned python3, never a PATH-resolved shim."""
    pinned = tmp_path / "pinned-python3"
    pinned.write_text(
        f'#!/bin/bash\nprintf "PINNED-RECORDER %s\\n" "$*" >> {entry.docker_calls}\nexit 0\n'
    )
    pinned.chmod(0o755)
    entry._write_executable(
        "python3", f"printf 'PATHSHIM-RECORDER ran\\n' >> {entry.docker_calls}\nexit 0\n"
    )
    result = entry.run(
        "run-record-manifest.sh",
        "--deploy-sha",
        _SHA,
        "--actor",
        "gh-runner:lwj04",
        env_extra={"NFM_G2_PYTHON_BIN": str(pinned)},
    )
    assert result.returncode == 0, result.stderr
    log = entry.docker_calls.read_text()
    assert "PATHSHIM-RECORDER" not in log
    assert "PINNED-RECORDER scripts/record_deploy_manifest.py" in log


def test_entry_preflight_ignores_path_shims(entry):
    """CR F7 follow-up: the trusted-PATH export must precede EVERY external
    binary the entries run as nfmdeploy, not just the exec'd interpreter.
    `mkdir` (entry-lock setup) and `id` (identity check) used to resolve
    from the inherited caller PATH before the export — a PATH shim placed
    ahead of them must not run."""
    entry._write_executable("mkdir", f"printf 'MKDIRSHIM ran\\n' >> {entry.docker_calls}\nexit 0\n")
    entry._write_executable("bash", f"printf 'body ran\\n' >> {entry.docker_calls}\nexit 0\n")
    result = entry.run("run-deploy.sh", env_extra={"DEPLOY_SHA": _SHA})
    assert result.returncode == 0, result.stderr

    RecorderSpy(entry)
    result = entry.run("run-record-manifest.sh", "--deploy-sha", _SHA, "--actor", "gh-runner:lwj04")
    assert result.returncode == 0, result.stderr
    assert (entry.repo / "recorder-calls.log").exists()

    log = entry.docker_calls.read_text() if entry.docker_calls.exists() else ""
    assert "MKDIRSHIM" not in log


# ---- NFM-4273 wiring: semantic sudoers parse + behavioral forwarding ----------


def test_sudoers_env_keep_scoped_to_deploy_entry_only():
    """Semantic parse (replaces a source-grep): env passthrough belongs to
    the deploy entry EXACTLY — the trio incl. DEPLOY_ACTOR (NFM-4273 B2),
    and no other entry takes env through env_reset (args-not-env)."""
    import re

    kept: dict[str, set[str]] = {}
    for line in _sudoers_lines():
        if not line.startswith("Defaults!"):
            continue
        match = re.match(r'Defaults!/usr/local/lib/nfm-g2/(\S+) env_keep \+= "([^"]*)"', line)
        assert match, f"unrecognized Defaults line: {line}"
        kept.setdefault(match.group(1), set()).update(match.group(2).split())
    assert kept.get("run-deploy.sh") == {"DEPLOY_SHA", "PROXY_PORT", "DEPLOY_ACTOR"}
    assert set(kept) == {"run-deploy.sh"}, "only the deploy entry takes env passthrough"


def test_run_deploy_forwards_actor_env_into_deploy_body(entry):
    """Behavioral (NFM-4273 B2): the caller's DEPLOY_ACTOR must reach the
    exec'd deploy body — gh-runner provenance survives the entry (a fake
    `bash` stands in for the deploy body and records what it inherited)."""
    entry._write_executable(
        "git",
        f'if [ "$1" = "rev-parse" ]; then printf "%s\\n" "{_SHA}"; exit 0; fi\n'
        'if [ "$1" = "merge-base" ]; then exit 0; fi\nexit 1\n',
    )
    entry._write_executable(
        "bash",
        f"printf 'body argv=%s actor=[%s] sha=[%s] port=[%s]\\n' \"$*\" "
        f'"$DEPLOY_ACTOR" "$DEPLOY_SHA" "$PROXY_PORT" >> {entry.docker_calls}\nexit 0\n',
    )
    result = entry.run(
        "run-deploy.sh", env_extra={"DEPLOY_SHA": _SHA, "DEPLOY_ACTOR": "gh-runner:lwj04"}
    )
    assert result.returncode == 0, result.stderr
    log = entry.docker_calls.read_text()
    assert "body argv=scripts/deploy_prod.sh" in log
    assert "actor=[gh-runner:lwj04]" in log
    assert f"sha=[{_SHA}]" in log


def test_run_deploy_defaults_actor_to_empty_for_manual_runs(entry):
    """Manual runs set no DEPLOY_ACTOR — the entry must export it EMPTY
    (deploy_prod.sh then defaults it), never leave it unset (set -u trap)."""
    entry._write_executable(
        "git",
        f'if [ "$1" = "rev-parse" ]; then printf "%s\\n" "{_SHA}"; exit 0; fi\n'
        'if [ "$1" = "merge-base" ]; then exit 0; fi\nexit 1\n',
    )
    entry._write_executable(
        "bash",
        f"printf 'body actor=[%s]\\n' \"$DEPLOY_ACTOR\" >> {entry.docker_calls}\nexit 0\n",
    )
    result = entry.run("run-deploy.sh", env_extra={"DEPLOY_SHA": _SHA})
    assert result.returncode == 0, result.stderr
    assert "body actor=[]" in entry.docker_calls.read_text()


def test_run_recovery_rollback_re_records_manifest_after_compose(entry, tmp_path):
    """NFM-4273 review F3: a sanctioned rollback changes live digests, so it
    must re-record the G4a manifest at the canonical path AFTER compose up —
    otherwise the next drift interval false-alarms the rollback. The python3
    spy appends to the SAME log as the fake docker so ORDER is provable."""
    gate_var = tmp_path / "gate-var"
    tag = "1a2b3c4d5e6f7a8b"
    entry._write_executable(
        "python3",
        f"printf 'python3 %s\\n' \"$*\" >> {entry.docker_calls}\n"
        f"printf 'manifest=%s world=%s\\n' \"$NFM_DEPLOY_MANIFEST\" "
        f'"$NFM_DEPLOY_MANIFEST_WORLD_READABLE" >> {entry.docker_calls}\nexit 0\n',
    )
    result = entry.run(
        "run-recovery.sh", "rollback", "--tag", tag, env_extra={"NFM_G2_VAR_DIR": str(gate_var)}
    )
    assert result.returncode == 0, result.stderr
    log = entry.docker_calls.read_text().splitlines()
    compose_at = next(i for i, ln in enumerate(log) if "up -d" in ln)
    record_at = next(
        i for i, ln in enumerate(log) if ln.startswith("python3 scripts/record_deploy_manifest.py")
    )
    assert record_at > compose_at, f"manifest re-record must follow compose up: {log}"
    assert f"--deploy-sha {tag}" in log[record_at]
    assert "run-recovery.sh:" in log[record_at]
    assert f"manifest={gate_var}/prod-deploy-manifest.json world=1" in log


# ---- watchdog context assertion ----------------------------------------------------


def test_parse_context():
    assert _parse_context("lwj04:nfm-ro") == ("lwj04", "nfm-ro")
    with pytest.raises(SystemExit):
        _parse_context("no-colon")


def test_assert_context_repairs_drift(tmp_path, monkeypatch):
    from nfm_docker_gate.audit import AuditLog

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[5:] == ["context", "show"]:
            return subprocess.CompletedProcess(argv, 0, stdout="desktop-linux\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "nfm_docker_gate.watchdog._run_as",
        lambda user, args: fake_run(["sudo", "-H", "-u", user, "docker", *args]),
    )

    audit = AuditLog(str(tmp_path / "wd.log"), "watchdog")
    assert_context("lwj04", "nfm-ro", audit)
    flattened = [" ".join(c[5:]) for c in calls]
    assert any("context use nfm-ro" in f for f in flattened)


def test_assert_context_noop_when_correct(tmp_path, monkeypatch):
    from nfm_docker_gate.audit import AuditLog

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[5:] == ["context", "show"]:
            return subprocess.CompletedProcess(argv, 0, stdout="nfm-ro\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "nfm_docker_gate.watchdog._run_as",
        lambda user, args: fake_run(["sudo", "-H", "-u", user, "docker", *args]),
    )

    audit = AuditLog(str(tmp_path / "wd.log"), "watchdog")
    assert_context("lwj04", "nfm-ro", audit)
    flattened = [" ".join(c[5:]) for c in calls]
    assert not any("context use" in f for f in flattened)


# ---- host_setup wiring -----------------------------------------------------------


def test_host_setup_installs_every_entry_and_plist():
    # Source inspection only — executing host_setup.sh needs root on macOS
    # (dscl, /usr/local, /etc/sudoers.d, /Library/LaunchDaemons), which CI
    # cannot provide. The on-host probe (probe_g2.sh AC-G2.3/G2.4) verifies
    # the INSTALLED state after the installer runs.
    text = (GATE_DIR / "host_setup.sh").read_text(encoding="utf-8")
    for name in SANCTIONED:
        assert name.removesuffix(".sh") in text, f"host_setup.sh does not install {name}"
    for plist in ("com.nfm.g2.docker-ro", "com.nfm.g2.docker-full", "com.nfm.g2.socket-watchdog"):
        assert plist in text
    # the wall itself
    assert "chmod 060" in text and "chgrp" in text


def test_config_json_matches_default_scope():
    import json

    from nfm_docker_gate.policy import ScopeConfig

    data = json.loads((GATE_DIR / "config.json").read_text(encoding="utf-8"))
    cfg = ScopeConfig.from_dict(data)
    assert cfg.prod_projects == ("nucpot-prod",)
    assert cfg.prod_name_prefixes == ("nucpot-prod",)


def test_entry_scripts_are_executable_in_repo():
    for script in ENTRIES:
        assert script.stat().st_mode & stat.S_IXUSR, f"{script.name} lost its exec bit"
