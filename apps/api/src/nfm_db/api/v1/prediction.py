"""ML prediction API endpoints (NFM-1598, NFM-1669).

- POST /api/v1/predict/phase        — phase classification from 8 physical features
- POST /api/v1/predict/temperature  — transition temperature prediction

All responses include model_version, confidence score, and warnings.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from nfm_db.ml.prediction_service import (
    predict_phase,
    predict_temperature,
)
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.prediction import (
    CompositionPredictRequest,
    PhasePredictRequest,
    PhasePredictResponse,
    PhaseProbabilityItem,
    PredictionWarningItem,
    TempPredictRequest,
    TempPredictResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/predict", tags=["ML预测"])


@router.post(
    "/phase",
    response_model=ApiResponse[PhasePredictResponse],
    summary="相类型预测",
    description=(
        "输入8维物理特征，预测核燃料合金的相类型及各类概率。\n\n"
        "Predict the crystallographic phase type of a nuclear fuel alloy "
        "from 8 computed physical features."
    ),
)
async def predict_phase_endpoint(
    payload: PhasePredictRequest,
) -> ApiResponse[PhasePredictResponse]:
    """Predict phase type from 8 physical features."""
    features = payload.to_feature_dict()
    result = predict_phase(features)

    if result is None:
        raise HTTPException(
            status_code=503,
            detail="Phase classifier model is not available. "
                   "Ensure the model artifact is deployed at models/phase_classifier_v01.joblib.",
        )

    return ApiResponse(
        success=True,
        data=PhasePredictResponse(
            predicted_phase=result["predicted_phase"],
            predicted_phase_label=result["predicted_phase_label"],
            probabilities=[
                PhaseProbabilityItem(
                    class_label=p["class"],
                    probability=p["probability"],
                )
                for p in result["probabilities"]
            ],
            confidence=result["confidence"],
            warnings=[
                PredictionWarningItem(code=w["code"], message=w["message"])
                for w in result.get("warnings", [])
            ],
            model_version=result["model_version"],
        ),
    )


@router.post(
    "/temperature",
    response_model=ApiResponse[TempPredictResponse],
    summary="相变温度预测",
    description=(
        "输入8维物理特征，预测核燃料合金的γ→α相变温度及95%置信区间。\n\n"
        "Predict the γ→α phase transition temperature of a nuclear fuel "
        "alloy from 8 computed physical features, with 95% confidence interval."
    ),
)
async def predict_temperature_endpoint(
    payload: TempPredictRequest,
) -> ApiResponse[TempPredictResponse]:
    """Predict phase transition temperature from 8 physical features."""
    features = payload.to_feature_dict()
    result = predict_temperature(features)

    if result is None:
        raise HTTPException(
            status_code=503,
            detail="Temperature predictor model is not available. "
                   "Ensure the model artifact is deployed at models/temp_predictor_v01.joblib.",
        )

    return ApiResponse(
        success=True,
        data=TempPredictResponse(
            predicted_temp_c=result["predicted_temp_c"],
            confidence_lower_c=result["confidence_lower_c"],
            confidence_upper_c=result["confidence_upper_c"],
            gpr_predicted_temp_c=result.get("gpr_predicted_temp_c"),
            svr_predicted_temp_c=result.get("svr_predicted_temp_c"),
            confidence=result["confidence"],
            warnings=[
                PredictionWarningItem(code=w["code"], message=w["message"])
                for w in result.get("warnings", [])
            ],
            model_version=result["model_version"],
        ),
    )


@router.post(
    "/phase-from-composition",
    response_model=ApiResponse[PhasePredictResponse],
    summary="从成分预测相类型",
    description=(
        "输入原始合金成分（元素→原子分数），自动计算8维物理特征后预测相类型。\n\n"
        "Convenience endpoint that accepts raw alloy composition, "
        "computes 8 physical features internally, then predicts phase type."
    ),
)
async def predict_phase_from_composition_endpoint(
    payload: CompositionPredictRequest,
) -> ApiResponse[PhasePredictResponse]:
    """Predict phase type from raw alloy composition.

    Computes physical features via ``compute_all_features()``, then
    delegates to ``predict_phase()``.
    """
    from nfm_db.ml.feature_engineering import compute_all_features

    features = compute_all_features(payload.composition)
    result = predict_phase(features)

    if result is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Phase classifier model is not available. "
                "Ensure the model artifact is deployed at "
                "models/phase_classifier_v01.joblib."
            ),
        )

    return ApiResponse(
        success=True,
        data=PhasePredictResponse(
            predicted_phase=result["predicted_phase"],
            predicted_phase_label=result["predicted_phase_label"],
            probabilities=[
                PhaseProbabilityItem(
                    class_label=p["class"],
                    probability=p["probability"],
                )
                for p in result["probabilities"]
            ],
            confidence=result["confidence"],
            warnings=[
                PredictionWarningItem(code=w["code"], message=w["message"])
                for w in result.get("warnings", [])
            ],
            model_version=result["model_version"],
        ),
    )
