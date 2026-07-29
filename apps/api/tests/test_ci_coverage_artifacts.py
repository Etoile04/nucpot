"""Guard the CI coverage artifact contract consumed by the KR-5 aggregator.

The KR-5 coverage aggregator (NFM-2046) reads Cobertura XML produced by a green
`main` CI run. It cannot import the repo's test suites directly, so its only
input is the artifact CI uploads. That makes the artifact *name* and *path* a
published interface: silently renaming or dropping an upload step breaks KR-5
measurement without failing any other test.

These tests pin that interface (NFM-2047, KR-5.4).

Contract
--------
- ``coverage-api-cobertura`` -> ``apps/api/coverage.xml``
- ``coverage-web-cobertura`` -> ``apps/web/coverage/cobertura-coverage.xml``

``.github/workflows/ci.yml`` is the canonical producer: one run emits both
artifacts from the same commit, so the aggregator can pair them atomically.
``test-api.yml`` re-publishes the API artifact for the standalone API workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"

API_ARTIFACT = "coverage-api-cobertura"
WEB_ARTIFACT = "coverage-web-cobertura"
API_COVERAGE_PATH = "apps/api/coverage.xml"
# NOT the vitest default (apps/web/coverage/cobertura-coverage.xml): KR-5.2
# (NFM-2045) emits a hand-written n/a stub here via
# apps/web/scripts/emit-coverage-stub.mjs, chained off `pnpm run test`.
WEB_COVERAGE_PATH = "apps/web/coverage.xml"

# GitHub retains artifacts for at most 90 days. The aggregator only needs the
# latest green main run, so >= 30 days comfortably satisfies AC-1.
MIN_RETENTION_DAYS = 30

# Each case: (workflow file, job name, artifact name, expected path fragment)
_ARTIFACT_CASES = [
    ("ci.yml", "backend", API_ARTIFACT, API_COVERAGE_PATH),
    ("ci.yml", "frontend", WEB_ARTIFACT, WEB_COVERAGE_PATH),
    ("test-api.yml", "test", API_ARTIFACT, API_COVERAGE_PATH),
]


def _load_workflow(filename: str) -> dict[str, Any]:
    path = _WORKFLOWS / filename
    assert path.is_file(), f"missing workflow file: {path}"
    parsed: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), f"{filename} did not parse to a mapping"
    return parsed


def _coverage_artifact_step(filename: str, job: str, artifact: str) -> dict[str, Any]:
    """Return the single ``actions/upload-artifact`` step publishing ``artifact``."""
    jobs = _load_workflow(filename).get("jobs", {})
    assert job in jobs, f"{filename} has no '{job}' job (found: {sorted(jobs)})"

    steps: list[dict[str, Any]] = jobs[job].get("steps", [])
    matches = [
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        and step.get("with", {}).get("name") == artifact
    ]
    assert len(matches) == 1, (
        f"{filename}:{job} must declare exactly one upload-artifact step named "
        f"{artifact!r}; found {len(matches)}"
    )
    return matches[0]


@pytest.mark.parametrize(("filename", "job", "artifact", "path"), _ARTIFACT_CASES)
def test_workflow_uploads_coverage_artifact(
    filename: str, job: str, artifact: str, path: str
) -> None:
    """Each producing job publishes its coverage XML under the contracted name."""
    step = _coverage_artifact_step(filename, job, artifact)
    assert path in step["with"]["path"], (
        f"{filename}:{job} artifact {artifact!r} must publish {path!r}"
    )


@pytest.mark.parametrize(("filename", "job", "artifact", "path"), _ARTIFACT_CASES)
def test_coverage_artifact_uploads_even_when_tests_fail(
    filename: str, job: str, artifact: str, path: str
) -> None:
    """``if: always()`` keeps a red run measurable instead of emitting nothing."""
    step = _coverage_artifact_step(filename, job, artifact)
    assert step.get("if") == "always()", (
        f"{filename}:{job} artifact {artifact!r} must upload with if: always()"
    )


@pytest.mark.parametrize(("filename", "job", "artifact", "path"), _ARTIFACT_CASES)
def test_coverage_artifact_retained_long_enough(
    filename: str, job: str, artifact: str, path: str
) -> None:
    """The aggregator must still find the artifact days after the run."""
    step = _coverage_artifact_step(filename, job, artifact)
    retention = step["with"].get("retention-days")
    assert retention is not None, f"{artifact!r} must declare retention-days"
    assert int(retention) >= MIN_RETENTION_DAYS, (
        f"{artifact!r} retention-days={retention} is below {MIN_RETENTION_DAYS}"
    )


def test_api_artifact_fails_loudly_when_coverage_missing() -> None:
    """apps/api always emits coverage.xml, so a missing file is a real defect.

    Without this the aggregator could silently fall back to a stale artifact.
    """
    step = _coverage_artifact_step("ci.yml", "backend", API_ARTIFACT)
    assert step["with"].get("if-no-files-found") == "error"


def test_web_artifact_fails_loudly_when_coverage_missing() -> None:
    """KR-5.2 emits the web stub deterministically, so absence is a real defect.

    ``apps/web/package.json`` chains ``node scripts/emit-coverage-stub.mjs`` off
    ``pnpm run test``, and that script writes ``apps/web/coverage.xml``
    unconditionally (verified by running it). If the file goes missing the
    emitter has broken, and the aggregator would lose its explicit "web is n/a"
    marker — so fail the step rather than warn.
    """
    step = _coverage_artifact_step("ci.yml", "frontend", WEB_ARTIFACT)
    assert step["with"].get("if-no-files-found") == "error"


def test_api_coverage_xml_is_actually_emitted_by_pytest_config() -> None:
    """The artifact path is only real if pytest is configured to write it."""
    pyproject = (_REPO_ROOT / "apps" / "api" / "pyproject.toml").read_text(encoding="utf-8")
    assert "--cov-report=xml" in pyproject, (
        "apps/api must emit coverage.xml for the CI artifact to have content"
    )
