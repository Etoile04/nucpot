"""Unit tests for blog post state machine."""

import pytest

from nfm_db.core.blog_state import (
    PermissionError,
    PostStatus,
    StateTransitionError,
    can_transition,
    get_auto_fields,
    get_next_actions,
    get_required_permission,
    validate_transition,
)


class TestStateTransitions:
    """Test state transition validation."""

    def test_draft_to_under_review(self):
        """Draft can transition to under_review."""
        assert can_transition(PostStatus.DRAFT, PostStatus.UNDER_REVIEW)

    def test_under_review_to_approved(self):
        """Under review can transition to approved."""
        assert can_transition(PostStatus.UNDER_REVIEW, PostStatus.APPROVED)

    def test_under_review_to_rejected(self):
        """Under review can transition to rejected."""
        assert can_transition(PostStatus.UNDER_REVIEW, PostStatus.REJECTED)

    def test_approved_to_published(self):
        """Approved can transition to published."""
        assert can_transition(PostStatus.APPROVED, PostStatus.PUBLISHED)

    def test_approved_to_draft(self):
        """Approved can transition back to draft."""
        assert can_transition(PostStatus.APPROVED, PostStatus.DRAFT)

    def test_rejected_to_draft(self):
        """Rejected can transition to draft."""
        assert can_transition(PostStatus.REJECTED, PostStatus.DRAFT)

    def test_published_no_transitions(self):
        """Published is a terminal state."""
        assert not can_transition(PostStatus.PUBLISHED, PostStatus.DRAFT)
        assert not can_transition(PostStatus.PUBLISHED, PostStatus.UNDER_REVIEW)

    def test_invalid_transition(self):
        """Invalid transition returns False."""
        assert not can_transition(PostStatus.DRAFT, PostStatus.PUBLISHED)
        assert not can_transition(PostStatus.PUBLISHED, PostStatus.APPROVED)


class TestRequiredPermissions:
    """Test permission requirements for transitions."""

    def test_submit_requires_permission(self):
        """Submit for review requires permission."""
        perm = get_required_permission(PostStatus.DRAFT, PostStatus.UNDER_REVIEW)
        assert perm == "submit_for_review"

    def test_approve_requires_review_permission(self):
        """Approve requires review permission."""
        perm = get_required_permission(PostStatus.UNDER_REVIEW, PostStatus.APPROVED)
        assert perm == "review_post"

    def test_reject_requires_review_permission(self):
        """Reject requires review permission."""
        perm = get_required_permission(PostStatus.UNDER_REVIEW, PostStatus.REJECTED)
        assert perm == "review_post"

    def test_publish_requires_publish_permission(self):
        """Publish requires publish permission."""
        perm = get_required_permission(PostStatus.APPROVED, PostStatus.PUBLISHED)
        assert perm == "publish_post"

    def test_edit_after_approval(self):
        """Edit after approval requires edit permission."""
        perm = get_required_permission(PostStatus.APPROVED, PostStatus.DRAFT)
        assert perm == "edit_post"

    def test_invalid_transition_raises_error(self):
        """Invalid transition raises StateTransitionError."""
        with pytest.raises(StateTransitionError):
            get_required_permission(PostStatus.DRAFT, PostStatus.PUBLISHED)


class TestTransitionValidation:
    """Test complete transition validation with permissions."""

    def test_valid_transition_with_permission(self):
        """Valid transition with permission succeeds."""
        # Should not raise
        validate_transition(
            PostStatus.DRAFT,
            PostStatus.UNDER_REVIEW,
            user_permissions={"submit_for_review"},
        )

    def test_valid_transition_without_permission_required(self):
        """Valid transition without permission check."""
        # Should not raise when no user_permissions provided
        validate_transition(
            PostStatus.DRAFT,
            PostStatus.UNDER_REVIEW,
            user_permissions=None,
        )

    def test_missing_permission_raises_error(self):
        """Missing required permission raises PermissionError."""
        with pytest.raises(PermissionError) as exc:
            validate_transition(
                PostStatus.DRAFT,
                PostStatus.UNDER_REVIEW,
                user_permissions={"edit_post"},
            )
        assert exc.value.required_permission == "submit_for_review"

    def test_invalid_transition_with_permissions(self):
        """Invalid transition raises error even with permissions."""
        with pytest.raises(StateTransitionError):
            validate_transition(
                PostStatus.DRAFT,
                PostStatus.PUBLISHED,
                user_permissions={"publish_post"},
            )


class TestAutoFields:
    """Test auto-generated fields for transitions."""

    def test_approve_sets_reviewed_at(self):
        """Approve action should set reviewed_at."""
        fields = get_auto_fields(PostStatus.UNDER_REVIEW, PostStatus.APPROVED)
        assert fields == {"reviewed_at": "now"}

    def test_reject_sets_reviewed_at(self):
        """Reject action should set reviewed_at."""
        fields = get_auto_fields(PostStatus.UNDER_REVIEW, PostStatus.REJECTED)
        assert fields == {"reviewed_at": "now"}

    def test_publish_sets_published_at(self):
        """Publish action should set published_at."""
        fields = get_auto_fields(PostStatus.APPROVED, PostStatus.PUBLISHED)
        assert fields == {"published_at": "now"}

    def test_draft_has_no_auto_fields(self):
        """Draft transition has no auto fields."""
        fields = get_auto_fields(PostStatus.DRAFT, PostStatus.UNDER_REVIEW)
        assert fields == {}


class TestNextActions:
    """Test available actions for each status."""

    def test_draft_actions(self):
        """Draft has submit action."""
        actions = get_next_actions(PostStatus.DRAFT)
        assert len(actions) == 1
        assert actions[0]["action"] == "under_review"
        assert actions[0]["permission"] == "submit_for_review"

    def test_under_review_actions(self):
        """Under review has approve and reject actions."""
        actions = get_next_actions(PostStatus.UNDER_REVIEW)
        assert len(actions) == 2
        action_values = {a["action"] for a in actions}
        assert "approved" in action_values
        assert "rejected" in action_values

    def test_approved_actions(self):
        """Approved has publish and edit actions."""
        actions = get_next_actions(PostStatus.APPROVED)
        assert len(actions) == 2
        action_values = {a["action"] for a in actions}
        assert "published" in action_values
        assert "draft" in action_values

    def test_published_no_actions(self):
        """Published has no actions (terminal state)."""
        actions = get_next_actions(PostStatus.PUBLISHED)
        assert len(actions) == 0

    def test_rejected_actions(self):
        """Rejected has edit action."""
        actions = get_next_actions(PostStatus.REJECTED)
        assert len(actions) == 1
        assert actions[0]["action"] == "draft"


class TestEdgeCases:
    """Edge cases for full coverage of blog_state module."""

    def test_get_auto_fields_invalid_transition_returns_empty(self):
        """Invalid transition returns empty dict (not an error)."""
        fields = get_auto_fields(PostStatus.DRAFT, PostStatus.PUBLISHED)
        assert fields == {}

    def test_get_auto_fields_published_terminal_returns_empty(self):
        """Published terminal state has no outgoing auto fields."""
        fields = get_auto_fields(PostStatus.PUBLISHED, PostStatus.DRAFT)
        assert fields == {}

    def test_approved_to_draft_no_auto_fields(self):
        """Approved back to draft has no auto fields."""
        fields = get_auto_fields(PostStatus.APPROVED, PostStatus.DRAFT)
        assert fields == {}

    def test_rejected_to_draft_no_auto_fields(self):
        """Rejected to draft has no auto fields."""
        fields = get_auto_fields(PostStatus.REJECTED, PostStatus.DRAFT)
        assert fields == {}
