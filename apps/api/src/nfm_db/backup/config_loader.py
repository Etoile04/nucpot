"""Loader for the backup config block (NFM-3024-T1).

Reads ``config.json`` from disk, extracts the ``backup`` block, and parses
it into a :class:`BackupConfig`. Emits a deprecation warning when an
operator still uses the legacy ``retentionDays`` alias without the new
``retention`` object.

This is the only side-effecting piece of the backup-config subsystem —
:class:`nfm_db.backup.tier_engine` and :class:`nfm_db.backup.schema` are pure.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

from nfm_db.backup.schema import BackupConfig

_logger = logging.getLogger(__name__)

_LEGACY_KEY = "retentionDays"
_NEW_KEY = "retention"


def _read_json(path: Path) -> dict[str, Any]:
    """Read and parse a JSON file. Raises ``FileNotFoundError`` / ``json.JSONDecodeError``."""
    with path.open("r", encoding="utf-8") as fh:
        return cast(dict[str, Any], json.load(fh))


def load_backup_config(path: str | Path) -> BackupConfig:
    """Load and validate the ``backup`` block of a ``config.json``.

    The JSON is expected to look like::

        {
            "backup": {
                "retention": {
                    "hourly": {"intervalMinutes": 60, "count": 24},
                    "daily":  {"intervalMinutes": 1440, "count": 7},
                    "weekly": {"intervalMinutes": 10080, "count": 4}
                },
                "maxTotalBytes": 12884901888,
                "minFreeBytes":  21474836480,
                "refuseOnFloorBreach": true
            }
        }

    A deprecation warning is logged when ``retentionDays`` is present and
    the new ``retention`` object is absent.
    """
    cfg_path = Path(path)
    document = _read_json(cfg_path)
    backup_block = document.get("backup", {})

    has_legacy = _LEGACY_KEY in backup_block
    has_new = _NEW_KEY in backup_block

    if has_legacy and not has_new:
        _logger.warning(
            "backup.retentionDays is deprecated and will be removed in the next "
            "release cycle; migrate to backup.retention with hourly/daily/weekly "
            "tier specs (see NFM-3024-T1)."
        )

    return BackupConfig.model_validate(backup_block)


<<<<<<< HEAD
def check_retention_deprecation(cfg: BackupConfig) -> None:
    """Emit a ``[DEPRECATION]`` warning if *cfg* uses the legacy path.

    This is the programmatic companion to the warning inside
    :func:`load_backup_config` — callers that construct a
    :class:`BackupConfig` in-memory (without loading from JSON) can
    still get the deprecation warning by calling this explicitly.
    """
    if cfg.retention is None and cfg.retention_days is not None:
        _logger.warning(
            "[DEPRECATION] backup.retentionDays is deprecated and will be "
            "removed in the next release cycle; migrate to backup.retention "
            "with hourly/daily/weekly tier specs (see NFM-3024-T1)."
        )


__all__ = ["check_retention_deprecation", "load_backup_config"]
=======
__all__ = ["load_backup_config"]
>>>>>>> origin/NFM-3041-nfm-3024-t1-config-schema-retention-tier-engine-hourly-daily-weekly
