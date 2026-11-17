"""I5 assembly-equivalence guard: create_app() faces vs. stored snapshots (NFM-5982).

C2 step 2 (NFM-5951, PR #1543) turned the module-level singleton assembly into
the ``create_app()`` factory.  This file — per the QA board re-review ruling
(2026-11-12, plan A) and ADR-026 report section C2 / item I5 — turns the
one-off "factory output == legacy singleton" comparison into an in-repo
regression test: any later assembly change (C2 step 4+ cluster split, mount
add/remove, middleware shuffle) must pass here first.

Four assertion faces:

1. **openapi path snapshot** — ``sorted(create_app().openapi()["paths"])``
   equals ``assembly_snapshots/openapi_paths.json`` path-for-path; failures
   print the added/missing path sets (a bare count cannot localize drift).
2. **middleware chain order** — the ``app.user_middleware`` class-name
   sequence equals the snapshot, order-sensitive.  Starlette's
   ``add_middleware`` is ``insert(0, ...)``, so **index 0 = outermost =
   last-added**; router mount order simultaneously decides route
   first-match resolution and openapi operation order — the mount face is
   pinned by assertion 1, the wrapping semantics by this one.
3. **exception-handler key set** — ``sorted(app.exception_handlers.keys())``
   (str-normalized: int status-code keys -> decimal string, class keys ->
   dotted qualified name) equals the snapshot.
4. **factory has no shared mutable state** — two consecutive ``create_app()``
   calls agree on all three faces.

Snapshot baseline: exported from the factory assembly at NFM-5982 landing.
Valid as the main baseline because (a) the parent ticket's AC3 verified the
one-off factory == legacy-singleton equivalence, (b) origin/main @ b340226f9
had no apps/api changes pending at snapshot time, and (c) main's inline
assembly issues exactly 46 ``include_router`` calls, matching the 46 entries
of ``ALL_ROUTER_MOUNTS``.  Snapshots are committed data — these tests assert
against the stored files, never against create_app() self-comparison
(assertion 4 is the explicitly-demanded intra-factory check).

Pure assembly surface: FastAPI construction + ``add_middleware`` +
``include_router`` + openapi schema derivation are all in-memory; lifespan
does not run at construction — no DB / network dependency.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI

from tests.support.app_factory import create_app

SNAPSHOTS_DIR = Path(__file__).resolve().parent / "assembly_snapshots"


def _build_app() -> FastAPI:
    """Full production assembly — the entry the ``nfm_db.main:app`` singleton uses."""
    return create_app()


def _load_snapshot(name: str) -> list[str]:
    """Load a stored snapshot list verbatim (storage order is assertion order)."""
    return json.loads((SNAPSHOTS_DIR / name).read_text(encoding="utf-8"))


def _openapi_paths(application: FastAPI) -> list[str]:
    return sorted(application.openapi()["paths"])


def _middleware_names(application: FastAPI) -> list[str]:
    """Class-name sequence of ``user_middleware`` in stored order.

    Stored order is index order: index 0 = outermost = last-added
    (Starlette ``add_middleware`` prepends), so the sequence is
    order-sensitive on purpose.
    """
    return [f"{mw.cls.__module__}.{mw.cls.__qualname__}" for mw in application.user_middleware]


def _handler_keys(application: FastAPI) -> list[str]:
    """Sorted, str-normalized ``exception_handlers`` keys.

    Status-code keys (int) -> decimal string; class keys -> dotted qualified
    name, so both registry key shapes compare as plain strings.
    """

    def _normalize(key: int | type[Exception]) -> str:
        return str(key) if isinstance(key, int) else f"{key.__module__}.{key.__qualname__}"

    return sorted(_normalize(k) for k in application.exception_handlers)


def test_openapi_paths_match_snapshot() -> None:
    """Assembly openapi paths equal the stored snapshot path-for-path."""
    actual = _openapi_paths(_build_app())
    expected = _load_snapshot("openapi_paths.json")
    if actual != expected:
        added = sorted(set(actual) - set(expected))
        missing = sorted(set(expected) - set(actual))
        # Failure must name the added/missing path sets — a bare count cannot
        # localize an assembly drift to the offending mount.
        pytest.fail(
            "openapi paths diverged from snapshot: "
            f"added (actual-not-in-snapshot, {len(added)}): {added}; "
            f"missing (snapshot-not-in-actual, {len(missing)}): {missing}"
        )
    assert actual == expected


def test_user_middleware_order_matches_snapshot() -> None:
    """Middleware chain equals the snapshot class-name sequence (order-sensitive)."""
    actual = _middleware_names(_build_app())
    expected = _load_snapshot("middleware_order.json")
    assert actual == expected, (
        f"user_middleware order drift (index 0 = outermost = last-added): "
        f"actual={actual} snapshot={expected}"
    )


def test_exception_handler_keys_match_snapshot() -> None:
    """Exception-handler key set equals the snapshot (str-normalized, sorted)."""
    actual = _handler_keys(_build_app())
    expected = _load_snapshot("exception_handlers.json")
    assert actual == expected, (
        f"exception_handlers key-set drift: actual={actual} snapshot={expected}"
    )


def test_create_app_no_shared_mutable_state() -> None:
    """Two consecutive create_app() calls agree on all three assembly faces."""
    first, second = _build_app(), _build_app()
    assert _openapi_paths(first) == _openapi_paths(second)
    assert _middleware_names(first) == _middleware_names(second)
    assert _handler_keys(first) == _handler_keys(second)
