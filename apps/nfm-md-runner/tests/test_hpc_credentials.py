"""Tests for HPC credentials management (NFM-377).

Credentials are loaded exclusively from environment variables.
Zero hardcoded credentials. Startup fails if required vars are missing.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from nfm_md_runner.hpc_credentials import (
    ClusterCredentials,
    load_cluster_credentials,
    require_hpc_credentials,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _strip_hpc_env(monkeypatch):
    """Remove all HPC_ env vars before each test for isolation."""
    to_remove = [k for k in os.environ if k.startswith("HPC_")]
    for k in to_remove:
        monkeypatch.delenv(k, raising=False)


def _set_gzh_env(monkeypatch):
    """Set minimal primary (Guangzhou) cluster env vars."""
    monkeypatch.setenv("HPC_GZH_HOST", "hpc.gznet.ac.cn")
    monkeypatch.setenv("HPC_GZH_USER", "nfmd_user")
    monkeypatch.setenv("HPC_GZH_SSH_KEY_PATH", "/home/nfmd_user/.ssh/id_ed25519")


def _set_tj_env(monkeypatch):
    """Set minimal backup (Tianjin) cluster env vars."""
    monkeypatch.setenv("HPC_TJ_HOST", "login.tj.hpc.cn")
    monkeypatch.setenv("HPC_TJ_USER", "nfmd_user_tj")
    monkeypatch.setenv("HPC_TJ_SSH_KEY_PATH", "/home/nfmd_user/.ssh/id_ed25519_tj")


# ---------------------------------------------------------------------------
# ClusterCredentials dataclass
# ---------------------------------------------------------------------------

class TestClusterCredentials:
    """Tests for the frozen dataclass."""

    def test_is_frozen(self):
        """ClusterCredentials must be immutable."""
        creds = ClusterCredentials(
            host="hpc.example.com",
            user="testuser",
            ssh_key_path="/home/test/.ssh/id_rsa",
        )
        with pytest.raises(AttributeError):
            creds.host = "evil.com"  # type: ignore[misc]

    def test_defaults_port_to_22(self):
        """SSH port defaults to 22."""
        creds = ClusterCredentials(
            host="hpc.example.com",
            user="testuser",
            ssh_key_path="/home/test/.ssh/id_rsa",
        )
        assert creds.port == 22

    def test_defaults_work_dir_to_none(self):
        """Remote work directory defaults to None."""
        creds = ClusterCredentials(
            host="hpc.example.com",
            user="testuser",
            ssh_key_path="/home/test/.ssh/id_rsa",
        )
        assert creds.work_dir is None

    def test_custom_port(self):
        """Port can be overridden."""
        creds = ClusterCredentials(
            host="hpc.example.com",
            user="testuser",
            ssh_key_path="/home/test/.ssh/id_rsa",
            port=2222,
        )
        assert creds.port == 2222


# ---------------------------------------------------------------------------
# load_cluster_credentials
# ---------------------------------------------------------------------------

class TestLoadClusterCredentials:
    """Tests for loading credentials from environment variables."""

    def test_load_primary_only(self, monkeypatch):
        """Load only the primary (GZH) cluster when TJ vars are absent."""
        _set_gzh_env(monkeypatch)

        primary, backup = load_cluster_credentials()

        assert primary is not None
        assert primary.host == "hpc.gznet.ac.cn"
        assert primary.user == "nfmd_user"
        assert primary.ssh_key_path == "/home/nfmd_user/.ssh/id_ed25519"
        assert backup is None

    def test_load_both_clusters(self, monkeypatch):
        """Load primary and backup when both are configured."""
        _set_gzh_env(monkeypatch)
        _set_tj_env(monkeypatch)

        primary, backup = load_cluster_credentials()

        assert primary is not None
        assert backup is not None
        assert backup.host == "login.tj.hpc.cn"
        assert backup.user == "nfmd_user_tj"

    def test_no_clusters_returns_none_tuple(self):
        """Return (None, None) when no HPC env vars are set."""
        primary, backup = load_cluster_credentials()
        assert primary is None
        assert backup is None

    def test_partial_gzh_raises(self, monkeypatch):
        """Partial primary config (missing user) raises ValueError."""
        monkeypatch.setenv("HPC_GZH_HOST", "hpc.gznet.ac.cn")
        # HPC_GZH_USER and HPC_GZH_SSH_KEY_PATH are missing
        with pytest.raises(ValueError, match="HPC_GZH_USER"):
            load_cluster_credentials()

    def test_partial_gzh_missing_key_raises(self, monkeypatch):
        """Partial primary config (missing SSH key) raises ValueError."""
        monkeypatch.setenv("HPC_GZH_HOST", "hpc.gznet.ac.cn")
        monkeypatch.setenv("HPC_GZH_USER", "nfmd_user")
        with pytest.raises(ValueError, match="HPC_GZH_SSH_KEY_PATH"):
            load_cluster_credentials()

    def test_partial_tj_raises(self, monkeypatch):
        """Partial backup config raises ValueError."""
        _set_gzh_env(monkeypatch)
        monkeypatch.setenv("HPC_TJ_HOST", "login.tj.hpc.cn")
        # Missing TJ user and key
        with pytest.raises(ValueError, match="HPC_TJ_USER"):
            load_cluster_credentials()

    def test_gzh_port_override(self, monkeypatch):
        """Optional HPC_GZH_PORT overrides default port 22."""
        _set_gzh_env(monkeypatch)
        monkeypatch.setenv("HPC_GZH_PORT", "2222")

        primary, _ = load_cluster_credentials()
        assert primary.port == 2222

    def test_tj_port_override(self, monkeypatch):
        """Optional HPC_TJ_PORT overrides default port 22."""
        _set_gzh_env(monkeypatch)
        _set_tj_env(monkeypatch)
        monkeypatch.setenv("HPC_TJ_PORT", "8022")

        _, backup = load_cluster_credentials()
        assert backup.port == 8022

    def test_gzh_work_dir(self, monkeypatch):
        """Optional HPC_GZH_WORK_DIR sets remote work directory."""
        _set_gzh_env(monkeypatch)
        monkeypatch.setenv("HPC_GZH_WORK_DIR", "/scratch/nfmd")

        primary, _ = load_cluster_credentials()
        assert primary.work_dir == "/scratch/nfmd"

    def test_empty_host_counts_as_unset(self, monkeypatch):
        """Empty string for HOST is treated as not configured."""
        monkeypatch.setenv("HPC_GZH_HOST", "")
        monkeypatch.setenv("HPC_GZH_USER", "user")
        monkeypatch.setenv("HPC_GZH_SSH_KEY_PATH", "/key")

        primary, backup = load_cluster_credentials()
        assert primary is None
        assert backup is None


# ---------------------------------------------------------------------------
# require_hpc_credentials
# ---------------------------------------------------------------------------

class TestRequireHPCCredentials:
    """Tests for the mandatory startup validation."""

    def test_require_succeeds_with_primary(self, monkeypatch):
        """require_hpc_credentials passes when primary is configured."""
        _set_gzh_env(monkeypatch)

        creds = require_hpc_credentials()
        assert creds.host == "hpc.gznet.ac.cn"

    def test_require_fails_with_no_clusters(self):
        """require_hpc_credentials raises when no clusters configured."""
        with pytest.raises(ValueError, match="No HPC cluster credentials"):
            require_hpc_credentials()

    def test_require_returns_primary(self, monkeypatch):
        """require_hpc_credentials returns primary cluster credentials."""
        _set_gzh_env(monkeypatch)
        _set_tj_env(monkeypatch)

        creds = require_hpc_credentials()
        assert creds.host == "hpc.gznet.ac.cn"

    def test_require_ssh_key_path_exists_check(self, monkeypatch):
        """require_hpc_credentials warns if SSH key path doesn't exist on disk."""
        _set_gzh_env(monkeypatch)

        # Should NOT raise — existence check is best-effort warning
        # (key may exist on the remote SSH jump host, not locally)
        creds = require_hpc_credentials()
        assert creds.ssh_key_path == "/home/nfmd_user/.ssh/id_ed25519"
