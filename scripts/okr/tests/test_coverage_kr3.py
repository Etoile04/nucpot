"""Tests for coverage_kr3.py — KR-COMPANY-3 deployment success rate.

NFM-2042. Reads the append-only deploy-event JSONL written by
``scripts/lib/deploy_event.sh`` and computes

    value = first_pass_success_count / total_events

The critical property (issue constraint C1): when the file is absent or empty
the metric is ``None`` with ``n == 0`` — never a fabricated 1.0, never a
ZeroDivisionError.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.okr.coverage_kr3 import (
    ENVIRONMENTS,
    KR3_TARGET,
    _default_prod_path,
    _resolve_prod_path,
    build_report,
    compute_value,
    filter_environment,
    filter_window,
    load_events,
    main,
)


def event(
    *,
    first_pass_success: bool = True,
    ts: str = "2026-07-20T12:00:00Z",
    environment: str = "staging",
) -> dict:
    """A schema-complete synthetic deploy event."""
    return {
        "event_id": "3f2a1c88-9d4e-4b17-a0c3-5e6f7a8b9c01",
        "ts": ts,
        "environment": environment,
        "triggered_by": "alice",
        "commit_sha": "abc1234",
        "first_pass_success": first_pass_success,
        "health_gate_first_poll_passed": first_pass_success,
        "rollback_triggered": not first_pass_success,
        "skip_flag_used": False,
        "duration_ms": 4321,
    }


def write_jsonl(path: Path, events: list[dict]) -> Path:
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


# ---------------------------------------------------------------------------
# load_events
# ---------------------------------------------------------------------------


class TestLoadEvents:
    def test_missing_file_yields_empty_list(self, tmp_path: Path) -> None:
        assert load_events(tmp_path / "nope.jsonl") == []

    def test_empty_file_yields_empty_list(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        path.write_text("")
        assert load_events(path) == []

    def test_reads_each_line_as_one_event(self, tmp_path: Path) -> None:
        path = write_jsonl(tmp_path / "e.jsonl", [event(), event(first_pass_success=False)])
        assert len(load_events(path)) == 2

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "e.jsonl"
        path.write_text(json.dumps(event()) + "\n\n   \n" + json.dumps(event()) + "\n")
        assert len(load_events(path)) == 2

    def test_skips_malformed_json_without_raising(self, tmp_path: Path) -> None:
        path = tmp_path / "e.jsonl"
        path.write_text(json.dumps(event()) + "\n{not json\n")
        assert len(load_events(path)) == 1

    def test_skips_events_missing_first_pass_success(self, tmp_path: Path) -> None:
        incomplete = {k: v for k, v in event().items() if k != "first_pass_success"}
        path = write_jsonl(tmp_path / "e.jsonl", [event(), incomplete])
        assert len(load_events(path)) == 1

    def test_skips_json_scalars_that_are_not_objects(self, tmp_path: Path) -> None:
        path = tmp_path / "e.jsonl"
        path.write_text('"a string"\n42\n' + json.dumps(event()) + "\n")
        assert len(load_events(path)) == 1


# ---------------------------------------------------------------------------
# compute_value
# ---------------------------------------------------------------------------


class TestComputeValue:
    def test_no_events_is_none_not_one(self) -> None:
        """The whole point of the KR: absence of data must not read as success."""
        assert compute_value([]) is None

    def test_all_successes(self) -> None:
        assert compute_value([event(), event()]) == 1.0

    def test_all_failures(self) -> None:
        assert compute_value([event(first_pass_success=False)] * 3) == 0.0

    def test_half_and_half(self) -> None:
        assert compute_value([event(), event(first_pass_success=False)]) == 0.5

    def test_one_of_three(self) -> None:
        events = [
            event(),
            event(first_pass_success=False),
            event(first_pass_success=False),
        ]
        assert compute_value(events) == 1 / 3

    def test_truthy_non_boolean_is_not_counted_as_success(self) -> None:
        """Only a real JSON ``true`` counts — no coercion of "true" strings."""
        sloppy = {**event(), "first_pass_success": "true"}
        assert compute_value([sloppy]) == 0.0


# ---------------------------------------------------------------------------
# filter_window
# ---------------------------------------------------------------------------


class TestFilterWindow:
    def test_unbounded_window_keeps_everything(self) -> None:
        events = [event(ts="2020-01-01T00:00:00Z"), event(ts="2030-01-01T00:00:00Z")]
        assert filter_window(events, None, None) == events

    def test_since_is_inclusive(self) -> None:
        assert len(filter_window([event(ts="2026-07-20T00:00:00Z")], "2026-07-20", None)) == 1

    def test_drops_events_before_since(self) -> None:
        events = [event(ts="2026-07-19T23:59:59Z"), event(ts="2026-07-20T00:00:00Z")]
        kept = filter_window(events, "2026-07-20", None)
        assert [e["ts"] for e in kept] == ["2026-07-20T00:00:00Z"]

    def test_until_is_inclusive_of_the_whole_day(self) -> None:
        assert len(filter_window([event(ts="2026-07-20T23:59:59Z")], None, "2026-07-20")) == 1

    def test_drops_events_after_until(self) -> None:
        assert filter_window([event(ts="2026-07-21T00:00:00Z")], None, "2026-07-20") == []

    def test_unparseable_ts_is_dropped_when_window_is_bounded(self) -> None:
        assert filter_window([{**event(), "ts": "not-a-date"}], "2026-07-01", None) == []

    def test_unparseable_ts_is_kept_when_window_is_unbounded(self) -> None:
        assert len(filter_window([{**event(), "ts": "not-a-date"}], None, None)) == 1

    def test_does_not_mutate_input(self) -> None:
        events = [event(ts="2026-07-20T12:00:00Z"), event(ts="2026-07-01T12:00:00Z")]
        before = json.dumps(events)
        filter_window(events, "2026-07-10", None)
        assert json.dumps(events) == before


# ---------------------------------------------------------------------------
# filter_environment (C6.1.4)
# ---------------------------------------------------------------------------


class TestFilterEnvironment:
    """The prod collector (C6.1.2) appends into the same JSONL as the staging
    writer, so from C6.1 onward the file is a mixed stream. Without this
    filter the KR conflates both environments and the v1 staging baseline
    silently shifts.
    """

    def test_environments_are_exactly_all_staging_and_production(self) -> None:
        # ADR-KR3-A2 §Consequences: ``all`` is the default to preserve the
        # v1 baseline (read the whole JSONL); ``staging`` and ``production``
        # are the explicit single-series filters.
        assert ENVIRONMENTS == ("all", "staging", "production")

    def test_keeps_only_the_named_environment(self) -> None:
        events = [
            event(environment="staging"),
            event(environment="production"),
            event(environment="staging"),
        ]
        assert len(filter_environment(events, "staging")) == 2
        assert len(filter_environment(events, "production")) == 1

    def test_match_is_exact_not_prefix(self) -> None:
        assert filter_environment([event(environment="staging-canary")], "staging") == []

    def test_drops_events_with_a_missing_environment_field(self) -> None:
        """A schema-incomplete row cannot be attributed to a stream, so it must
        not be silently counted into the default one."""
        no_env = {k: v for k, v in event().items() if k != "environment"}
        assert filter_environment([no_env], "staging") == []

    def test_does_not_mutate_input(self) -> None:
        events = [event(environment="staging"), event(environment="production")]
        before = json.dumps(events)
        filter_environment(events, "staging")
        assert json.dumps(events) == before


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


class TestBuildReport:
    def test_shape_is_exactly_the_five_spec_keys(self, tmp_path: Path) -> None:
        # The C6.1 contract adds an ``environment`` key to the report so
        # downstream consumers know which series the metric is computed
        # for. The original five keys are still present.
        path = write_jsonl(tmp_path / "e.jsonl", [event()])
        assert set(build_report(path, None, None)) == {
            "value",
            "target",
            "n",
            "computed_at",
            "source_window",
            "environment",
        }

    def test_target_is_the_kr3_threshold(self, tmp_path: Path) -> None:
        assert build_report(tmp_path / "absent.jsonl", None, None)["target"] == KR3_TARGET
        assert KR3_TARGET == 0.90

    def test_absent_file_reports_null_value_and_zero_n(self, tmp_path: Path) -> None:
        report = build_report(tmp_path / "absent.jsonl", None, None)
        assert report["value"] is None
        assert report["n"] == 0

    def test_two_event_file_reports_half(self, tmp_path: Path) -> None:
        path = write_jsonl(tmp_path / "e.jsonl", [event(), event(first_pass_success=False)])
        report = build_report(path, None, None)
        assert report["value"] == 0.5
        assert report["n"] == 2

    def test_computed_at_is_iso8601_utc(self, tmp_path: Path) -> None:
        computed_at = build_report(tmp_path / "absent.jsonl", None, None)["computed_at"]
        assert computed_at.endswith("Z")
        assert len(computed_at) == 20

    def test_source_window_echoes_the_bounds(self, tmp_path: Path) -> None:
        report = build_report(tmp_path / "absent.jsonl", "2026-07-01", "2026-07-30")
        assert report["source_window"] == {"since": "2026-07-01", "until": "2026-07-30"}

    def test_source_window_bounds_are_null_when_unbounded(self, tmp_path: Path) -> None:
        report = build_report(tmp_path / "absent.jsonl", None, None)
        assert report["source_window"] == {"since": None, "until": None}

    def test_n_counts_only_in_window_events(self, tmp_path: Path) -> None:
        path = write_jsonl(
            tmp_path / "e.jsonl",
            [event(ts="2026-01-01T00:00:00Z"), event(ts="2026-07-20T00:00:00Z")],
        )
        report = build_report(path, "2026-07-01", None)
        assert report["n"] == 1
        assert report["value"] == 1.0

    def test_environment_default_is_all_not_a_single_series(self, tmp_path: Path) -> None:
        """ADR-KR3-A2 §Consequences: the default filter is 'all', so a caller
        that says nothing gets the whole JSONL — staging + production —
        which preserves the v1 baseline (today's JSONL only has staging, so
        the result is identical to the v1 behaviour, but the issue is
        reserved explicitly going forward)."""
        path = write_jsonl(
            tmp_path / "e.jsonl",
            [event(environment="staging"), event(environment="production")],
        )
        report = build_report(path, None, None)
        assert report["n"] == 2
        assert report["environment"] == "all"

    def test_environment_is_added_to_the_report_shape(self, tmp_path: Path) -> None:
        """C6.1.4: the report tells the consumer which series the metric is
        for, so an explicit ``--environment`` value is echoed in the payload.
        The pre-change spec forbade the key; the new spec requires it."""
        report = build_report(tmp_path / "absent.jsonl", None, None, environment="production")
        assert report["environment"] == "production"

    def test_n_reflects_the_filtered_count_not_the_file_total(self, tmp_path: Path) -> None:
        path = write_jsonl(
            tmp_path / "e.jsonl",
            [event(environment="staging")] * 3 + [event(environment="production")] * 2,
        )
        assert build_report(path, None, None, environment="staging")["n"] == 3
        assert build_report(path, None, None, environment="production")["n"] == 2

    def test_each_environment_gets_its_own_value(self, tmp_path: Path) -> None:
        """Both streams in one file must not contaminate each other's rate."""
        path = write_jsonl(
            tmp_path / "e.jsonl",
            [
                event(environment="staging"),
                event(environment="staging", first_pass_success=False),
                event(environment="production"),
                event(environment="production"),
            ],
        )
        assert build_report(path, None, None, environment="staging")["value"] == 0.5
        assert build_report(path, None, None, environment="production")["value"] == 1.0

    def test_environment_filter_composes_with_the_date_window(self, tmp_path: Path) -> None:
        path = write_jsonl(
            tmp_path / "e.jsonl",
            [
                event(environment="staging", ts="2026-01-01T00:00:00Z"),
                event(environment="staging", ts="2026-07-20T00:00:00Z"),
                event(environment="production", ts="2026-07-20T00:00:00Z"),
            ],
        )
        report = build_report(path, "2026-07-01", None, environment="staging")
        assert report["n"] == 1

    def test_prod_only_file_reports_null_for_staging(self, tmp_path: Path) -> None:
        """Filtering to an absent stream is 'no data', not a fabricated 1.0."""
        path = write_jsonl(tmp_path / "e.jsonl", [event(environment="production")])
        report = build_report(path, None, None, environment="staging")
        assert report["value"] is None
        assert report["n"] == 0


