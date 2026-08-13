"""Exception hierarchy for the nfm_node_client SDK.

All exceptions raised by the public API inherit from :class:`NfmNodeClientError`,
so callers can write a single ``except NfmNodeClientError`` block and let
specific subtypes inform their retry / alerting logic.
"""

from __future__ import annotations


class NfmNodeClientError(Exception):
    """Base class for all errors raised by nfm_node_client."""


class RegistrationError(NfmNodeClientError):
    """Raised when the hub rejects a node registration request."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class HeartbeatError(NfmNodeClientError):
    """Raised when a heartbeat ping fails after retries are exhausted."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class UploadError(NfmNodeClientError):
    """Raised when an upload session cannot be created or completed."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SyncStatusError(NfmNodeClientError):
    """Raised when the sync-status query fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RetriesExhaustedError(NfmNodeClientError):
    """Raised when all retry attempts have been exhausted.

    Carries the number of attempts made and the last underlying exception
    so callers can log / surface the failure with full context.
    """

    def __init__(
        self,
        message: str,
        attempts: int,
        last_exception: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_exception: BaseException | None = last_exception


__all__ = [
    "HeartbeatError",
    "NfmNodeClientError",
    "RegistrationError",
    "RetriesExhaustedError",
    "SyncStatusError",
    "UploadError",
]
