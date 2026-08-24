"""Stub ``paperclip_issue_lookup`` for the
``tools/reconcile_cancelled_blockers.py`` subprocess smoke test
(NFM-3600 RE feedback).

Mirrors the public surface of ``scripts/paperclip_issue_lookup.py`` that
``tools/reconcile_cancelled_blockers.py`` actually consumes
(``Ok``, ``ApiError``, ``lookup_issues``, ``lookup_issue``) but never
hits the network — the stub returns synthetic :class:`Ok` results.

The subprocess test (``apps/api/tests/test_tools_reconcile_cancelled_blockers.py``)
pre-injects this module into :data:`sys.modules` *before* invoking the
real script via :func:`runpy.run_path`, so the script's own
``sys.path.insert(0, str(_SCRIPTS))`` cannot shadow it.

The real helper also exposes ``NotFound``, ``AuthError``, ``WrongPath``
and ``WrongPathError``; this stub deliberately omits them because the
dry-run script does not import them and N818 (Error-suffix rule) would
otherwise force a name drift between stub and production.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Ok:
    """Synthetic successful lookup — never hits the network."""

    issues: list[dict] = field(default_factory=list)
    pages_consumed: int = 1
    truncated: bool = False


@dataclass(frozen=True)
class ApiError:
    """Synthetic API error — mirrors the real helper's dataclass shape."""

    http_status: int
    body: str
    kind: str = "unknown"


def lookup_issues(
    q: str | None = None,
    status: list[str] | None = None,
    assignee_agent_id: str | None = None,
    project_id: str | None = None,
    max_pages: int = 10,
) -> Ok:
    """Return a synthetic ``Ok(issues=[])`` — never hits the network.

    Signature MUST stay in lock-step with the real helper. The smoke
    test verifies the kwargs accepted by ``tools/reconcile_cancelled_blockers.py``
    are a subset of those accepted by the real helper.
    """
    return Ok()


def lookup_issue(identifier: str, max_pages: int = 10) -> Ok:
    """Per-identifier stub — not exercised by the dry-run path but kept
    for surface parity with the real helper.

    The real helper returns ``Ok(issues=[row])`` for a found identifier
    or ``NotFound`` otherwise; for the smoke test we always return an
    empty ``Ok`` so the dry-run script's `_collect_paperclip_dependents`
    loop sees zero dependents and exits cleanly.
    """
    return Ok()


__all__ = [
    "ApiError",
    "Ok",
    "lookup_issue",
    "lookup_issues",
]
