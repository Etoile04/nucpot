"""Unit tests for feedback priority auto-classification logic."""


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

    def test_bug_report_escalates_on_不可用_in_title(self) -> None:  # noqa: N802
        result = classify_priority(FeedbackType.BUG_REPORT, "网站不可用", "描述")
        assert result == Priority.HIGH

    def test_bug_report_escalates_on_500_in_description(self) -> None:
        result = classify_priority(FeedbackType.BUG_REPORT, "标题", "页面显示 500 错误")
        assert result == Priority.HIGH

    def test_bug_report_escalates_on_崩溃_in_title(self) -> None:  # noqa: N802
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

    def test_usage_inquiry_not_escalated_by_崩溃(self) -> None:  # noqa: N802
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


class TestCalculatePages:
    """Test pagination calculation."""

    def test_exact_division(self) -> None:
        from nfm_db.services.feedback import calculate_pages
        assert calculate_pages(100, 20) == 5

    def test_with_remainder(self) -> None:
        from nfm_db.services.feedback import calculate_pages
        assert calculate_pages(101, 20) == 6

    def test_single_item(self) -> None:
        from nfm_db.services.feedback import calculate_pages
        assert calculate_pages(1, 20) == 1

    def test_zero_total(self) -> None:
        from nfm_db.services.feedback import calculate_pages
        assert calculate_pages(0, 20) == 0

    def test_less_than_page_size(self) -> None:
        from nfm_db.services.feedback import calculate_pages
        assert calculate_pages(5, 20) == 1

    def test_limit_one(self) -> None:
        from nfm_db.services.feedback import calculate_pages
        assert calculate_pages(10, 1) == 10


class TestBuildListQuery:
    """Test _build_list_query filter construction."""

    def test_query_with_no_filters(self) -> None:
        from nfm_db.services.feedback import _build_list_query
        from nfm_db.schemas.feedback import FeedbackListQuery
        stmt = _build_list_query(FeedbackListQuery())
        # Just verify it returns a select without error
        assert stmt is not None

    def test_query_with_status_filter(self) -> None:
        from nfm_db.services.feedback import _build_list_query
        from nfm_db.models.feedback import FeedbackStatus
        from nfm_db.schemas.feedback import FeedbackListQuery
        stmt = _build_list_query(
            FeedbackListQuery(status=FeedbackStatus.OPEN)
        )
        assert stmt is not None

    def test_query_with_priority_filter(self) -> None:
        from nfm_db.services.feedback import _build_list_query
        from nfm_db.models.feedback import Priority
        from nfm_db.schemas.feedback import FeedbackListQuery
        stmt = _build_list_query(
            FeedbackListQuery(priority=Priority.HIGH)
        )
        assert stmt is not None

    def test_query_with_type_filter(self) -> None:
        from nfm_db.services.feedback import _build_list_query
        from nfm_db.models.feedback import FeedbackType
        from nfm_db.schemas.feedback import FeedbackListQuery
        stmt = _build_list_query(
            FeedbackListQuery(feedback_type=FeedbackType.BUG_REPORT)
        )
        assert stmt is not None

    def test_query_with_all_filters(self) -> None:
        from nfm_db.services.feedback import _build_list_query
        from nfm_db.models.feedback import FeedbackStatus, FeedbackType, Priority
        from nfm_db.schemas.feedback import FeedbackListQuery
        stmt = _build_list_query(
            FeedbackListQuery(
                status=FeedbackStatus.OPEN,
                priority=Priority.HIGH,
                feedback_type=FeedbackType.BUG_REPORT,
                page=2,
                limit=10,
            )
        )
        assert stmt is not None
