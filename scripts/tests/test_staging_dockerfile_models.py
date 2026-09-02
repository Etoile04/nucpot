"""Tests for docker/staging-api.Dockerfile — model-artifact baking guard.

Context: NFM-4077 (parent: NFM-4054 AC-OC-4 staging verification).

The staging API container must have ML model artifacts (apps/api/models/)
baked into the image at /app/models so /api/v1/predict/energy can serve
without a separate volume mount. prod-api.Dockerfile already does this with
`COPY apps/api/models/ ./models/`. This test guards against accidental removal
of the same line from the staging Dockerfile.

Regression history:
- Pre-NFM-4077 staging image had no /app/models directory. /predict/energy
  returned 503 "Energy predictor model is not available" because
  prediction_service.py resolves MODELS_DIR from src/nfm_db/ml/ at 6 parent
  hops. Without the COPY, the build never populated the path.
- NFM-4077 added `COPY apps/api/models/ ./models/` to docker/staging-api.Dockerfile.
- These tests verify (a) the COPY line is present and (b) the corresponding
  models directory in the repo is non-empty (so the COPY will actually
  deliver artifacts).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING_DOCKERFILE = REPO_ROOT / "docker" / "staging-api.Dockerfile"
PROD_DOCKERFILE = REPO_ROOT / "docker" / "prod-api.Dockerfile"
MODELS_DIR = REPO_ROOT / "apps" / "api" / "models"

# Required artifacts for /api/v1/predict/energy to serve 200 (NFM-4054 AC).
REQUIRED_MODEL_FILES = (
    "energy_predictor_v30.joblib",
    "energy_predictor_v3.0_metrics.json",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"required file missing: {path}"
    return path.read_text(encoding="utf-8")


def test_staging_dockerfile_copies_models_directory() -> None:
    """The staging Dockerfile must COPY apps/api/models/ into the image.

    Without this COPY, the staging container has no /app/models directory and
    /api/v1/predict/energy returns 503 (NFM-4077 reproduction). Mirror the
    prod pattern so staging stays self-contained.
    """
    content = _read(STAGING_DOCKERFILE)
    assert "COPY apps/api/models/" in content, (
        "docker/staging-api.Dockerfile is missing `COPY apps/api/models/`.\n"
        "Without this COPY the staging container has no /app/models directory,\n"
        "and /api/v1/predict/energy returns 503 'Energy predictor model is\n"
        "not available'. Mirror the prod-api.Dockerfile pattern.\n"
        "Ref: NFM-4077."
    )


def test_staging_dockerfile_copies_models_into_app_models() -> None:
    """The COPY target must be /app/models (i.e. ./models/ relative to WORKDIR)."""
    content = _read(STAGING_DOCKERFILE)
    assert "COPY apps/api/models/ ./models/" in content, (
        "docker/staging-api.Dockerfile models COPY must target ./models/ "
        "(WORKDIR is /app, so this resolves to /app/models). Got a different "
        "target path. prediction_service.py resolves MODELS_DIR via 6 parent "
        "hops from src/nfm_db/ml/ to /app/models — the COPY must populate "
        "exactly that path."
    )


def test_staging_models_copy_parity_with_prod() -> None:
    """The staging models COPY should mirror prod so behaviour stays consistent."""
    staging = _read(STAGING_DOCKERFILE)
    prod = _read(PROD_DOCKERFILE)
    staging_has = "COPY apps/api/models/ ./models/" in staging
    prod_has = "COPY apps/api/models/ ./models/" in prod
    assert prod_has, (
        "prod-api.Dockerfile missing the expected models COPY line — "
        "this test's reference is stale. Update both Dockerfiles together."
    )
    assert staging_has, (
        "prod-api.Dockerfile has `COPY apps/api/models/ ./models/` but "
        "staging-api.Dockerfile does not. This drift breaks /predict/energy "
        "on staging. Mirror the line in staging."
    )


@pytest.mark.parametrize("required", REQUIRED_MODEL_FILES)
def test_required_model_artifact_present_in_repo(required: str) -> None:
    """Every artifact the COPY bakes must exist in the repo.

    If a maintainer removes energy_predictor_v30.joblib locally without
    also removing the COPY, the resulting image silently loses the artifact
    and /predict/energy regresses to 503. Guard the source-of-truth.
    """
    target = MODELS_DIR / required
    assert target.is_file(), (
        f"Required model artifact missing from apps/api/models/: {required}\n"
        f"staging-api.Dockerfile's COPY will deliver an empty directory "
        f"without this file. /api/v1/predict/energy cannot serve without it."
    )


def test_required_model_metrics_have_rd2_label_fields() -> None:
    """v3.0 metrics sidecar must carry the rd2_label contract (NFM-4054 AC).

    Without rd2_label and rd2_label_status in the sidecar, EnergyPredictResponse
    cannot surface them — the AC-OC-4 schema fields would always be None even
    after the schema lands in main.
    """
    import json

    metrics_path = MODELS_DIR / "energy_predictor_v3.0_metrics.json"
    assert metrics_path.is_file(), (
        f"Missing {metrics_path}. See REQUIRED_MODEL_FILES in this test."
    )
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload.get("rd2_label") == "[EXPLORATORY]", (
        f"Expected rd2_label='[EXPLORATORY]' in {metrics_path}, "
        f"got {payload.get('rd2_label')!r}. The /predict/energy contract "
        f"requires this annotation for honesty."
    )
    assert payload.get("rd2_label_status") == "permanent", (
        f"Expected rd2_label_status='permanent' in {metrics_path}, "
        f"got {payload.get('rd2_label_status')!r}."
    )
