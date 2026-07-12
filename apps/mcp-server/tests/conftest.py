"""Shared test fixtures for the NFM MCP server test suite."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest

# NOTE: nfm_db imports are done lazily inside test functions or
# via tests that explicitly need them.  The integration test file
# imports nfm_db schemas directly since it tests Phase B wiring.
