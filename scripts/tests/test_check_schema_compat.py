"""Unit tests for scripts/check_schema_compat.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = SCRIPT_DIR / "check_schema_compat.py"


SAMPLE_SOURCE = '''
from typing import Literal
from pydantic import BaseModel, Field

class AxisInfo(BaseModel):
    label: str = ""
    unit: str = ""

class Plot(BaseModel):
    title: str
    points: list[int] = Field(default_factory=list)
    axis: AxisInfo = Field(default_factory=AxisInfo)
    plot_type: Literal["line", "scatter"] = "line"
'''


SOURCE_NO_BREAK = '''
from pydantic import BaseModel

class Stable(BaseModel):
    a: str
    b: int = 0
'''


SOURCE_WITH_NEW_FIELD = '''
from pydantic import BaseModel

class Stable(BaseModel):
    a: str
    b: int = 0
    c: float = 1.0
'''


SOURCE_WITH_REMOVED_FIELD = '''
from pydantic import BaseModel

class Stable(BaseModel):
    a: str
'''


SOURCE_TYPE_CHANGED = '''
from pydantic import BaseModel

class Stable(BaseModel):
    a: int
    b: int = 0
'''


SOURCE_REQUIRED_ADDED = '''
from pydantic import BaseModel

class Stable(BaseModel):
    a: str
    b: int = 0
    c: float
'''


SOURCE_LITERAL_REMOVED = '''
from typing import Literal
from pydantic import BaseModel

class Plot(BaseModel):
    plot_type: Literal["line"] = "line"
'''


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
    )


def _write_source(tmp_path: Path, name: str, body: str) -> Path:
    src = tmp_path / name
    src.write_text(body)
    return src


def test_update_then_check_no_change(tmp_path: Path) -> None:
    src = _write_source(tmp_path, "m.py", SOURCE_NO_BREAK)
    baseline = tmp_path / "bl"
    baseline.mkdir()

    r1 = _run(["--update-baseline", "--baseline", str(baseline),
               "--source", str(src)])
    assert r1.returncode == 0, r1.stderr
    assert (baseline / "Stable.json").exists()

    r2 = _run(["--baseline", str(baseline), "--source", str(src)])
    assert r2.returncode == 0, r2.stderr
    assert "OK: no breaking changes" in r2.stderr


def test_removed_field_is_breaking(tmp_path: Path) -> None:
    baseline = tmp_path / "bl"
    baseline.mkdir()
    b_src = _write_source(tmp_path, "b.py", SOURCE_NO_BREAK)
    _run(["--update-baseline", "--baseline", str(baseline), "--source", str(b_src)])

    c_src = _write_source(tmp_path, "c.py", SOURCE_WITH_REMOVED_FIELD)
    r = _run(["--baseline", str(baseline), "--source", str(c_src)])
    assert r.returncode == 1
    assert "removed" in (r.stdout + r.stderr).lower()


def test_type_change_is_breaking(tmp_path: Path) -> None:
    baseline = tmp_path / "bl"
    baseline.mkdir()
    b_src = _write_source(tmp_path, "b.py", SOURCE_NO_BREAK)
    _run(["--update-baseline", "--baseline", str(baseline), "--source", str(b_src)])

    c_src = _write_source(tmp_path, "c.py", SOURCE_TYPE_CHANGED)
    r = _run(["--baseline", str(baseline), "--source", str(c_src)])
    assert r.returncode == 1
    assert "type changed" in r.stdout


def test_added_required_field_is_breaking(tmp_path: Path) -> None:
    baseline = tmp_path / "bl"
    baseline.mkdir()
    b_src = _write_source(tmp_path, "b.py", SOURCE_NO_BREAK)
    _run(["--update-baseline", "--baseline", str(baseline), "--source", str(b_src)])

    c_src = _write_source(tmp_path, "c.py", SOURCE_REQUIRED_ADDED)
    r = _run(["--baseline", str(baseline), "--source", str(c_src)])
    assert r.returncode == 1
    assert "required" in r.stdout


def test_added_optional_field_is_non_breaking(tmp_path: Path) -> None:
    baseline = tmp_path / "bl"
    baseline.mkdir()
    b_src = _write_source(tmp_path, "b.py", SOURCE_NO_BREAK)
    _run(["--update-baseline", "--baseline", str(baseline), "--source", str(b_src)])

    c_src = _write_source(tmp_path, "c.py", SOURCE_WITH_NEW_FIELD)
    r = _run(["--baseline", str(baseline), "--source", str(c_src)])
    assert r.returncode == 0
    assert "added (non-breaking)" in r.stdout


def test_literal_value_removed_is_breaking(tmp_path: Path) -> None:
    baseline = tmp_path / "bl"
    baseline.mkdir()
    b_src = _write_source(tmp_path, "b.py", SAMPLE_SOURCE)
    _run(["--update-baseline", "--baseline", str(baseline), "--source", str(b_src)])

    c_src = _write_source(tmp_path, "c.py", SOURCE_LITERAL_REMOVED)
    r = _run(["--baseline", str(baseline), "--source", str(c_src)])
    assert r.returncode == 1
    assert "literal" in r.stdout.lower()


def test_fail_on_warn_treats_additions_as_failures(tmp_path: Path) -> None:
    baseline = tmp_path / "bl"
    baseline.mkdir()
    b_src = _write_source(tmp_path, "b.py", SOURCE_NO_BREAK)
    _run(["--update-baseline", "--baseline", str(baseline), "--source", str(b_src)])

    c_src = _write_source(tmp_path, "c.py", SOURCE_WITH_NEW_FIELD)
    r = _run(["--baseline", str(baseline), "--source", str(c_src), "--fail-on-warn"])
    assert r.returncode == 1
