"""PATH-shim integration test for ``scripts/lib/collect_prod_run.sh``.

The Code Reviewer (2026-07-30) flagged that the bash orchestrator
exits silently when the API call fails, and that the Python unit
suite never exercises ``collect_prod_run.sh`` — which is exactly why
the ``.workflow.id`` no-op bug escaped detection in NFM-2111 round 1.

This test puts a fake ``gh`` binary on PATH so we can exercise the
full bash orchestrator end-to-end (workflow-id resolve → run
enumeration → artifact listing → zip download → dispatch to the
Python collector) without talking to the real GitHub API.

Two regression cases:

1. **Happy path** — fake gh returns a valid workflow object and a
   single run with one artifact zip containing a valid event JSON.
   The collector must write exactly one JSONL line and one processed
   ledger row.

2. **API failure visibility** — fake gh exits non-zero on the
   workflow-id lookup. The orchestrator must NOT silently ``exit 0``;
   it must propagate the failure so the GHA run reports red. This
   is the regression test for the reviewer's CRITICAL #1 part 2.
"""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_LIB = REPO_ROOT / "scripts" / "lib"
ORCHESTRATOR = SCRIPTS_LIB / "collect_prod_run.sh"


# ---------------------------------------------------------------------------
# Fake ``gh`` factory
# ---------------------------------------------------------------------------


