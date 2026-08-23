"""NFM-3586: ADR-009 §4.3-c — feature flag + audit log writer parity with §4.1.

Verifies:

* Feature flag defaults OFF and reads ``NFM_ADR_009_RECONCILIATION_HOOK_ENABLED``.
* Audit log writer writes entries with the exact §4.1 byte-for-byte shape
  (field-by-field equality).
* Writer short-circuits when the flag is OFF (no DB writes).
* Idempotency guard: writing the same ``(routine, dependent_id,
  closing_issue_id, run_date)`` tuple twice produces a single audit row.

This file self-contains the test fixtures (the global ``conftest.py``
imports the full app; the model and flag reader here only need a
minimal in-memory SQLite database to exercise the contract).
"""

from __future__ import annotations

import importlib
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _reload_flag_module():
    """Reload the flag module so module-level memoisation re-runs."""
    from nfm_db.services import adr009_flag

    return importlib.reload(adr009_flag)


def _reload_audit_module():
    from nfm_db.services import adr009_audit, adr009_flag

    importlib.reload(adr009_flag)
    return importlib.reload(adr009_audit)


@pytest.fixture
def flag_module(monkeypatch):
    monkeypatch.delenv("NFM_ADR_009_RECONCILIATION_HOOK_ENABLED", raising=False)
    return _reload_flag_module()


@pytest.fixture
def audit_module():
    return _reload_audit_module()


class TestAdr009Flag:
    """ADR_009_RECONCILIATION_HOOK_ENABLED env-var gate."""

    def test_default_is_off(self, flag_module):
        assert flag_module.is_reconcile_routine_enabled() is False

    def test_truthy_values_enable(self, flag_module, monkeypatch):
        for truthy in ("true", "1", "yes", "on", "TRUE", " True "):
            monkeypatch.setenv("NFM_ADR_009_RECONCILIATION_HOOK_ENABLED", truthy)
            mod = _reload_flag_module()
            assert mod.is_reconcile_routine_enabled() is True, truthy

    def test_falsy_values_stay_off(self, flag_module, monkeypatch):
        for falsy in ("false", "0", "no", "off", "FALSE"):
            monkeypatch.setenv("NFM_ADR_009_RECONCILIATION_HOOK_ENABLED", falsy)
            mod = _reload_flag_module()
            assert mod.is_reconcile_routine_enabled() is False, falsy

    def test_unknown_value_treated_as_off(self, flag_module, monkeypatch):
        monkeypatch.setenv("NFM_ADR_009_RECONCILIATION_HOOK_ENABLED", "banana")
        mod = _reload_flag_module()
        assert mod.is_reconcile_routine_enabled() is False


