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
ALL_BASH = ENTRIES + [GATE_DIR / "host_setup.sh", GATE_DIR / "probe_g2.sh"]

SANCTIONED = [
    "run-deploy.sh",
    "run-pre-deploy-assert.sh",
    "run-recovery.sh",
    "run-worker-inspect.sh",
    "run-sql.sh",
]


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
    return [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_sudoers_every_grant_is_an_enumerated_g2_entry():
    grants = [line for line in _sudoers_lines() if "NOPASSWD" in line]
    assert len(grants) == len(SANCTIONED)
    for line in grants:
        assert line.startswith("%admin ALL=(nfmdeploy) NOPASSWD: /usr/local/lib/nfm-g2/")
        command = line.rsplit(":", 1)[1].strip()
        assert command in {f"/usr/local/lib/nfm-g2/{name}" for name in SANCTIONED}


def test_sudoers_no_wildcards_or_blanket_all():
    for line in _sudoers_lines():
        assert "*" not in line
        assert "NOPASSWD: ALL" not in line
        assert not line.rstrip().endswith(" ALL") or line.startswith("%admin")


def test_sudoers_defaults_are_command_scoped():
    defaults = [line for line in _sudoers_lines() if line.startswith("Defaults")]
    assert defaults, "expected at least one env_keep Default"
    for line in defaults:
        assert line.startswith("Defaults!/usr/local/lib/nfm-g2/")
        assert "env_keep" in line


def test_sudoers_covers_every_sanctioned_entry():
    text = (GATE_DIR / "sudoers.d" / "nfm-prod-deploy").read_text(encoding="utf-8")
    for name in SANCTIONED:
        assert f"/usr/local/lib/nfm-g2/{name}" in text


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
        self.bin_dir = tmp_path / "fakebin"
        self.bin_dir.mkdir()
        self.repo = tmp_path / "fakerepo"
        (self.repo / "tools" / "pre-deploy-assert-smoke").mkdir(parents=True)
        self.docker_calls = tmp_path / "docker-calls.txt"
        self._write_executable("id", 'printf "nfmdeploy\\n"')
        self._write_executable(
            "docker",
            f'printf \'%s\\n\' "$*" >> {self.docker_calls}\nexit 0\n',
        )
        # stub of the real assert.sh — records argv, exits 0
        stub = self.repo / "tools" / "pre-deploy-assert-smoke" / "assert.sh"
        stub.write_text(f"#!/bin/bash\nprintf 'assert %s\\n' \"$*\" >> {self.docker_calls}\nexit 0\n")
        stub.chmod(0o755)

    def _write_executable(self, name: str, body: str) -> None:
        target = self.bin_dir / name
        target.write_text(f"#!/bin/bash\n{body}\n")
        target.chmod(0o755)

    def run(self, script: str, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = f"{self.bin_dir}:{env['PATH']}"
        env.setdefault("NFM_G2_REPO", str(self.repo))  # test hook; env_reset kills it in prod
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["/bin/bash", str(GATE_DIR / "entries" / script), *args],
            capture_output=True, text=True, env=env, timeout=20,
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
        capture_output=True, text=True, timeout=20,
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
    result = entry.run("run-sql.sh", "--db-name", "nfm_db", "apps/api/sql/001_x.sql",
                       env_extra={"NFM_G2_REPO": str(entry.repo)})
    assert result.returncode == 0, result.stderr
    recorded = entry.docker_calls.read_text()
    assert "exec -i nucpot-prod-db psql -U nfm -d nfm_db" in recorded


def test_run_sql_missing_file_fails_loudly(entry):
    result = entry.run("run-sql.sh", "nope/missing.sql",
                       env_extra={"NFM_G2_REPO": str(entry.repo)})
    assert result.returncode == 66


def test_run_recovery_restart_db_allowed(entry):
    assert entry.run("run-recovery.sh", "restart", "db").returncode == 0


def test_run_pre_deploy_assert_rejects_foreign_image(entry):
    assert entry.run("run-pre-deploy-assert.sh", "--image", "alpine:latest",
                     "--db-container", "nucpot-prod-db").returncode == 64


def test_run_pre_deploy_assert_rejects_foreign_db_container(entry):
    assert entry.run("run-pre-deploy-assert.sh", "--image", "nucpot-prod-api:candidate-abc",
                     "--db-container", "nucpot-staging-db").returncode == 64


def test_run_pre_deploy_assert_rejects_shell_metacharacters(entry):
    assert entry.run("run-pre-deploy-assert.sh", "--image", "nucpot-prod-api:candidate-$(id)",
                     "--db-container", "nucpot-prod-db").returncode == 64


def test_run_pre_deploy_assert_accepts_candidate_tag(entry):
    result = entry.run("run-pre-deploy-assert.sh", "--image", "nucpot-prod-api:candidate-abc1234",
                       "--db-container", "nucpot-prod-db",
                       "--db-user", "nfm", "--db-name", "nfm_db", "--distinct-exit", "64")
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

    monkeypatch.setattr("nfm_docker_gate.watchdog._run_as", lambda user, args: fake_run(
        ["sudo", "-H", "-u", user, "docker", *args]))

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

    monkeypatch.setattr("nfm_docker_gate.watchdog._run_as", lambda user, args: fake_run(
        ["sudo", "-H", "-u", user, "docker", *args]))

    audit = AuditLog(str(tmp_path / "wd.log"), "watchdog")
    assert_context("lwj04", "nfm-ro", audit)
    flattened = [" ".join(c[5:]) for c in calls]
    assert not any("context use" in f for f in flattened)


# ---- host_setup wiring -----------------------------------------------------------


def test_host_setup_installs_every_entry_and_plist():
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
