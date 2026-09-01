"""Production-corpus guards for the KG→staging element_system pin (NFM-4048).

NFM-4037 normalised the Owen2023 morphology labels (``amorphous UO2``,
``Cr-doped UO2`` …) so the F8 scorecard's ``element_system`` predicates
become reachable.  Its E2E QA verdict raised two warnings:

* **W1** — 5 of the 17 production Owen2023 Material labels were never
  pinned by a test.  They worked, but nothing guarded them.
* **W2** — the audit-pin test only checked its own allowlist for internal
  consistency; it never consulted the production corpus.

``test_kg_to_staging_bridge`` now drives its pin from the checked-in
snapshot of production ``kg_nodes`` (W1 + W2).  This module adds the two
guards that belong beside it but not inside an already-large module:

* the *semantic* assertion — every prod label must reach an F8 bucket, so
  a future normalisation change cannot quietly strand a label off the
  scorecard even while staying self-consistent with its own pin;
* the *staleness* assertion — an opt-in check that the snapshot still
  matches the live corpus, for runs that have a database (CI does not).
"""

from __future__ import annotations

import os

import asyncpg
import pytest

from nfm_db.services.kg_to_staging_bridge import _canonical_element_system
from tests._helpers.owen2023_corpus import (
    F8_ACCEPTED_ELEMENT_SYSTEMS,
    NON_F8_PROD_LABELS,
    OWEN2023_SOURCE_ID,
    build_snapshot,
    load_snapshot,
    snapshot_labels,
)

#: asyncpg is the project-standard PostgreSQL driver (declared in
#: ``apps/api/pyproject.toml``).  NFM-4048 originally reached for
#: ``psycopg[binary]`` because the CR harness had it installed system-wide;
#: the project venv (``uv sync``) does not, so the drift check was silently
#: skipped — the very vacuous-guard pattern NFM-4048 exists to eliminate.
#: Importing eagerly at module top is safe: ``asyncpg`` is a hard dependency,
#: so the only ways this import fails are an interpreter fault (a real
#: configuration error worth surfacing) or a venv mismatch — both deserve
#: a hard failure rather than a silent skip.

#: Set to a libpq connection string to enable the live-corpus drift check.
#: Deliberately NOT ``DATABASE_URL``: pointing the drift check at whatever
#: database happens to be configured would make it fail on an empty dev shard.
_LIVE_DSN_ENV = "NFM_OWEN2023_CORPUS_DSN"

_LIVE_CORPUS_SQL = """
SELECT DISTINCT label
FROM kg_nodes
WHERE source_id = %(source_id)s
  AND node_type = 'Material'
"""

#: F8-reachable label count NFM-4037 measured on this corpus (16 of 17 — the
#: 17th is the ``Cr2O3`` carve-out).  A floor, not an equality: a growing
#: corpus should raise it, and lowering it must be a deliberate, explained edit.
_F8_REACHABILITY_FLOOR = 16


