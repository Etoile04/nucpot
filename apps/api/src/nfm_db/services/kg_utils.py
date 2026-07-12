"""Shared utilities for Knowledge Graph services."""

from __future__ import annotations

import json


def parse_aliases(raw: str | None) -> list[str]:
    """Parse a JSON-encoded aliases column into ``list[str]``.

    Canonical behaviour:
    * ``None`` → ``[]``
    * Valid JSON list → every element coerced to ``str``
    * Non-list JSON or invalid JSON → ``[]``
    """
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
        return []
    except (json.JSONDecodeError, TypeError):
        return []
