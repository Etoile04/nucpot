"""Tests for the potential provider protocol + filters (NFM-296 Task 2)."""

from __future__ import annotations

import uuid
from typing import get_type_hints

import pytest


def test_potential_filters_defaults():
    from nfm_db.services.providers.base import PotentialFilters

    f = PotentialFilters()
    assert f.page == 1
    assert f.limit == 20
    assert f.type_filter is None
    assert f.elements is None
    assert f.query is None
    assert f.sort == "updated"


def test_potential_filters_accepts_all_fields():
    from nfm_db.services.providers.base import PotentialFilters

    f = PotentialFilters(
        page=2,
        limit=50,
        type_filter="eam",
        elements=["Al", "Cu"],
        query="mishin",
        sort="name",
    )
    assert f.page == 2
    assert f.limit == 50
    assert f.type_filter == "eam"
    assert f.elements == ["Al", "Cu"]
    assert f.query == "mishin"
    assert f.sort == "name"


def test_potential_provider_protocol_exists():
    from nfm_db.services.providers.base import PotentialProvider

    assert PotentialProvider is not None
    # Protocol must declare the two required coroutine methods
    assert hasattr(PotentialProvider, "list_summaries")
    assert hasattr(PotentialProvider, "get_detail")


def test_stub_satisfies_protocol_structurally():
    """A minimal async object with the two methods should satisfy the Protocol."""
    import asyncio

    from nfm_db.schemas.potential import PotentialDetail, PotentialSummary
    from nfm_db.services.providers.base import PotentialFilters, PotentialProvider

    class _Stub:
        async def list_summaries(self, filters: PotentialFilters) -> list[PotentialSummary]:
            return []

        async def get_detail(self, potential_id: uuid.UUID) -> PotentialDetail | None:
            return None

    stub = _Stub()
    # isinstance against a runtime_checkable Protocol
    assert isinstance(stub, PotentialProvider)

    # Confirm the stub's methods are coroutines when awaited
    result = asyncio.run(stub.list_summaries(PotentialFilters()))
    assert result == []


def test_potential_filters_is_pydantic_model():
    from pydantic import BaseModel

    from nfm_db.services.providers.base import PotentialFilters

    assert issubclass(PotentialFilters, BaseModel)


@pytest.mark.parametrize(
    "field",
    ["page", "limit", "type_filter", "elements", "query", "sort"],
)
def test_filters_field_annotations(field):
    from nfm_db.services.providers.base import PotentialFilters

    hints = get_type_hints(PotentialFilters)
    assert field in hints, f"PotentialFilters missing field {field!r}"
