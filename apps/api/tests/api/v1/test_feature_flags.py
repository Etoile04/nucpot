"""Tests for the internal feature-flag service (NFM-4180).

Covers the pure evaluation logic: deterministic per-subject bucketing,
percentage-rollout cohort gating, and router registration. Storage-backed
paths (get_flag/upsert_flag) are exercised through the service layer in
integration; these tests stay DB-free so they run in any environment.
"""

from __future__ import annotations

from fastapi import APIRouter

from nfm_db.api.v1.feature_flags import router
from nfm_db.models.feature_flag import FeatureFlag
from nfm_db.services.feature_flag import bucket_for_subject, evaluate_flag

KEY = "DATA_LOSS_NOTICE"


def make_flag(enabled: bool, rollout: int) -> FeatureFlag:
    return FeatureFlag(key=KEY, enabled=enabled, rollout_percentage=rollout)


class TestBucketing:
    """Deterministic cohort bucketing."""

    def test_bucket_is_stable_for_same_key_and_subject(self) -> None:
        assert bucket_for_subject(KEY, "subject-a") == bucket_for_subject(KEY, "subject-a")

    def test_bucket_is_in_range(self) -> None:
        for i in range(200):
            assert 0 <= bucket_for_subject(KEY, f"s-{i}") <= 99

    def test_bucket_differs_across_keys(self) -> None:
        """Different flags must not share cohorts wholesale."""
        differing = sum(
            1
            for i in range(100)
            if bucket_for_subject(KEY, f"s-{i}")
            != bucket_for_subject("OTHER_FLAG", f"s-{i}")
        )
        assert differing > 50

    def test_distribution_is_approximately_uniform(self) -> None:
        """A 10% rollout must bucket roughly 10% of subjects."""
        in_cohort = sum(
            1 for i in range(10_000) if bucket_for_subject(KEY, f"s-{i}") < 10
        )
        assert 850 <= in_cohort <= 1_150  # ±15% relative tolerance


class TestEvaluateFlag:
    """Cohort gating rules."""

    def test_disabled_flag_is_off_for_everyone(self) -> None:
        for i in range(50):
            evaluation = evaluate_flag(make_flag(enabled=False, rollout=100), f"s-{i}")
            assert evaluation.value is False

    def test_full_rollout_is_on_for_everyone(self) -> None:
        for i in range(50):
            evaluation = evaluate_flag(make_flag(enabled=True, rollout=100), f"s-{i}")
            assert evaluation.value is True

    def test_ten_percent_rollout_sticks_per_subject(self) -> None:
        flag = make_flag(enabled=True, rollout=10)
        first = evaluate_flag(flag, "canary-candidate").value
        for _ in range(5):
            assert evaluate_flag(flag, "canary-candidate").value is first

    def test_evaluation_reports_metadata(self) -> None:
        evaluation = evaluate_flag(make_flag(enabled=True, rollout=10), "s-1")
        assert evaluation.key == KEY
        assert evaluation.enabled is True
        assert evaluation.rollout_percentage == 10
        assert evaluation.bucket == bucket_for_subject(KEY, "s-1")


class TestRouter:
    """Route registration contract."""

    def test_router_is_api_router_instance(self) -> None:
        assert isinstance(router, APIRouter)

    def test_router_exposes_evaluate_update_list(self) -> None:
        paths = {getattr(route, "path", "") for route in router.routes}
        assert "/feature-flags" in paths
        assert "/feature-flags/{key}/evaluate" in paths
        assert "/feature-flags/{key}" in paths
