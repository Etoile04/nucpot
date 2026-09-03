"""Internal feature-flag API: list, per-subject evaluate, admin update (NFM-4180)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_admin
from nfm_db.database import get_db
from nfm_db.models.user import User
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.feature_flag import (
    FeatureFlagEvaluation,
    FeatureFlagResponse,
    FeatureFlagUpdate,
)
from nfm_db.services.feature_flag import evaluate_flag, get_flag, list_flags, upsert_flag

router = APIRouter(tags=["功能开关"])


@router.get(
    "/feature-flags",
    response_model=ApiResponse[list[FeatureFlagResponse]],
    summary="列出功能开关",
    description="列出全部功能开关的存储状态（管理员）。\n\nList all feature flags with their stored state (admin).",
)
async def list_feature_flags_endpoint(
    _admin: Annotated[User, Depends(require_admin)],
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[list[FeatureFlagResponse]]:
    flags = await list_flags(session)
    return ApiResponse(
        success=True,
        data=[FeatureFlagResponse.model_validate(flag) for flag in flags],
    )


@router.get(
    "/feature-flags/{key}/evaluate",
    response_model=ApiResponse[FeatureFlagEvaluation],
    summary="评估功能开关",
    description="为给定 subject（匿名客户端 ID）评估开关值。公开端点：subject 是随机 UUID，不是身份标识。\n\nEvaluate a flag for a subject (anonymous client id). Public endpoint: the subject is a random UUID, not an identity.",
)
async def evaluate_feature_flag_endpoint(
    key: str,
    subject: str = Query(min_length=1, max_length=64),
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[FeatureFlagEvaluation]:
    flag = await get_flag(session, key)
    if flag is None:
        raise HTTPException(status_code=404, detail=f"Unknown feature flag: {key}")
    evaluation: FeatureFlagEvaluation = evaluate_flag(flag, subject)
    return ApiResponse(success=True, data=evaluation)


@router.put(
    "/feature-flags/{key}",
    response_model=ApiResponse[FeatureFlagResponse],
    summary="更新功能开关",
    description="更新开关的启用状态 / 百分比放量 / 描述（管理员）。改动即时生效，无需重新部署。\n\nUpdate a flag's enabled state / rollout percentage / description (admin). Takes effect immediately, no redeploy.",
)
async def update_feature_flag_endpoint(
    key: str,
    payload: FeatureFlagUpdate,
    _admin: Annotated[User, Depends(require_admin)],
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[FeatureFlagResponse]:
    flag = await upsert_flag(session, key, payload)
    if flag is None:
        raise HTTPException(status_code=404, detail=f"Unknown feature flag: {key}")
    await session.commit()
    return ApiResponse(success=True, data=FeatureFlagResponse.model_validate(flag))
