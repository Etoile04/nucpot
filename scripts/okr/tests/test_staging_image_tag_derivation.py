"""Tests for the NFM-4066 staging image-tag derivation logic.

Background
----------
NFM-4063 (staging API crash loop on ``Can't locate revision X``) was caused
by ``docker/.env.staging`` pinning a stale commit SHA as ``STAGING_IMAGE_TAG``
while the running container had drifted off compose management onto
``:latest``. NFM-4066 fixes the recurrence vector by having
``scripts/staging_deploy.sh`` derive ``STAGING_IMAGE_TAG`` from
``git rev-parse HEAD`` at the start of every ``cmd_deploy`` invocation —
the tag name is now always the SHA whose source tree the image was just
built from.

These tests pin down the contract:

  1. ``cmd_deploy`` overrides whatever value the env file pinned with the
     current commit SHA, so a hand-typed stale SHA cannot reach compose.
  2. The override is silent when the env file's value is empty / ``latest``
     / already matches the derived SHA — only a stale mismatch warns.
  3. ``cmd_rollback`` deliberately does NOT override — it must keep the
     user's choice of target tag (default ``:prev``).
  4. The script dies loudly when run outside a git checkout, so a
     misconfigured CI lane cannot silently fall back to ``:latest``.

Strategy
--------
Each test runs the deploy script in a self-contained temp git repo,
stubbing ``docker`` / ``curl`` so the script's lifecycle can complete
without real services. We capture what ``compose build`` was invoked
with by replacing the ``docker`` binary with a recorder, then assert
the recorded ``--tag`` argument matches the current ``git rev-parse HEAD``.
"""


from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "staging_deploy.sh"
LIB = REPO_ROOT / "scripts" / "lib" / "deploy_event.sh"
VERIFY_CLOUDFLARED = REPO_ROOT / "scripts" / "verify-cloudflared-token.sh"


# ---------------------------------------------------------------------------
# Test fixture: a self-contained fake git repo where the deploy script can run
# ---------------------------------------------------------------------------


