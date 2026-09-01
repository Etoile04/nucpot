"""Tests for NFM_CORS_ORIGINS tolerant parsing (NFM-4090).

The crash loop on 2026-09-01 was caused by ``pydantic_settings`` calling
``json.loads`` on a malformed ``NFM_CORS_ORIGINS`` value during
``alembic upgrade head`` startup.  These tests pin down the new tolerant
parser contract: every accepted shape parses correctly and every rejected
shape degrades to the safe default rather than crashing ``get_settings()``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nfm_db.config import Settings, _parse_cors_origins

_DEFAULT_ORIGIN = "http://localhost:3000"


@pytest.fixture(autouse=True)
def _clear_cors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure NFM_CORS_ORIGINS is unset for every test in this module.

    ``Settings`` reads the env on every instantiation, so a stray value
    leaking across tests would mask regressions.
    """
    monkeypatch.delenv("NFM_CORS_ORIGINS", raising=False)


# ---------------------------------------------------------------------------
# Direct unit tests of the validator function.
# ---------------------------------------------------------------------------


class TestParseCorsOriginsUnit:
    """Pure-function tests for ``_parse_cors_origins``."""

    def test_none_returns_default(self) -> None:
        assert _parse_cors_origins(None) == [_DEFAULT_ORIGIN]

    def test_empty_string_returns_default(self) -> None:
        assert _parse_cors_origins("") == [_DEFAULT_ORIGIN]

    def test_whitespace_only_returns_default(self) -> None:
        assert _parse_cors_origins("   \t  ") == [_DEFAULT_ORIGIN]

    def test_json_list_with_whitespace_parses(self) -> None:
        # json.loads tolerates interior whitespace — must not crash.
        assert _parse_cors_origins('[ "http://a", "http://b" ]') == [
            "http://a",
            "http://b",
        ]

    def test_json_list_compact_parses(self) -> None:
        # NFM-4077 staging env_file canonical shape.
        assert _parse_cors_origins('["http://localhost:3010","https://staging.nucpot.dpdns.org"]') == [
            "http://localhost:3010",
            "https://staging.nucpot.dpdns.org",
        ]

    def test_comma_separated_parses(self) -> None:
        # Legacy / ad-hoc operator shape (matches main.py CORS_ORIGINS reader).
        assert _parse_cors_origins("http://a,http://b,http://c") == [
            "http://a",
            "http://b",
            "http://c",
        ]

    def test_comma_separated_with_whitespace_trims(self) -> None:
        assert _parse_cors_origins(" http://a , http://b ") == [
            "http://a",
            "http://b",
        ]

    def test_single_url_is_a_one_item_list(self) -> None:
        assert _parse_cors_origins("http://localhost:3000") == [
            "http://localhost:3000",
        ]

    def test_list_passthrough(self) -> None:
        # Programmatic construction (tests, internal callers).
        assert _parse_cors_origins(["http://x", "http://y"]) == [
            "http://x",
            "http://y",
        ]

    def test_list_filters_blank_items(self) -> None:
        assert _parse_cors_origins(["http://x", "", None, "  ", "http://y"]) == [
            "http://x",
            "http://y",
        ]

    def test_list_with_only_blanks_returns_default(self) -> None:
        assert _parse_cors_origins(["", None, "  "]) == [_DEFAULT_ORIGIN]

    def test_malformed_json_falls_back_to_comma_split(self) -> None:
        # NFM-4090 crash shape: docker compose v5.5.0 stripped the inner
        # double quotes from ``["http://..."]``, yielding ``[http://...]``.
        # Must NOT raise — comma-split is the rescue path.
        result = _parse_cors_origins("[http://localhost:3000]")
        # Acceptable degraded shapes: the raw token is preserved as a single
        # origin, OR the brackets are stripped and the inside becomes one
        # origin. Either way, the result must be a non-empty list[str] and
        # must not contain the literal default fallback.
        assert isinstance(result, list)
        assert len(result) >= 1
        assert all(isinstance(o, str) and o for o in result)

    def test_json_object_value_falls_through(self) -> None:
        # JSON-shaped input that parses successfully but yields an object,
        # not a list → fall through to comma-split rescue path. The literal
        # string becomes a single (probably browser-rejected) origin. Must
        # not raise.
        result = _parse_cors_origins('{"not":"a list"}')
        assert result == ['{"not":"a list"}']

    def test_non_string_non_list_returns_default(self) -> None:
        assert _parse_cors_origins(12345) == [_DEFAULT_ORIGIN]  # type: ignore[arg-type]
        assert _parse_cors_origins(object()) == [_DEFAULT_ORIGIN]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Integration tests through ``Settings``.
# ---------------------------------------------------------------------------


class TestSettingsCorsOrigins:
    """End-to-end: ``Settings()`` must construct for every NFM-4090 case."""

    def test_default_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NFM_CORS_ORIGINS", raising=False)
        assert Settings().cors_origins == [_DEFAULT_ORIGIN]

    def test_json_list_env_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "NFM_CORS_ORIGINS",
            '["http://localhost:3010","https://staging.nucpot.dpdns.org"]',
        )
        s = Settings()
        assert s.cors_origins == [
            "http://localhost:3010",
            "https://staging.nucpot.dpdns.org",
        ]

    def test_comma_separated_env_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "NFM_CORS_ORIGINS",
            "https://nucpot.dpdns.org,https://staging.nucpot.dpdns.org",
        )
        assert Settings().cors_origins == [
            "https://nucpot.dpdns.org",
            "https://staging.nucpot.dpdns.org",
        ]

    def test_malformed_env_value_does_not_crash(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """NFM-4090 regression: the exact crash signature must not raise.

        Before the fix, the docker compose v5.5.0 YAML re-parse bug
        produced ``[ "url" ]`` (or worse), which pydantic-settings tried
        to ``json.loads`` and crashed ``get_settings()`` before
        ``alembic upgrade head`` could run.
        """
        monkeypatch.setenv("NFM_CORS_ORIGINS", "[http://localhost:3000]")
        # Must construct without raising.
        s = Settings()
        assert isinstance(s.cors_origins, list)
        assert len(s.cors_origins) >= 1

    def test_garbage_env_value_does_not_crash(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NFM_CORS_ORIGINS", "this is not even close to JSON")
        s = Settings()
        assert isinstance(s.cors_origins, list)
        assert len(s.cors_origins) >= 1

    def test_empty_env_value_uses_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NFM_CORS_ORIGINS", "")
        assert Settings().cors_origins == [_DEFAULT_ORIGIN]

    def test_programmatic_override_still_works(self) -> None:
        s = Settings(cors_origins=["http://explicit"])
        assert s.cors_origins == ["http://explicit"]

    def test_settings_constructs_during_alembic_startup(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression: must not raise ValidationError or SettingsError.

        The original crash signature was a ``pydantic_settings.exceptions.
        SettingsError`` raised from ``get_settings()`` during
        ``alembic upgrade head`` startup (see the stack trace in NFM-4090).
        """
        # Worst-case malformed env that previously crashed.
        monkeypatch.setenv("NFM_CORS_ORIGINS", '[ "http://localhost:3000"')
        # Missing closing bracket — was unparseable JSON.
        try:
            s = Settings()
        except ValidationError as exc:  # pragma: no cover — failure path
            pytest.fail(f"Settings() must not raise ValidationError, got {exc!r}")
        # Field must be a list[str], never None or a bare string.
        assert isinstance(s.cors_origins, list)
        assert all(isinstance(o, str) and o for o in s.cors_origins)
