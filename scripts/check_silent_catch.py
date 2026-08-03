#!/usr/bin/env python3
"""Fail when Python source contains undocumented pass-only exception handlers."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

NO_OP_MARKER = "# no-op:"


def _is_documented_pass(source_lines: list[str], statement: ast.stmt) -> bool:
    """Return whether a pass statement has an inline no-op reason."""
    return NO_OP_MARKER in source_lines[statement.lineno - 1]


def find_silent_catches(root: Path) -> list[tuple[Path, int]]:
    """Return pass-only exception handlers lacking a documented reason."""
    violations: list[tuple[Path, int]] = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        source_lines = source.splitlines()
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or not node.body:
                continue
            if not all(isinstance(statement, ast.Pass) for statement in node.body):
                continue
            if all(_is_documented_pass(source_lines, statement) for statement in node.body):
                continue
            violations.append((path, node.lineno))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("apps/api/src"))
    args = parser.parse_args()

    violations = find_silent_catches(args.root)
    for path, line in violations:
        print(f"{path}:{line}: undocumented silent exception handler")
    if violations:
        print(f"Found {len(violations)} undocumented silent exception handler(s).")
        return 1
    print("No undocumented silent exception handlers found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
