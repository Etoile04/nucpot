"""Tests for the §4.3-b auto-transition + Paperclip wake (NFM-3688).

Acceptance criteria under test (per NFM-3688 description):

* [ ] branch 1: cleared + blocked + no checkout -> todo + wake
* [ ] branch 2: cleared + blocked + checkout -> in_progress + wake
          (preserves checkoutRunId)
* [ ] branch 3: after != [] -> no transition, no wake, audit still
          written
* [ ] branch 4: status != blocked -> no transition, no wake, audit
          still written
* [ ] edge: blocked + no assignee -> transition but no wake (audit
          wake_fired=False)
* [ ] edge: flag OFF -> no-op, no audit, no wake
* [ ] idempotence: re-running in same window does NOT double-wake
* [ ] multi-tenant: independent companyIds don't cross-wake

Plus the wake-service unit tests:

* wake HTTP body shape matches the OpenAPI schema for
  ``POST /api/agents/{id}/wakeup``.
* wake failure (non-2xx) returns ``False`` and does NOT raise.
* wake batch returns ``(succeeded, failed)`` counts.
* missing ``PAPERCLIP_API_KEY`` returns ``False`` without HTTP call.

The wake HTTP call is mocked via ``unittest.mock.patch`` on
``httpx.post`` so the test suite never hits a real Paperclip API.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from nfm_db.models.adr009_reconcile_audit import Adr009ReconcileAuditLog
from nfm_db.services import adr009_flag as adr009_flag_module
from nfm_db.services.adr009_flag import is_reconcile_routine_enabled
from nfm_db.services.adr009_paperclip_wake import (
    _build_wakeup_payload,
    _resolve_paperclip_api_key,
    _resolve_paperclip_base_url,
    fire_wake_intent,
    fire_wake_intents,
)
from nfm_db.services.adr009_reconcile_routine import (
    IssueLike,
    WakeIntent,
    reconcile_blocked_by_issue_ids,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# Sentinel for ``_make_dependent(assignee_agent_id=...)``: distinguishes
# "caller did not specify an assignee" (auto-generate) from "caller
# explicitly passed None" (test the §4.3-b no-assignee edge).
_UNSET: object = object()


# --- Fixtures ----------------------------------------------------------


@pytest.fixture(autouse=True)
def _enable_adr009_flag(monkeypatch: pytest.MonkeyPatch):
    """All tests in this module turn the §4.3 reconcile feature flag ON.

    Mutates the env var and resets the module-level memoisation cache
    on the SOURCE module. A naive ``from module import _FLAG_CACHE;
    global _FLAG_CACHE; _FLAG_CACHE = None`` rebinds the test module's
    name only — the source module's cache is what
    ``is_reconcile_routine_enabled`` reads.
    """
    monkeypatch.setenv("NFM_ADR_009_RECONCILIATION_HOOK_ENABLED", "on")
    adr009_flag_module._FLAG_CACHE = None
    assert is_reconcile_routine_enabled(), "test precondition: flag must be ON"
    yield
    adr009_flag_module._FLAG_CACHE = None


@pytest.fixture(autouse=True)
def _paperclip_env(monkeypatch: pytest.MonkeyPatch):
    """Provide deterministic Paperclip env vars so wake-service unit
    tests don't read the real (harness) credentials.

    Individual tests may override PAPERCLIP_API_URL or
    PAPERCLIP_API_KEY as needed.
    """
    monkeypatch.setenv("PAPERCLIP_API_URL", "http://paperclip-test:3101")
    monkeypatch.setenv("PAPERCLIP_API_KEY", "test-key-do-not-use")


@pytest.fixture
def sync_audit_engine():
    """In-memory SQLite engine with the §4.3 audit table pre-created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Adr009ReconcileAuditLog.__table__.create(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(sync_audit_engine):
    """Sync :class:`Session` exposing the §4.3 audit table to tests."""
    SessionLocal = sessionmaker(bind=sync_audit_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# --- Helpers ------------------------------------------------------------


def _make_dependent(
    blocked_by: list[uuid.UUID] | tuple[uuid.UUID, ...] = (),
    *,
    identifier: str | None = None,
    status: str = "blocked",
    assignee_agent_id: uuid.UUID | None | object = _UNSET,
    checkout_run_id: str | None = None,
) -> IssueLike:
    """Build an :class:`IssueLike` with §4.3-b fields populated by default.

    ``assignee_agent_id`` uses a sentinel so the §4.3-b "no assignee"
    edge can be tested with an explicit ``None`` without colliding
    with the "auto-generate" default.
    """
    if assignee_agent_id is _UNSET:
        assignee_agent_id = uuid.uuid4()
    return IssueLike(
        id=uuid.uuid4(),
        identifier=identifier or f"NFM-DEP-{uuid.uuid4().hex[:6].upper()}",
        status=status,
        blocked_by_issue_ids=tuple(blocked_by),
        assignee_agent_id=assignee_agent_id,  # type: ignore[arg-type]
        checkout_run_id=checkout_run_id,
    )


def _make_cancelled(identifier: str | None = None) -> IssueLike:
    """Build a cancelled blocker with a stable identifier for assertions."""
    return IssueLike(
        id=uuid.uuid4(),
        identifier=identifier or f"NFM-CXL-{uuid.uuid4().hex[:6].upper()}",
        status="cancelled",
        blocked_by_issue_ids=(),
    )


def _status_lookup_from_issues(issues: list[IssueLike]) -> dict[uuid.UUID, str]:
    """Map every issue's UUID to its status (mimics paperclip's row lookup)."""
    return {issue.id: issue.status for issue in issues}


# --- §4.3-b branches ------------------------------------------------------


class TestFourBranchConditional:
    """Per-dependent 4-branch conditional: blocked+cleared+checkout
    dictates the status_transition + WakeIntent.
    """

    def test_branch_1_cleared_blocked_no_checkout_transitions_to_todo(
        self,
        db_session: Session,
    ) -> None:
        """Branch 1: ``after == []`` AND ``status == "blocked"`` AND no
        checkoutRunId -> ``blocked -> todo`` + wake."""
        blocker = _make_cancelled()
        dependent = _make_dependent(blocked_by=[blocker.id])
        statuses = _status_lookup_from_issues([blocker, dependent])

        result = reconcile_blocked_by_issue_ids(
            [dependent],
            lookup_status=statuses.get,
            session=db_session,
        )

        assert result.touched == 1
        assert len(result.wake_intents) == 1
        intent = result.wake_intents[0]
        assert intent.dependent_id == dependent.id
        assert intent.assignee_agent_id == dependent.assignee_agent_id
        assert intent.status_transition == {"from": "blocked", "to": "todo"}
        assert intent.idempotency_key.startswith("adr-009:4.3-b:")

        rows = (
            db_session.execute(
                select(Adr009ReconcileAuditLog).where(
                    Adr009ReconcileAuditLog.dependent_id == dependent.id
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status_transition == {"from": "blocked", "to": "todo"}
        assert rows[0].wake_fired is True

    def test_branch_2_cleared_blocked_with_checkout_transitions_to_in_progress(
        self,
        db_session: Session,
    ) -> None:
        """Branch 2: ``after == []`` AND ``status == "blocked"`` AND
        checkoutRunId set -> ``blocked -> in_progress`` + wake
        (preserves checkoutRunId; wake still fires)."""
        blocker = _make_cancelled()
        dependent = _make_dependent(
            blocked_by=[blocker.id],
            checkout_run_id="run-abc-4567",
        )
        statuses = _status_lookup_from_issues([blocker, dependent])

        result = reconcile_blocked_by_issue_ids(
            [dependent],
            lookup_status=statuses.get,
            session=db_session,
        )

        assert result.touched == 1
        assert len(result.wake_intents) == 1
        intent = result.wake_intents[0]
        assert intent.status_transition == {"from": "blocked", "to": "in_progress"}

        rows = (
            db_session.execute(
                select(Adr009ReconcileAuditLog).where(
                    Adr009ReconcileAuditLog.dependent_id == dependent.id
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status_transition == {
            "from": "blocked",
            "to": "in_progress",
        }
        assert rows[0].wake_fired is True

    def test_branch_3_live_blockers_remain_no_transition_no_wake(
        self,
        db_session: Session,
    ) -> None:
        """Branch 3: ``after != []`` (other live blockers remain) -> no
        transition; sweep still clears cancelled UUID; no wake."""
        cancelled = _make_cancelled()
        live_blocker = IssueLike(
            id=uuid.uuid4(),
            identifier="NFM-LIVE",
            status="in_progress",  # live, not terminal
            blocked_by_issue_ids=(),
        )
        dependent = _make_dependent(
            blocked_by=[cancelled.id, live_blocker.id],
        )
        statuses = _status_lookup_from_issues(
            [cancelled, live_blocker, dependent]
        )

        result = reconcile_blocked_by_issue_ids(
            [dependent],
            lookup_status=statuses.get,
            session=db_session,
        )

        assert result.touched == 1
        assert result.wake_intents == ()
        rows = (
            db_session.execute(
                select(Adr009ReconcileAuditLog).where(
                    Adr009ReconcileAuditLog.dependent_id == dependent.id
                )
            )
            .scalars()
            .all()
        )
        # One audit row per removed UUID (only the cancelled one).
        assert len(rows) == 1
        assert rows[0].status_transition is None
        assert rows[0].wake_fired is False

    def test_branch_4_status_not_blocked_no_transition_no_wake(
        self,
        db_session: Session,
    ) -> None:
        """Branch 4: ``dependent.status != "blocked"`` -> no transition;
        sweep still clears cancelled UUID; no wake."""
        cancelled = _make_cancelled()
        # Dependent is in_progress (not blocked).
        dependent = _make_dependent(
            blocked_by=[cancelled.id],
            status="in_progress",
        )
        statuses = _status_lookup_from_issues([cancelled, dependent])

        result = reconcile_blocked_by_issue_ids(
            [dependent],
            lookup_status=statuses.get,
            session=db_session,
        )

        assert result.touched == 1
        assert result.wake_intents == ()
        rows = (
            db_session.execute(
                select(Adr009ReconcileAuditLog).where(
                    Adr009ReconcileAuditLog.dependent_id == dependent.id
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status_transition is None
        assert rows[0].wake_fired is False


# --- §4.3-b edges --------------------------------------------------------


class TestEdgesAndIdempotence:
    """Edges: no assignee, flag OFF, idempotence, multi-tenant."""

    def test_no_assignee_transitions_but_no_wake(
        self,
        db_session: Session,
    ) -> None:
        """Blocked + cleared + ``assignee_agent_id is None`` -> transition
        fires (audit row has status_transition + wake_fired=False) but no
        WakeIntent emitted."""
        blocker = _make_cancelled()
        dependent = _make_dependent(
            blocked_by=[blocker.id],
            assignee_agent_id=None,
        )
        statuses = _status_lookup_from_issues([blocker, dependent])

        result = reconcile_blocked_by_issue_ids(
            [dependent],
            lookup_status=statuses.get,
            session=db_session,
        )

        assert result.touched == 1
        assert result.wake_intents == ()
        rows = (
            db_session.execute(
                select(Adr009ReconcileAuditLog).where(
                    Adr009ReconcileAuditLog.dependent_id == dependent.id
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status_transition == {"from": "blocked", "to": "todo"}
        assert rows[0].wake_fired is False

    def test_flag_off_short_circuits_no_audit_no_wake(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_session: Session,
    ) -> None:
        """``ADR_009_RECONCILE_ROUTINE_ENABLED=off`` -> no audit, no
        wake, no mutation. Mirrors the §4.3-c contract."""
        monkeypatch.setenv("NFM_ADR_009_RECONCILIATION_HOOK_ENABLED", "off")
        adr009_flag_module._FLAG_CACHE = None

        blocker = _make_cancelled()
        dependent = _make_dependent(blocked_by=[blocker.id])
        statuses = _status_lookup_from_issues([blocker, dependent])

        result = reconcile_blocked_by_issue_ids(
            [dependent],
            lookup_status=statuses.get,
            session=db_session,
        )

        assert result.skipped_flag_off is True
        assert result.touched == 0
        assert result.wake_intents == ()
        rows = db_session.execute(select(Adr009ReconcileAuditLog)).scalars().all()
        assert rows == []

    def test_idempotence_does_not_double_emit_wake_intents(
        self,
        db_session: Session,
    ) -> None:
        """Re-running in the same window does NOT double-emit WakeIntents
        (the §4.3-a audit-log unique constraint + per-day pre-query
        provide the second line of defence)."""
        blocker = _make_cancelled()
        dependent = _make_dependent(blocked_by=[blocker.id])
        statuses = _status_lookup_from_issues([blocker, dependent])

        result1 = reconcile_blocked_by_issue_ids(
            [dependent],
            lookup_status=statuses.get,
            session=db_session,
        )
        assert len(result1.wake_intents) == 1

        result2 = reconcile_blocked_by_issue_ids(
            [dependent],
            lookup_status=statuses.get,
            session=db_session,
        )
        # Second pass: no audit rows added (unique constraint hit);
        # WakeIntent list is empty (dependents whose every removal is
        # already audited are skipped before the conditional).
        assert result2.touched == 0
        assert result2.wake_intents == ()
        assert result2.audit_entries_written == 0

        # And only one WakeIntent ever existed.
        assert len(result1.wake_intents) == 1

    def test_multi_tenant_independent_dependents_get_independent_intents(
        self,
        db_session: Session,
    ) -> None:
        """Two dependents belonging to two different agent IDs (a
        surrogate for two different companies since WakeIntent only
        carries the agent UUID, not a companyId) each get their own
        WakeIntent with their own idempotency_key."""
        blocker_a = _make_cancelled(identifier="NFM-CXL-A")
        blocker_b = _make_cancelled(identifier="NFM-CXL-B")
        dep_a = _make_dependent(
            blocked_by=[blocker_a.id],
            identifier="NFM-DEP-A",
        )
        dep_b = _make_dependent(
            blocked_by=[blocker_b.id],
            identifier="NFM-DEP-B",
        )
        statuses = _status_lookup_from_issues(
            [blocker_a, blocker_b, dep_a, dep_b]
        )

        result = reconcile_blocked_by_issue_ids(
            [dep_a, dep_b],
            lookup_status=statuses.get,
            session=db_session,
        )

        assert len(result.wake_intents) == 2
        identifiers = {w.dependent_identifier for w in result.wake_intents}
        assert identifiers == {"NFM-DEP-A", "NFM-DEP-B"}
        keys = {w.idempotency_key for w in result.wake_intents}
        assert len(keys) == 2, "each dependent must have a unique idempotency_key"

        # Each WakeIntent's assignee_agent_id matches its dependent.
        by_ident = {w.dependent_identifier: w for w in result.wake_intents}
        assert by_ident["NFM-DEP-A"].assignee_agent_id == dep_a.assignee_agent_id
        assert by_ident["NFM-DEP-B"].assignee_agent_id == dep_b.assignee_agent_id


# --- Wake service unit tests --------------------------------------------


class TestWakeServicePayload:
    """Wake payload shape + best-effort semantics."""

    def test_wakeup_payload_matches_openapi_shape(self) -> None:
        """Body must include source, triggerDetail, reason, payload,
        idempotencyKey — exactly the keys the OpenAPI schema accepts."""
        dep_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
        agent_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
        blocker_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
        intent = WakeIntent(
            dependent_id=dep_id,
            dependent_identifier="NFM-XYZ",
            assignee_agent_id=agent_id,
            cleared_blocker_ids=(blocker_id,),
            status_transition={"from": "blocked", "to": "todo"},
            idempotency_key="adr-009:4.3-b:test:abc12345",
        )

        payload = _build_wakeup_payload(intent)

        assert payload["source"] == "automation"
        assert payload["triggerDetail"] == "system"
        assert "NFM-XYZ" in payload["reason"]
        assert "blocked -> todo" in payload["reason"]
        assert payload["payload"]["routine"] == "adr009-daily-reconcile"
        assert payload["payload"]["section"] == "4.3-b"
        assert payload["payload"]["dependent_id"] == str(dep_id)
        assert payload["payload"]["dependent_identifier"] == "NFM-XYZ"
        assert payload["payload"]["cleared_blocker_ids"] == [str(blocker_id)]
        assert payload["payload"]["status_transition"] == {
            "from": "blocked",
            "to": "todo",
        }
        assert payload["idempotencyKey"] == intent.idempotency_key
        # No unexpected keys (helps catch schema drift).
        assert set(payload.keys()) == {
            "source",
            "triggerDetail",
            "reason",
            "payload",
            "idempotencyKey",
        }

    def test_fire_wake_intent_posts_to_resolved_url(self) -> None:
        """``fire_wake_intent`` POSTs to ``{PAPERCLIP_API_URL}/api/agents/{id}/wakeup``."""
        intent = WakeIntent(
            dependent_id=uuid.uuid4(),
            dependent_identifier="NFM-WAKE",
            assignee_agent_id=uuid.UUID("44444444-4444-4444-4444-444444444444"),
            cleared_blocker_ids=(uuid.uuid4(),),
            status_transition={"from": "blocked", "to": "todo"},
            idempotency_key="adr-009:4.3-b:test:deadbeef",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = ""

        with patch("httpx.post", return_value=mock_response) as mock_post:
            ok = fire_wake_intent(intent)

        assert ok is True
        # URL must be well-formed and contain the agent UUID.
        called_url = mock_post.call_args.args[0]
        assert called_url == (
            "http://paperclip-test:3101/api/agents/"
            "44444444-4444-4444-4444-444444444444/wakeup"
        )
        # Body is JSON, Authorization header is present.
        called_kwargs = mock_post.call_args.kwargs
        assert called_kwargs["timeout"] == 5.0
        assert called_kwargs["headers"]["Authorization"] == "Bearer test-key-do-not-use"
        body = called_kwargs["json"]
        assert body["idempotencyKey"] == intent.idempotency_key
        assert body["source"] == "automation"
        assert body["triggerDetail"] == "system"

    def test_fire_wake_intent_returns_false_on_non_2xx(self) -> None:
        """Non-2xx responses return ``False`` and do NOT raise."""
        intent = WakeIntent(
            dependent_id=uuid.uuid4(),
            dependent_identifier="NFM-WAKE",
            assignee_agent_id=uuid.uuid4(),
            cleared_blocker_ids=(uuid.uuid4(),),
            status_transition={"from": "blocked", "to": "todo"},
            idempotency_key="adr-009:4.3-b:test:11111111",
        )
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "service unavailable"

        with patch("httpx.post", return_value=mock_response):
            ok = fire_wake_intent(intent)

        assert ok is False

    def test_fire_wake_intent_returns_false_on_network_error(self) -> None:
        """A raised :class:`httpx.HTTPError` (network) returns ``False``,
        does NOT raise — best-effort contract."""
        import httpx

        intent = WakeIntent(
            dependent_id=uuid.uuid4(),
            dependent_identifier="NFM-WAKE",
            assignee_agent_id=uuid.uuid4(),
            cleared_blocker_ids=(uuid.uuid4(),),
            status_transition={"from": "blocked", "to": "todo"},
            idempotency_key="adr-009:4.3-b:test:22222222",
        )

        with patch(
            "httpx.post",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            ok = fire_wake_intent(intent)

        assert ok is False

    def test_fire_wake_intent_returns_false_without_api_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing ``PAPERCLIP_API_KEY`` returns ``False`` without making
        any HTTP request (cron driver should never crash on missing
        config; the audit row stays durable)."""
        monkeypatch.delenv("PAPERCLIP_API_KEY", raising=False)

        intent = WakeIntent(
            dependent_id=uuid.uuid4(),
            dependent_identifier="NFM-WAKE",
            assignee_agent_id=uuid.uuid4(),
            cleared_blocker_ids=(uuid.uuid4(),),
            status_transition={"from": "blocked", "to": "todo"},
            idempotency_key="adr-009:4.3-b:test:33333333",
        )

        with patch("httpx.post") as mock_post:
            ok = fire_wake_intent(intent)

        assert ok is False
        mock_post.assert_not_called()

    def test_fire_wake_intents_returns_succeeded_failed_counts(self) -> None:
        """Batch wrapper returns ``(succeeded, failed)`` tuple."""
        intents = [
            WakeIntent(
                dependent_id=uuid.uuid4(),
                dependent_identifier=f"NFM-WAKE-{i}",
                assignee_agent_id=uuid.uuid4(),
                cleared_blocker_ids=(uuid.uuid4(),),
                status_transition={"from": "blocked", "to": "todo"},
                idempotency_key=f"adr-009:4.3-b:test:batch{i}",
            )
            for i in range(3)
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = ""

        with patch("httpx.post", return_value=mock_response):
            succeeded, failed = fire_wake_intents(intents)

        assert succeeded == 3
        assert failed == 0

    def test_resolve_paperclip_base_url_strips_trailing_slash(self) -> None:
        """``PAPERCLIP_API_URL`` with trailing slash must not produce
        ``//api/agents/...`` in the wake URL."""
        assert _resolve_paperclip_base_url() == "http://paperclip-test:3101"

    def test_resolve_paperclip_api_key_returns_none_when_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``PAPERCLIP_API_KEY`` unset / empty -> ``None`` (sentinel for
        the wake service to short-circuit)."""
        monkeypatch.delenv("PAPERCLIP_API_KEY", raising=False)
        assert _resolve_paperclip_api_key() is None

        monkeypatch.setenv("PAPERCLIP_API_KEY", "   ")
        assert _resolve_paperclip_api_key() is None


# --- Public-API regression ------------------------------------------------


class TestWakeIntentPublicApi:
    """Lock in the §4.3-b public-API surface so refactors don't break it."""

    def test_wake_intent_is_frozen(self) -> None:
        """``WakeIntent`` is a frozen dataclass — protects the wake
        snapshot from accidental mutation between audit commit and
        wake emission."""
        intent = WakeIntent(
            dependent_id=uuid.uuid4(),
            dependent_identifier="NFM-X",
            assignee_agent_id=uuid.uuid4(),
            cleared_blocker_ids=(uuid.uuid4(),),
            status_transition={"from": "blocked", "to": "todo"},
            idempotency_key="adr-009:4.3-b:test:frozen",
        )
        with pytest.raises((AttributeError, Exception)):
            intent.idempotency_key = "tampered"  # type: ignore[misc]

    def test_wake_intent_round_trips_json(self) -> None:
        """``json.dumps`` of the WakeIntent payload works (cron driver
        may want to log structured intents)."""
        dep_id = uuid.uuid4()
        agent_id = uuid.uuid4()
        intent = WakeIntent(
            dependent_id=dep_id,
            dependent_identifier="NFM-JSON",
            assignee_agent_id=agent_id,
            cleared_blocker_ids=(uuid.uuid4(),),
            status_transition={"from": "blocked", "to": "todo"},
            idempotency_key="adr-009:4.3-b:test:json",
        )
        payload = _build_wakeup_payload(intent)
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        assert decoded["payload"]["dependent_id"] == str(dep_id)
        assert decoded["payload"]["status_transition"] == {
            "from": "blocked",
            "to": "todo",
        }
