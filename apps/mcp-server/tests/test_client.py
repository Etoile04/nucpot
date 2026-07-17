"""Tests for ZoteroClient and its helper functions.

Verifies client initialization, read/write methods, and
the _build_template helper via mocking of the pyzotero layer.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nfm_mcp.zotero.client import ZoteroClient, _build_template, format_item


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_zotero() -> MagicMock:
    """Mock pyzotero.Zotero instance."""
    with patch("nfm_mcp.zotero.client.zotero_lib") as mock_lib:
        mock_zot = MagicMock()
        mock_lib.Zotero.return_value = mock_zot
        yield mock_zot


@pytest.fixture()
def client(mock_zotero: MagicMock) -> ZoteroClient:
    return ZoteroClient(api_key="test-key", user_id="12345")


# ---------------------------------------------------------------------------
# Client initialization tests
# ---------------------------------------------------------------------------


class TestZoteroClientInit:
    """Test ZoteroClient initialization."""

    def test_creates_zotero_instance(self) -> None:
        with patch("nfm_mcp.zotero.client.zotero_lib") as mock_lib:
            _client = ZoteroClient(api_key="key", user_id="42", library_type="group")
            mock_lib.Zotero.assert_called_once_with("42", "group", "key")

    def test_defaults_to_user_library(self) -> None:
        with patch("nfm_mcp.zotero.client.zotero_lib") as mock_lib:
            _client = ZoteroClient(api_key="k", user_id="1")
            mock_lib.Zotero.assert_called_once_with("1", "user", "k")


# ---------------------------------------------------------------------------
# Read method tests
# ---------------------------------------------------------------------------


class TestReadMethods:
    """Test ZoteroClient read operations."""

    def test_search_items(self, client: ZoteroClient, mock_zotero: MagicMock) -> None:
        mock_zotero.items.return_value = [{"key": "A"}]
        result = client.search_items("test query", limit=5)
        assert len(result) == 1
        assert result[0]["key"] == "A"
        mock_zotero.items.assert_called_once_with(q="test query", limit=5)

    def test_get_collections(self, client: ZoteroClient, mock_zotero: MagicMock) -> None:
        mock_zotero.collections.return_value = [{"key": "C1"}]
        result = client.get_collections()
        assert len(result) == 1

    def test_get_collection_items(
        self, client: ZoteroClient, mock_zotero: MagicMock
    ) -> None:
        mock_zotero.collection_items.return_value = [{"key": "X1"}]
        result = client.get_collection_items("C1", limit=10)
        assert len(result) == 1
        mock_zotero.collection_items.assert_called_once_with("C1", limit=10)

    def test_get_item(self, client: ZoteroClient, mock_zotero: MagicMock) -> None:
        mock_zotero.item.return_value = {"key": "I1", "data": {"title": "T"}}
        result = client.get_item("I1")
        assert result["key"] == "I1"
        mock_zotero.item.assert_called_once_with("I1")

    def test_get_recent_items(
        self, client: ZoteroClient, mock_zotero: MagicMock
    ) -> None:
        mock_zotero.items.return_value = [{"key": "R1"}]
        result = client.get_recent_items(limit=3)
        assert len(result) == 1
        mock_zotero.items.assert_called_once_with(
            limit=3, sort="dateAdded", direction="desc"
        )


# ---------------------------------------------------------------------------
# Write method tests
# ---------------------------------------------------------------------------


class TestWriteMethods:
    """Test ZoteroClient write operations."""

    def test_add_article(self, client: ZoteroClient, mock_zotero: MagicMock) -> None:
        mock_zotero.item_template.return_value = {
            "title": "",
            "creators": [],
            "collections": [],
        }
        mock_zotero.create_items.return_value = {
            "successful": {"0": {"key": "NEW"}},
            "failed": {},
        }
        result = client.add_article(
            {"title": "Test", "authors": [], "doi": "10.1/a"},
            collection_key="C1",
        )
        assert result["successful"]["0"]["key"] == "NEW"
        mock_zotero.create_items.assert_called_once()
        # Verify collection_key was set
        call_arg = mock_zotero.create_items.call_args[0][0][0]
        assert call_arg["collections"] == ["C1"]

    def test_add_article_no_collection(
        self, client: ZoteroClient, mock_zotero: MagicMock
    ) -> None:
        mock_zotero.item_template.return_value = {
            "title": "",
            "creators": [],
            "collections": [],
        }
        mock_zotero.create_items.return_value = {"successful": {}, "failed": {}}
        client.add_article({"title": "Test"})
        call_arg = mock_zotero.create_items.call_args[0][0][0]
        assert call_arg["collections"] == []

    def test_add_multiple_articles(
        self, client: ZoteroClient, mock_zotero: MagicMock
    ) -> None:
        mock_zotero.item_template.return_value = {
            "title": "",
            "creators": [],
            "collections": [],
        }
        mock_zotero.create_items.return_value = {
            "successful": {"0": {"key": "A"}},
            "failed": {},
        }
        result = client.add_multiple_articles(
            [{"title": "P1"}, {"title": "P2"}],
            collection_key="C1",
        )
        assert len(result["successful"]) == 1

    def test_create_collection(
        self, client: ZoteroClient, mock_zotero: MagicMock
    ) -> None:
        mock_zotero.create_collections.return_value = {
            "successful": {"0": {"key": "COL1"}},
            "failed": {},
        }
        result = client.create_collection("My Folder", parent_key="P1")
        assert result["successful"]["0"]["key"] == "COL1"
        mock_zotero.create_collections.assert_called_once_with(
            [{"name": "My Folder", "parentCollection": "P1"}]
        )

    def test_create_collection_no_parent(
        self, client: ZoteroClient, mock_zotero: MagicMock
    ) -> None:
        mock_zotero.create_collections.return_value = {"successful": {}, "failed": {}}
        client.create_collection("Simple")
        mock_zotero.create_collections.assert_called_once_with(
            [{"name": "Simple"}]
        )

    def test_add_item_to_collection(
        self, client: ZoteroClient, mock_zotero: MagicMock
    ) -> None:
        mock_zotero.item.return_value = {
            "key": "I1",
            "data": {"collections": ["OLD"]},
        }
        mock_zotero.update_item.return_value = None
        client.add_item_to_collection("I1", "NEW_C")
        # Verify the item was updated with the new collection added
        updated_item = mock_zotero.update_item.call_args[0][0]
        assert "NEW_C" in updated_item["data"]["collections"]
        assert "OLD" in updated_item["data"]["collections"]

    def test_add_item_to_collection_already_present(
        self, client: ZoteroClient, mock_zotero: MagicMock
    ) -> None:
        mock_zotero.item.return_value = {
            "key": "I1",
            "data": {"collections": ["TARGET"]},
        }
        client.add_item_to_collection("I1", "TARGET")
        # Should not update if already in collection
        mock_zotero.update_item.assert_not_called()

    def test_add_item_to_collection_no_existing(
        self, client: ZoteroClient, mock_zotero: MagicMock
    ) -> None:
        mock_zotero.item.return_value = {
            "key": "I1",
            "data": {},
        }
        mock_zotero.update_item.return_value = None
        client.add_item_to_collection("I1", "C1")
        updated_item = mock_zotero.update_item.call_args[0][0]
        assert updated_item["data"]["collections"] == ["C1"]


# ---------------------------------------------------------------------------
# _build_template tests
# ---------------------------------------------------------------------------


class TestBuildTemplate:
    """Test the _build_template helper function."""

    def test_basic_template(self, mock_zotero: MagicMock) -> None:
        mock_zotero.item_template.return_value = {
            "title": "",
            "creators": [],
            "collections": [],
        }
        result = _build_template(mock_zotero, {"title": "Paper"})
        assert result["title"] == "Paper"

    def test_template_with_all_fields(self, mock_zotero: MagicMock) -> None:
        mock_zotero.item_template.return_value = {
            "title": "",
            "creators": [],
            "collections": [],
        }
        meta = {
            "title": "Full Paper",
            "authors": [{"firstName": "A", "lastName": "B"}],
            "journal": "Nature",
            "year": "2024",
            "doi": "10.1/a",
            "abstract": "Abstract text",
            "volume": "10",
            "issue": "2",
            "pages": "100-110",
            "url": "https://example.com",
            "pmid": "12345",
            "issn": "0000-0001",
        }
        result = _build_template(mock_zotero, meta)
        assert result["title"] == "Full Paper"
        assert result["publicationTitle"] == "Nature"
        assert result["date"] == "2024"
        assert result["DOI"] == "10.1/a"
        assert result["abstractNote"] == "Abstract text"
        assert result["volume"] == "10"
        assert result["issue"] == "2"
        assert result["pages"] == "100-110"
        assert result["url"] == "https://example.com"
        assert "PMID: 12345" in result["extra"]
        assert "ISSN: 0000-0001" in result["extra"]

    def test_template_with_creators(self, mock_zotero: MagicMock) -> None:
        mock_zotero.item_template.return_value = {
            "title": "",
            "creators": [],
            "collections": [],
        }
        meta = {
            "title": "Authored",
            "authors": [
                {"firstName": "John", "lastName": "Doe"},
                {"firstName": "Jane", "lastName": "Smith"},
            ],
        }
        result = _build_template(mock_zotero, meta)
        assert len(result["creators"]) == 2
        assert result["creators"][0]["lastName"] == "Doe"

    def test_template_filters_non_dict_authors(self, mock_zotero: MagicMock) -> None:
        mock_zotero.item_template.return_value = {
            "title": "",
            "creators": [],
            "collections": [],
        }
        meta = {
            "title": "Mixed",
            "authors": [
                {"firstName": "Valid", "lastName": "Author"},
                "not_a_dict",
                None,
            ],
        }
        result = _build_template(mock_zotero, meta)
        assert len(result["creators"]) == 1

    def test_template_no_extras_without_pmid_issn(
        self, mock_zotero: MagicMock
    ) -> None:
        mock_zotero.item_template.return_value = {
            "title": "",
            "creators": [],
            "collections": [],
        }
        result = _build_template(mock_zotero, {"title": "Minimal"})
        assert "extra" not in result
