"""Unit tests for feedback priority auto-classification logic."""

import pytest

from nfm_db.models.feedback import FeedbackType, Priority
from nfm_db.services.feedback import classify_priority


class TestClassifyPriority:
    """Test priority classification rules from design doc."""

    # --- Default priority by feedback_type ---

    def test_bug_report_defaults_to_medium(self) -> None:
        result = classify_priority(FeedbackType.BUG_REPORT, "普通问题", "描述内容")
        assert result == Priority.MEDIUM

    def test_data_correction_defaults_to_high(self) -> None:
        result = classify_priority(FeedbackType.DATA_CORRECTION, "数据错误", "描述内容")
        assert result == Priority.HIGH

    def test_feature_request_defaults_to_low(self) -> None:
        result = classify_priority(FeedbackType.FEATURE_REQUEST, "新功能", "描述内容")
        assert result == Priority.LOW

    def test_usage_inquiry_defaults_to_medium(self) -> None:
        result = classify_priority(FeedbackType.USAGE_INQUIRY, "使用咨询", "描述内容")
        assert result == Priority.MEDIUM

    # --- Bug report keyword escalation ---

    def test_bug_report_escalates_on_不可用_in_title(self) -> None:
        result = classify_priority(FeedbackType.BUG_REPORT, "网站不可用", "描述")
        assert result == Priority.HIGH

    def test_bug_report_escalates_on_500_in_description(self) -> None:
        result = classify_priority(FeedbackType.BUG_REPORT, "标题", "页面显示 500 错误")
        assert result == Priority.HIGH

    def test_bug_report_escalates_on_崩溃_in_title(self) -> None:
        result = classify_priority(FeedbackType.BUG_REPORT, "程序崩溃了", "描述")
        assert result == Priority.HIGH

    def test_bug_report_escalates_on_crash_in_description(self) -> None:
        result = classify_priority(FeedbackType.BUG_REPORT, "标题", "The app crash randomly")
        assert result == Priority.HIGH

    def test_bug_report_escalates_on_error_in_title(self) -> None:
        result = classify_priority(FeedbackType.BUG_REPORT, "error occurred", "描述")
        assert result == Priority.HIGH

    def test_bug_report_escalates_on_keyword_in_page_url(self) -> None:
        result = classify_priority(
            FeedbackType.BUG_REPORT, "标题", "描述", page_url="/api/unavailable"
        )
        assert result == Priority.HIGH

    # --- Non-bug types are not affected by keywords ---

    def test_feature_request_not_escalated_by_500(self) -> None:
        result = classify_priority(FeedbackType.FEATURE_REQUEST, "500", "500错误")
        assert result == Priority.LOW

    def test_usage_inquiry_not_escalated_by_崩溃(self) -> None:
        result = classify_priority(FeedbackType.USAGE_INQUIRY, "崩溃", "崩溃了")
        assert result == Priority.MEDIUM

    # --- Case insensitivity ---

    def test_keyword_matching_is_case_insensitive(self) -> None:
        result = classify_priority(FeedbackType.BUG_REPORT, "DOWN", "描述")
        assert result == Priority.HIGH

    # --- No keywords, no escalation ---

    def test_bug_report_no_keyword_stays_medium(self) -> None:
        result = classify_priority(FeedbackType.BUG_REPORT, "样式问题", "按钮颜色不对")
        assert result == Priority.MEDIUM
