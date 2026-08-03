#!/usr/bin/env python3
"""AST-based CI guard against silent exception swallowing.

Walks all .py files under apps/api/src/ and flags any ``ast.ExceptHandler``
whose body consists entirely of ``ast.Pass`` statements (i.e. bare
``except …: pass`` with no logging, reraise, or other side-effect).

Lines annotated with ``# no-op: <reason>`` are exempt — the comment is
stripped before the AST check so the ``pass`` node is never visited.

Exit codes:
    0 — no silent catches found
    1 — one or more silent catches detected (CI failure)

Reference: NFM-2410 / NFM-2408
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

NOOP_PATTERN = re.compile(r"#\s*no-op:\s*.+$")

TARGET_DIR = Path("apps/api/src")


def _source_has_noop_annotation(source: str, lineno: int) -> bool:
    """Return True if the given 1-based line number carries a ``# no-op:`` comment."""
    lines = source.splitlines()
    if lineno - 1 < 0 or lineno - 1 >= len(lines):
        return False
    return bool(NOOP_PATTERN.search(lines[lineno - 1]))


class SilentCatchVisitor(ast.NodeVisitor):
    """Collect file:line locations of except handlers whose body is only ``pass``."""

    def __init__(self, source: str, filename: str) -> None:
        super().__init__()
        self._source = source
        self._filename = filename
        self.violations: list[str] = []

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.generic_visit(node)

        # Skip if the `pass` line carries a `# no-op:` annotation
        for child in node.body:
            if isinstance(child, ast.Pass):
                if _source_has_noop_annotation(self._source, child.lineno):
                    return

        # Flag if the body is empty or consists solely of Pass nodes
        non_pass = [n for n in node.body if not isinstance(n, ast.Pass)]
        if not non_pass:
            self.violations.append(f"{self._filename}:{node.lineno}")


def check_file(path: Path) -> list[str]:
    """Parse a single Python file and return any silent-catch violations."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    visitor = SilentCatchVisitor(source, str(path))
    visitor.visit(tree)
    return visitor.violations


def check_directory(root: Path) -> list[str]:
    """Walk *root* recursively and collect violations from all .py files."""
    violations: list[str] = []
    for py_file in sorted(root.rglob("*.py")):
        violations.extend(check_file(py_file))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AST-based check for silent exception swallowing",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=str(TARGET_DIR),
        help="Directory to scan (default: apps/api/src)",
    )
    args = parser.parse_args()

    target = Path(args.target)
    if not target.is_dir():
        print(f"Error: {target} is not a directory", file=sys.stderr)
        return 2

    violations = check_directory(target)

    if violations:
        print(f"Silent exception swallowing detected ({len(violations)}):")
        for v in violations:
            print(f"  {v}")
        return 1

    print("OK: no silent exception swallowing found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
