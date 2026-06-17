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
from tests.nvl_conformance import assert_nvl_contract
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
    assert_nvl_contract(payload, corpus_id=FIXTURE_CORPUS)


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
    assert_nvl_contract(payload, check_envelope=False, check_stats_consistency=False)


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
    assert_nvl_contract(response.json(), corpus_id=FIXTURE_CORPUS)
