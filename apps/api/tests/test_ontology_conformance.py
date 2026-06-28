"""Dual-provider NVL contract conformance gate (T2 — HARD CI GATE).

The SAME ``assert_nvl_contract`` assertions run against every provider so any
drift turns CI red (NFM-266 invariant #2). Providers:

* the canonical versioned fixture (``tests/fixtures/nvl_contract_sample.json``)
  — the frozen reference a corpus resolves to;
* the real vendored viewer artifact — element-only (it predates the envelope),
  proves the contract truthfully describes what the viewer consumes;
* the backend endpoint output (TestClient) — RED until the endpoint lands in T4,
  then the live drift firewall.

Contract-as-firewall (NFM-246 ADR): the viewer consumes this exact element shape,
so a Phase 1 data-source swap breaks zero viewer code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.nvl_conformance import (
    ContractViolationError,
    _assert_valid_record_ref,
    assert_nvl_contract,
)
from tests.ontology_seed import seed_corpus

_FIXTURES = Path(__file__).parent / "fixtures"
_CANONICAL_FIXTURE = _FIXTURES / "nvl_contract_sample.json"
_VIEWER_ARTIFACT = (
    Path(__file__).resolve().parents[2]
    / "apps" / "web" / "public" / "ontology-viewer" / "data"
    / "nvl_ontology_data.json"
)

FIXTURE_CORPUS = "smirnov2014"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Provider 1 — canonical versioned fixture
# ---------------------------------------------------------------------------


def test_canonical_fixture_conforms_to_contract() -> None:
    """The frozen static fixture passes full contract conformance."""
    payload = _load_json(_CANONICAL_FIXTURE)
    assert_nvl_contract(payload, corpus_id=FIXTURE_CORPUS, check_record_ref=True)


# ---------------------------------------------------------------------------
# Provider 1b — real vendored viewer artifact (element-only, optional)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VIEWER_ARTIFACT.exists(),
    reason="vendored viewer artifact not present in this checkout",
)
def test_real_viewer_artifact_conforms_to_element_contract() -> None:
    """The artifact the Phase 0 viewer ships conforms to the element contract.

    Element-only (no envelope): the vendored file is pre-versioning. This proves
    the contract's element rules match viewer reality — the firewall foundation.
    """
    payload = _load_json(_VIEWER_ARTIFACT)
    assert_nvl_contract(
        payload,
        check_envelope=False,
        check_stats_consistency=False,
        check_record_ref=False,
    )


# ---------------------------------------------------------------------------
# Provider 2 — backend endpoint (RED until T4; the live drift firewall)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_endpoint_conforms_to_contract(
    async_client,
    db_session: AsyncSession,
) -> None:
    """The backend endpoint output conforms to the versioned contract.

    Seeded corpus ``smirnov2014`` must derive a graph that passes the SAME
    conformance checker as the static fixture. RED until the ontology endpoint
    (T4) and derivation service (T3) land.
    """
    await seed_corpus(
        db_session,
        source=FIXTURE_CORPUS,
        rows=[
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
            },
            {
                "element_system": "U",
                "property_name": "bulk_modulus",
                "value": 200.0,
                "unit": "GPa",
                "method": "EXP",
            },
        ],
    )

    response = await async_client.get(
        f"/api/v1/ontology/corpora/{FIXTURE_CORPUS}/graph",
    )
    assert response.status_code == 200, response.text
    assert_nvl_contract(
        response.json(),
        corpus_id=FIXTURE_CORPUS,
        check_record_ref=True,
    )


# ---------------------------------------------------------------------------
# Phase 2 — record_ref deep-link stability across both green providers
# ---------------------------------------------------------------------------


_SEED_ROWS = [
    {
        "element_system": "UO2",
        "property_name": "lattice_constant",
        "value": 5.47,
        "unit": "angstrom",
        "method": "DFT",
    },
    {
        "element_system": "U",
        "property_name": "bulk_modulus",
        "value": 200.0,
        "unit": "GPa",
        "method": "EXP",
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["canonical_fixture", "backend_endpoint"])
async def test_record_ref_stability(
    provider: str,
    async_client,
    db_session: AsyncSession,
) -> None:
    """record_ref is a stable, shareable deep link for both green providers.

    Same material identity ⇒ the same deterministic, origin-relative URL — no
    scheme/host, no transient/auth keys. The fixture (frozen reference) and the
    backend endpoint (live derivation) must agree (NFM-267 §3).
    """
    if provider == "canonical_fixture":
        payload = _load_json(_CANONICAL_FIXTURE)
    else:
        await seed_corpus(db_session, source=FIXTURE_CORPUS, rows=_SEED_ROWS)
        response = await async_client.get(
            f"/api/v1/ontology/corpora/{FIXTURE_CORPUS}/graph",
        )
        assert response.status_code == 200, response.text
        payload = response.json()

    # Full conformance incl. the record_ref firewall.
    assert_nvl_contract(payload, corpus_id=FIXTURE_CORPUS, check_record_ref=True)

    individuals = {
        n["id"]: n["record_ref"]
        for n in payload["nodes"]
        if n.get("type") == "individual"
    }
    # Deterministic per material identity, identical across providers.
    assert individuals["mat:UO2"] == "/materials/UO2?corpus=smirnov2014"
    assert individuals["mat:U"] == "/materials/U?corpus=smirnov2014"
    # Relative + shareable by construction.
    assert individuals["mat:UO2"].startswith("/")
    assert "://" not in individuals["mat:UO2"]


# ---------------------------------------------------------------------------
# record_ref forbidden-key firewall — regression coverage (NFM-282 follow-up)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "/materials/stats-alloy?corpus=constants-db",  # 'ts' inside 'stats'/'constants'
        "/materials/nts-specimen?corpus=x",            # 'ts' inside 'nts'
        "/materials/Ts?corpus=x",                       # standalone 'Ts' segment
    ],
)
def test_record_ref_does_not_false_positive_on_ts_substrings(ref: str) -> None:
    """NFM-282: bare ``"ts"`` substring-matched legitimate names ('stats',
    'nts', 'constants') and rejected valid record_refs. After narrowing to
    ``"timestamp"``, these scientific/material names must pass the firewall."""
    _assert_valid_record_ref(ref, where="test")  # must not raise


@pytest.mark.parametrize(
    "ref",
    [
        "/materials/UO2?corpus=x&timestamp=1700000000",  # transient key (was bare 'ts')
        "/materials/UO2?corpus=x&session=abc",
        "/materials/UO2?corpus=x&token=abc",
        "/materials/UO2?corpus=x&expires=123",
    ],
)
def test_record_ref_still_rejects_transient_keys(ref: str) -> None:
    """NFM-282: narrowing ``"ts"`` → ``"timestamp"`` must not weaken rejection
    of the actual transient/auth keys that make a deep link session-bound."""
    with pytest.raises(ContractViolationError):
        _assert_valid_record_ref(ref, where="test")

