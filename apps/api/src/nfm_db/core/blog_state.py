"""Blog post state machine for review workflow."""

import enum
from dataclasses import dataclass
from typing import Literal


class PostStatus(str, enum.Enum):
    """Blog post status for review workflow."""

    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


@dataclass(frozen=True)
class StateTransition:
    """Represents a valid state transition."""

    from_status: PostStatus
    to_status: PostStatus
    requires_permission: str | None = None
    auto_fields: dict[str, str] | None = None


# Define all valid state transitions
VALID_TRANSITIONS: dict[PostStatus, list[StateTransition]] = {
    PostStatus.DRAFT: [
        StateTransition(
            from_status=PostStatus.DRAFT,
            to_status=PostStatus.UNDER_REVIEW,
            requires_permission="submit_for_review",
        ),
    ],
    PostStatus.UNDER_REVIEW: [
        StateTransition(
            from_status=PostStatus.UNDER_REVIEW,
            to_status=PostStatus.APPROVED,
            requires_permission="review_post",
            auto_fields={"reviewed_at": "now"},
        ),
        StateTransition(
            from_status=PostStatus.UNDER_REVIEW,
            to_status=PostStatus.REJECTED,
            requires_permission="review_post",
            auto_fields={"reviewed_at": "now"},
        ),
    ],
    PostStatus.APPROVED: [
        StateTransition(
            from_status=PostStatus.APPROVED,
            to_status=PostStatus.PUBLISHED,
            requires_permission="publish_post",
            auto_fields={"published_at": "now"},
        ),
        StateTransition(
            from_status=PostStatus.APPROVED,
            to_status=PostStatus.DRAFT,
            requires_permission="edit_post",
        ),
    ],
    PostStatus.PUBLISHED: [
        # Terminal state - no outgoing transitions
    ],
    PostStatus.REJECTED: [
        StateTransition(
            from_status=PostStatus.REJECTED,
            to_status=PostStatus.DRAFT,
            requires_permission="edit_post",
        ),
    ],
}


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(
        self,
        from_status: PostStatus,
        to_status: PostStatus,
        reason: str = "Invalid state transition",
    ):
        self.from_status = from_status
        self.to_status = to_status
        self.reason = reason
        super().__init__(
            f"Cannot transition from {from_status.value} to {to_status.value}: {reason}"
        )


class PermissionError(Exception):
    """Raised when user lacks required permission for transition."""

    def __init__(self, required_permission: str):
        self.required_permission = required_permission
        super().__init__(f"Permission required: {required_permission}")


def can_transition(
    from_status: PostStatus,
    to_status: PostStatus,
) -> bool:
    """Check if a state transition is valid.

    Args:
        from_status: Current post status
        to_status: Desired post status

    Returns:
        True if transition is valid, False otherwise
    """
    valid_transitions = VALID_TRANSITIONS.get(from_status, [])
    return any(t.to_status == to_status for t in valid_transitions)


def get_required_permission(
    from_status: PostStatus,
    to_status: PostStatus,
) -> str | None:
    """Get the permission required for a state transition.

    Args:
        from_status: Current post status
        to_status: Desired post status

    Returns:
        Required permission string, or None if no permission required

    Raises:
        StateTransitionError: If transition is invalid
    """
    valid_transitions = VALID_TRANSITIONS.get(from_status, [])
    for transition in valid_transitions:
        if transition.to_status == to_status:
            return transition.requires_permission

    raise StateTransitionError(from_status, to_status)


def validate_transition(
    from_status: PostStatus,
    to_status: PostStatus,
    user_permissions: set[str] | None = None,
) -> None:
    """Validate a state transition and user permissions.

    Args:
        from_status: Current post status
        to_status: Desired post status
        user_permissions: Set of user's permissions (optional)

    Raises:
        StateTransitionError: If transition is invalid
        PermissionError: If user lacks required permission
    """
    # Check if transition is valid
    if not can_transition(from_status, to_status):
        raise StateTransitionError(from_status, to_status)

    # Check permissions if provided
    if user_permissions is not None:
        required = get_required_permission(from_status, to_status)
        if required and required not in user_permissions:
            raise PermissionError(required)


def get_auto_fields(
    from_status: PostStatus,
    to_status: PostStatus,
) -> dict[str, str]:
    """Get fields that should be auto-set during transition.

    Args:
        from_status: Current post status
        to_status: Desired post status

    Returns:
        Dictionary of field names to their values ("now" for current timestamp)
    """
    valid_transitions = VALID_TRANSITIONS.get(from_status, [])
    for transition in valid_transitions:
        if transition.to_status == to_status:
            return transition.auto_fields or {}
    return {}


def get_next_actions(status: PostStatus) -> list[dict[str, str]]:
    """Get available actions for a given status.

    Args:
        status: Current post status

    Returns:
        List of available actions with their display names and required permissions
    """
    actions = []
    valid_transitions = VALID_TRANSITIONS.get(status, [])

    for transition in valid_transitions:
        actions.append({
            "action": transition.to_status.value,
            "permission": transition.requires_permission or "",
        })

    return actions
