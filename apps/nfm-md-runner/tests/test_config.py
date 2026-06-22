"""
Unit tests for configuration module

Tests environment variable loading, validation, and security checks.
"""

import os
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from nfm_md_runner.config import Settings, settings, verify_environment


@pytest.fixture
def clean_env() -> Generator[None, None, None]:
    """Fixture to provide clean environment for each test"""
    # Store original env vars
    original_env = os.environ.copy()

    # Clear relevant env vars
    for key in list(os.environ.keys()):
        if key.startswith("NFM_"):
            del os.environ[key]

    yield

    # Restore original env vars
    os.environ.clear()
    os.environ.update(original_env)


def test_default_settings():
    """Test default settings values"""
    default_settings = Settings()

    assert default_settings.app_name == "nfm-md-runner"
    assert default_settings.app_version == "0.1.0"
    assert default_settings.debug is False
    assert default_settings.lammps_executable == "lmp"


def test_environment_variables_override(clean_env):
    """Test that environment variables override defaults"""
    os.environ["NFM_DEBUG"] = "true"
    os.environ["NFM_LAMMPS_EXECUTABLE"] = "lmp_mpi"

    test_settings = Settings()

    assert test_settings.debug is True
    assert test_settings.lammps_executable == "lmp_mpi"


def test_hpc_configuration_validation(clean_env):
    """Test HPC configuration validation happens at use time"""
    # Partial HPC configuration should allow Settings creation
    # but fail when trying to use HPC features
    os.environ["NFM_HPC_HOST"] = "tianjin.hpc.cn"
    os.environ["NFM_HPC_USER"] = "testuser"
    # Missing NFM_HPC_SSH_KEY_PATH

    # Settings() should be creatable (validation happens at use time)
    test_settings = Settings()

    # But hpc_connection_string should fail
    with pytest.raises(ValueError, match="SSH key"):
        test_settings.hpc_connection_string()


def test_hpc_connection_string(clean_env):
    """Test HPC connection string generation"""
    os.environ["NFM_HPC_HOST"] = "tianjin.hpc.cn"
    os.environ["NFM_HPC_USER"] = "testuser"
    os.environ["NFM_HPC_SSH_KEY_PATH"] = "/tmp/test_key"

    test_settings = Settings()
    connection_string = test_settings.hpc_connection_string()

    assert connection_string == "testuser@tianjin.hpc.cn"


def test_hpc_connection_string_incomplete(clean_env):
    """Test that incomplete HPC configuration raises error"""
    os.environ["NFM_HPC_HOST"] = "tianjin.hpc.cn"
    # Missing NFM_HPC_USER and NFM_HPC_SSH_KEY_PATH

    test_settings = Settings()

    with pytest.raises(ValueError, match="HPC host and user must be set"):
        test_settings.hpc_connection_string()


def test_security_check_passed_no_hpc(clean_env):
    """Test security check passes when HPC not configured"""
    test_settings = Settings()
    assert test_settings.security_check_passed is True


def test_security_check_failed_no_ssh_key(clean_env):
    """Test security check fails when HPC configured without SSH key"""
    os.environ["NFM_HPC_HOST"] = "tianjin.hpc.cn"
    os.environ["NFM_HPC_USER"] = "testuser"
    # Missing SSH key

    test_settings = Settings()
    assert test_settings.security_check_passed is False


def test_ovito_internal_use_only(clean_env):
    """Test OVITO requires internal use flag"""
    os.environ["NFM_OVITO_ENABLED"] = "true"
    # Missing NFM_INTERNAL_USE flag

    test_settings = Settings()
    assert test_settings.security_check_passed is False


def test_ovito_internal_use_allowed(clean_env):
    """Test OVITO passes with internal use flag"""
    os.environ["NFM_OVITO_ENABLED"] = "true"
    os.environ["NFM_INTERNAL_USE"] = "true"

    test_settings = Settings()
    assert test_settings.security_check_passed is True


def test_verify_environment_success(clean_env):
    """Test environment verification succeeds with valid configuration"""
    os.environ["NFM_INTERNAL_USE"] = "true"

    assert verify_environment() is True


def test_verify_environment_hpc_incomplete(clean_env):
    """Test environment verification fails with incomplete HPC config"""
    os.environ["NFM_HPC_HOST"] = "tianjin.hpc.cn"
    os.environ["NFM_HPC_USER"] = "testuser"
    # Missing SSH key

    with pytest.raises(ValueError, match="Incomplete HPC configuration"):
        verify_environment()


def test_verify_environment_ovito_incomplete(clean_env):
    """Test environment verification fails with incomplete OVITO config"""
    os.environ["NFM_OVITO_ENABLED"] = "true"
    # Missing NFM_OVITO_PYTHON_PATH

    with pytest.raises(ValueError, match="OVITO enabled but.*not set"):
        verify_environment()


def test_directory_creation(clean_env, tmp_path):
    """Test that configured directories are created"""
    os.environ["NFM_WORKSPACE_DIR"] = str(tmp_path / "workspace")
    os.environ["NFM_OUTPUT_DIR"] = str(tmp_path / "output")

    test_settings = Settings()

    assert test_settings.workspace_dir.exists()
    assert test_settings.output_dir.exists()
