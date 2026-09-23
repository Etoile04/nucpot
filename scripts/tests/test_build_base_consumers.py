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

ADR-022 D2 note (ruled 2026-09-23, NFM-5169 Option A): D2 is re-scoped to
BuildKit-verified CI-only build paths. Per the NFM-5159 build-matrix audit
no in-scope consumer Dockerfile has one — prod-api/staging-api build on
deploy-host classic-builder paths (production-deployment.yml candidate build,
deploy_prod.sh, staging_deploy.sh, all behind the nfm-g2 gate), e2e-hub/
e2e-resource on host-driven docker-compose.e2e.yml (classic). The classic
builder hard-errors on ``RUN --mount`` ("the --mount option requires
BuildKit") and the gate rejects buildkit's privileged boot container
(NFM-4357; re-verified 2026-09-23: ``container config rejected:
Privileged=true``). So these Dockerfiles must carry NO BuildKit-only syntax
and keep ``--no-cache-dir``/``--no-cache`` installs with the tuna->pypi
retry ladder (D5) as the pip-side resilience line.
"""

from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parents[2]

BUILD_BASE_REF = "ghcr.io/etoile04/nucpot-build-base:stable"

CONSUMER_DOCKERFILES = (
    REPO_ROOT / "docker" / "prod-api.Dockerfile",
    REPO_ROOT / "docker" / "staging-api.Dockerfile",
    REPO_ROOT / "docker" / "lightrag.Dockerfile",
)

# NFM-5169 Option A normative rule: BuildKit-only syntax is permitted ONLY in
# Dockerfiles whose EVERY build path has BuildKit verified available. Per the
# NFM-5159 build-matrix audit these five all build on classic-builder paths
# (deploy-host scripts behind the nfm-g2 gate, host-driven compose for e2e) —
# one classic path vetoes BuildKit syntax for the whole file.
CLASSIC_BUILDER_DOCKERFILES = (
    REPO_ROOT / "docker" / "prod-api.Dockerfile",
    REPO_ROOT / "docker" / "staging-api.Dockerfile",
    REPO_ROOT / "docker" / "lightrag.Dockerfile",
    REPO_ROOT / "docker" / "e2e-hub.Dockerfile",
    REPO_ROOT / "docker" / "e2e-resource.Dockerfile",
)

# BuildKit-only syntax the classic builder cannot parse: RUN/COPY flags
# --mount/--ssh and RUN/COPY heredocs (``RUN <<EOF``). ``COPY --from`` is
# classic-safe (multi-stage, docker 17.05) and deliberately NOT matched.
_BUILDKIT_ONLY_SYNTAX = re.compile(r"(?im)^\s*(?:RUN|COPY)\s+(?:--(?:mount|ssh)\b|<<)")


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


def test_classic_builder_dockerfiles_have_no_buildkit_only_syntax() -> None:
    """NFM-5169 Option A: no BuildKit-only syntax on classic-built files.

    Every Dockerfile in CLASSIC_BUILDER_DOCKERFILES is consumed by at least
    one classic-builder path (deploy-host scripts behind the nfm-g2 gate or
    host-driven compose). The classic builder hard-errors on ``RUN --mount``
    ("the --mount option requires BuildKit"), so a well-meant pip cache mount
    added later would break every deploy-host build of that image. BuildKit
    syntax stays permitted only where every build path has BuildKit verified
    (today: CI ubuntu-latest jobs only, e.g. the build-base workflow).
    """
    for path in CLASSIC_BUILDER_DOCKERFILES:
        content = _code_lines(_read(path))
        hits = _BUILDKIT_ONLY_SYNTAX.findall(content)
        assert not hits, (
            f"{path.name} uses BuildKit-only syntax {hits!r} but builds on "
            "classic-builder paths (NFM-5169 Option A re-scope of ADR-022 "
            "D2). Use --no-cache-dir installs + the tuna->pypi ladder (D5) "
            "instead; a cache mount here hard-errors the deploy-host build."
        )


def test_classic_builder_pip_legs_stay_no_cache() -> None:
    """Deploy-host installs keep --no-cache-dir/--no-cache (NFM-5169).

    With D2 re-scoped off these paths, an uncached install IS the contract:
    the classic builder has no cache-mount escape hatch, and D5 retry
    ladders bound the residual pip exposure. Re-adding a cache dir flag pair
    (e.g. ``--cache-dir``) on a classic path just burns deploy time on a
    cache the next build cannot hit.
    """
    expected_no_cache = {
        "prod-api.Dockerfile": "pip install --no-cache-dir",
        "lightrag.Dockerfile": "pip install --no-cache-dir",
        "e2e-hub.Dockerfile": "pip install --no-cache-dir",
        "e2e-resource.Dockerfile": "pip install --no-cache-dir",
        "staging-api.Dockerfile": "uv pip install --system --no-cache",
    }
    for path in CLASSIC_BUILDER_DOCKERFILES:
        content = _code_lines(_read(path))
        assert expected_no_cache[path.name] in content, (
            f"{path.name} lost its {expected_no_cache[path.name]!r} install "
            "leg. NFM-5169 Option A keeps classic-builder installs uncached "
            "with D5 ladders as the resilience line."
        )


def test_buildkit_only_syntax_matcher_has_teeth() -> None:
    """The guard regex must catch every banned form and pass classic-safe ones.

    Pins the matcher itself against synthetic strings so a future regex edit
    cannot silently defang test_classic_builder_dockerfiles_have_no_
    buildkit_only_syntax without a visible failure here.
    """
    banned = (
        "RUN --mount=type=cache,target=/root/.cache/pip pip install .",
        "run --mount=type=secret,id=tokens cat /run/secrets/tokens",
        "COPY --mount=type=bind,source=dist,target=/app/dist dist /app/dist",
        "RUN --ssh=default git clone git@github.com:example/repo.git",
        "RUN <<EOF\npip install .\nEOF",
        "COPY <<EOF /app/deps.txt\nflask\nEOF",
    )
    for line in banned:
        assert _BUILDKIT_ONLY_SYNTAX.search(line), f"matcher missed: {line!r}"

    classic_safe = (
        "RUN pip install --no-cache-dir .",
        "COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv",
        "COPY apps/api/pyproject.toml ./",
        "RUN mkdir -p /app/data",
        "# comment: RUN --mount=type=cache would be BuildKit-only",
    )
    for line in classic_safe:
        assert not _BUILDKIT_ONLY_SYNTAX.search(line), (
            f"matcher false-positives on classic-safe line: {line!r}"
        )