def _write_fake_gh(
    bin_dir: Path,
    *,
    workflow_id: int = 300666980,
    run_ids: list[int] | None = None,
    archive_url_to_zip: dict[str, Path] | None = None,
    fail_on: str | None = None,
    artifact_name: str = "nfm-deploy-event-123456789-1",
) -> Path:
    """Write a ``gh`` shim into ``bin_dir`` that handles ``gh api``.

    The shim inspects argv and emits a canned response based on the
    endpoint URL. Only the modes the orchestrator uses are implemented.
    Uses portable bash 3.2-compatible syntax (no associative arrays).

    The default ``artifact_name`` mirrors the real producer-side naming
    from ``.github/workflows/production-deployment.yml`` line 592:
    ``nfm-deploy-event-<run_id>-<run_attempt>``. Crucially, the artifact
    name has no ``.json`` suffix — the ``.json`` file is only inside
    the uploaded zip. NFM-2144 root cause: an earlier revision of the
    orchestrator's jq filter required ``endswith(".json")`` and
    therefore rejected every producer artifact.
    """
    run_ids = run_ids if run_ids is not None else [123456789]
    archive_url_to_zip = archive_url_to_zip or {}
    # Encode the URL→zip map as newline-separated lines in a sidecar
    # file that the shim reads. Avoids associative arrays.
    map_file = bin_dir / "archive_map.txt"
    map_file.write_text(
        "\n".join(f"{u}\t{p}" for u, p in archive_url_to_zip.items()) + "\n",
        encoding="utf-8",
    )
    runs_block = " ".join(str(r) for r in run_ids)

    shim = bin_dir / "gh"
    shim.write_text(
        f"""#!/usr/bin/env bash
# Fake gh for collect_prod_run.sh PATH-shim tests. Bash 3.2 compatible.
set -e

if [ "$1" != "api" ]; then
  echo "fake gh: unsupported subcommand $1" >&2
  exit 2
fi
shift  # drop 'api'

# Strip a trailing ``--jq <filter>`` pair. Walk args left-to-right;
# the orchestrator puts ``--jq <filter>`` at the very end, so the
# endpoint is always the first positional arg, and ``--jq`` will be
# the second-to-last arg we see.
endpoint=""
filter=""
saw_jq=0
prev=""
for arg in "$@"; do
  if [ "$saw_jq" -eq 1 ]; then
    filter="$arg"
    break
  fi
  if [ "$arg" = "--jq" ]; then
    saw_jq=1
    continue
  fi
  endpoint="$arg"
  prev="$arg"
done

if [ -n "${{FAIL_ON:-}}" ] && [[ "$endpoint" == *"${{FAIL_ON}}"* ]]; then
  echo "fake gh: simulated API failure on $endpoint" >&2
  exit 22
fi

# Workflow-id lookup: returns flat object with ``id``.
if [[ "$endpoint" == *"/actions/workflows/"* ]] && [[ "$endpoint" != *"/runs"* ]]; then
  if [ "$filter" = ".id" ]; then
    printf '{workflow_id}'
    exit 0
  fi
  printf '{{"id":{workflow_id},"name":"Production Deployment","path":".github/workflows/production-deployment.yml","state":"active"}}'
  exit 0
fi

# Run enumeration.
if [[ "$endpoint" == *"/runs?per_page=50"* ]]; then
  if [ "$filter" = ".workflow_runs[].id" ]; then
    for rid in {runs_block}; do
      printf '%s\\n' "$rid"
    done
    exit 0
  fi
  printf '{{"workflow_runs":['
  first=1
  for rid in {runs_block}; do
    if [ $first -eq 0 ]; then printf ','; fi
    printf '{{"id":%s}}' "$rid"
    first=0
  done
  printf ']}}'
  exit 0
fi

# Artifact listing.
if [[ "$endpoint" == *"/artifacts"* ]]; then
  map_path="${{FAKE_GH_BIN_DIR}}/archive_map.txt"
  # Use [[ ]] (no shell glob expansion) instead of [ ... = pattern ] which
  # would try to expand the leading/trailing ``*`` as filenames in cwd and
  # exit non-zero on macOS bash when no match exists. NFM-2144 surfaced
  # this latent bug — the happy-path test reaches this branch whereas the
  # failure-path test never did.
  if [[ "$filter" == *".archive_download_url"* ]]; then
    if [ -f "$map_path" ]; then
      while IFS=$'\\t' read -r url zip_path; do
        [ -z "$url" ] && continue
        printf '%s\\n' "$url"
      done < "$map_path"
    fi
    exit 0
  fi
  printf '{{"artifacts":['
  first=1
  if [ -f "$map_path" ]; then
    while IFS=$'\\t' read -r url zip_path; do
      [ -z "$url" ] && continue
      if [ $first -eq 0 ]; then printf ','; fi
      name="${{FAKE_ARTIFACT_NAME:-nfm-deploy-event-123456789-1}}"
      printf '{{"id":1,"name":"%s","archive_download_url":"%s"}}' "$name" "$url"
      first=0
    done < "$map_path"
  fi
  printf ']}}'
  exit 0
fi

# Archive download: print the zip bytes for the matching URL.
map_path="${{FAKE_GH_BIN_DIR}}/archive_map.txt"
if [ -f "$map_path" ]; then
  while IFS=$'\\t' read -r url zip_path; do
    [ -z "$url" ] && continue
    if [ "$endpoint" = "$url" ]; then
      cat "$zip_path"
      exit 0
    fi
  done < "$map_path"
fi

echo "fake gh: unhandled endpoint $endpoint" >&2
exit 1
""",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def _make_event_zip(zip_path: Path, event: dict[str, Any]) -> Path:
    """Write a zip containing a single ``event.json`` to ``zip_path``."""
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("event.json", json.dumps(event, separators=(",", ":")))
    return zip_path


def _valid_event() -> dict[str, Any]:
    return {
        "event_id": "11111111-2222-4333-8444-555666777888",
        "ts": "2026-07-30T10:00:00Z",
        "environment": "production",
        "triggered_by": "alice",
        "commit_sha": "abcdef0",
        "first_pass_success": True,
        "health_gate_first_poll_passed": True,
        "rollback_triggered": False,
        "skip_flag_used": False,
        "duration_ms": 4321,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBashOrchestrator:
    """End-to-end exercise of ``collect_prod_run.sh`` against a fake gh."""

    def test_workflow_id_lookup_failure_exits_nonzero(self, tmp_path: Path) -> None:
        """Reviewer finding CRITICAL #1 part 2: when the API call fails,
        the orchestrator must propagate the failure (exit != 0), not
        silently ``exit 0`` and report a green run with zero events.

        Pre-fix, ``2>/dev/null || echo ""`` would yield an empty value
        → "workflow not found — nothing to do" → ``exit 0`` → GHA
        green-light, zero events collected.
        """
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fail_on = "/actions/workflows/production-deployment.yml"
        _write_fake_gh(bin_dir, fail_on=fail_on)

        jsonl = tmp_path / "events.jsonl"
        processed = tmp_path / "events.jsonl.processed"

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GITHUB_REPOSITORY": "Etoile04/nucpot",
            "NFMD_DEPLOY_EVENTS_PATH": str(jsonl),
            "NFMD_DEPLOY_EVENTS_PROCESSED_PATH": str(processed),
            "FAKE_GH_BIN_DIR": str(bin_dir),
            "FAIL_ON": fail_on,
        }

        result = subprocess.run(
            ["bash", str(ORCHESTRATOR)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        # Must NOT silently exit 0 — that was the bug.
        assert result.returncode != 0, (
            f"orchestrator should have failed loudly; rc={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        # And the visible-failure message must reach stderr (not be swallowed).
        assert "FAILED" in result.stderr, (
            f"expected visible failure message; got stderr={result.stderr!r}"
        )
        # And no event should have been collected.
        assert not jsonl.exists() or jsonl.read_text() == ""

    def test_artifact_name_run_id_attempt_suffix_accepted(self, tmp_path: Path) -> None:
        """Regression: producer uploads artifacts named
        ``nfm-deploy-event-<run_id>-<run_attempt>`` with NO ``.json``
        suffix on the artifact name (the ``.json`` file lives inside
        the uploaded zip). The orchestrator's jq filter must accept
        this naming so the event is actually processed into the JSONL
        and the processed ledger.

        Pre-fix, the filter required ``endswith(".json")`` and rejected
        every real producer artifact, so 50 production runs were all
        recorded as ``missing`` and zero events were collected (the
        exact failure mode observed in NFM-2144 collector run 30527829952).
        """
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        # Build a real-world event zip and point the fake gh at it.
        archive_url = "https://api.github.com/fake/download/event.zip"
        zip_path = tmp_path / "event.zip"
        event = _valid_event()
        _make_event_zip(zip_path, event)
        _write_fake_gh(
            bin_dir,
            run_ids=[123456789],
            archive_url_to_zip={archive_url: zip_path},
        )

        jsonl = tmp_path / "events.jsonl"
        processed = tmp_path / "events.jsonl.processed"

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GITHUB_REPOSITORY": "Etoile04/nucpot",
            "NFMD_DEPLOY_EVENTS_PATH": str(jsonl),
            "NFMD_DEPLOY_EVENTS_PROCESSED_PATH": str(processed),
            "FAKE_GH_BIN_DIR": str(bin_dir),
            # The default artifact_name in _write_fake_gh already matches
            # the real producer pattern, but set this explicitly so the
            # intent is documented at the test site.
            "FAKE_ARTIFACT_NAME": "nfm-deploy-event-123456789-1",
        }

        result = subprocess.run(
            ["bash", str(ORCHESTRATOR)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, (
            f"orchestrator should have succeeded; rc={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        # No "missing" ledger row — the artifact was actually processed.
        assert "[collect-prod-run] run_id=123456789 no nfm-deploy-event" not in (result.stdout), (
            "orchestrator recorded the real producer artifact as missing; "
            "the jq filter is rejecting artifacts without a '.json' suffix"
        )
        # JSONL gains exactly one line.
        assert jsonl.exists(), "JSONL should exist after a successful collect"
        jsonl_lines = [ln for ln in jsonl.read_text().splitlines() if ln.strip()]
        assert len(jsonl_lines) == 1, (
            f"expected exactly 1 JSONL line, got {len(jsonl_lines)}: {jsonl_lines!r}"
        )
        # The single JSONL line is the valid event we put in the zip.
        parsed = json.loads(jsonl_lines[0])
        assert parsed == event, f"JSONL line does not match the event we processed: {parsed!r}"
        # The processed ledger gains exactly one row matching
        # ``<run_id>\t<sha256>\t<processed>``.
        assert processed.exists(), "ledger should exist after a successful collect"
        ledger_lines = [ln for ln in processed.read_text().splitlines() if ln.strip()]
        assert len(ledger_lines) == 1, (
            f"expected exactly 1 ledger row, got {len(ledger_lines)}: {ledger_lines!r}"
        )
        row = ledger_lines[0].split("\t")
        assert len(row) == 3, f"ledger row must have 3 tab-separated fields: {row!r}"
        assert row[0] == "123456789", f"first field is run_id, got {row[0]!r}"
        assert row[2] == "processed", f"third field is status, got {row[2]!r}"
        # sha256 field is 64 hex chars (matches the spec).
        assert len(row[1]) == 64 and all(c in "0123456789abcdef" for c in row[1]), (
            f"second field is sha256 (64 hex), got {row[1]!r}"
        )
