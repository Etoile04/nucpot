"""Test configuration for NFM domain skills validation."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "skill: mark test as skill-specific")
