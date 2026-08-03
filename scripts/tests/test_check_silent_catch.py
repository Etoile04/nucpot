from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_scanner():
    script = Path(__file__).parents[1] / "check_silent_catch.py"
    spec = importlib.util.spec_from_file_location("check_silent_catch", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_find_silent_catches_flags_undocumented_pass(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("try:\n    work()\nexcept Exception:\n    pass\n")

    scanner = _load_scanner()

    assert scanner.find_silent_catches(tmp_path) == [(source, 3)]


def test_find_silent_catches_allows_documented_no_op(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        "try:\n    work()\nexcept Exception:\n"
        "    pass  # no-op: fallback is intentional\n"
    )

    scanner = _load_scanner()

    assert scanner.find_silent_catches(tmp_path) == []
