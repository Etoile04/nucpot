"""Data models for the backup system.

NFM-3050 / NFM-3024-B — immutable snapshot records that flow through
the GFS tier engine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BackupSnapshot:
    """A single backup snapshot on disk (or in S3).

    Attributes:
        filename:  On-disk filename, e.g. ``nucpot-1723456789.sql.gz``.
        mtime:     Modification time as Unix epoch (seconds). Used for
                   age calculations in the tier engine.
        size_bytes: File size in bytes. Used for capacity guardrails.
        tier:       GFS tier label assigned by the classifier
                   (``"hourly"``, ``"daily"``, ``"weekly"``, or ``"prune"``).
                   ``None`` means not yet classified (pre-migration snapshot).
    """

    filename: str
    mtime: float
    size_bytes: int
    tier: str | None = None
