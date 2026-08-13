"""Tests for nfm_db.services.doi_fetcher (NFM-1488)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nfm_db.services.doi_fetcher import (
    DOI_REGEX,
    MAX_CONTENT_LENGTH,
    REQUEST_TIMEOUT,
    DOIFetchError,
    SemanticScholarFetcher,
    fetch_paper_content,
    validate_doi_format,
)


class TestValidateDoiFormat:
    def test_valid_doi(self) -> None:
        assert validate_doi_format("10.1234/test") is True

    def test_valid_doi_long_prefix(self) -> None:
        assert validate_doi_format("10.123456789/article") is True

    def test_empty_string(self) -> None:
        assert validate_doi_format("") is False

    def test_missing_prefix(self) -> None:
        assert validate_doi_format("1234/test") is False

    def test_whitespace_rejected(self) -> None:
        assert validate_doi_format("10.1234/test article") is False

    def test_doi_with_special_chars(self) -> None:
        assert validate_doi_format("10.1000/xyz-123_abc") is True


class TestConstants:
    def test_max_content_length(self) -> None:
        assert MAX_CONTENT_LENGTH == 2_000_000

    def test_request_timeout(self) -> None:
        assert REQUEST_TIMEOUT == 30

    def test_doi_regex_compiled(self) -> None:
        assert hasattr(DOI_REGEX, "match")


class TestDOIFetchError:
    def test_is_exception(self) -> None:
        assert issubclass(DOIFetchError, Exception)

    def test_message_preserved(self) -> None:
        err = DOIFetchError("test error")
        assert str(err) == "test error"

    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(DOIFetchError):
            raise DOIFetchError("fail")


class TestSemanticScholarFetcher:
    def test_init_default_timeout(self) -> None:
        f = SemanticScholarFetcher()
        assert f._timeout == 30

    def test_init_custom_timeout(self) -> None:
        f = SemanticScholarFetcher(timeout=60)
        assert f._timeout == 60

    @patch("nfm_db.services.doi_fetcher.httpx")
    def test_fetch_with_abstract_fallback(self, mock_httpx: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "title": "Test Paper",
            "abstract": "This is the abstract.",
            "openAccessPdf": None,
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        fetcher = SemanticScholarFetcher()
        result = fetcher.fetch("10.1234/test")
        assert "Test Paper" in result
        assert "This is the abstract." in result

    @patch("nfm_db.services.doi_fetcher.httpx")
    def test_fetch_no_content_raises(self, mock_httpx: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"title": "", "abstract": "", "openAccessPdf": None}
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        fetcher = SemanticScholarFetcher()
        with pytest.raises(DOIFetchError, match="No content available"):
            fetcher.fetch("10.1234/empty")

    @patch("nfm_db.services.doi_fetcher.httpx")
    def test_fetch_http_error_raises(self, mock_httpx: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = Exception("Not Found")
        mock_httpx.get.return_value = mock_resp

        fetcher = SemanticScholarFetcher()
        with pytest.raises(DOIFetchError, match="Not Found"):
            fetcher.fetch("10.1234/notfound")

    @patch("nfm_db.services.doi_fetcher.httpx")
    def test_fetch_pdf_fallback_on_pdf_error(self, mock_httpx: MagicMock) -> None:
        metadata_resp = MagicMock()
        metadata_resp.status_code = 200
        metadata_resp.json.return_value = {
            "title": "PDF Paper",
            "abstract": "Abstract text.",
            "openAccessPdf": {"url": "https://example.com/paper.pdf"},
        }
        metadata_resp.raise_for_status = MagicMock()
        pdf_resp = MagicMock()
        pdf_resp.status_code = 500
        pdf_resp.raise_for_status.side_effect = Exception("PDF fail")
        mock_httpx.get.side_effect = [metadata_resp, pdf_resp]

        fetcher = SemanticScholarFetcher()
        result = fetcher.fetch("10.1234/pdf")
        assert "Abstract text." in result

    @patch("nfm_db.services.doi_fetcher.httpx")
    def test_fetch_abstract_only_no_title(self, mock_httpx: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"title": "", "abstract": "Just abstract.", "openAccessPdf": None}
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        fetcher = SemanticScholarFetcher()
        result = fetcher.fetch("10.1234/notitle")
        assert result == "Just abstract."


class TestFetchPaperContent:
    @patch("nfm_db.services.doi_fetcher.get_doi_fetcher")
    def test_delegates_to_fetcher(self, mock_get: MagicMock) -> None:
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = "# Title\n\nContent"
        mock_get.return_value = mock_fetcher
        result = fetch_paper_content("10.1234/test")
        assert result == "# Title\n\nContent"
        mock_fetcher.fetch.assert_called_once_with("10.1234/test")

    @patch("nfm_db.services.doi_fetcher.get_doi_fetcher")
    def test_empty_result_raises(self, mock_get: MagicMock) -> None:
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = "   "
        mock_get.return_value = mock_fetcher
        with pytest.raises(DOIFetchError, match="Empty content"):
            fetch_paper_content("10.1234/empty")

    @patch("nfm_db.services.doi_fetcher.get_doi_fetcher")
    def test_result_truncated_to_max_length(self, mock_get: MagicMock) -> None:
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = "x" * (MAX_CONTENT_LENGTH + 1000)
        mock_get.return_value = mock_fetcher
        result = fetch_paper_content("10.1234/long")
        assert len(result) == MAX_CONTENT_LENGTH


class TestGetDoiFetcher:
    def test_returns_fetcher_instance(self) -> None:
        from nfm_db.services.doi_fetcher import DOIFetcherBackend, get_doi_fetcher
        fetcher = get_doi_fetcher()
        assert isinstance(fetcher, DOIFetcherBackend)

    def test_returns_same_instance(self) -> None:
        from nfm_db.services.doi_fetcher import get_doi_fetcher
        f1 = get_doi_fetcher()
        f2 = get_doi_fetcher()
        assert f1 is f2
