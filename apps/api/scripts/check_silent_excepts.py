#!/usr/bin/env python3
"""AST-based CI guard against silent exception swallowing.

Walks ``apps/api/src/**/*.py`` and reports any ``ast.ExceptHandler`` whose
body silently swallows the caught exception without observable side-effects:

  * ``ast.Pass`` — bare ``except ...: pass``
  * ``ast.Expr(Constant(value=Ellipsis))`` — lone ``except ...: ...``
  * ``ast.Continue`` with no logging call anywhere in the handler body

The following are explicitly **not** violations:

  * ``return`` (including ``return None``) — observable behaviour, not a
    silent swallow.
  * ``continue`` preceded or accompanied by a logging call (e.g.
    ``logger.warning(...)``, ``logging.error(...)``,
    ``logging.getLogger(__name__).error(...)``).

Detected findings can be suppressed by entries in the TOML allowlist at
``apps/api/scripts/silent_except_allowlist.toml``. Every allowlist entry
MUST carry a non-empty ``justification``; otherwise the checker exits
with an :class:`AllowlistConfigError` before scanning.

Exit codes
----------
``0``  no findings remain after allowlist suppression.
``1``  one or more findings remain (CI failure).
``2``  configuration error (bad allowlist, missing target directory).

References: NFM-2491, NFM-2234 (AC), NFM-2410 (predecessor at
``scripts/check_silent_catch.py``).
"""

from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class AllowlistConfigError(ValueError):
    """Raised when the TOML allowlist is malformed or has an empty justification."""


class AllowlistKey(NamedTuple):
    """Identifier for a specific suppressed finding."""

    path: str
    line: int
    category: str


class Finding(NamedTuple):
    """A single silent-except violation."""

    path: str
    line: int
    category: str
    snippet: str


# ---------------------------------------------------------------------------
# Logging detection
# ---------------------------------------------------------------------------

# Method names that count as a logging call when invoked on a logger-like
# object. ``warn`` is included for the deprecated alias still seen in the
# wild (``logging.warn``).
_LOG_METHODS: frozenset[str] = frozenset(
    {"debug", "info", "warning", "warn", "error", "critical", "exception", "log"}
)
# Names that conventionally refer to a logger instance at module scope.
_LOGGER_NAMES: frozenset[str] = frozenset({"logger", "logging", "log"})


