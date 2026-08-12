"""Backup configuration package — NFM-3014.

Re-exports Settings and get_settings from config/settings.py for backward
compatibility with the existing ``from nfm_db.config import ...`` import paths.
Also exports BackupConfig and the deprecation checker.
"""

from nfm_db.config.backup import (  # noqa: F401
    BackupConfig,
    check_retention_deprecation,
)
from nfm_db.config.settings import LIGHTRAG_VERSION, Settings, get_settings  # noqa: F401

__all__ = [
    "BackupConfig",
    "LIGHTRAG_VERSION",
    "Settings",
    "check_retention_deprecation",
    "get_settings",
]
