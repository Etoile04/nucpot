"""Unit tests for tools/prod-tag-retention/prune.sh — NFM-3448.

Exercises the candidate-tag retention script with a fake ``docker`` shim on
PATH so the script runs on ubuntu-latest PR runners without Docker. The fake
shim returns canned ``docker images --format`` rows so each test can pin a
specific set of tags + CreatedAt timestamps and assert which ``docker rmi``
invocations the prune step issues.

The CI rebuild pipeline (``.github/workflows/production-deployment.yml``)
double-tags each api candidate build as ``nucpot-prod-api:candidate-<sha>``
and ``nucpot-prod-api:latest``. After deploy-prod the candidate tag is
redundant (the SHA-tagged image is the rollback primitive per ADR-NFM-2139
§5 D1), so ``prune.sh`` keeps only the most-recent ``--keep`` candidate tags
per repository and removes the rest.

Six behaviour groups covered:

  1. USAGE         — ``--help`` prints and exits 0; unknown arg exits 2;
                     ``--repo`` and ``--keep`` are required.
  2. NO_CANDIDATES — repository has zero candidate tags → exits 0, no
                     ``docker rmi`` issued.
  3. UNDER_KEEP    — repository has fewer than ``--keep`` candidate tags →
                     exits 0, no ``docker rmi`` issued.
  4. TRIM_OVERFLOW — repository has more than ``--keep`` candidate tags →
                     oldest are removed, newest are kept, exit 0.
  5. MIXED_TAGS    — repository has BOTH candidate-* and SHA tags → only
                     candidate-* tags are considered for pruning; SHA tags
                     (handled by the separate SHA prune at the end of
                     deploy-prod) are left untouched.
  6. MULTI_REPO    — same caller iterates api + lightrag + web in one run;
                     each repository is pruned independently.

The live-Docker integration test (optional, gated on a self-hosted runner
with Docker) would live alongside this in a future smoke.sh. CI exercise
today: the existing ``pre-deploy-assert-smoke`` job already calls
``pytest tools/prod-tag-retention/test_prune.py`` for free — see the job
edit landing in this same PR.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
PRUNE_SCRIPT = SCRIPT_DIR / "prune.sh"


# ---------------------------------------------------------------------------
# Fake docker harness — same shape as tools/pre-deploy-assert-smoke and
# tools/post-deploy-cutover-assert.
# ---------------------------------------------------------------------------


def _write_fake_docker(bin_dir: Path, body: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "docker"
    shim.write_text("#!/usr/bin/env bash\n" + body + "\n")
    shim.chmod(0o755)
    return bin_dir


def _reset_log(log_path: Path) -> None:
    """Truncate the rmi log so a stale file from a previous run cannot bleed
    into this run's assertions. The fake-docker shim appends to whichever
    ``$DOCKER_RMI_LOG`` points at; if the file existed before, prior
    entries would survive alongside the ones the current run produced.
    """
    if log_path.exists():
        log_path.unlink()


def _run_prune(
    args: list[str],
    bin_dir: Path | None = None,
    env_extra: dict | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(PRUNE_SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def _fake_docker_with_images(rows: list[tuple[str, str, str, str]]) -> str:
    """Return a fake docker shim body that serves ``docker images --format``
    rows from the supplied tuples ``(repository, tag, image_id, created_at)``.

    The shim logs every invocation of ``docker rmi`` (one line per removed
    image ID) to ``$DOCKER_RMI_LOG`` so tests can assert what was removed
    without depending on partial / docker-side parse of script stdout.
    """
    if not rows:
        images_block = 'echo ""'
    else:
        lines = "\n".join(
            f'  echo "{repo}|{tag}|{img_id}|{created}"'
            for repo, tag, img_id, created in rows
        )
        images_block = (
            'if [[ "$2" == "--format" ]]; then\n'
            f'{lines}\n'
            "  fi"
        )

    rmi_log = '"${DOCKER_RMI_LOG:-/tmp/nfmd-prune-rmi-default.log}"'
    return f"""
{images_block}

# The script may invoke removal via either ``docker rmi -f X`` or
# ``docker image rm -f X``; both forms must end up logging the image ID
# so tests can assert on removals.
if [[ "$1" == "rmi" ]] || ([[ "$1" == "image" ]] && [[ "$2" == "rm" ]]); then
  : > "{rmi_log}.tmp"
  skip_next=0
  for arg in "$@"; do
    if [[ $skip_next -eq 1 ]]; then
      skip_next=0
      continue
    fi
    case "$arg" in
      rmi|image|rm|-f)
        # ``docker image rm`` consumes two top-level subcommands; the
        # next arg might be a flag too, so don't try to be clever —
        # just skip these literal command parts and never write them.
        continue
        ;;
    esac
    echo "rmi $arg" >> "{rmi_log}.tmp"
  done
  if [[ -s "{rmi_log}.tmp" ]]; then
    cat "{rmi_log}.tmp" >> "{rmi_log}"
  fi
  rm -f "{rmi_log}.tmp"
  echo "removed"
  exit 0