def _is_logging_call(node: ast.AST) -> bool:
    """Return True if *node* is a call to a logger method."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    # logger.warning(...) / logging.error(...) / log.info(...)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id in _LOGGER_NAMES and func.attr in _LOG_METHODS:
            return True
    # logging.getLogger(__name__).warning(...)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
        inner = func.value.func
        if (
            isinstance(inner, ast.Attribute)
            and isinstance(inner.value, ast.Name)
            and inner.value.id == "logging"
            and inner.attr == "getLogger"
            and func.attr in _LOG_METHODS
        ):
            return True
    return False


def _handler_has_logging(handler: ast.ExceptHandler) -> bool:
    """True if the handler body contains any logging call."""
    return any(_is_logging_call(child) for child in ast.walk(handler))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify(stmt: ast.stmt) -> str | None:
    """Return the violation category for *stmt* or ``None`` if it is silent-safe."""
    if isinstance(stmt, ast.Pass):
        return "pass"
    if (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    ):
        return "ellipsis"
    if isinstance(stmt, ast.Continue):
        return "continue"
    return None


def _snippet(source: str, lineno: int) -> str:
    """Return the stripped source line for *lineno* (1-based), or ``""`` if out of range."""
    lines = source.splitlines()
    index = lineno - 1
    if 0 <= index < len(lines):
        return lines[index].strip()
    return ""


# ---------------------------------------------------------------------------
# File and directory scanning
# ---------------------------------------------------------------------------


def check_file(path: Path, allowlist: dict[AllowlistKey, str]) -> list[Finding]:
    """Return all unsuppressed silent-except findings in *path*."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    findings: list[Finding] = []
    path_str = str(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        has_logging = _handler_has_logging(node)
        handler_line = node.lineno
        for stmt in node.body:
            category = _classify(stmt)
            if category is None:
                continue
            if category == "continue" and has_logging:
                # `continue` after a logging call is observably handled.
                continue
            key = AllowlistKey(path=path_str, line=handler_line, category=category)
            if key in allowlist:
                continue
            findings.append(
                Finding(
                    path=path_str,
                    line=handler_line,
                    category=category,
                    snippet=_snippet(source, stmt.lineno),
                )
            )
    return findings


def _walk_python_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        yield path


def check_directory(
    root: Path, allowlist: dict[AllowlistKey, str]
) -> list[Finding]:
    """Walk *root* recursively and collect findings from every ``*.py`` file."""
    findings: list[Finding] = []
    for py_file in _walk_python_files(root):
        findings.extend(check_file(py_file, allowlist))
    return findings


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


def load_allowlist(path: Path) -> dict[AllowlistKey, str]:
    """Load a TOML allowlist.

    Every ``[[allowlist]]`` entry MUST have ``path``, ``line``, ``category``,
    and a non-empty ``justification``. Raises :class:`AllowlistConfigError`
    on any malformed entry.
    """
    try:
        with Path(path).open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise AllowlistConfigError(f"Allowlist file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise AllowlistConfigError(f"Invalid TOML in {path}: {exc}") from exc

    entries = data.get("allowlist", [])
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise AllowlistConfigError(
            "`allowlist` must be an array of tables (e.g. [[allowlist]])"
        )

    result: dict[AllowlistKey, str] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise AllowlistConfigError(
                f"allowlist[{index}] must be a table"
            )
        missing = {"path", "line", "category", "justification"} - set(entry.keys())
        if missing:
            raise AllowlistConfigError(
                f"allowlist[{index}] missing required fields: {sorted(missing)}"
            )
        justification = entry["justification"]
        if not isinstance(justification, str) or not justification.strip():
            raise AllowlistConfigError(
                f"allowlist[{index}] requires a non-empty justification "
                "(NFM-2234 AC: documented rationale is mandatory)"
            )
        key = AllowlistKey(
            path=str(entry["path"]),
            line=int(entry["line"]),
            category=str(entry["category"]),
        )
        result[key] = justification
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


_DEFAULT_ROOT = Path("apps/api/src")
_DEFAULT_ALLOWLIST = Path("apps/api/scripts/silent_except_allowlist.toml")


def _emit_github_error(finding: Finding) -> None:
    """Emit a GitHub Actions ``::error::`` annotation for *finding*."""
    message = (
        f"silent {finding.category} in except handler "
        f"(no logging, no reraise): {finding.snippet}"
    )
    print(
        f"::error file={finding.path},line={finding.line}::" f"{message}",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AST-based detector for silent exception swallows.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_DEFAULT_ROOT,
        help=f"Root directory to walk (default: {_DEFAULT_ROOT})",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=_DEFAULT_ALLOWLIST,
        help=(
            "Path to TOML allowlist (default: "
            f"{_DEFAULT_ALLOWLIST})"
        ),
    )
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        print(f"Error: {args.root} is not a directory", file=sys.stderr)
        return 2

    allowlist: dict[AllowlistKey, str] = {}
    if args.allowlist.exists():
        try:
            allowlist = load_allowlist(args.allowlist)
        except AllowlistConfigError as exc:
            print(f"Allowlist configuration error: {exc}", file=sys.stderr)
            return 2
    else:
        print(
            f"Note: allowlist {args.allowlist} not found; "
            "running with no suppressions.",
            file=sys.stderr,
        )

    findings = check_directory(args.root, allowlist)

    for finding in findings:
        _emit_github_error(finding)

    if findings:
        print(
            f"\n{len(findings)} silent-except finding(s) detected "
            f"under {args.root}.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: no silent exception swallowing found under {args.root}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())