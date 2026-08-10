"""Path-safety utilities (NFM-2781 HOTFIX CR1).

The ``safe_resolve()`` helper guards
:func:`nfm_db.api.v1.extraction_gaps.get_gap_source_text` against
path-traversal attacks via ``chunk.source_reference``.  Before this
module existed, the endpoint passed arbitrary ``source_ref`` strings
straight to ``pathlib.Path(...).read_text()`` — a ``domain_expert`` user
could set ``source_reference = "/etc/passwd"`` or
``"../../../../proc/self/environ"`` and read arbitrary server files.

The contract:

* ``safe_resolve(source_ref, allowlist)`` returns the resolved
  ``pathlib.Path`` after confirming it lives inside ``allowlist``.
* Any escape attempt (parent-relative, absolute, or symlink) raises
  :class:`PathNotAllowedError` carrying the *attempted* path (not the
  resolved one) and a short reason for logging.

The allowlist base directory is provided by callers — typically wired
through :class:`nfm_db.config.Settings` via the ``NFM_SOURCE_BASE`` env
var with default ``/var/nfm-data/sources/``.
"""

from __future__ import annotations

from pathlib import Path


class PathNotAllowedError(Exception):
    """Raised when a source path escapes the configured allowlist.

    Attributes:
        attempted_path: The raw string the caller supplied (NOT the
            resolved path, to avoid leaking server-side location hints
            in the exception message).
        reason: Short human-readable reason for logging.
    """

    def __init__(self, attempted_path: str, reason: str) -> None:
        self.attempted_path = attempted_path
        self.reason = reason
        super().__init__(f"Path not allowed: {reason} ({attempted_path!r})")


def safe_resolve(source_ref: str, allowlist: Path | str) -> Path:
    """Resolve ``source_ref`` against the allowlist and return the Path.

    The check sequence is:

    1. Coerce both sides to ``pathlib.Path`` and ``resolve()`` them.
       ``Path.resolve()`` already normalises ``..`` segments and follows
       symlinks on the way to the canonical real path, so a single
       resolve call covers all three escape vectors.
    2. Confirm the resolved path is the same as, or a descendant of,
       the resolved allowlist via :meth:`Path.is_relative_to`.
    3. If not, raise :class:`PathNotAllowedError`.

    Args:
        source_ref: Arbitrary caller-supplied path string.
        allowlist: Allowlist base directory (Path or str).

    Returns:
        The resolved :class:`pathlib.Path`.

    Raises:
        PathNotAllowedError: If the resolved path escapes the allowlist
            via ``..``, absolute path, or symlink.
    """
    if not isinstance(source_ref, str):
        raise TypeError(
            f"source_ref must be a str, got {type(source_ref).__name__}",
        )

    base = Path(allowlist).resolve()

    # Empty source_ref is a degenerate case; treat it as not-allowed so
    # callers cannot accidentally pass through an empty string.
    if not source_ref:
        raise PathNotAllowedError(
            attempted_path=source_ref,
            reason="empty source path",
        )

    try:
        candidate = Path(source_ref).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathNotAllowedError(
            attempted_path=source_ref,
            reason=f"unresolvable path: {exc}",
        ) from exc

    # Path.is_relative_to() (Python 3.9+) returns True if `candidate`
    # is the same as `base` or a descendant.  This is the single check
    # that catches all three escape vectors because `Path.resolve()`
    # has already collapsed `..` and chased symlinks.
    if not candidate.is_relative_to(base):
        # Build a short reason that does NOT echo the resolved server
        # path (which could leak the layout of the production host).
        if (
            source_ref.startswith("..")
            or "/.." in source_ref
            or source_ref.endswith("..")
        ):
            reason = "parent-directory escape outside allowlist"
        elif source_ref.startswith("/"):
            reason = "absolute path outside allowlist"
        else:
            reason = "symlink or canonical path outside allowlist"

        raise PathNotAllowedError(
            attempted_path=source_ref,
            reason=reason,
        )

    return candidate


__all__ = [
    "PathNotAllowedError",
    "safe_resolve",
]