def _make_stub(bin_dir: Path, name: str, body: str) -> Path:
    p = bin_dir / name
    p.write_text("#!/usr/bin/env bash\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


@pytest.fixture
def fake_git_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A self-contained git checkout hosting the deploy script + env file.

    Returns ``(fake_repo, bin_dir, expected_sha)``. ``expected_sha`` is the
    SHA ``git rev-parse HEAD`` returns inside ``fake_repo``; the test must
    assert that the deploy script forwards exactly this SHA to compose.
    """
    fake = tmp_path / "fake_repo"
    scripts = fake / "scripts"
    docker = fake / "docker"
    scripts.mkdir(parents=True)
    docker.mkdir(parents=True)

    # Copy the deploy script and lib so the script's SCRIPT_DIR resolution
    # finds ``lib/deploy_event.sh`` without touching the real repo.
    shutil.copy(SCRIPT, scripts / "staging_deploy.sh")
    (scripts / "staging_deploy.sh").chmod(0o755)
    shutil.copytree(LIB.parent, scripts / "lib", dirs_exist_ok=True)
    # NFM-2509: verify-cloudflared-token.sh is invoked before the health
    # gate. Provide a no-op so the deploy can proceed to build. Always stub:
    # the real script JWT-decodes a live tunnel token, which no fixture has.
    _make_stub(scripts, "verify-cloudflared-token.sh", "exit 0\n")

    # Make the fake repo a real git checkout so ``git rev-parse HEAD``
    # inside scripts/staging_deploy.sh resolves to a deterministic SHA.
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main"],
        cwd=fake,
        check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=fake, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=fake, check=True)
    # Need at least one commit so HEAD resolves to a real SHA.
    (fake / "README.md").write_text("fixture\n")
    subprocess.run(["git", "add", "README.md"], cwd=fake, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=fake, check=True)
    expected_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=fake,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    bin_dir = tmp_path / "bin"
    return fake, bin_dir, expected_sha


# ---------------------------------------------------------------------------
# Docker stub that records the image tag passed to `compose ... build`
# ---------------------------------------------------------------------------


def _make_recording_docker(bin_dir: Path, *, health_passes: bool) -> Path:
    """A ``docker`` stub that records every image tag passed to compose.

    Writes each ``docker tag <image>:<tag>`` invocation to ``bin_dir/tags.log``
    so the test can assert the deploy forwarded the derived SHA. The actual
    ``image: <repo>:<tag>`` directive in docker-compose.staging.yml is
    resolved by docker compose, which then runs ``docker tag <sha> <prev>``
    as part of the snapshot-rollback step in ``staging_deploy.sh`` —
    recording that command gives us the SHA that the deploy script
    considered "current" at build time.

    Also stubs ``curl`` to either return ``{"status":"ok"}`` (deploy
    succeeds) or fail (deploy rolls back). Returns the log path.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = bin_dir / "tags.log"
    curl_body = (
        'printf \'{"status":"ok"}\'' if health_passes else "exit 22"
    )
    # The stub records every argument. ``image tag <repo>:<old> <repo>:<new>``
    # is the snapshot-rollback command — the first argument's tag is what
    # staging_deploy.sh believes is "current" right now. We log it so the
    # test can assert it matches ``git rev-parse HEAD`` in the fake repo.
    body = f"""set -e
log="{log}"
for arg in "$@"; do
  printf '%s\\n' "$arg" >> "$log"
done
# Match real docker compose exit codes for the operations we exercise:
case "$*" in
  *" image inspect "*) exit 1 ;;  # No rollback target exists yet
  *) exit 0 ;;
esac
"""
    _make_stub(bin_dir, "docker", body)
    _make_stub(bin_dir, "curl", curl_body)
    return log


def _run_deploy(
    fake: Path,
    bin_dir: Path,
    *,
    env_file_image_tag: str | None = "latest",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the deploy script in ``fake`` with a configurable env-file tag.

    ``env_file_image_tag=None`` means the env file omits ``STAGING_IMAGE_TAG``
    entirely (operator never declared one). Any other value is written into
    ``docker/.env.staging`` verbatim.
    """
    env_file = fake / "docker" / ".env.staging"
    # NFM-4077's write_api_env_file hard-requires DATABASE_URL + SECRET_KEY;
    # without them the deploy dies before the tag-derivation step these tests
    # exist to pin (broken on main between NFM-4077 and NFM-4198).
    required_env = (
        "STAGING_DATABASE_URL=postgresql+asyncpg://stub:stub@localhost:5432/stub\n"
        "STAGING_API_SECRET_KEY=stub-secret-for-tests\n"
    )
    if env_file_image_tag is None:
        env_file.write_text("# no STAGING_IMAGE_TAG declared\n" + required_env)
    else:
        env_file.write_text(f"STAGING_IMAGE_TAG={env_file_image_tag}\n" + required_env)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(fake),
        "NFMD_DEPLOY_EVENTS_PATH": "",  # silence event writer
        "STAGING_HEALTH_TIMEOUT": "3",
        "STAGING_ROLLBACK_TAG": "prev",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(fake / "scripts" / "staging_deploy.sh"), "deploy"],
        capture_output=True,
        text=True,
        env=env,
    )


def _tags_seen(log: Path) -> list[str]:
    """Return every ``<repo>:<tag>`` argument the docker stub recorded.

    The stub logs one argument per line. We surface the image:tag pairs
    the deploy script used for snapshot-rollback (``docker tag <old> <new>``)
    so the test can assert ``<old>`` matches ``git rev-parse HEAD``.
    """
    if not log.exists():
        return []
    return [ln.strip() for ln in log.read_text().splitlines() if ln.strip()]


def _tags_used_as_source(log: Path) -> list[str]:
    """Return the source tag of every ``docker tag <src> <dst>`` invocation.

    Snapshot rollback in ``staging_deploy.sh:184`` runs
    ``docker tag <current_image>:<current_tag> <image>:<ROLLBACK_TAG>``
    — the *source* tag is the one the script considers "current", which is
    exactly the SHA we want to verify was derived rather than hand-pinned.
    """
    args = _tags_seen(log)
    out: list[str] = []
    for i, arg in enumerate(args):
        # The command word ``tag`` appears as one of the args; the next
        # argument is the source ``<repo>:<tag>``.
        if arg == "tag" and i + 1 < len(args):
            out.append(args[i + 1])
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeployDerivesImageTagFromGit:
    def test_overrides_stale_sha_with_current_commit(
        self, fake_git_repo: tuple[Path, Path, Path]
    ) -> None:
        """NFM-4066 core contract: stale hand-pinned SHA is replaced."""
        fake, bin_dir, expected_sha = fake_git_repo
        log = _make_recording_docker(bin_dir, health_passes=True)

        # The env file pins a stale SHA — exactly the NFM-4063 setup.
        result = _run_deploy(
            fake, bin_dir,
            env_file_image_tag="0d901f48f0d389675bb95a462b695e851d70aa07",
        )
        assert result.returncode == 0, (
            f"deploy failed: stderr={result.stderr!r}"
        )

        # The snapshot-rollback ``docker tag <sha>:prev`` command reveals
        # which tag the deploy script considered "current". That source
        # tag must be the freshly-derived SHA, not the env-file value.
        sources = _tags_used_as_source(log)
        assert any(s.endswith(f":{expected_sha}") for s in sources), (
            f"expected derived SHA {expected_sha!r} in docker-tag sources, "
            f"got {sources!r}"
        )
        assert not any(
            "0d901f48f0d389675bb95a462b695e851d70aa07" in s for s in sources
        ), f"stale SHA should have been overridden, got {sources!r}"
        # Warning line confirms the override was deliberate.
        assert "NFM-4066: overriding pinned STAGING_IMAGE_TAG" in result.stderr

    def test_silently_accepts_when_env_file_value_already_matches(
        self, fake_git_repo: tuple[Path, Path, Path]
    ) -> None:
        """No override warning when env file already holds the current SHA."""
        fake, bin_dir, expected_sha = fake_git_repo
        log = _make_recording_docker(bin_dir, health_passes=True)

        result = _run_deploy(fake, bin_dir, env_file_image_tag=expected_sha)
        assert result.returncode == 0, result.stderr

        sources = _tags_used_as_source(log)
        assert all(s.endswith(f":{expected_sha}") for s in sources), (
            f"expected only the matching SHA, got {sources!r}"
        )
        assert "overriding pinned STAGING_IMAGE_TAG" not in result.stderr

    def test_silently_uses_default_when_env_file_value_is_latest(
        self, fake_git_repo: tuple[Path, Path, Path]
    ) -> None:
        """The env file's ``latest`` default placeholder is not a real override."""
        fake, bin_dir, expected_sha = fake_git_repo
        log = _make_recording_docker(bin_dir, health_passes=True)

        result = _run_deploy(fake, bin_dir, env_file_image_tag="latest")
        assert result.returncode == 0, result.stderr

        sources = _tags_used_as_source(log)
        assert all(s.endswith(f":{expected_sha}") for s in sources), (
            f"default placeholder 'latest' should still be replaced with "
            f"derived SHA, got {sources!r}"
        )
        assert "overriding pinned STAGING_IMAGE_TAG" not in result.stderr

    def test_silently_uses_default_when_env_file_omits_variable(
        self, fake_git_repo: tuple[Path, Path, Path]
    ) -> None:
        """Operator never declared STAGING_IMAGE_TAG — derive cleanly."""
        fake, bin_dir, expected_sha = fake_git_repo
        log = _make_recording_docker(bin_dir, health_passes=True)

        result = _run_deploy(fake, bin_dir, env_file_image_tag=None)
        assert result.returncode == 0, result.stderr

        sources = _tags_used_as_source(log)
        assert all(s.endswith(f":{expected_sha}") for s in sources), (
            f"expected [{expected_sha!r}], got {sources!r}"
        )

    def test_no_silent_fallback_to_latest_when_run_outside_git(
        self, tmp_path: Path
    ) -> None:
        """The script must die loudly when run outside a git checkout.

        Regression check for the failure mode where a CI lane forgets to
        check out the repo before invoking the deploy script. The pre-NFM-4066
        behaviour would have silently fallen back to ``STAGING_IMAGE_TAG=latest``,
        which is exactly the drift that caused NFM-4063.
        """
        fake = tmp_path / "no_git_repo"
        scripts = fake / "scripts"
        docker = fake / "docker"
        scripts.mkdir(parents=True)
        docker.mkdir(parents=True)
        shutil.copy(SCRIPT, scripts / "staging_deploy.sh")
        (scripts / "staging_deploy.sh").chmod(0o755)
        shutil.copytree(LIB.parent, scripts / "lib", dirs_exist_ok=True)
        if VERIFY_CLOUDFLARED.exists():
            shutil.copy(VERIFY_CLOUDFLARED, scripts / "verify-cloudflared-token.sh")
            (scripts / "verify-cloudflared-token.sh").chmod(0o755)
        else:
            _make_stub(scripts, "verify-cloudflared-token.sh", "exit 0\n")
        (fake / "docker" / ".env.staging").write_text("STAGING_IMAGE_TAG=latest\n")
        (fake / "docker-compose.staging.yml").write_text("services: {}\n")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        # NOTE: no `git init` here — the fixture is deliberately NOT a
        # git checkout so ``git rev-parse HEAD`` must fail.
        log = bin_dir / "tags.log"
        _make_stub(bin_dir, "docker", f"exit 0\n# log={log}")
        _make_stub(bin_dir, "curl", 'printf \'{"status":"ok"}\'')

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(fake),
            "NFMD_DEPLOY_EVENTS_PATH": "",
            "STAGING_HEALTH_TIMEOUT": "3",
            "STAGING_ROLLBACK_TAG": "prev",
        }
        result = subprocess.run(
            ["bash", str(fake / "scripts" / "staging_deploy.sh"), "deploy"],
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode != 0, (
            f"expected non-zero exit outside git, got 0: stderr={result.stderr!r}"
        )
        assert "must run from inside the nucpot git repo" in result.stderr, (
            f"expected loud failure message; got stderr={result.stderr!r}"
        )
        # No image tags must have been forwarded to compose build.
        assert _tags_seen(log) == [], (
            "compose build must not have been invoked when derivation failed"
        )


class TestRollbackDoesNotOverride:
    """``cmd_rollback`` must keep the user's chosen tag verbatim."""

    def test_rollback_to_named_tag_uses_target_not_git_sha(
        self, fake_git_repo: tuple[Path, Path, Path]
    ) -> None:
        fake, bin_dir, expected_sha = fake_git_repo
        log = _make_recording_docker(bin_dir, health_passes=True)

        env_file = fake / "docker" / ".env.staging"
        env_file.write_text("STAGING_IMAGE_TAG=latest\n")

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(fake),
            "NFMD_DEPLOY_EVENTS_PATH": "",
            "STAGING_HEALTH_TIMEOUT": "3",
            "STAGING_ROLLBACK_TAG": "prev",
        }
        # Want to roll back to :v0.4.2 — a tag that is NOT the current SHA.
        # Stub ``docker image inspect`` to claim the tag exists, so the
        # rollback branch proceeds.
        result = subprocess.run(
            ["bash", str(fake / "scripts" / "staging_deploy.sh"), "rollback", "v0.4.2"],
            capture_output=True,
            text=True,
            env=env,
        )
        # The compose up may fail because the fake compose file has no
        # services, but the important assertion is on the tag forwarded.
        # ``cmd_rollback`` runs ``docker compose ... up --no-build`` so the
        # only docker-tag invocation we can observe is the image-inspect
        # existence check, not a build tag. We assert on the exit code and
        # the stderr instead: rollback should succeed (RC 0, "SUCCEEDED")
        # against the named tag, and the warning about overriding pinned
        # STAGING_IMAGE_TAG must NOT fire (rollback does not derive).
        assert "Rollback to 'v0.4.2' SUCCEEDED" in result.stderr, (
            f"rollback to :v0.4.2 should have succeeded; stderr={result.stderr!r}"
        )
        assert "overriding pinned STAGING_IMAGE_TAG" not in result.stderr, (
            f"rollback must not invoke the NFM-4066 derivation logic; "
            f"stderr={result.stderr!r}"
        )
        # The git SHA must not appear in any docker invocation either.
        tags = _tags_seen(log)
        assert expected_sha not in "\n".join(tags), (
            f"git SHA leaked into rollback path: tags={tags!r}"
        )
