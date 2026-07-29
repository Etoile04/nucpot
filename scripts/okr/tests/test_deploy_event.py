"""Tests for scripts/lib/deploy_event.sh — the shared KR-3 deploy-event writer.

NFM-2042. The writer is a sourceable shell helper used by both
``scripts/staging_deploy.sh`` and ``.github/workflows/production-deployment.yml``.
These tests drive it through ``bash`` against a temp JSONL path.

Contract under test:
- ``deploy_event_emit`` appends exactly one JSONL line per call.
- The line carries the 10 fields fixed by NFM-2035 spec section 3.1.
- Booleans serialise as JSON booleans; ``duration_ms`` as a JSON number.
- The writer NEVER fails its caller (acceptance criterion 5): a write error
  warns on stderr and returns 0.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "scripts" / "lib" / "deploy_event.sh"

SCHEMA_FIELDS = frozenset({
    "event_id",
    "ts",
    "environment",
    "triggered_by",
    "commit_sha",
    "first_pass_success",
    "health_gate_first_poll_passed",
    "rollback_triggered",
    "skip_flag_used",
    "duration_ms",
})

_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ISO8601_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin"

_DEFAULT_ARGS = (
    "--environment staging "
    "--triggered-by alice "
    "--commit-sha abc1234 "
    "--first-pass-success true "
    "--health-gate-first-poll-passed true "
    "--rollback-triggered false "
    "--skip-flag-used false "
    "--duration-ms 4321"
)


def run_bash(script: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={"PATH": _BASE_PATH, **env},
    )


def run_emit(
    events_path: Path, args: str = _DEFAULT_ARGS, repeat: int = 1
) -> subprocess.CompletedProcess[str]:
    """Source the writer and call ``deploy_event_emit`` ``repeat`` times."""
    calls = "\n".join([f"deploy_event_emit {args}"] * repeat)
    return run_bash(
        f'set -euo pipefail\n. "{LIB}"\n{calls}\n',
        NFMD_DEPLOY_EVENTS_PATH=str(events_path),
    )


def read_events(events_path: Path) -> list[dict]:
    lines = [ln for ln in events_path.read_text().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


class TestEmitWritesOneLine:
    def test_appends_exactly_one_line(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        result = run_emit(events)
        assert result.returncode == 0, result.stderr
        assert len(read_events(events)) == 1

    def test_second_call_appends_rather_than_truncates(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        run_emit(events, repeat=2)
        assert len(read_events(events)) == 2

    def test_creates_parent_directory_when_missing(self, tmp_path: Path) -> None:
        events = tmp_path / "docker" / "nested" / ".deploy-events.jsonl"
        result = run_emit(events)
        assert result.returncode == 0, result.stderr
        assert events.exists()
        assert len(read_events(events)) == 1


class TestEventSchema:
    def test_has_exactly_the_spec_fields(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        run_emit(events)
        assert set(read_events(events)[0]) == SCHEMA_FIELDS

    def test_event_id_is_uuid_v4(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        run_emit(events)
        assert _UUID4_RE.match(read_events(events)[0]["event_id"])

    def test_event_ids_are_unique_across_calls(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        run_emit(events, repeat=3)
        ids = [e["event_id"] for e in read_events(events)]
        assert len(set(ids)) == 3

    def test_ts_is_iso8601_utc(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        run_emit(events)
        assert _ISO8601_UTC_RE.match(read_events(events)[0]["ts"])

    def test_scalar_fields_round_trip(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        run_emit(events)
        event = read_events(events)[0]
        assert event["environment"] == "staging"
        assert event["triggered_by"] == "alice"
        assert event["commit_sha"] == "abc1234"

    def test_booleans_are_json_booleans_not_strings(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        run_emit(events)
        event = read_events(events)[0]
        assert event["first_pass_success"] is True
        assert event["health_gate_first_poll_passed"] is True
        assert event["rollback_triggered"] is False
        assert event["skip_flag_used"] is False

    def test_duration_ms_is_a_number(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        run_emit(events)
        assert read_events(events)[0]["duration_ms"] == 4321

    def test_false_first_pass_success_round_trips(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        args = _DEFAULT_ARGS.replace(
            "--first-pass-success true", "--first-pass-success false"
        ).replace("--rollback-triggered false", "--rollback-triggered true")
        run_emit(events, args=args)
        event = read_events(events)[0]
        assert event["first_pass_success"] is False
        assert event["rollback_triggered"] is True


class TestRobustness:
    """The writer must never break a deploy (acceptance criterion 5)."""

    def test_write_failure_warns_but_returns_zero(self, tmp_path: Path) -> None:
        # A path whose parent is a regular file can never be created.
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory\n")
        result = run_emit(blocker / "sub" / "events.jsonl")
        assert result.returncode == 0
        assert "deploy-event" in result.stderr.lower()

    def test_quotes_in_triggered_by_do_not_corrupt_json(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        args = _DEFAULT_ARGS.replace(
            "--triggered-by alice", "--triggered-by 'bob\"quote'"
        )
        run_emit(events, args=args)
        assert read_events(events)[0]["triggered_by"] == 'bob"quote'

    def test_unknown_boolean_value_defaults_to_false(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        args = _DEFAULT_ARGS.replace("--skip-flag-used false", "--skip-flag-used garbage")
        run_emit(events, args=args)
        assert read_events(events)[0]["skip_flag_used"] is False

    def test_non_numeric_duration_defaults_to_zero(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        args = _DEFAULT_ARGS.replace("--duration-ms 4321", "--duration-ms abc")
        run_emit(events, args=args)
        assert read_events(events)[0]["duration_ms"] == 0

    def test_missing_required_arg_still_returns_zero(self, tmp_path: Path) -> None:
        """Never fail the caller, even on a programming error at the call site."""
        events = tmp_path / "events.jsonl"
        args = _DEFAULT_ARGS.replace("--commit-sha abc1234", "")
        result = run_emit(events, args=args)
        assert result.returncode == 0


class TestDefaultPath:
    def test_defaults_to_repo_docker_deploy_events(self) -> None:
        result = run_bash(f'set -euo pipefail\n. "{LIB}"\ndeploy_event_path\n')
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(REPO_ROOT / "docker" / ".deploy-events.jsonl")

    def test_env_override_wins(self, tmp_path: Path) -> None:
        target = tmp_path / "custom.jsonl"
        result = run_bash(
            f'set -euo pipefail\n. "{LIB}"\ndeploy_event_path\n',
            NFMD_DEPLOY_EVENTS_PATH=str(target),
        )
        assert result.stdout.strip() == str(target)
