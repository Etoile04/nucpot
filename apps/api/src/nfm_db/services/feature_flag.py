"""Feature-flag service: storage access and cohort evaluation (NFM-4180)."""

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.feature_flag import FeatureFlag
from nfm_db.schemas.feature_flag import FeatureFlagEvaluation, FeatureFlagUpdate

# Percentage-rollout hashing domain separator. Changing it re-buckets every
# subject, so treat it as frozen once shipped.
_BUCKET_SALT = "nfm-feature-flag-v1"


def bucket_for_subject(key: str, subject: str) -> int:
    """Deterministically map (flag key, subject) to a stable 0–99 bucket.

    The same browser (subject id) always lands in the same bucket for a
    given flag, so a 10% rollout is a sticky canary cohort rather than a
    per-request coin flip.
    """
    digest = hashlib.sha256(f"{_BUCKET_SALT}:{key}:{subject}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def evaluate_flag(flag: FeatureFlag, subject: str) -> FeatureFlagEvaluation:
    """Evaluate a stored flag for one subject."""
    bucket = bucket_for_subject(flag.key, subject)
    return FeatureFlagEvaluation(
        key=flag.key,
        enabled=flag.enabled,
        rollout_percentage=flag.rollout_percentage,
        value=flag.enabled and bucket < flag.rollout_percentage,
        bucket=bucket,
    )


async def get_flag(session: AsyncSession, key: str) -> FeatureFlag | None:
    """Fetch one flag row by key, or None when the key is unknown."""
    return await session.get(FeatureFlag, key)


async def list_flags(session: AsyncSession) -> list[FeatureFlag]:
    """List all flag rows ordered by key."""
    result = await session.execute(select(FeatureFlag).order_by(FeatureFlag.key))
    return list(result.scalars().all())


async def upsert_flag(
    session: AsyncSession,
    key: str,
    payload: FeatureFlagUpdate,
) -> FeatureFlag | None:
    """Update an existing flag. Unknown keys return None (no implicit create).

    Flags are created by migration seed only, so operators cannot typo a
    new flag key into existence via the API.
    """
    flag = await session.get(FeatureFlag, key)
    if flag is None:
        return None

    if payload.enabled is not None:
        flag.enabled = payload.enabled
    if payload.rollout_percentage is not None:
        flag.rollout_percentage = payload.rollout_percentage
    if payload.description is not None:
        flag.description = payload.description

    await session.flush()
    await session.refresh(flag)
    return flag
