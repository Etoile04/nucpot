"""
HPC Credentials Management (NFM-377).

Loads cluster connection credentials exclusively from environment variables.
Zero hardcoded credentials. Partial configuration is rejected at startup.

Environment Variables:
    Primary (Guangzhou):
        HPC_GZH_HOST          — SSH hostname
        HPC_GZH_USER          — SSH username
        HPC_GZH_SSH_KEY_PATH  — Path to SSH private key
        HPC_GZH_PORT          — SSH port (optional, default 22)
        HPC_GZH_WORK_DIR      — Remote working directory (optional)

    Backup (Tianjin):
        HPC_TJ_HOST           — SSH hostname
        HPC_TJ_USER           — SSH username
        HPC_TJ_SSH_KEY_PATH   — Path to SSH private key
        HPC_TJ_PORT           — SSH port (optional, default 22)
        HPC_TJ_WORK_DIR       — Remote working directory (optional)
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Tuple

logger = logging.getLogger(__name__)

# Prefixes for the two supported clusters
_PREFIX_GZH = "HPC_GZH_"
_PREFIX_TJ = "HPC_TJ_"

# Required keys (without prefix)
_REQUIRED_KEYS = ("HOST", "USER", "SSH_KEY_PATH")

# Optional keys (without prefix)
_OPTIONAL_KEYS = ("PORT", "WORK_DIR")


@dataclass(frozen=True)
class ClusterCredentials:
    """Immutable SSH connection credentials for an HPC cluster.

    All fields are set at construction time. No mutation.
    """

    host: str
    user: str
    ssh_key_path: str
    port: int = 22
    work_dir: str | None = None


def _load_one_cluster(
    prefix: str,
) -> ClusterCredentials | None:
    """Attempt to load credentials for a cluster from env vars.

    Returns None if no credentials are configured for this cluster.
    Raises ValueError if the configuration is partial (some vars set,
    others missing).

    Args:
        prefix: Environment variable prefix (e.g. ``HPC_GZH_``).

    Returns:
        ClusterCredentials if fully configured, else None.

    Raises:
        ValueError: If partial configuration is detected.
    """
    env = {k: os.environ.get(f"{prefix}{k}") for k in _REQUIRED_KEYS}

    # Collect non-empty values — empty strings and None count as unset
    non_empty = {k for k, v in env.items() if v}

    # No non-empty values → cluster not configured
    if not non_empty:
        return None

    # If HOST is empty, the cluster is unusable — treat as not configured
    if not env.get("HOST"):
        return None

    # Partial: some required keys present, others missing
    missing = [k for k in _REQUIRED_KEYS if k not in non_empty]
    if missing:
        missing_vars = ", ".join(f"{prefix}{k}" for k in missing)
        raise ValueError(
            f"Incomplete HPC cluster configuration: {missing_vars} "
            f"required when any {prefix}* variable is set"
        )

    # Load optional fields
    port_str = os.environ.get(f"{prefix}PORT")
    port = int(port_str) if port_str else 22

    work_dir = os.environ.get(f"{prefix}WORK_DIR") or None

    return ClusterCredentials(
        host=env["HOST"],
        user=env["USER"],
        ssh_key_path=env["SSH_KEY_PATH"],
        port=port,
        work_dir=work_dir,
    )


def load_cluster_credentials() -> Tuple[ClusterCredentials | None, ClusterCredentials | None]:
    """Load credentials for both clusters from environment variables.

    Returns:
        A tuple of (primary, backup). Either may be None if not
        configured.

    Raises:
        ValueError: If either cluster is partially configured.
    """
    primary = _load_one_cluster(_PREFIX_GZH)
    backup = _load_one_cluster(_PREFIX_TJ)

    if primary is None:
        logger.debug("Primary cluster (GZH) not configured")

    if backup is None:
        logger.debug("Backup cluster (TJ) not configured")

    return primary, backup


def require_hpc_credentials() -> ClusterCredentials:
    """Load and return the primary cluster credentials.

    Used at startup to fail-fast if no HPC cluster is configured.

    Returns:
        The primary ClusterCredentials.

    Raises:
        ValueError: If no cluster credentials are available.
    """
    primary, backup = load_cluster_credentials()

    if primary is None:
        raise ValueError(
            "No HPC cluster credentials configured. "
            f"Set {_PREFIX_GZH}HOST, {_PREFIX_GZH}USER, "
            f"and {_PREFIX_GZH}SSH_KEY_PATH environment variables."
        )

    # Best-effort: warn if key path doesn't exist locally
    # (it may exist on a remote jump host, so we don't error)
    if not os.path.exists(primary.ssh_key_path):
        logger.warning(
            "SSH key not found at %s — will be checked at "
            "connection time",
            primary.ssh_key_path,
        )

    return primary