def test_build_snapshot_threads_source_id_and_query():
    """NFM-4051 CR LOW: ``build_snapshot`` must rebuild ``source_id`` and
    ``query`` from the caller's argument, not the previous template.

    Without this guarantee, ``scripts/.../refresh-owen2023-label-snapshot.py
    --source-id <other-uuid>`` would silently write foreign labels under
    the existing snapshot's provenance, while the
    ``provenance is complete`` guard continued to pass against the
    unchanged ``query`` field.
    """
    template = {
        "_comment": ["carry me through"],
        "captured_for_issue": "NFM-4048",
        "schema_version": 1,
        "source_name": "Owen2023",
        "node_type": "Material",
        "captured_from": "previous-run",
        "captured_at": "2026-08-31",
        "newest_node_created_at": "2026-08-31T00:00:00+00:00",
        "labels": ["UO2"],
        "label_count": 1,
        "source_id": OWEN2023_SOURCE_ID,
        "query": "SELECT DISTINCT label FROM kg_nodes WHERE source_id = "
        f"'{OWEN2023_SOURCE_ID}' AND node_type = 'Material'",
    }

    # Default source_id: round-trip preserves the checked-in fixture shape.
    out_default = build_snapshot(
        ["UO2"],
        captured_at="2026-09-01",
        captured_from="test",
        template=template,
    )
    assert out_default["source_id"] == OWEN2023_SOURCE_ID
    assert out_default["source_name"] == "Owen2023"
    assert (
        out_default["query"] == f"SELECT DISTINCT label FROM kg_nodes WHERE source_id = "
        f"'{OWEN2023_SOURCE_ID}' AND node_type = 'Material'"
    )

    # Custom source_id: rebuild source_id, source_name, and query from
    # the argument — template provenance must not leak through.
    other = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    out_custom = build_snapshot(
        ["UO2"],
        captured_at="2026-09-01",
        captured_from="test",
        source_id=other,
        template=template,
    )
    assert out_custom["source_id"] == other
    assert out_custom["source_name"] == "source-aaaaaaaa"
    assert f"'{other}'" in out_custom["query"]
    assert OWEN2023_SOURCE_ID not in out_custom["query"]
    # Only the truly orthogonal comment fields survive from template.
    assert out_custom["_comment"] == ["carry me through"]
    assert out_custom["captured_for_issue"] == "NFM-4048"


def test_snapshot_provenance_is_complete():
    """A snapshot without provenance cannot be audited or refreshed, and a
    reviewer has no way to judge how stale it is."""
    payload = load_snapshot()

    assert payload["source_id"] == OWEN2023_SOURCE_ID
    assert payload["node_type"] == "Material"
    for key in ("captured_at", "captured_from", "query"):
        value = payload.get(key)
        assert isinstance(value, str) and value.strip(), f"missing provenance: {key}"
    assert OWEN2023_SOURCE_ID in payload["query"], (
        "the recorded query must name the source_id it was run against, so a "
        "refresher reproduces the same slice"
    )


@pytest.mark.parametrize("label", snapshot_labels())
def test_every_prod_label_reaches_an_f8_bucket(label):
    """NFM-4048 AC-2/AC-4: each production Owen2023 Material label must
    canonicalise into an ``element_system`` the F8 scorecard accepts.

    ``NON_F8_PROD_LABELS`` is the documented carve-out: ``Cr2O3`` is the
    secondary chromia phase, not a UO2 matrix, so it correctly passes
    through rather than being coerced onto a bucket that would misdescribe
    its chemistry.  Any *new* label that lands outside the buckets is a
    reachability regression and fails here.
    """
    actual = _canonical_element_system(label)

    if label in NON_F8_PROD_LABELS:
        assert actual not in F8_ACCEPTED_ELEMENT_SYSTEMS, (
            f"{label!r} is listed as an intentional non-F8 pass-through but now "
            f"canonicalises to {actual!r}. If coercing it is correct, remove it "
            "from NON_F8_PROD_LABELS; if not, this is a bug."
        )
        assert actual == label, f"{label!r} should pass through unchanged, got {actual!r}"
        return

    assert actual in F8_ACCEPTED_ELEMENT_SYSTEMS, (
        f"{label!r} canonicalises to {actual!r}, which no F8 scorecard predicate "
        f"matches (accepted: {sorted(F8_ACCEPTED_ELEMENT_SYSTEMS)}). Rows for this "
        "material would be invisible to every F8 check."
    )


