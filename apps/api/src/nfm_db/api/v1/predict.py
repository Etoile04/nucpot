"""Phase prediction endpoint (NFM-1567).

POST /api/v1/predict/phase — wraps ``PhaseClassifier.predict_phase``
behind a FastAPI route with Pydantic validation.

The model artifact is loaded lazily on first request and cached for the
lifetime of the process. Tests override ``get_artifact_path`` to point
at the real trained artifact.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from nfm_db.ml.phase_classifier import PhaseClassifier, predict_phase
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.predict import CompositionIn, PhasePredictionOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/predict", tags=["ML 预测"])

_DEFAULT_ARTIFACT: Path = Path("models/phase_classifier_v1.0.0.joblib")


def get_artifact_path() -> Path:
    """Return the path to the trained PhaseClassifier artifact.

    Override this dependency in tests to point at a different file
    (e.g. ``tmp_path / "missing.joblib"`` for 503 tests).
    """
    return _DEFAULT_ARTIFACT


@lru_cache(maxsize=1)
def _load_classifier(artifact: Path) -> PhaseClassifier:
    """Load and cache the trained PhaseClassifier from *artifact*.

    Raises:
        FileNotFoundError: If the artifact does not exist.
        RuntimeError: If deserialization fails.
    """
    if not artifact.exists():
        raise FileNotFoundError(
            f"PhaseClassifier artifact not found: {artifact}"
        )
    clf = PhaseClassifier.load(artifact)
    logger.info("PhaseClassifier loaded from %s", artifact)
    return clf


@router.post(
    "/phase",
    response_model=ApiResponse[PhasePredictionOut],
    summary="预测合金相稳定性",
    description=(
        "接收元素组成 (原子分数), 返回预测相标签 (H/M)、"
        "校准概率、推断簇类型和 8 个物理特征值。"
        "\n\nAccept a composition (element → atomic fraction) and return "
        "predicted phase label (H/M), calibrated probabilities, inferred "
        "cluster type, and 8 physical feature values."
    ),
)
async def predict_phase_endpoint(
    payload: CompositionIn,
    artifact: Path = Depends(get_artifact_path),
) -> ApiResponse[PhasePredictionOut]:
    """Predict phase stability for a U-X alloy composition.

    Returns 200 with the prediction on success, 503 if the model
    artifact is missing, and 422 for invalid compositions (handled
    automatically by Pydantic).
    """
    # --- load model -------------------------------------------------
    try:
        classifier = _load_classifier(artifact)
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail=(
                "PhaseClassifier model artifact is not available. "
                "Please ensure the artifact has been trained and deployed."
            ),
        ) from None
    except Exception as exc:
        logger.exception("Failed to load PhaseClassifier from %s", artifact)
        raise HTTPException(
            status_code=503,
            detail="Failed to load PhaseClassifier model.",
        ) from exc

    # --- validate composition --------------------------------------
    comp = payload.composition
    if not comp:
        raise HTTPException(
            status_code=422,
            detail="Composition must be non-empty.",
        )

    for elem, frac in comp.items():
        if frac < 0:
            raise HTTPException(
                status_code=422,
                detail=f"Atomic fraction for {elem} must be non-negative "
                f"(got {frac}).",
            )

    total = sum(comp.values())
    if not (0.0 < total <= 1.5):
        raise HTTPException(
            status_code=422,
            detail=f"Atomic fractions must sum to ≈1.0 (got {total:.4f}).",
        )

    # --- run inference ---------------------------------------------
    try:
        result = predict_phase(composition=comp, classifier=classifier)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except RuntimeError as exc:
        logger.exception("Inference error")
        raise HTTPException(status_code=503, detail=str(exc)) from None

    # --- build response --------------------------------------------
    prediction = PhasePredictionOut(
        phase=result["phase"],
        probabilities=result["probabilities"],
        cluster_type=result["cluster_type"],
        features=result["features"],
    )
    return ApiResponse(success=True, data=prediction)
