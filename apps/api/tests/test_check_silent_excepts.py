from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "check_silent_excepts.py"


def load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_silent_excepts", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load checker at {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_source(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fixture.py"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("statement", "category"),
    [("pass", "pass"), ("...", "ellipsis"), ("continue", "continue")],
)
def test_detects_silent_handler_categories(
    tmp_path: Path, statement: str, category: str
) -> None:
    checker = load_checker()
    path = write_source(
        tmp_path,
        f"for item in items:\n    try:\n        use(item)\n    except Exception:\n        {statement}\n",
    )

    findings = checker.check_file(path, {})

    assert len(findings) == 1
    assert findings[0].category == category


def test_continue_with_logging_is_not_silent(tmp_path: Path) -> None:
    checker = load_checker()
    path = write_source(
        tmp_path,
        "for item in items:\n"
        "    try:\n"
        "        use(item)\n"
        "    except Exception:\n"
        "        logger.warning('skipped')\n"
        "        continue\n",
    )

    assert checker.check_file(path, {}) == []


def test_return_is_not_a_violation(tmp_path: Path) -> None:
    checker = load_checker()
    path = write_source(
        tmp_path,
        "def load():\n"
        "    try:\n"
        "        return read()\n"
        "    except Exception:\n"
        "        return None\n",
    )

    assert checker.check_file(path, {}) == []


def test_allowlist_requires_non_empty_justification(tmp_path: Path) -> None:
    checker = load_checker()
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(
        '[[allowlist]]\npath = "fixture.py"\nline = 4\ncategory = "pass"\njustification = ""\n',
        encoding="utf-8",
    )

    with pytest.raises(checker.AllowlistConfigError, match="justification"):
        checker.load_allowlist(allowlist)


def test_allowlisted_finding_is_suppressed(tmp_path: Path) -> None:
    checker = load_checker()
    path = write_source(
        tmp_path,
        "try:\n    operation()\nexcept Exception:\n    pass\n",
    )
    allowed = {
        checker.AllowlistKey(
            path=str(path),
            line=3,
            category="pass",
        ): "Legacy compatibility shim intentionally ignores this failure.",
    }

    assert checker.check_file(path, allowed) == []
