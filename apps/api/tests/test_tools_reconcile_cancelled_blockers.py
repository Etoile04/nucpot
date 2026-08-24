"""Subprocess smoke test for ``tools/reconcile_cancelled_blockers.py`` (NFM-3600).

Why this exists
---------------
Release Engineer pre-merge smoke of the §4.3-b dry-run script crashed
with::

    lookup_issues() got an unexpected keyword argument 'query'

because ``tools/reconcile_cancelled_blockers.py:98`` (pre-fix) called
``lookup_issues(query=None)`` while the helper's signature is
``lookup_issues(q=None, ...)``. The phantom-merge 4-gate passed
because the cited code existed on the branch — the defect only
surfaced at runtime when the script's CLI entry point was actually
invoked.

This test fires up the script as a real subprocess so the same crash
cannot recur without breaking CI. The stub at
``apps/api/tests/_helpers/stub_paperclip_issue_lookup.py`` is injected
into ``sys.modules`` before the script runs so the dry-run completes
without ever touching the network.

The regression we are preventing
--------------------------------
* ``TypeError: lookup_issues() got an unexpected keyword argument 'query'``
* Any future drift between the script's call-site kwargs and the helper's
  signature (e.g. ``status=foo`` typo, missing kwarg, deprecated arg).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Repo layout: this test file lives at apps/api/tests/, so the repo root
# is three parents up. ``tools/reconcile_cancelled_blockers.py`` lives at
# ``<repo_root>/tools/`` and the stub helper lives at
# ``<repo_root>/apps/api/tests/_helpers/stub_paperclip_issue_lookup.py``.
THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[3]
STUB_PATH = THIS_FILE.parent / "_helpers" / "stub_paperclip_issue_lookup.py"
SCRIPT_PATH = REPO_ROOT / "tools" / "reconcile_cancelled_blockers.py"

# The launcher snippet runs INSIDE the subprocess. It:
#   1. Pre-injects the stub into sys.modules under the real helper's name
#      BEFORE the script's own sys.path.insert(0, str(_SCRIPTS)) runs.
#   2. Resets argv so argparse sees ``--dry-run``.
#   3. Calls runpy.run_path() on the real script. This is genuinely a
#      subprocess — the script's top-level code (path injection, the
#      ``from nfm_db.services.adr009_reconcile_routine import ...`` block,
#      the flag-cache reset, etc.) all execute in this fresh interpreter.
_LAUNCHER = """
import os
import runpy
import sys

# 1. Make the stub importable and inject it under the real helper's name.
sys.path.insert(0, {stub_dir!r})
import stub_paperclip_issue_lookup as _stub
sys.modules["paperclip_issue_lookup"] = _stub

# 2. argv for argparse in the dry-run script.
sys.argv = ["reconcile_cancelled_blockers.py", "--dry-run"]

# 3. The script does its own sys.path.insert(0, ...) to expose apps/api/src
#    and scripts/. Letting it run is the whole point of the smoke test —
#    a regression that breaks that path would surface here too.
runpy.run_path({script_path!r}, run_name="__main__")
"""


def _run_dry_run_subprocess(
    tmp_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Launch the dry-run script in a real subprocess with the stub injected.

    Returns the :class:`subprocess.CompletedProcess` so individual tests can
    assert on ``returncode`` / ``stdout`` / ``stderr``.
    """
    launcher = _LAUNCHER.format(
        stub_dir=str(STUB_PATH.parent),
        script_path=str(SCRIPT_PATH),
    )
    launcher_file = tmp_path / "launcher.py"
    launcher_file.write_text(launcher)

    # Force the feature flag ON — the dry-run script reads it through
    # ``is_reconcile_routine_enabled`` and resets the cache before the check.
    env = {
        **os.environ,
        "NFM_ADR_009_RECONCILIATION_HOOK_ENABLED": "on",
        # Belt-and-braces: the real helper would raise AuthError without a
        # key, and we want any accidental shadowing of the stub to surface
        # loudly rather than silently passing.
        "PAPERCLIP_API_KEY": "",
        "PAPERCLIP_API_URL": "",
        "PAPERCLIP_COMPANY_ID": "",
    }
    # Drop inherited PYTHONPATH so a parent pytest run cannot pre-import
    # ``paperclip_issue_lookup`` from outside the stub path.
    env.pop("PYTHONPATH", None)
    # ``apps/api/src`` must be importable so the script's
    # ``from nfm_db.services.adr009_reconcile_routine import ...`` resolves.
    env["PYTHONPATH"] = str(REPO_ROOT / "apps" / "api" / "src")

    return subprocess.run(
        [sys.executable, str(launcher_file)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_dry_run_script_exits_zero_against_stub(tmp_path: Path) -> None:
    """The dry-run script must complete (exit 0) when the lookup helper
    returns a stubbed ``Ok(issues=[])``.

    Pre-fix, this would crash with::

        TypeError: lookup_issues() got an unexpected keyword argument 'query'
    """
    result = _run_dry_run_subprocess(tmp_path)

    assert result.returncode == 0, (
        f"dry-run script exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_dry_run_script_emits_scan_count_and_does_not_mutate(
    tmp_path: Path,
) -> None:
    """AC §4.3-b items 1 & 2: script must run the routine without mutating
    AND print scan count, dependents touched, UUIDs to remove.

    The stub returns ``Ok(issues=[])`` so all counts are zero. The important
    assertion is that the routine ran (the report header is printed) and the
    script exited 0 — a TypeError at the call site would suppress the
    report and produce a non-zero exit.
    """
    result = _run_dry_run_subprocess(tmp_path)

    assert result.returncode == 0
    assert "ADR-009 §4.3 dry-run" in result.stdout, (
        "dry-run report header missing — the routine did not run.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "issues scanned" in result.stdout
    assert "dependents touched" in result.stdout
    assert "UUIDs to remove" in result.stdout


def test_dry_run_script_does_not_call_lookup_issues_with_query_kwarg(
    tmp_path: Path,
) -> None:
    """Regression guard — the NFM-3600 defect.

    Pre-fix, ``tools/reconcile_cancelled_blockers.py`` called
    ``lookup_issues(query=None)`` and the helper signature uses
    ``q=None``. We do not parse the script's bytecode here; instead we
    exercise the script end-to-end and assert the kwarg mismatch does
    NOT surface anywhere in stdout/stderr.
    """
    result = _run_dry_run_subprocess(tmp_path)

    combined = result.stdout + result.stderr
    assert "unexpected keyword argument 'query'" not in combined, (
        "RE-defect regression: the script is calling lookup_issues with "
        "query=... instead of q=... .\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "AttributeError" not in combined, (
        "Latent defect regression: the script is reading a non-existent "
        "attribute on a frozen dataclass (e.g. result.ok, result.kind).\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
