"""
Pytest configuration for nfm-md-runner test suite.

Registers custom markers and provides shared fixtures.
"""

import pytest


def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line(
        "markers",
        "hpc: Tests requiring real HPC cluster access (skipped in CI by default)",
    )
    config.addinivalue_line(
        "markers",
        "integration: Integration tests requiring external dependencies",
    )
    config.addinivalue_line(
        "markers",
        "unit: Unit tests (no external dependencies)",
    )
    config.addinivalue_line(
        "markers",
        "security: Security-focused tests (credential handling, SSH safety)",
    )
