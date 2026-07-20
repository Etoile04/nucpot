"""Composition generation API endpoint (Sprint 4 DoD V4-7).

POST /api/v1/composition/generate — generate candidate alloy compositions
using the cluster composition model and compute 8 physical features.
"""

from __future__ import annotations

import logging
import random

from fastapi import APIRouter

from nfm_db.ml.cluster_model import get_element_cluster_type
from nfm_db.ml.feature_engineering import compute_all_features
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.composition import (
    CompositionCandidate,
    CompositionGenerateRequest,
    CompositionGenerateResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/composition", tags=["成分设计"])


@router.post(
    "/generate",
    response_model=ApiResponse[CompositionGenerateResponse],
    summary="生成候选合金成分",
    description=(
        "基于团簇成分模型（ClusterCompositionGenerator）生成候选铀合金成分，\n"
        "并计算每个成分的8维物理特征。\n\n"
        "Generate candidate U-alloy compositions using the cluster composition "
        "model and compute 8 physical features for each candidate."
    ),
)
async def generate_compositions(
    payload: CompositionGenerateRequest,
) -> ApiResponse[CompositionGenerateResponse]:
    """Generate candidate compositions and compute features."""
    rng = random.Random(payload.seed)
    solutes = payload.solutes
    u_min = payload.u_fraction_min
    u_max = payload.u_fraction_max
    n = payload.n_samples

    # Validate solute cluster types
    cluster_types: dict[str, str | None] = {
        s: get_element_cluster_type(s) for s in solutes
    }

    candidates: list[CompositionCandidate] = []
    type_dist: dict[str, int] = {"I": 0, "II": 0, "III": 0, "IV": 0}

    for _ in range(n):
        # U fraction uniformly in [u_min, u_max]
        u_frac = rng.uniform(u_min, u_max)
        remaining = 1.0 - u_frac

        if len(solutes) == 1:
            fractions = {solutes[0]: remaining}
        else:
            # Distribute remaining among solutes using Dirichlet-like sampling
            raw = [rng.random() for _ in solutes]
            total = sum(raw)
            fractions = {
                s: remaining * (r / total) for s, r in zip(solutes, raw)
            }

        composition = {"U": u_frac}
        composition.update(fractions)

        # Determine primary cluster type (from highest-fraction solute)
        primary_solute = max(fractions, key=lambda k: fractions[k])
        primary_type = cluster_types.get(primary_solute)
        if primary_type:
            type_dist[primary_type] = type_dist.get(primary_type, 0) + 1

        # Compute 8 physical features
        features = compute_all_features(composition)

        candidates.append(
            CompositionCandidate(
                composition=composition,
                cluster_types={
                    s: cluster_types[s] or "unknown" for s in solutes
                },
                features=features,
            )
        )

    return ApiResponse(
        success=True,
        data=CompositionGenerateResponse(
            candidates=candidates,
            total_generated=len(candidates),
            cluster_type_distribution=type_dist,
            solutes_used=solutes,
        ),
    )
