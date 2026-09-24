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

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

BUILD_BASE_REF = "ghcr.io/etoile04/nucpot-build-base:stable"

BASE_IMAGE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "base-image.yml"

# NFM-5203: every platform a consumer build path resolves `stable` FOR.
# The deploy host is arm64 macOS (classic builder, DOCKER_BUILDKIT=0), CI
# consumers are amd64 ubuntu-latest — the nightly must serve BOTH.
REQUIRED_BASE_PLATFORMS = ("linux/amd64", "linux/arm64")

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


def _build_job() -> dict:
    """The `build` job dict from .github/workflows/base-image.yml."""
    workflow = yaml.safe_load(_read(BASE_IMAGE_WORKFLOW))
    jobs = workflow.get("jobs") or {}
    build = jobs.get("build")
    assert build, "base-image.yml lost its `build` job (nightly publisher)"
    return build


def test_base_image_workflow_publishes_multiarch_manifest() -> None:
    """NFM-5203: the nightly must publish amd64 AND arm64 under `stable`.

    The production deploy runner is a self-hosted arm64 macOS host (classic
    builder, DOCKER_BUILDKIT=0 pinned by production-deployment.yml), while
    the nightly builds on amd64 ubuntu-latest. Without a `platforms:` input
    build-push-action publishes linux/amd64 only, and the deploy host's
    ``FROM ghcr.io/etoile04/nucpot-build-base:stable`` resolves for
    linux/arm64 -> "no matching manifest in the manifest list entries" —
    the 2026-09-23 Production Deployment hard-down (4 red runs on main,
    6 commits undeployed). CI stayed green because ci.yml consumers are
    amd64, which is exactly the deploy-host-only regression shape this
    guard pins: both REQUIRED_BASE_PLATFORMS must be in the build-push
    step's platforms list, and docker/setup-qemu-action must be present so
    the arm64 leg can execute under emulation on the amd64 hosted runner.
    """
    steps = _build_job().get("steps") or []
    build_push = next(
        (
            step
            for step in steps
            if str(step.get("uses", "")).startswith("docker/build-push-action")
        ),
        None,
    )
    assert build_push, (
        "base-image.yml lost its docker/build-push-action step — the nightly "
        "publisher (ADR-022 D1 / NFM-5156) must build AND push `stable`."
    )
    platforms = build_push.get("with", {}).get("platforms") or ""
    for platform in REQUIRED_BASE_PLATFORMS:
        assert platform in platforms.split(","), (
            f"base-image.yml build-push platforms={platforms!r} omits "
            f"{platform!r}. The deploy host resolves `stable` for arm64 and "
            "CI consumers for amd64 — the nightly must publish BOTH or the "
            "arm64 deploy build dies at FROM (NFM-5203 Production Deployment "
            "hard-down)."
        )

    assert any(
        str(step.get("uses", "")).startswith("docker/setup-qemu-action")
        for step in steps
    ), (
        "base-image.yml lacks docker/setup-qemu-action. The arm64 leg of a "
        "multi-arch build cannot execute on the amd64 ubuntu-latest runner "
        "without the binfmt handlers it installs (NFM-5203)."
    )

    # Order matters: the emulators must be registered before the build starts.
    step_indexes = {
        str(step.get("uses", "")).split("@")[0]: idx for idx, step in enumerate(steps)
    }
    assert step_indexes.get("docker/setup-qemu-action", len(steps)) < step_indexes.get(
        "docker/build-push-action", -1
    ), (
        "docker/setup-qemu-action must run BEFORE docker/build-push-action — "
        "a QEMU step appended after the build leaves the arm64 leg "
        "unbuildable on the amd64 runner (NFM-5203)."
    )


def test_base_image_build_timeout_sized_for_qemu() -> None:
    """NFM-5203: the build backstop must cover QEMU-emulated arm64 legs.

    The 15-minute backstop was sized for a native amd64 build. Under
    docker/setup-qemu-action the arm64 leg runs emulated and runs the same
    apt legs materially slower, so the hang backstop must be at least 25
    minutes or a healthy nightly can be failed-red by its own timeout
    (turning the shock absorber into the outage — the NFM-5151 blind spot
    this workflow's queue-never-cancel design already guards elsewhere).
    """
    timeout = _build_job().get("timeout-minutes") or 0
    assert timeout >= 25, (
        f"base-image.yml build job timeout-minutes={timeout!r} is not sized "
        "for the QEMU-emulated arm64 leg of the multi-arch publish (NFM-5203 "
        "fix); raise the backstop to >= 25."
    )