# ---------------------------------------------------------------------------
# _resolve_prod_path / _default_prod_path (ADR-KR3 §C6.3.2)
# ---------------------------------------------------------------------------


class TestResolveProdPathFallback:
    """ADR-KR3 §C6.3.2 — the prod path fallback must not name a user.

    The C6.3.2 invariant is that the fallback must use runtime expansion
    (``Path.home()``), not a hardcoded literal like
    ``/Users/lwj04/.nfmd/master-deploy-events.jsonl``. The check below
    monkeypatches ``Path.home()`` to a synthetic path whose own string
    contains no operator username — if the module had a hardcoded
    literal anywhere, it would survive the monkeypatch and trip the
    assertion. (We cannot use ``tmp_path`` here because pytest's
    ``tmp_path`` itself embeds the host's username.)
    """

    _SYNTHETIC_HOME = Path("/opt/synthetic/nfmd-home")

    def test_fallback_contains_no_lwj04_literal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: self._SYNTHETIC_HOME))
        monkeypatch.delenv("NFMD_PROD_EVENTS_PATH", raising=False)

        resolved = _resolve_prod_path(None)
        assert "lwj04" not in str(resolved), (
            f"prod-path fallback {resolved!r} contains 'lwj04' — must use "
            "runtime Path.home() expansion, not a hardcoded username "
            "literal (ADR-KR3 §C6.3.2)"
        )

    def test_default_prod_path_contains_no_lwj04_literal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: self._SYNTHETIC_HOME))

        assert "lwj04" not in str(_default_prod_path())

    def test_fallback_contains_no_hardcoded_user_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The literal ``/Users/<name>`` and ``/home/<name>`` forms must
        never appear in the resolved fallback. Catches both the macOS
        and Linux conventions."""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: self._SYNTHETIC_HOME))
        monkeypatch.delenv("NFMD_PROD_EVENTS_PATH", raising=False)

        resolved_str = str(_resolve_prod_path(None))
        import re as _re

        offenders = _re.findall(r"(?:/Users/|/home/)[A-Za-z0-9._-]+/", resolved_str)
        assert not offenders, (
            f"prod-path fallback {resolved_str!r} contains a hardcoded "
            "user-path literal — ADR-KR3 §C6.3.2 forbids this in committed code"
        )

    def test_fallback_resolves_under_dot_nfmd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_home = tmp_path / "synthetic-home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        monkeypatch.delenv("NFMD_PROD_EVENTS_PATH", raising=False)

        expected = fake_home / ".nfmd" / "master-deploy-events.jsonl"
        assert _resolve_prod_path(None) == expected
        assert _default_prod_path() == expected

    def test_env_var_wins_over_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: self._SYNTHETIC_HOME))
        monkeypatch.setenv("NFMD_PROD_EVENTS_PATH", "/opt/custom-prod.jsonl")

        # Env var wins; the synthetic-home / fallback is never reached.
        assert _resolve_prod_path(None) == Path("/opt/custom-prod.jsonl")

    def test_explicit_arg_wins_over_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: self._SYNTHETIC_HOME))
        monkeypatch.setenv("NFMD_PROD_EVENTS_PATH", "/opt/env-prod.jsonl")

        assert _resolve_prod_path("/opt/arg-prod.jsonl") == Path("/opt/arg-prod.jsonl")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestMain:
    def test_prints_json_and_exits_zero(self, tmp_path: Path, capsys) -> None:
        path = write_jsonl(tmp_path / "e.jsonl", [event(), event(first_pass_success=False)])
        assert main(["--path", str(path)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["value"] == 0.5
        assert payload["n"] == 2

    def test_absent_file_is_not_an_error(self, tmp_path: Path, capsys) -> None:
        assert main(["--path", str(tmp_path / "absent.jsonl")]) == 0
        assert json.loads(capsys.readouterr().out)["value"] is None

    def test_reads_path_from_env_when_flag_omitted(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        path = write_jsonl(tmp_path / "e.jsonl", [event()])
        monkeypatch.setenv("NFMD_DEPLOY_EVENTS_PATH", str(path))
        assert main([]) == 0
        assert json.loads(capsys.readouterr().out)["n"] == 1

    def test_rejects_malformed_since(self) -> None:
        with pytest.raises(SystemExit):
            main(["--since", "20-07-2026"])


# ---------------------------------------------------------------------------
# CLI --environment (C6.1.4)
# ---------------------------------------------------------------------------


def mixed_jsonl(tmp_path: Path) -> Path:
    """3 staging + 2 production rows — the C6.1 mixed-stream shape."""
    return write_jsonl(
        tmp_path / "mixed.jsonl",
        [event(environment="staging")] * 3 + [event(environment="production")] * 2,
    )


class TestMainEnvironment:
    def test_staging_selects_three_of_five(self, tmp_path: Path, capsys) -> None:
        path = mixed_jsonl(tmp_path)
        assert main(["--path", str(path), "--environment", "staging"]) == 0
        assert json.loads(capsys.readouterr().out)["n"] == 3

    def test_production_selects_two_of_five(self, tmp_path: Path, capsys) -> None:
        path = mixed_jsonl(tmp_path)
        assert main(["--path", str(path), "--environment", "production"]) == 0
        assert json.loads(capsys.readouterr().out)["n"] == 2

    def test_omitting_the_flag_is_all_not_explicit_staging(self, tmp_path: Path, capsys) -> None:
        """The v1 baseline regression guard, updated for the C6.1 contract.
        Default is ``all`` (whole JSONL); explicit ``--environment staging``
        filters to one series. The two outputs differ in ``n`` — that's the
        discriminator, not a regression."""
        path = mixed_jsonl(tmp_path)

        assert main(["--path", str(path)]) == 0
        default_out = json.loads(capsys.readouterr().out)
        assert main(["--path", str(path), "--environment", "staging"]) == 0
        explicit_out = json.loads(capsys.readouterr().out)

        assert default_out["environment"] == "all"
        assert explicit_out["environment"] == "staging"
        # Default reads the whole mixed JSONL; explicit staging only
        # reads the staging half.
        assert default_out["n"] == 5
        assert explicit_out["n"] == 3

    def test_report_keys_are_unchanged_by_the_new_flag(self, tmp_path: Path, capsys) -> None:
        """Acceptance: ``value``/``n``/``computed_at``/``environment`` shape
        must not drift. The new ``environment`` key is the only addition
        to the v1 spec."""
        assert main(["--path", str(mixed_jsonl(tmp_path)), "--environment", "production"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == {
            "value",
            "target",
            "n",
            "computed_at",
            "source_window",
            "environment",
        }

    def test_rejects_an_unknown_environment(self, tmp_path: Path, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--path", str(mixed_jsonl(tmp_path)), "--environment", "dev"])
        assert exc.value.code != 0
        assert "dev" in capsys.readouterr().err

    def test_rejects_an_empty_environment(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--path", str(mixed_jsonl(tmp_path)), "--environment", ""])
        assert exc.value.code != 0

    def test_help_documents_the_flag(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "--environment" in out
        assert "staging" in out and "production" in out