def test_f8_reachability_ratio_is_recorded():
    """NFM-4048 AC-4: pin the aggregate the zero-mutation measurement
    reports, so stranding labels shows up as a count regression and not
    only as a per-label failure.

    Two independent assertions, because they catch different mistakes:
    the identity catches a normalisation change that strands a label, and
    the floor catches the *other* way to make this suite green — deleting
    labels from the snapshot instead of pinning them.
    """
    labels = snapshot_labels()
    reachable = [
        label for label in labels if _canonical_element_system(label) in F8_ACCEPTED_ELEMENT_SYSTEMS
    ]
    carved_out = sorted(NON_F8_PROD_LABELS & set(labels))

    assert len(reachable) == len(labels) - len(carved_out), (
        f"{len(labels) - len(carved_out) - len(reachable)} label(s) beyond the "
        f"documented carve-out {carved_out} no longer reach an F8 bucket — see "
        "the per-label failures above for which."
    )

    assert len(reachable) >= _F8_REACHABILITY_FLOOR, (
        f"only {len(reachable)} label(s) reach an F8 bucket, below the "
        f"{_F8_REACHABILITY_FLOOR} NFM-4037 measured on this corpus. Either the "
        "snapshot lost labels (check `git diff` on the fixture — do not shrink it "
        "to silence the pin) or prod genuinely dropped an extraction, in which "
        "case lower this floor in the same commit that explains why."
    )


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get(_LIVE_DSN_ENV),
    reason=f"set {_LIVE_DSN_ENV} to diff the snapshot against the live KG corpus",
)
def test_snapshot_matches_live_corpus():
    """NFM-4048 AC-2 (literal form): iterate the live production corpus —
    every label in ``kg_nodes`` where ``source_id = 9320cb50-…`` — and
    assert the checked-in snapshot still describes it.

    Skipped by default: CI has no production database.  Run it from a host
    that does (``NFM_OWEN2023_CORPUS_DSN=postgresql://… pytest -m
    integration -k live_corpus``) to catch a snapshot that has gone stale.
    Read-only: a single SELECT, no writes, no schema access.

    Note (NFM-4051): uses ``asyncpg`` (declared in ``pyproject.toml``)
    rather than ``psycopg[binary]`` (not declared; the project venv has no
    copy, so the original ``importorskip`` silently skipped this test even
    when the DSN was set and the DB was reachable).  Because ``asyncpg`` is
    imported eagerly at module top, a missing driver is now a collection
    error — fail-loud, not silently skipped.
    """
    dsn = os.environ[_LIVE_DSN_ENV]

    async def _fetch() -> list[str]:
        conn = await asyncpg.connect(dsn, timeout=10)
        try:
            rows = await conn.fetch(
                _LIVE_CORPUS_SQL.replace("%(source_id)s", "$1"), OWEN2023_SOURCE_ID
            )
        finally:
            await conn.close()
        return sorted(row["label"] for row in rows)

    try:
        live = pytest_asyncio_run(_fetch())
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.fail(f"could not read the live Owen2023 corpus via {_LIVE_DSN_ENV}: {exc}")

    assert live, (
        f"the live corpus for source {OWEN2023_SOURCE_ID} is empty — the DSN "
        "probably points at a shard without the Owen2023 extraction, which "
        "would make this check vacuous"
    )

    snapshot = list(snapshot_labels())
    added = [label for label in live if label not in snapshot]
    removed = [label for label in snapshot if label not in live]
    assert not added and not removed, (
        "snapshot is stale — regenerate with "
        "scripts/nfm-4048-refresh-owen2023-label-snapshot.py "
        f"(new in prod: {added}; gone from prod: {removed})"
    )


def pytest_asyncio_run(coro):
    """Tiny shim that runs an asyncpg coroutine from a sync pytest test.

    ``pytest-asyncio`` is declared in the dev extra but this file uses the
    standard ``asyncpg`` library directly — no event-loop fixtures.  Keeping
    the runner inline avoids pulling ``pytest-asyncio`` into this test
    module's contract and lets the drift check live alongside the rest of
    ``test_kg_bridge_owen2023_prod_corpus.py`` without an ``asyncio_mode``
    global flip.
    """
    import asyncio

    return asyncio.run(coro)
