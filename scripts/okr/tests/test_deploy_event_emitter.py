"""Unit tests for scripts/lib/deploy_event_emitter.py — the Python mirror
of scripts/lib/deploy_event.sh used by the production durable producer
(NFM-2110, ADR-KR3-A1 C6.1.1).

Why a Python mirror: the bash lib sources from ``scripts/lib/deploy_event.sh``
on the self-hosted runner, but the production durable producer runs on a
hosted ``ubuntu-latest`` runner where sourcing the bash lib does not work.
The Python emitter assembles the same §3.1 schema and emits it as a JSON
object to stdout — the workflow captures stdout and uploads as a GHA
artifact. It deliberately does NOT write to the JSONL file (the collector
handles that per C6.1.1 stage 2).
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE = REPO_ROOT / "scripts" / "lib" / "deploy_event_emitter.py"

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

# GHA `needs.<job>.result` enum values. Per ADR-KR3-A1 C6.1.1, only `success`
# maps to first_pass_success=true; failure / skipped / cancelled are all false.
# A skipped or cancelled smoke test is not a first-pass success.
GHA_RESULT_TO_FIRST_PASS = {
    "success": True,
    "failure": False,
    "skipped": False,
    "cancelled": False,
}

_CLI_BASE = [
    "--environment", "production",
    "--triggered-by", "alice",
    "--commit-sha", "abc1234",
    "--first-pass-success", "true",
    "--health-gate-first-poll-passed", "true",
    "--rollback-triggered", "false",
    "--skip-flag-used", "false",
    "--duration-ms", "4321",
]


def _import_module():
    """Import the emitter module by file path (no third-party deps)."""
    spec = importlib.util.spec_from_file_location("deploy_event_emitter", MODULE)
    assert spec is not None and spec.loader is not None, MODULE
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def emitter():
    return _import_module()


def _event(**overrides):
    """Build a baseline event with sensible defaults; tests override fields."""
    kwargs = dict(
        environment="production",
        triggered_by="alice",
        commit_sha="abc1234",
        first_pass_success=True,
        health_gate_first_poll_passed=True,
        rollback_triggered=False,
        skip_flag_used=False,
        duration_ms=4321,
    )
    kwargs.update(overrides)
    return _import_module().build_event(**kwargs)


class TestEventSchema:
    def test_has_exactly_the_spec_fields(self) -> None:
        event = _event()
        assert set(event) == SCHEMA_FIELDS
        assert len(event) == 10

    def test_environment_is_production_literal(self) -> None:
        assert _event(environment="production")["environment"] == "production"

    def test_scalar_fields_round_trip(self) -> None:
        event = _event()
        assert event["triggered_by"] == "alice"
        assert event["commit_sha"] == "abc1234"
        assert event["duration_ms"] == 4321

    def test_event_id_is_uuid_v4(self) -> None:
        event = _event()
        assert _UUID4_RE.match(event["event_id"]), event["event_id"]

    def test_event_ids_are_unique_across_calls(self) -> None:
        ids = {_event()["event_id"] for _ in range(20)}
        assert len(ids) == 20

    def test_ts_is_iso8601_utc(self) -> None:
        event = _event()
        assert _ISO8601_UTC_RE.match(event["ts"]), event["ts"]


class TestBooleanLiteralisation:
    """Booleans must serialise as JSON ``true``/``false`` (lowercase), not
    Python ``True``/``False``. The bash lib achieves this with
    ``_deploy_event_bool``; the Python mirror must match byte-for-byte so
    a downstream JSON consumer cannot distinguish the two producers.
    """

    def test_json_dumps_emits_lowercase_literals(self) -> None:
        event = _event(
            first_pass_success=True,
            rollback_triggered=False,
        )
        raw = json.dumps(event)
        assert '"first_pass_success": true' in raw
        assert '"rollback_triggered": false' in raw
        # Python repr must not leak into the JSON output.
        assert "True" not in raw
        assert "False" not in raw

    def test_string_true_is_coerced_to_json_true(self) -> None:
        """GHA ``needs.*`` values are strings; the producer passes them as
        strings from ``${{ needs.smoke-test.result }}``. ``"true"`` must
        become the JSON boolean ``true`` (mirroring bash ``_deploy_event_bool``)."""
        event = _event(
            first_pass_success="true",
            health_gate_first_poll_passed="true",
            rollback_triggered="false",
            skip_flag_used="false",
        )
        assert event["first_pass_success"] is True
        assert event["health_gate_first_poll_passed"] is True
        assert event["rollback_triggered"] is False
        assert event["skip_flag_used"] is False

    def test_unrecognised_string_defaults_to_false(self) -> None:
        """A metric that measures the team must not be gameable by a
        malformed flag silently reading as success. Mirrors bash."""
        event = _event(
            first_pass_success="garbage",
            health_gate_first_poll_passed="",
            rollback_triggered=None,
            skip_flag_used="yes-please",  # not in the bash truth set
        )
        assert event["first_pass_success"] is False
        assert event["health_gate_first_poll_passed"] is False
        assert event["rollback_triggered"] is False
        assert event["skip_flag_used"] is False


class TestFirstPassSuccessFromGHAEnum:
    """``first_pass_success`` must reflect ``needs.smoke-test.result`` per
    ADR-KR3-A1 C6.1.1. The GHA job-result enum is one of
    ``success``/``failure``/``skipped``/``cancelled``.
    """

    @pytest.mark.parametrize(
        "smoke_result,expected",
        sorted(GHA_RESULT_TO_FIRST_PASS.items()),
    )
    def test_gha_enum_to_first_pass(self, smoke_result: str, expected: bool) -> None:
        # The workflow converts `needs.smoke-test.result` (a GHA enum string)
        # to the boolean first_pass_success before invoking the CLI. The
        # emitter therefore accepts the literal "true"/"false" string and
        # must produce the corresponding JSON boolean.
        event = _event(
            first_pass_success="true" if expected else "false",
        )
        assert event["first_pass_success"] is expected

    def test_all_four_enum_values_are_covered(self) -> None:
        # Pin the enum mapping — adding a new GHA result string should
        # require updating both the map and the parametrize above.
        assert set(GHA_RESULT_TO_FIRST_PASS) == {"success", "failure", "skipped", "cancelled"}


class TestResolvedPath:
    """The emitter reads NFMD_DEPLOY_EVENTS_PATH with a sane fallback. It
    does NOT write to the file (the collector owns writes per C6.1.1).
    """

    def test_default_path_is_repo_docker_deploy_events(self, monkeypatch) -> None:
        monkeypatch.delenv("NFMD_DEPLOY_EVENTS_PATH", raising=False)
        assert (
            _import_module().resolve_events_path()
            == REPO_ROOT / "docker" / ".deploy-events.jsonl"
        )

    def test_env_override_wins(self, monkeypatch, tmp_path: Path) -> None:
        target = tmp_path / "custom.jsonl"
        monkeypatch.setenv("NFMD_DEPLOY_EVENTS_PATH", str(target))
        assert _import_module().resolve_events_path() == target


class TestCLI:
    """The CLI emits a single JSON object to stdout (one event per run).
    The workflow captures stdout and uploads as a GHA artifact named
    ``nfm-deploy-event-<run_id>-<attempt>.json``.
    """

    def test_emits_single_json_object_to_stdout(self) -> None:
        result = subprocess.run(
            [sys.executable, str(MODULE), *_CLI_BASE],
            capture_output=True,
            text=True,
            check=True,
        )
        event = json.loads(result.stdout)
        assert set(event) == SCHEMA_FIELDS
        assert event["environment"] == "production"
        assert event["rollback_triggered"] is False

    def test_does_not_write_to_events_path(self, tmp_path: Path) -> None:
        """The producer must NOT touch the JSONL. Even when the env var
        points at a writable path, the CLI must not create the file
        (C6.1.1: producer is artifact-upload only; collector owns writes).
        """
        target = tmp_path / "events.jsonl"
        result = subprocess.run(
            [sys.executable, str(MODULE), *_CLI_BASE],
            capture_output=True,
            text=True,
            check=True,
            env={"NFMD_DEPLOY_EVENTS_PATH": str(target)},
        )
        assert not target.exists(), (
            "Producer must NOT write to NFMD_DEPLOY_EVENTS_PATH; the "
            "collector workflow owns writes."
        )
        # The JSON event was still emitted to stdout.
        assert json.loads(result.stdout)["environment"] == "production"

    def test_first_pass_failure_runs_clean(self) -> None:
        """Boolean values are JSON booleans, not strings — round-trip via
        the CLI to guarantee the contract survives shell argument parsing."""
        result = subprocess.run(
            [
                sys.executable, str(MODULE),
                "--environment", "production",
                "--triggered-by", "alice",
                "--commit-sha", "abc1234",
                "--first-pass-success", "false",   # smoke-test failed
                "--health-gate-first-poll-passed", "false",
                "--rollback-triggered", "false",
                "--skip-flag-used", "false",
                "--duration-ms", "4321",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        event = json.loads(result.stdout)
        assert event["first_pass_success"] is False
        raw = result.stdout
        assert '"first_pass_success": false' in raw
        assert "False" not in raw
