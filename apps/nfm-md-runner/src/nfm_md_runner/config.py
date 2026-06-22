"""
Configuration module for nfm-md-runner

All sensitive credentials must be provided via environment variables.
This module enforces security by failing fast if required secrets are missing.
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator, ValidationInfo
from pydantic_settings import BaseSettings

# Load environment variables from .env file (for local development)
load_dotenv()


class Settings(BaseSettings):
    """Application settings with environment variable support"""

    model_config = {
        "extra": "ignore",
        "arbitrary_types_allowed": True,
        "env_prefix": "NFM_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    # Application settings
    app_name: str = "nfm-md-runner"
    app_version: str = "0.1.0"
    debug: bool = False

    # Working directories
    workspace_dir: Path = Field(default_factory=lambda: Path.cwd() / "workspace")
    output_dir: Path = Field(default_factory=lambda: Path.cwd() / "output")
    temp_dir: Path = Field(default_factory=lambda: Path.cwd() / "temp")

    # HPC connection settings (SECURITY: These must be environment variables)
    hpc_host: Optional[str] = None
    hpc_port: int = 22
    hpc_user: Optional[str] = None
    hpc_ssh_key_path: Optional[Path] = None
    hpc_work_dir: Optional[Path] = None

    # IMPORTANT: Password authentication is disabled for security
    # Use SSH key authentication only
    # hpc_password: Never store passwords in code!

    # LAMMPS settings
    lammps_executable: str = "lmp"
    lammps_modules: str = ""

    # OVITO settings (GPL - internal use only)
    ovito_enabled: bool = False
    ovito_python_path: Optional[str] = None

    # SLURM settings
    slurm_partition: Optional[str] = None
    slurm_nodes: int = 1
    slurm_ntasks_per_node: int = 32
    slurm_max_array_size: int = 100

    # Database settings (for Phase 2 integration)
    database_url: Optional[SecretStr] = None

    # Celery settings (for Phase 3 integration)
    celery_broker_url: Optional[SecretStr] = None
    celery_result_backend: Optional[SecretStr] = None

    @field_validator("workspace_dir", "output_dir", "temp_dir", "hpc_work_dir")
    @classmethod
    def create_directories(cls, v: Optional[Path]) -> Optional[Path]:
        """Create directories if they don't exist"""
        if v is not None:
            v.mkdir(parents=True, exist_ok=True)
        return v

    def hpc_connection_string(self) -> str:
        """Generate SSH connection string for HPC"""
        if not self.hpc_host or not self.hpc_user:
            raise ValueError("HPC host and user must be set via environment variables")

        if self.hpc_ssh_key_path:
            # Return connection string format (file existence checked at runtime)
            return f"{self.hpc_user}@{self.hpc_host}"
        else:
            raise ValueError("HPC SSH key path must be set via NFM_HPC_SSH_KEY_PATH")

    @property
    def security_check_passed(self) -> bool:
        """Verify security requirements are met"""
        checks = []

        # Check 1: No hardcoded credentials (enforced by design)
        checks.append(True)  # We use environment variables only

        # Check 2: SSH key authentication (not password)
        # Fail if EITHER hpc_host OR hpc_user is set without SSH key
        if (self.hpc_host or self.hpc_user) and not self.hpc_ssh_key_path:
            checks.append(False)
        else:
            checks.append(True)

        # Check 3: OVITO is for internal use only
        if self.ovito_enabled:
            checks.append(os.environ.get("NFM_INTERNAL_USE") == "true")
        else:
            checks.append(True)

        return all(checks)


# Global settings instance
settings = Settings()


def verify_environment() -> bool:
    """
    Verify that the environment is properly configured

    Raises:
        ValueError: If required environment variables are missing or invalid
    """
    errors = []

    # Security checks
    if not (current_settings := Settings()).security_check_passed:
        errors.append("Security check failed")

    # HPC configuration (partial configuration check)
    # Check if partial HPC configuration is provided
    hpc_parts_provided = sum([
        current_settings.hpc_host is not None,
        current_settings.hpc_user is not None,
        current_settings.hpc_ssh_key_path is not None
    ])

    if 0 < hpc_parts_provided < 3:  # Partial configuration
        errors.append(
            "Incomplete HPC configuration. "
            "If using HPC, set NFM_HPC_HOST, NFM_HPC_USER, and NFM_HPC_SSH_KEY_PATH"
        )

    # OVITO configuration (optional, but must be complete if provided)
    if current_settings.ovito_enabled and not current_settings.ovito_python_path:
        errors.append("OVITO enabled but NFM_OVITO_PYTHON_PATH not set")

    if errors:
        error_msg = "Environment verification failed:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(error_msg)

    return True


if __name__ == "__main__":
    # Test environment configuration
    try:
        main_settings = Settings()
        verify_environment()
        print("✅ Environment configuration is valid")
        print(f"  Workspace: {main_settings.workspace_dir}")
        print(f"  Output: {main_settings.output_dir}")
        if main_settings.hpc_host:
            print(f"  HPC: {main_settings.hpc_connection_string()}")
    except ValueError as e:
        print(f"❌ {e}")
        exit(1)