@pytest.fixture
def audit_engine():
    from nfm_db.models.adr009_reconcile_audit import (
        Adr009ReconcileAuditLog,
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Adr009ReconcileAuditLog.__table__.create(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def audit_session(audit_engine):
    SessionLocal = sessionmaker(bind=audit_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def audit_module_on(monkeypatch):
    """Audit module with the feature flag forced ON.

    The shape tests need to exercise the write path; the short-circuit
    tests use this fixture in reverse (set OFF explicitly) to verify the
    no-op path.
    """
    monkeypatch.setenv("NFM_ADR_009_RECONCILIATION_HOOK_ENABLED", "true")
    return _reload_audit_module()


def _sample_payload(**overrides):
    closing_id = overrides.pop("closing_issue_id", uuid.uuid4())
    dependent_id = overrides.pop("dependent_id", uuid.uuid4())
    payload = {
        "ts": datetime(2026, 8, 24, 6, 0, 0, tzinfo=UTC),
        "routine": "adr009-daily-reconcile",
        "closing_issue_id": closing_id,
        "closing_issue_identifier": "NFM-9999",
        "dependent_id": dependent_id,
        "dependent_identifier": "NFM-1234",
        "before_blockedByIssueIds": [uuid.uuid4(), uuid.uuid4()],
        "after_blockedByIssueIds": [],
        "status_transition": {"from": "blocked", "to": "todo"},
        "wake_fired": True,
        "feature_flag": "ADR_009_RECONCILIATION_HOOK_ENABLED",
    }
    payload.update(overrides)
    return payload


class TestAdr009AuditShape:
    """Audit row must round-trip back to the §4.1 spec."""

    def test_round_trip_preserves_all_fields(self, audit_session, audit_module_on):
        payload = _sample_payload()
        entry = audit_module_on.write_audit_entry(audit_session, **payload)
        audit_session.commit()

        loaded = audit_module_on.get_audit_entry(audit_session, entry.id)
        assert loaded is not None
        # SQLAlchemy DateTime(timezone=True) round-trips the wall time but
        # the in-memory SQLite driver normalises to naive UTC; compare
        # the wall times explicitly so the contract is dialect-agnostic.
        assert loaded.ts == payload["ts"].replace(tzinfo=None)
        assert loaded.routine == payload["routine"]
        assert loaded.closing_issue_id == payload["closing_issue_id"]
        assert loaded.closing_issue_identifier == payload["closing_issue_identifier"]
        assert loaded.dependent_id == payload["dependent_id"]
        assert loaded.dependent_identifier == payload["dependent_identifier"]
        assert loaded.before_blockedByIssueIds == payload["before_blockedByIssueIds"]
        assert loaded.after_blockedByIssueIds == payload["after_blockedByIssueIds"]
        assert loaded.status_transition == payload["status_transition"]
        assert loaded.wake_fired is True
        assert loaded.feature_flag == "ADR_009_RECONCILIATION_HOOK_ENABLED"

    def test_status_transition_can_be_null(self, audit_session, audit_module_on):
        payload = _sample_payload(status_transition=None)
        entry = audit_module_on.write_audit_entry(audit_session, **payload)
        audit_session.commit()

        loaded = audit_module_on.get_audit_entry(audit_session, entry.id)
        assert loaded is not None
        assert loaded.status_transition is None

    def test_routine_field_for_43_is_daily_reconcile(self, audit_session, audit_module_on):
        payload = _sample_payload(routine="adr009-daily-reconcile")
        entry = audit_module_on.write_audit_entry(audit_session, **payload)
        audit_session.commit()

        loaded = audit_module_on.get_audit_entry(audit_session, entry.id)
        assert loaded.routine == "adr009-daily-reconcile"


class TestAdr009AuditShortCircuit:
    """Writer must no-op when the feature flag is OFF."""

    def test_off_flag_skips_write(self, audit_session, monkeypatch):
        monkeypatch.setenv("NFM_ADR_009_RECONCILIATION_HOOK_ENABLED", "false")
        mod = _reload_audit_module()
        result = mod.write_audit_entry(
            audit_session,
            **_sample_payload(),
        )
        audit_session.commit()
        assert result is None

        from sqlalchemy import select

        from nfm_db.models.adr009_reconcile_audit import (
            Adr009ReconcileAuditLog,
        )

        rows = audit_session.execute(select(Adr009ReconcileAuditLog)).scalars().all()
        assert rows == []

    def test_on_flag_persists_row(self, audit_session, audit_module_on):
        entry = audit_module_on.write_audit_entry(audit_session, **_sample_payload())
        audit_session.commit()

        from sqlalchemy import select

        from nfm_db.models.adr009_reconcile_audit import (
            Adr009ReconcileAuditLog,
        )

        rows = audit_session.execute(select(Adr009ReconcileAuditLog)).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == entry.id


class TestAdr009AuditIdempotency:
    """Same ``(routine, dependent_id, closing_issue_id, run_date)`` ⇒ one row."""

    def test_double_write_is_a_noop(self, audit_session, audit_module_on):
        payload = _sample_payload(ts=datetime(2026, 8, 24, 6, 0, 0, tzinfo=UTC))
        first = audit_module_on.write_audit_entry(audit_session, **payload)
        audit_session.commit()
        second = audit_module_on.write_audit_entry(audit_session, **payload)
        audit_session.commit()
        assert first is not None
        assert second is None

        from sqlalchemy import select

        from nfm_db.models.adr009_reconcile_audit import (
            Adr009ReconcileAuditLog,
        )

        rows = audit_session.execute(select(Adr009ReconcileAuditLog)).scalars().all()
        assert len(rows) == 1

    def test_different_run_date_writes_new_row(self, audit_session, audit_module_on):
        payload_a = _sample_payload(ts=datetime(2026, 8, 24, 6, 0, 0, tzinfo=UTC))
        payload_b = _sample_payload(ts=datetime(2026, 8, 25, 6, 0, 0, tzinfo=UTC))
        audit_module_on.write_audit_entry(audit_session, **payload_a)
        audit_module_on.write_audit_entry(audit_session, **payload_b)
        audit_session.commit()

        from sqlalchemy import select

        from nfm_db.models.adr009_reconcile_audit import (
            Adr009ReconcileAuditLog,
        )

        rows = audit_session.execute(select(Adr009ReconcileAuditLog)).scalars().all()
        assert len(rows) == 2
        run_dates = sorted(r.run_date for r in rows)
        assert run_dates == [date(2026, 8, 24), date(2026, 8, 25)]

    def test_different_dependent_writes_new_row(self, audit_session, audit_module_on):
        audit_module_on.write_audit_entry(audit_session, **_sample_payload())
        audit_module_on.write_audit_entry(
            audit_session,
            **_sample_payload(dependent_id=uuid.uuid4()),
        )
        audit_session.commit()

        from sqlalchemy import select

        from nfm_db.models.adr009_reconcile_audit import (
            Adr009ReconcileAuditLog,
        )

        rows = audit_session.execute(select(Adr009ReconcileAuditLog)).scalars().all()
        assert len(rows) == 2