fi

echo ""
exit 0
""".strip("\n")


def _read_rmi_log(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []
    return [
        line.removeprefix("rmi ")
        for line in log_path.read_text().splitlines()
        if line.startswith("rmi ")
    ]


# ---------------------------------------------------------------------------
# 1. Usage / argument validation
# ---------------------------------------------------------------------------


def test_help_exits_zero():
    result = _run_prune(["--help"])
    assert result.returncode == 0
    assert "candidate" in result.stdout.lower()


def test_missing_repo_arg_exits_nonzero():
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-prune-noop"), "exit 0\n")
    result = _run_prune(["--keep", "3"], bin_dir=bin_dir)
    assert result.returncode != 0
    assert "--repo is required" in result.stderr


def test_missing_keep_arg_exits_nonzero():
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-prune-noop"), "exit 0\n")
    result = _run_prune(["--repo", "nucpot-prod-api"], bin_dir=bin_dir)
    assert result.returncode != 0
    assert "--keep is required" in result.stderr


def test_unknown_arg_exits_two():
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-prune-noop"), "exit 0\n")
    result = _run_prune(
        ["--repo", "x", "--keep", "3", "--bogus-flag"],
        bin_dir=bin_dir,
    )
    assert result.returncode == 2
    assert "Unknown arg" in result.stderr


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_keep_must_be_positive_int(bad):
    bin_dir = _write_fake_docker(Path("/tmp/nfmd-prune-noop-2"), "exit 0\n")
    result = _run_prune(["--repo", "x", "--keep", bad], bin_dir=bin_dir)
    assert result.returncode != 0, f"--keep {bad!r} should be rejected"


# ---------------------------------------------------------------------------
# 2. NO_CANDIDATES — repository has no candidate tags
# ---------------------------------------------------------------------------


def test_no_candidate_tags_exits_zero_no_rmi():
    rows = [
        ("nucpot-prod-api", "caedcc9abcdef", "img-1", "2026-08-21 04:00:00 +0000 UTC"),
        ("nucpot-prod-api", "latest",      "img-2", "2026-08-21 04:00:00 +0000 UTC"),
    ]
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-prune-empty"),
        _fake_docker_with_images(rows),
    )
    log_path = bin_dir / "rmi.log"
    _reset_log(log_path)
    result = _run_prune(
        ["--repo", "nucpot-prod-api", "--keep", "3"],
        bin_dir=bin_dir,
        env_extra={"DOCKER_RMI_LOG": str(log_path)},
    )
    assert result.returncode == 0, result.stderr
    assert _read_rmi_log(log_path) == []


# ---------------------------------------------------------------------------
# 3. UNDER_KEEP — fewer candidates than --keep
# ---------------------------------------------------------------------------


def test_under_keep_exits_zero_no_rmi():
    rows = [
        ("nucpot-prod-api", "candidate-aaaa", "img-a", "2026-08-21 06:00:00 +0000 UTC"),
        ("nucpot-prod-api", "candidate-bbbb", "img-b", "2026-08-21 07:00:00 +0000 UTC"),
    ]
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-prune-under"),
        _fake_docker_with_images(rows),
    )
    log_path = bin_dir / "rmi.log"
    _reset_log(log_path)
    result = _run_prune(
        ["--repo", "nucpot-prod-api", "--keep", "3"],
        bin_dir=bin_dir,
        env_extra={"DOCKER_RMI_LOG": str(log_path)},
    )
    assert result.returncode == 0, result.stderr
    assert _read_rmi_log(log_path) == []


# ---------------------------------------------------------------------------
# 4. TRIM_OVERFLOW — more candidates than --keep; oldest removed
# ---------------------------------------------------------------------------


def test_overflow_keeps_newest_drops_oldest():
    rows = [
        ("nucpot-prod-api", "candidate-1111111", "img-1", "2026-08-21 00:00:00 +0000 UTC"),
        ("nucpot-prod-api", "candidate-2222222", "img-2", "2026-08-21 02:00:00 +0000 UTC"),
        ("nucpot-prod-api", "candidate-3333333", "img-3", "2026-08-21 04:00:00 +0000 UTC"),
        ("nucpot-prod-api", "candidate-4444444", "img-4", "2026-08-21 06:00:00 +0000 UTC"),
        ("nucpot-prod-api", "candidate-5555555", "img-5", "2026-08-21 08:00:00 +0000 UTC"),
        ("nucpot-prod-api", "candidate-6666666", "img-6", "2026-08-21 10:00:00 +0000 UTC"),
    ]
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-prune-overflow"),
        _fake_docker_with_images(rows),
    )
    log_path = bin_dir / "rmi.log"
    _reset_log(log_path)
    result = _run_prune(
        ["--repo", "nucpot-prod-api", "--keep", "3"],
        bin_dir=bin_dir,
        env_extra={"DOCKER_RMI_LOG": str(log_path)},
    )
    assert result.returncode == 0, result.stderr
    removed = _read_rmi_log(log_path)
    assert sorted(removed) == sorted(["img-1", "img-2", "img-3"])
    for kept in ("img-4", "img-5", "img-6"):
        assert kept not in removed


# ---------------------------------------------------------------------------
# 5. MIXED_TAGS — candidate-* coexists with SHA tags; only candidates pruned
# ---------------------------------------------------------------------------


def test_mixed_tags_leaves_sha_tags_alone():
    rows = [
        ("nucpot-prod-api", "candidate-old1", "img-c1", "2026-08-21 00:00:00 +0000 UTC"),
        ("nucpot-prod-api", "candidate-old2", "img-c2", "2026-08-21 02:00:00 +0000 UTC"),
        ("nucpot-prod-api", "candidate-old3", "img-c3", "2026-08-21 04:00:00 +0000 UTC"),
        ("nucpot-prod-api", "candidate-new",  "img-c4", "2026-08-21 08:00:00 +0000 UTC"),
        ("nucpot-prod-api", "caedcc9...",     "img-s1", "2026-08-21 04:30:00 +0000 UTC"),
        ("nucpot-prod-api", "9a210e8...",     "img-s2", "2026-08-21 06:00:00 +0000 UTC"),
        ("nucpot-prod-api", "latest",         "img-l1", "2026-08-21 06:00:00 +0000 UTC"),
    ]
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-prune-mixed"),
        _fake_docker_with_images(rows),
    )
    log_path = bin_dir / "rmi.log"
    _reset_log(log_path)
    result = _run_prune(
        ["--repo", "nucpot-prod-api", "--keep", "2"],
        bin_dir=bin_dir,
        env_extra={"DOCKER_RMI_LOG": str(log_path)},
    )
    assert result.returncode == 0, result.stderr
    removed = _read_rmi_log(log_path)
    assert sorted(removed) == sorted(["img-c1", "img-c2"])
    for sha_id in ("img-s1", "img-s2", "img-l1", "img-c3", "img-c4"):
        assert sha_id not in removed


# ---------------------------------------------------------------------------
# 6. MULTI_REPO — caller iterates api + lightrag + web
# ---------------------------------------------------------------------------


def test_multi_repo_each_pruned_independently():
    rows = [
        ("nucpot-prod-api",      "candidate-a1", "api-img-1", "2026-08-21 00:00:00 +0000 UTC"),
        ("nucpot-prod-api",      "candidate-a2", "api-img-2", "2026-08-21 02:00:00 +0000 UTC"),
        ("nucpot-prod-api",      "candidate-a3", "api-img-3", "2026-08-21 04:00:00 +0000 UTC"),
        ("nucpot-prod-api",      "candidate-a4", "api-img-4", "2026-08-21 06:00:00 +0000 UTC"),
        ("nucpot-prod-web",      "candidate-w1", "web-img-1", "2026-08-21 06:00:00 +0000 UTC"),
        ("nucpot-prod-lightrag", "candidate-l1", "lr-img-1",  "2026-08-21 00:00:00 +0000 UTC"),
        ("nucpot-prod-lightrag", "candidate-l2", "lr-img-2",  "2026-08-21 02:00:00 +0000 UTC"),
        ("nucpot-prod-lightrag", "candidate-l3", "lr-img-3",  "2026-08-21 04:00:00 +0000 UTC"),
    ]
    bin_dir = _write_fake_docker(
        Path("/tmp/nfmd-prune-multi"),
        _fake_docker_with_images(rows),
    )
    log_path = bin_dir / "rmi.log"
    _reset_log(log_path)
    removed: list[str] = []
    for repo, keep in (
        ("nucpot-prod-api", "2"),
        ("nucpot-prod-web", "2"),
        ("nucpot-prod-lightrag", "1"),
    ):
        result = _run_prune(
            ["--repo", repo, "--keep", keep],
            bin_dir=bin_dir,
            env_extra={"DOCKER_RMI_LOG": str(log_path)},
        )
        assert result.returncode == 0, f"{repo}: {result.stderr}"

    removed = _read_rmi_log(log_path)
    assert "api-img-1" in removed
    assert "api-img-2" in removed
    assert "api-img-3" not in removed
    assert "api-img-4" not in removed
    assert "web-img-1" not in removed
    assert "lr-img-1" in removed
    assert "lr-img-2" in removed
    assert "lr-img-3" not in removed
