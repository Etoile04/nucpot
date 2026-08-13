"""Tests for nfm_node_client.exceptions."""

from __future__ import annotations

import pytest

from nfm_node_client.exceptions import (
    HeartbeatError,
    NfmNodeClientError,
    RegistrationError,
    RetriesExhaustedError,
    SyncStatusError,
    UploadError,
)


@pytest.mark.unit
def test_nfm_node_client_error_is_base_exception() -> None:
    """NfmNodeClientError is the base for all SDK errors."""
    exc = NfmNodeClientError("boom")
    assert isinstance(exc, Exception)
    assert str(exc) == "boom"


@pytest.mark.unit
def test_registration_error_is_nfm_node_client_error() -> None:
    """RegistrationError is a subclass of NfmNodeClientError."""
    exc = RegistrationError("register failed")
    assert isinstance(exc, NfmNodeClientError)
    assert "register failed" in str(exc)


@pytest.mark.unit
def test_heartbeat_error_is_nfm_node_client_error() -> None:
    """HeartbeatError is a subclass of NfmNodeClientError."""
    exc = HeartbeatError("heartbeat failed")
    assert isinstance(exc, NfmNodeClientError)


@pytest.mark.unit
def test_upload_error_is_nfm_node_client_error() -> None:
    """UploadError is a subclass of NfmNodeClientError."""
    exc = UploadError("upload failed", status_code=500)
    assert isinstance(exc, NfmNodeClientError)
    assert exc.status_code == 500


@pytest.mark.unit
def test_sync_status_error_is_nfm_node_client_error() -> None:
    """SyncStatusError is a subclass of NfmNodeClientError."""
    exc = SyncStatusError("status query failed")
    assert isinstance(exc, NfmNodeClientError)


@pytest.mark.unit
def test_retries_exhausted_error_carries_attempts() -> None:
    """RetriesExhaustedError carries the number of attempts and last exception."""
    last = ValueError("nope")
    exc = RetriesExhaustedError("gave up", attempts=3, last_exception=last)
    assert isinstance(exc, NfmNodeClientError)
    assert exc.attempts == 3
    assert exc.last_exception is last


@pytest.mark.unit
def test_exception_hierarchy_is_catchable_as_base() -> None:
    """All domain errors can be caught as NfmNodeClientError."""
    for cls in (RegistrationError, HeartbeatError, UploadError, SyncStatusError):
        try:
            raise cls("boom")
        except NfmNodeClientError as exc:
            assert str(exc) == "boom"
