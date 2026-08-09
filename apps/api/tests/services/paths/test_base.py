"""Tests for the gap-fill path-handler protocol + DispatchResult (NFM-2648).

Mirrors the structure used by ``apps/api/tests/providers/test_base.py``:
asserts importability, structural Protocol satisfaction, field-level
introspection, and immutability of the frozen result dataclass.

Note: ``nfm_db.services.gap_dispatch_service.DispatchResult`` is a
distinct, pre-existing class with a different schema; the new
``nfm_db.services.paths.base.DispatchResult`` is the Strategy-pattern
result returned by individual path implementations.
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, fields, is_dataclass
from typing import Any, get_type_hints

import pytest

# ---------------------------------------------------------------------------
# Importability / surface
# ---------------------------------------------------------------------------


def test_dispatch_result_importable_from_base():
    from nfm_db.services.paths.base import DispatchResult

    assert DispatchResult is not None


def test_gap_fill_path_protocol_importable_from_base():
    from nfm_db.services.paths.base import GapFillPath

    assert GapFillPath is not None


def test_base_module_exposes_both_symbols():
    """Both names must be exposed via ``from nfm_db.services.paths.base``."""
    import nfm_db.services.paths.base as base_mod

    assert hasattr(base_mod, "GapFillPath")
    assert hasattr(base_mod, "DispatchResult")


def test_paths_package_init_exports_base_symbols():
    """``paths/__init__.py`` must re-export the base symbols."""
    import nfm_db.services.paths as paths_pkg
    import nfm_db.services.paths.base as paths_base

    assert paths_pkg.DispatchResult is paths_base.DispatchResult
    assert paths_pkg.GapFillPath is paths_base.GapFillPath


# ---------------------------------------------------------------------------
# DispatchResult: dataclass + frozen-ness + field shape
# ---------------------------------------------------------------------------


def test_dispatch_result_is_a_dataclass():
    from nfm_db.services.paths.base import DispatchResult

    assert is_dataclass(DispatchResult)


def test_dispatch_result_is_frozen():
    from nfm_db.services.paths.base import DispatchResult

    dc_params = getattr(DispatchResult, "__dataclass_params__", None)
    assert dc_params is not None, (
        "DispatchResult must use @dataclass(...) — got bare class"
    )
    assert dc_params.frozen is True, (
        "DispatchResult MUST be a frozen dataclass"
    )


def test_dispatch_result_has_exactly_five_fields():
    from nfm_db.services.paths.base import DispatchResult

    names = tuple(f.name for f in fields(DispatchResult))
    assert len(names) == 5, (
        f"DispatchResult must have exactly 5 fields; got {names!r}"
    )
    # Field order is not asserted; set semantics are.
    assert set(names) == {
        "success",
        "path",
        "reference",
        "error",
        "data_found",
    }


def test_dispatch_result_reference_field_is_str_or_none():
    """``reference`` must be ``str | None`` (PEP 604)."""
    import types as _types
    import typing as _typing

    from nfm_db.services.paths.base import DispatchResult

    hints = get_type_hints(DispatchResult)
    assert "reference" in hints
    ref = hints["reference"]
    # ``str | None`` evaluates to a ``types.UnionType`` instance under
    # Python 3.10+. ``typing.get_args`` returns ``(str, NoneType)``
    # for both PEP 604 and ``Union[str, None]`` syntaxes, so the args
    # check below catches both — but the ``isinstance`` check confirms
    # the source form is the PEP 604 union type, not ``typing.Union``.
    assert isinstance(ref, _types.UnionType), (
        f"reference must be str | None (PEP 604); got {type(ref).__name__} "
        f"({ref!r})"
    )
    assert set(_typing.get_args(ref)) == {str, type(None)}


def test_dispatch_result_error_field_is_str_or_none():
    """``error`` must be ``str | None`` (PEP 604)."""
    import types as _types
    import typing as _typing

    from nfm_db.services.paths.base import DispatchResult

    hints = get_type_hints(DispatchResult)
    assert "error" in hints
    err = hints["error"]
    assert isinstance(err, _types.UnionType), (
        f"error must be str | None (PEP 604); got {type(err).__name__} "
        f"({err!r})"
    )
    assert set(_typing.get_args(err)) == {str, type(None)}


def test_dispatch_result_success_is_bool():
    from nfm_db.services.paths.base import DispatchResult

    hints = get_type_hints(DispatchResult)
    assert hints["success"] is bool


def test_dispatch_result_path_is_str():
    from nfm_db.services.paths.base import DispatchResult

    hints = get_type_hints(DispatchResult)
    assert hints["path"] is str


def test_dispatch_result_data_found_is_bool():
    from nfm_db.services.paths.base import DispatchResult

    hints = get_type_hints(DispatchResult)
    assert hints["data_found"] is bool


def test_base_module_does_not_use_optional_alias():
    """The module must NOT use ``Optional[str]``; only ``str | None``."""
    import nfm_db.services.paths.base as base_mod

    src_path = base_mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as fh:
        source = fh.read()
    assert "Optional[" not in source, (
        "Issue requires PEP 604 ``str | None``; do not import or use "
        "``Optional`` from typing."
    )


def test_dispatch_result_can_be_constructed_with_all_fields():
    from nfm_db.services.paths.base import DispatchResult

    result = DispatchResult(
        success=True,
        path="literature",
        reference="doi:10.1234/example",
        error=None,
        data_found=True,
    )
    assert result.success is True
    assert result.path == "literature"
    assert result.reference == "doi:10.1234/example"
    assert result.error is None
    assert result.data_found is True


def test_dispatch_result_accepts_optional_str_none_values():
    from nfm_db.services.paths.base import DispatchResult

    result = DispatchResult(
        success=False,
        path="dft",
        reference=None,
        error="external_db unreachable",
        data_found=False,
    )
    assert result.reference is None
    assert result.error == "external_db unreachable"


def test_dispatch_result_is_immutable():
    from nfm_db.services.paths.base import DispatchResult

    result = DispatchResult(
        success=True,
        path="literature",
        reference=None,
        error=None,
        data_found=False,
    )
    with pytest.raises(FrozenInstanceError):
        result.success = False  # type: ignore[misc]


def test_dispatch_result_equality_by_value():
    """Frozen dataclasses get structural ``__eq__`` from the decorator."""
    from nfm_db.services.paths.base import DispatchResult

    a = DispatchResult(
        success=True,
        path="literature",
        reference="r1",
        error=None,
        data_found=True,
    )
    b = DispatchResult(
        success=True,
        path="literature",
        reference="r1",
        error=None,
        data_found=True,
    )
    c = DispatchResult(
        success=False,
        path="literature",
        reference="r1",
        error=None,
        data_found=True,
    )
    assert a == b
    assert a != c


def test_dispatch_result_hashable():
    """Frozen dataclasses are hashable by default."""
    from nfm_db.services.paths.base import DispatchResult

    result = DispatchResult(
        success=True,
        path="literature",
        reference="r1",
        error=None,
        data_found=True,
    )
    # Must not raise.
    assert isinstance(hash(result), int)


# ---------------------------------------------------------------------------
# GapFillPath: protocol shape + runtime checkability
# ---------------------------------------------------------------------------


def test_gap_fill_path_declares_can_handle():
    from nfm_db.services.paths.base import GapFillPath

    assert hasattr(GapFillPath, "can_handle"), (
        "GapFillPath must declare ``can_handle``"
    )


def test_gap_fill_path_declares_execute():
    from nfm_db.services.paths.base import GapFillPath

    assert hasattr(GapFillPath, "execute"), (
        "GapFillPath must declare ``execute``"
    )


def test_gap_fill_path_is_runtime_checkable():
    """``runtime_checkable`` lets ``isinstance`` work in tests + router."""
    from nfm_db.services.paths.base import GapFillPath

    assert getattr(GapFillPath, "_is_runtime_protocol", False) is True, (
        "GapFillPath must be decorated with @runtime_checkable"
    )


def test_stub_satisfies_protocol_structurally():
    """A minimal async stub with both methods must be ``isinstance``-valid."""
    from nfm_db.services.paths.base import DispatchResult, GapFillPath

    class _Stub:
        async def can_handle(self, request: Any) -> bool:
            return True

        async def execute(self, request: Any) -> DispatchResult:
            return DispatchResult(
                success=True,
                path="stub",
                reference=None,
                error=None,
                data_found=False,
            )

    stub = _Stub()
    assert isinstance(stub, GapFillPath)

    # Confirm the stub's methods are awaitable.
    can_handle_result = asyncio.run(stub.can_handle(object()))
    assert can_handle_result is True

    execute_result = asyncio.run(stub.execute(object()))
    assert isinstance(execute_result, DispatchResult)
    assert execute_result.path == "stub"


def test_non_path_object_fails_isinstance_check():
    from nfm_db.services.paths.base import GapFillPath

    class _NotAPath:
        pass

    assert not isinstance(_NotAPath(), GapFillPath)


# ---------------------------------------------------------------------------
# Async semantics
# ---------------------------------------------------------------------------


def test_can_handle_is_coroutine_function():
    """Issue constraint: protocol methods must be async."""
    import inspect

    from nfm_db.services.paths.base import GapFillPath

    can_handle = GapFillPath.__dict__["can_handle"]
    assert inspect.iscoroutinefunction(can_handle), (
        "can_handle must be declared async"
    )


def test_execute_is_coroutine_function():
    """Issue constraint: protocol methods must be async."""
    import inspect

    from nfm_db.services.paths.base import GapFillPath

    execute = GapFillPath.__dict__["execute"]
    assert inspect.iscoroutinefunction(execute), (
        "execute must be declared async"
    )
