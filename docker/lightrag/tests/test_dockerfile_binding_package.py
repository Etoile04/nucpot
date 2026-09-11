"""Regression test for NFM-4527 — Docker image must pre-install the LLM binding's
Python client package.

Background
----------
lightrag's ``pipmaster.core`` lazy-installs the binding's Python client
(``openai``, ``ollama``, ``anthropic``, ...) at container START. From inside
the prod container, ``pypi.org`` is GFW-blocked — only
``pypi.tuna.tsinghua.edu.cn`` is reachable. pipmaster does NOT honor the
Dockerfile's ``-i tuna...`` mirror config; it goes straight to the public
registry and loops forever:

    ERROR:pipmaster.core:❌ Failed to handle package: ollama
    ERROR:pipmaster.core:❌ Failed to handle package: ollama
    ...

Result: ``nucpot-prod-lightrag`` stays ``health: starting`` indefinitely and
the Web UI's ``/api/v1/lightrag/query`` errors out.

NFM-4525 (PR #1276) attempted to fix the qwen3.5:4b-nvfp4 thinking-mode issue
by switching ``PROD_LIGHTRAG_LLM_BINDING`` from ``openai`` → ``ollama`` and
patching the binding's ``_ollama_model_if_cache`` via
``docker/lightrag/sitecustomize.py``. The patch is correct in principle, but
the patch never gets to run because the image is missing the ``ollama``
Python package — pipmaster consumes the entire startup budget trying to fetch
it from ``pypi.org``.

NFM-4527 fixes this by baking ``ollama`` (+ ``httpx``, a hard transitive dep)
into the image at build time. This test prevents the regression from coming
back: any future ``LLM_BINDING=<x>`` flip on the prod sidecar MUST be paired
with adding ``<x>`` to the Dockerfile's pip-install line.

What this test enforces
-----------------------
1. The Dockerfile installs ``lightrag-hku[api]==<pinned-version>`` (the
   service itself).
2. The Dockerfile installs ``asyncpg`` (NFM-932 — PGVectorStorage runtime
   import).
3. The Dockerfile installs the binding package named in
   ``PROD_LIGHTRAG_LLM_BINDING`` (NFM-4527 — prevents the
   pipmaster-lazy-install / GFW-loop class of failures).
4. The Dockerfile installs ``httpx`` (NFM-4527 belt-and-suspenders — explicit
   transitive dep of the ``ollama`` client so pipmaster never gets a chance
   to lazy-install).
5. At least one ``pip install`` retry uses the Tsinghua mirror (the
   primary-host-only build path is GFW-unreliable per NFM-3328).

Failure modes
-------------
- New ``LLM_BINDING=<x>`` added without ``<x>`` in the Dockerfile → test
  fails with a clear "missing binding package" message; the next image build
  ships a loop-bound sidecar.
- Binding package removed during cleanup → same failure.
- Tsinghua mirror line dropped → falls back to bare ``pypi.org``; build host
  flakes on intermittent GFW (NFM-3328).
- ``lightrag-hku[api]`` or ``asyncpg`` removed → next rebuild boots a
  half-built sidecar.

Maintenance
-----------
The binding-name → package-name map below is the SOLE source of truth. Any
new binding added to lightrag must also be added here, or this test will
fail with "no package mapping for binding '<x>'".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE_PATH = REPO_ROOT / "docker" / "lightrag.Dockerfile"
PROD_ENV_EXAMPLE_PATH = REPO_ROOT / "docker" / ".env.prod.example"


# ---------------------------------------------------------------------------
# Binding → Python package map
# ---------------------------------------------------------------------------
# lightrag's pipmaster lazy-installs the package named here when
# LLM_BINDING=<key>. If you add a new LLM_BINDING value on the prod sidecar,
# add the matching PyPI package name to this dict in the same PR.
BINDING_PACKAGE_MAP: dict[str, str] = {
    "openai": "openai",
    "ollama": "ollama",
    "anthropic": "anthropic",
    "azure_openai": "openai",  # shares the openai client
    "gemini": "google-genai",
    "zhipuai": "zhipuai",
    "bedrock": "boto3",
}


def _read_prod_binding() -> str | None:
    """Parse ``PROD_LIGHTRAG_LLM_BINDING=<value>`` from docker/.env.prod.example.

    Returns the bare value (e.g. ``"ollama"``) or ``None`` if the key is
    absent / commented out.
    """
    contents = PROD_ENV_EXAMPLE_PATH.read_text()
    match = re.search(
        r"^\s*PROD_LIGHTRAG_LLM_BINDING\s*=\s*([^\s#]+)",
        contents,
        re.MULTILINE,
    )
    if not match:
        return None
    value = match.group(1).strip().strip('"').strip("'")
    return value or None


def _pip_install_packages(dockerfile_text: str) -> set[str]:
    """Extract the union of every top-level pip package named on a
    ``RUN pip install`` block in the Dockerfile.

    This is intentionally lenient: it captures bare package names, versioned
    specs (``pkg>=1.2.3``), quoted forms (``'pkg>=1.2.3'``), and extras
    (``lightrag-hku[api]==1.5.4`` — we keep the ``lightrag-hku`` head). The
    set is what we match against in the assertions below.

    The Dockerfile uses shell line-continuation (``\\\n``) to wrap the retry
    ladder across multiple physical lines. We collapse those to a single
    logical line per ``RUN`` before tokenizing so a continuation that puts
    ``ollama`` on the next line still counts as part of the same block.
    """
    # Collapse literal-backslash-newline (Dockerfile line-continuation) into
    # single spaces so the whole ``RUN pip install ...`` reads as one line.
    flat = re.sub(r"\\\n", " ", dockerfile_text)

    packages: set[str] = set()
    # Match each ``RUN pip install`` block (everything up to the next ``\n``
    # — by construction, that's the entire logical line of the collapsed
    # text).
    block_pattern = re.compile(r"RUN\s+pip\s+install[^\n]*")
    for block in block_pattern.findall(flat):
        for token in block.split():
            # Strip trailing ``||`` (shell OR short-circuit operator).
            if token == "||":
                continue
            # Drop pip flags (``-i``, ``--no-cache-dir``, ...).
            if token.startswith("-"):
                continue
            # Drop the mirror URL itself.
            if token.startswith("https://") or token.startswith("http://"):
                continue
            # Drop ``pip`` itself if it slipped through.
            if token == "pip" or token == "install":
                continue
            # Drop shell keywords / parens that surface after tokenizing.
            if token in {"&&", "(", ")", "sleep", "do", "done"}:
                continue
            # Strip leading quote, keep package spec.
            cleaned = token.strip("'\"")
            if not cleaned:
                continue
            # Drop obvious non-package tokens: numbers, sleep-N syntax, etc.
            if re.match(r"^\d+$", cleaned):
                continue
            # Head before any version specifier / extras.
            head = re.split(r"[<>=![]", cleaned, maxsplit=1)[0]
            if head:
                packages.add(head)
    return packages


def _pip_install_lines(dockerfile_text: str) -> list[str]:
    """Return each ``RUN pip install`` block as a single logical line.

    Continuation backslashes are collapsed to spaces so the whole retry
    ladder reads as one logical line per ``RUN``. The mirror assertion
    scopes its search to this set so a ``pypi.tuna.tsinghua.edu.cn``
    reference that lives in a comment (or anywhere outside an actual
    ``pip install`` rung) does NOT count as a satisfied guard — the
    previous whole-text search let the NFM-3328 build-host guard slip
    through exactly that mutation.
    """
    flat = re.sub(r"\\\n", " ", dockerfile_text)
    return re.findall(r"RUN\s+pip\s+install[^\n]*", flat)


# ---------------------------------------------------------------------------
# Static guards
# ---------------------------------------------------------------------------
def test_dockerfile_installs_lightrag_hku():
    """The service itself must be baked into the image."""
    contents = DOCKERFILE_PATH.read_text()
    packages = _pip_install_packages(contents)
    assert "lightrag-hku" in packages, (
        f"{DOCKERFILE_PATH.relative_to(REPO_ROOT)} must `pip install` "
        "`lightrag-hku[api]` — the service runtime. Found packages: "
        f"{sorted(packages)}"
    )


def test_dockerfile_installs_asyncpg():
    """NFM-932: PGVectorStorage imports asyncpg at runtime; lightrag-hku[api]
    does NOT pull it in."""
    contents = DOCKERFILE_PATH.read_text()
    packages = _pip_install_packages(contents)
    assert "asyncpg" in packages, (
        f"{DOCKERFILE_PATH.relative_to(REPO_ROOT)} must `pip install asyncpg` "
        "(NFM-932 — PGVectorStorage runtime import). "
        f"Found packages: {sorted(packages)}"
    )


def test_dockerfile_installs_prod_llm_binding_package():
    """NFM-4527: the LLM binding's Python client must be pre-installed.

    Without this, pipmaster lazy-installs the binding's package at container
    START against ``pypi.org`` (GFW-blocked from inside the container) and
    the sidecar loops on ``Failed to handle package: <x>`` forever, never
    reaching ``healthy``.
    """
    binding = _read_prod_binding()
    if binding is None:
        pytest.skip(
            f"{PROD_ENV_EXAMPLE_PATH.relative_to(REPO_ROOT)} does not set "
            "PROD_LIGHTRAG_LLM_BINDING — nothing to enforce."
        )

    expected_pkg = BINDING_PACKAGE_MAP.get(binding)
    if expected_pkg is None:
        pytest.fail(
            f"PROD_LIGHTRAG_LLM_BINDING={binding!r} has no entry in "
            "BINDING_PACKAGE_MAP. Add the matching PyPI package name to the "
            "map in docker/lightrag/tests/test_dockerfile_binding_package.py "
            "AND to the Dockerfile's pip install line."
        )

    contents = DOCKERFILE_PATH.read_text()
    packages = _pip_install_packages(contents)
    assert expected_pkg in packages, (
        f"{DOCKERFILE_PATH.relative_to(REPO_ROOT)} must `pip install "
        f"{expected_pkg}` to satisfy PROD_LIGHTRAG_LLM_BINDING={binding!r} "
        "(NFM-4527). Without it, the prod sidecar crash-loops on pipmaster "
        "lazy-install at startup (GFW-blocked pypi.org). "
        f"Found packages: {sorted(packages)}"
    )


def test_dockerfile_installs_httpx_explicit_transitive_dep():
    """NFM-4527 belt-and-suspenders: ``httpx`` is a hard transitive dep of
    the ``ollama`` Python client. Installing it explicitly removes the last
    excuse pipmaster has to lazy-install at runtime."""
    binding = _read_prod_binding()
    if binding != "ollama":
        pytest.skip(
            f"httpx-as-transitive-dep rule is specific to the ollama binding; "
            f"current binding is {binding!r}."
        )

    contents = DOCKERFILE_PATH.read_text()
    packages = _pip_install_packages(contents)
    assert "httpx" in packages, (
        f"{DOCKERFILE_PATH.relative_to(REPO_ROOT)} must `pip install httpx` "
        "(NFM-4527 belt-and-suspenders — transitive dep of the ollama client). "
        f"Found packages: {sorted(packages)}"
    )


def test_dockerfile_uses_tsinghua_mirror_on_at_least_one_retry():
    """NFM-3328: the prod build host is GFW-unreliable against pypi.org.
    At least one ``pip install`` rung must point at the Tsinghua mirror.

    The search is scoped to actual ``RUN pip install`` lines via
    ``_pip_install_lines()`` so a ``pypi.tuna.tsinghua.edu.cn`` reference
    that lives in a comment (or any text outside an actual pip-install
    rung) does NOT count as a satisfied guard — that mutation previously
    passed and let the build-host egress constraint slip through.
    """
    contents = DOCKERFILE_PATH.read_text()
    pip_lines = _pip_install_lines(contents)
    found_mirror = any(
        "pypi.tuna.tsinghua.edu.cn" in line for line in pip_lines
    )
    assert found_mirror, (
        f"{DOCKERFILE_PATH.relative_to(REPO_ROOT)} has no `pip install` rung "
        "pointing at https://pypi.tuna.tsinghua.edu.cn/simple (NFM-3328). "
        "Build host pypi.org egress is GFW-unreliable — at least one retry "
        "in the ladder must use the Tsinghua mirror. (The URL must appear on "
        "an actual `RUN pip install` line — references in comments do not "
        "count, since `pip` itself does not parse comments.)"
    )
