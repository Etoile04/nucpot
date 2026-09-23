"""Tests for ADR-022 D1 consumer Dockerfiles — ghcr build-base adoption.

NFM-5159 (ADR-022 D1 consumer side, NFM-5155 scope B): docker/prod-api.Dockerfile,
docker/staging-api.Dockerfile and docker/lightrag.Dockerfile build FROM the
pre-baked ``ghcr.io/etoile04/nucpot-build-base:stable`` image (built nightly by
.github/workflows/base-image.yml, NFM-5156) so routine builds perform ZERO
apt-get network legs. The 2026-09-23 timeout-cancel pair (NFM-5153/
NFM-5154) came from apt mirror stalls inside consumer builds; moving those
legs into the nightly base build is the ADR-022 D1 fix.

These are content assertions in the style of test_staging_dockerfile_models.py:
the Dockerfiles are build inputs, not importable code, so the contract is
pinned by parsing the file text.

ADR-022 D2 note: pip BuildKit cache mounts (``RUN --mount=type=cache,...``)
are deliberately NOT asserted here. Every deploy-host build path runs the
classic builder behind the nfm-g2 gate, which rejects buildkit's boot
container (NFM-4357; re-verified 2026-09-23: ``container config rejected:
Privileged=true``), and the classic builder hard-errors on ``RUN --mount``.
D2 on consumer Dockerfiles is pending the ADR-013 G5 vs ADR-022 conflict
ruling — see the NFM-5159 thread. The tuna->pypi retry ladder (D5) stays
as the pip-side resilience line in the meantime.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

BUILD_BASE_REF = "ghcr.io/etoile04/nucpot-build-base:stable"

CONSUMER_DOCKERFILES = (
    REPO_ROOT / "docker" / "prod-api.Dockerfile",
    REPO_ROOT / "docker" / "staging-api.Dockerfile",
    REPO_ROOT / "docker" / "lightrag.Dockerfile",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"required file missing: {path}"
    return path.read_text(encoding="utf-8")


def _from_lines(content: str) -> list:
    return [
        line.strip()
        for line in content.splitlines()
        if line.strip().upper().startswith("FROM ")
    ]


def _code_lines(content: str) -> str:
    """Comment-striipped text — assertions target instructions, not prose.

    The Dockerfiles' rationale comments legitimately mention the tokens
    being banned (e.g. "ZERO apt-get network legs" as the stated goal), so
    invocations and env assignments are checked on comment-free lines only.
    """
    return "\n".join(
        line for line in content.splitlines() if not line.lstrip().startswith("#")
    )


def test_consumer_dockerfiles_build_from_ghcr_build_base() -> None:
    """All three ADR-022 scope-B consumers FROM the pre-baked base image.

    If a Dockerfile still FROMs python:3.12-slim directly, its build re-runs
    the flaky apt legs the base image exists to absorb (NFM-5153 class).
    """
    for path in CONSUMER_DOCKERFILES:
        content = _read(path)
        froms = _from_lines(content)
        assert froms, f"{path.name} has no FROM instruction"
        assert froms[0] == f"FROM {BUILD_BASE_REF}" or froms[0].startswith(
            f"FROM {BUILD_BASE_REF}"
        ), (
            f"{path.name} must FROM {BUILD_BASE_REF} (ADR-022 D1 / NFM-5159); "
            f"found: {froms[0]!r}"
        )


def test_consumer_dockerfiles_have_zero_apt_get_legs() -> None:
    """Zero apt-get invocations may remain in the consumer Dockerfiles.

    The apt build deps (gcc libpq-dev libcurl4-openssl-dev curl
    ca-certificates) are pre-baked into the base image; a leftover apt-get
    leg reintroduces the mirror-stall failure class (2026-09-23 brownout).
    """
    for path in CONSUMER_DOCKERFILES:
        content = _code_lines(_read(path))
        assert "apt-get" not in content, (
            f"{path.name} still contains an apt-get invocation. apt legs "
            "belong in docker/build-base.Dockerfile (nightly shock absorber, "
            "ADR-022 D1) — consumer builds must stay zero-apt. Ref: NFM-5159."
        )


def test_prod_api_retains_pip_retry_ladder() -> None:
    """D5: the tuna->pypi ladder survives as the pip-side second line.

    ADR-022 D5 keeps the retry ladder behind the (pending) D2 cache mounts;
    on the classic-builder deploy paths it is the ONLY pip resilience line.
    """
    content = _read(REPO_ROOT / "docker" / "prod-api.Dockerfile")
    assert "pypi.tuna.tsinghua.edu.cn/simple" in content, (
        "docker/prod-api.Dockerfile lost the tuna mirror leg of the pip "
        "retry ladder (ADR-022 D5 / NFM-2418). Ref: NFM-5159."
    )


def test_prod_api_guards_untouched() -> None:
    """ADR-015 GIT_SHA plumbing, xgboost layer and migration guards stay.

    NFM-5159 hard constraint: only network legs change — the deploy-sha
    build-arg (ADR-015 §4), the xgboost defensive pin and the prod-migration
    guard COPY must survive the base-image switch byte-for-byte in effect.
    """
    content = _read(REPO_ROOT / "docker" / "prod-api.Dockerfile")
    for required in (
        'ARG GIT_SHA=""',
        "ENV NFM_GIT_SHA=${GIT_SHA}",
        "'xgboost>=3.0,<4'",
        "COPY apps/api/scripts/check_prod_migration.py"
        " /usr/local/bin/check_prod_migration.py",
        "COPY apps/api/migrations/ ./migrations/",
    ):
        assert required in content, (
            f"docker/prod-api.Dockerfile lost required plumbing: {required!r}. "
            "ADR-015/xgboost/migration-guard lines are protected by the "
            "NFM-5159 hard constraints."
        )


def test_consumer_dockerfiles_carry_no_proxy_env() -> None:
    """ADR-018 / ADR-022 D4: no proxy env survives in consumer Dockerfiles.

    staging-api.Dockerfile historically carried an ARG/ENV HTTP_PROXY_URL
    block routing uv through the host's Clash proxy; D4 rejects proxy
    re-introduction (single point of failure, measured 5.5x latency), and
    the NFM-5159 hard constraints say NO proxy env anywhere.
    """
    for path in CONSUMER_DOCKERFILES:
        content = _code_lines(_read(path))
        for banned in ("HTTP_PROXY_URL", "http_proxy=", "https_proxy="):
            assert banned not in content, (
                f"{path.name} still references {banned!r}. Proxy env is "
                "banned by ADR-018 / ADR-022 D4 (deproxy egress). Ref: "
                "NFM-5159."
            )
