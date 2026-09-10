"""Regression test for NFM-4600 — Docker image must pre-install the
``pgvector`` PyPI package used by LightRAG's PGVectorStorage.

Background
----------
The prod sidecar (``nucpot-prod-lightrag``) logs show an infinite
``🔄 Updating package: pgvector`` spinner even while the container is
otherwise ``healthy``. The loop comes from lightrag's
``pipmaster.core``, which lazy-installs storage / dialect glue packages
at container START against ``pypi.org`` — a destination that is
GFW-blocked from inside the prod container (mirrors the
NFM-4527 ``ollama`` pattern, NFM-932 ``asyncpg`` pattern).

PGVectorStorage resolves ``import pgvector`` (the PyPI package — used
for SQLAlchemy vector-type adapters / asyncpg-side dialect glue). It
is NOT a transitive dep of ``lightrag-hku[api]`` and ``pip install
asyncpg`` does not pull it in. Without baking it into the image,
pipmaster burns the post-deploy latency budget (NFM-4502) on every
container start and the semantic RAG path degrades silently — the
ILIKE fallback (RAG-B, §3.2) carries user-visible traffic until the
loop resolves (it doesn't, from inside the container).

What this test enforces
-----------------------
1. The Dockerfile installs ``pgvector`` (NFM-4600 — prevents the
   pipmaster-lazy-install / GFW-loop class of failures for the
   PGVectorStorage runtime path).
2. The Dockerfile pins ``pgvector`` to a ``<1.0`` major version so a
   future upstream 1.x bump that breaks the asyncpg / sqlalchemy
   2.0 surface area used by LightRAG's PGVectorStorage cannot silently
   land in the prod image.
3. The Tsinghua mirror guard lives in
   ``test_dockerfile_binding_package.py`` (the SOLE source of truth for
   the build-host mirror egress constraint, NFM-3328). This file does
   NOT duplicate it — the binding-package test already covers that
   axis, and shadowing it here would either be a verbatim duplicate
   (zero added coverage) or drift over time.

Failure modes
-------------
- ``pgvector`` removed from the Dockerfile → next rebuild ships a
  sidecar that loops on ``Updating package: pgvector`` until
  ``pypi.org`` is somehow reachable (it isn't, from inside the
  container). The semantic RAG path silently degrades to the
  rule-based ILIKE fallback without raising any error.
- ``pgvector`` pin relaxed to ``>=1.0`` (or any unbounded spec) → a
  future upstream major bump can land in prod with a 1.x
  asyncpg-dialect break and brick PGVectorStorage. The pin keeps
  upgrades an explicit Dockerfile change.
- Tsinghua mirror line dropped → covered by
  ``test_dockerfile_binding_package.py``.

Maintenance
-----------
This file is the SOLE source of truth for the ``pgvector`` package
pin. Any future relaxation of the version spec (e.g. allowing 1.x
after upstream stabilizes its asyncpg surface) MUST be paired with
updating the assertions below.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE_PATH = REPO_ROOT / "docker" / "lightrag.Dockerfile"


def _pip_install_lines(dockerfile_text: str) -> list[str]:
    """Return each ``RUN pip install`` block as a single logical line.

    Mirrors ``test_dockerfile_binding_package._pip_install_packages``
    so the two tests stay structurally aligned. Continuation
    backslashes are collapsed to spaces so the whole retry ladder reads
    as one logical line per ``RUN``.
    """
    flat = re.sub(r"\\\n", " ", dockerfile_text)
    return re.findall(r"RUN\s+pip\s+install[^\n]*", flat)


def _all_pip_install_text(dockerfile_text: str) -> str:
    """Concatenate every ``RUN pip install`` block (collapsing
    continuations). Used to look for ``pgvector`` across the whole retry
    ladder — the package only needs to appear on at least one rung for
    the build to succeed; later rungs are fallbacks."""
    return "\n".join(_pip_install_lines(dockerfile_text))


# ---------------------------------------------------------------------------
# Static guards
# ---------------------------------------------------------------------------
def test_dockerfile_installs_pgvector():
    """NFM-4600: the pgvector PyPI package must be baked into the image.

    Without it, lightrag's pipmaster hits the GFW-blocked pypi.org
    lazy-install path on every container START and the sidecar logs
    show an infinite ``Updating package: pgvector`` spinner.
    """
    contents = DOCKERFILE_PATH.read_text()
    pip_text = _all_pip_install_text(contents)
    assert "pgvector" in pip_text, (
        f"{DOCKERFILE_PATH.relative_to(REPO_ROOT)} must `pip install pgvector` "
        "(NFM-4600 — PGVectorStorage runtime import). Without it, pipmaster "
        "lazy-installs the package against GFW-blocked pypi.org at container "
        "START and the sidecar loops on `Updating package: pgvector` even "
        "while `healthy`. The semantic RAG path then silently degrades to "
        "the rule-based ILIKE fallback."
    )


def test_dockerfile_pins_pgvector_below_1_0():
    """NFM-4600: pin ``pgvector`` to ``<1.0`` so a future upstream 1.x
    bump that breaks asyncpg / SQLAlchemy 2.0 cannot silently land in
    the prod image.

    Relaxing this pin requires an explicit Dockerfile change that
    re-validates the PGVectorStorage end-to-end path against the new
    upstream major.
    """
    contents = DOCKERFILE_PATH.read_text()
    pip_text = _all_pip_install_text(contents)
    # Extract the ``pgvector`` package spec (the quoted token starting
    # with ``pgvector`` up to the closing quote) and assert it carries
    # an upper bound of ``<1.0``.  The token may live inside a single
    # quote pair (e.g. ``'pgvector>=0.3.0,<1.0'``) — match the whole
    # spec across newlines (continuations are already collapsed).
    spec_match = re.search(r"'pgvector[^']*'|\"pgvector[^\"]*\"", pip_text)
    assert spec_match, (
        f"{DOCKERFILE_PATH.relative_to(REPO_ROOT)} has no quoted `pgvector` "
        "package spec on a `pip install` line (NFM-4600). The package must "
        "be baked into the image (see test_dockerfile_installs_pgvector)."
    )
    spec = spec_match.group(0)
    assert "<1.0" in spec, (
        f"{DOCKERFILE_PATH.relative_to(REPO_ROOT)} pins `pgvector` as "
        f"`{spec}` — must carry an upper bound of `<1.0` (NFM-4600). The "
        "pin keeps upstream 1.x asyncpg/SQLAlchemy-2.0 breaking changes "
        "from silently landing in the prod image — relax only after "
        "re-validating PGVectorStorage end-to-end against the new upstream "
        "major."
    )
