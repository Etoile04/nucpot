"""Configuration management for NucPot Verification Service."""

from __future__ import annotations

import os


class Settings:
    """Application settings from environment variables."""

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://nucpot:***@localhost:5432/nucpot",
    )

    # Service
    HOST: str = os.getenv("VERIFY_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("VERIFY_PORT", "8000"))

    # Calculation
    ASE_CONVERGENCE: float = float(os.getenv("ASE_CONVERGENCE", "0.001"))
    MAX_STEPS: int = int(os.getenv("ASE_MAX_STEPS", "200"))


settings = Settings()
