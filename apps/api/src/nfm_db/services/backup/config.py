"""Configuration models for backup capacity guardrails (NFM-3016,
NFM-3024-E AC2).

Config keys consumed from environment via the ``NFM_`` prefix:
- ``NFM_BACKUP_MAX_TOTAL_BYTES``   — cap on total backup size (default 12 GiB)
- ``NFM_BACKUP_MIN_FREE_BYTES``    — floor on free disk space (default 20 GiB)
- ``NFM_BACKUP_REFUSE_ON_FLOOR``   — enable/disable floor check (default true)
- ``NFM_BACKUP_PUSH_ON_REFUSAL``   — emit [SRE-WARNING] on refusal (default true)
"""

from __future__ import annotations

from dataclasses import dataclass

# Default constants -----------------------------------------------------------
_DEFAULT_MAX_TOTAL_BYTES: int = 12 * 1024**3   # 12 GiB
_DEFAULT_MIN_FREE_BYTES: int = 20 * 1024**3    # 20 GiB
_DEFAULT_REFUSE_ON_FLOOR: bool = True
_DEFAULT_PUSH_ON_REFUSAL: bool = True


@dataclass(frozen=True)
class BackupCapacityConfig:
    """Immutable configuration for backup capacity guardrails.

    Attributes:
        max_total_bytes:  Cap on total backup size. After any write the
                          pruner runs until total ≤ cap.
        min_free_bytes:   Floor on free disk space. Writes that would
                          drop free space below this value are refused.
        refuse_on_floor_breach: When *True* the floor check is active.
        push_on_refusal:  When *True* a refusal emits ``[SRE-WARNING]``.
                          When *False* the refusal is still recorded on
                          ``/api/admin/backups/stats`` but the SRE push
                          is suppressed (NFM-3024-E AC2).
    """

    max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES
    min_free_bytes: int = _DEFAULT_MIN_FREE_BYTES
    refuse_on_floor_breach: bool = _DEFAULT_REFUSE_ON_FLOOR
    push_on_refusal: bool = _DEFAULT_PUSH_ON_REFUSAL

    @staticmethod
    def from_env(env: dict[str, str] | None = None) -> BackupCapacityConfig:
        """Build config from an environment mapping (defaults to ``os.environ``)."""
        import os

        source = env if env is not None else os.environ

        def _int(key: str, default: int) -> int:
            raw = source.get(key)
            if raw is None:
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        def _bool(key: str, default: bool) -> bool:
            raw = source.get(key)
            if raw is None:
                return default
            return raw.lower() in ("1", "true", "yes", "on")

        return BackupCapacityConfig(
            max_total_bytes=_int("NFM_BACKUP_MAX_TOTAL_BYTES", _DEFAULT_MAX_TOTAL_BYTES),
            min_free_bytes=_int("NFM_BACKUP_MIN_FREE_BYTES", _DEFAULT_MIN_FREE_BYTES),
            refuse_on_floor_breach=_bool("NFM_BACKUP_REFUSE_ON_FLOOR", _DEFAULT_REFUSE_ON_FLOOR),
            push_on_refusal=_bool("NFM_BACKUP_PUSH_ON_REFUSAL", _DEFAULT_PUSH_ON_REFUSAL),
        )
