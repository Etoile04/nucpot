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

import asyncio
import os

import pytest

from nfm_db.services.kg_to_staging_bridge import _canonical_element_system
from tests._helpers.owen2023_corpus import (
    F8_ACCEPTED_ELEMENT_SYSTEMS,
    NON_F8_PROD_LABELS,
    OWEN2023_SOURCE_ID,
    load_snapshot,
    snapshot_labels,
)

#: Set to a libpq connection string to enable the live-corpus drift check.
#: Deliberately NOT ``DATABASE_URL``: pointing the drift check at whatever
#: database happens to be configured would make it fail on an empty dev shard.
#: Format is the plain ``postgresql://user:pass@host:port/db`` form that
#: ``asyncpg.connect`` accepts (the SQLAlchemy ``+asyncpg`` suffix is *not*
#: recognised by ``asyncpg`` itself — pass a libpq URL or the keyword form).
_LIVE_DSN_ENV = "NFM_OWEN2023_CORPUS_DSN"

_LIVE_CORPUS_SQL = """
SELECT DISTINCT label
FROM kg_nodes
WHERE source_id = $1
  AND node_type = 'Material'
"""

#: F8-reachable label count NFM-4037 measured on this corpus (16 of 17 — the
#: 17th is the ``Cr2O3`` carve-out).  A floor, not an equality: a growing
#: corpus should raise it, and lowering it must be a deliberate, explained edit.
_F8_REACHABILITY_FLOOR = 16

#: Hard ceiling on a single drift-check run.  The prod SELECT is bounded by
#: the size of ``kg_nodes`` for one source_id, but the connection itself
#: should never block the suite indefinitely when something is wrong.
_LIVE_CONNECT_TIMEOUT_SECONDS = 10


async def _fetch_live_labels(dsn: str) -> list[str]:
    """Open a short-lived asyncpg connection and return the live Owen2023 labels.

    Parameterised query (``$1``) — never string-interpolate the source_id.
    """
    import asyncpg  # lazy: imported after the skipif so absence is a fail, not an ImportError at collect

    conn = await asyncpg.connect(dsn, timeout=_LIVE_CONNECT_TIMEOUT_SECONDS)
    try:
        rows = await conn.fetch(_LIVE_CORPUS_SQL, OWEN2023_SOURCE_ID)
    finally:
        await conn.close()
    return sorted(row["label"] for row in rows)


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

    Driver-missing is an **error**, not a skip: if the operator set the
    DSN they asked for the drift check, and a green "skip" silently
    neuters the audit pin (the vacuous-guard failure NFM-4048 exists to
    close).  ``asyncpg`` is the project's declared PG driver
    (``pyproject.toml``), so its absence is an environment problem the
    operator needs to see.
    """
    import asyncpg  # lazy: see the fail-loud ImportError branch below

    dsn = os.environ[_LIVE_DSN_ENV]
    try:
        live = asyncio.run(_fetch_live_labels(dsn))
    except ImportError as exc:
        # Defensive: asyncpg was importable at module import time but the
        # asyncpg.connect() call pulled in something unavailable.  Treat
        # the same as a driver-missing at collection time.
        pytest.fail(
            f"{_LIVE_DSN_ENV} is set but asyncpg cannot complete its import "
            f"graph ({exc}). Install the project dependencies (see "
            "`pyproject.toml` — `asyncpg>=0.30.0` is declared) or unset "
            f"{_LIVE_DSN_ENV} to skip the drift check."
        )
    except (
        OSError,
        asyncpg.PostgresError,
        TimeoutError,
    ) as exc:
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
