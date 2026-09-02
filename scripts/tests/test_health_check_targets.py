"""Regression tests for scripts/health_check.py target URL drift.

Context: NFM-4071 (parent NFM-4065/4067, supersedes NFM-4069 Branch B).
A 2026-07-20 commit (a69b0d33d, #249 "smoke test hardening") blanket-applied
the NFM main API's `/api/v1/` path prefix to every target in
`scripts/health_check.py` — including three pointed at `verify.nucpot.dpdns.org`,
which serves a *different* application (NucPot AutoVC 0.2.0) on its own
Cloudflare tunnel and only exposes unversioned `/api/*` routes. The result
was three perpetually-failing monitor checks for a host nginx never sees.

These tests guard against the *class* of bug, not just the three known
instances: they iterate the module-level `TARGETS` list and assert that no
URL combines a `verify.*` host with an `/api/v1/` path. If anyone adds
another mismatched target in the future, the test fails with the exact
offending name + URL — not a silent monitor regression.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import health_check  # noqa: E402


def _is_verify_api_v1(url: str) -> bool:
    """Return True iff `url` mixes a `verify.*` host with an `/api/v1/` path.

    The host match is intentionally broad (`verify.` prefix or bare `verify`)
    so the test catches future variants like `verify-staging.nucpot.dpdns.org`
    or a bare `https://verify/api/v1/...`. The path match is exact-prefix
    `/api/v1/` (with trailing slash) so it does not spuriously flag
    `/api/v1beta/` or `/api/v10/` style alternatives.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    return host.startswith("verify.") and path.startswith("/api/v1/")


def test_targets_do_not_combine_verify_host_with_api_v1_path() -> None:
    """No monitor target may combine a `verify.*` host with an `/api/v1/` path.

    See module docstring for the regression history. The test scans the
    module-level TARGETS list rather than hardcoding the three known
    offenders, so it guards against new mismatched targets added later.
    """
    assert hasattr(health_check, "TARGETS"), "health_check.TARGETS must exist"
    assert health_check.TARGETS, "health_check.TARGETS must be non-empty"

    violations: list[tuple[str, str]] = []
    for target in health_check.TARGETS:
        if _is_verify_api_v1(target.url):
            violations.append((target.name, target.url))

    assert not violations, (
        "Monitor targets combine `verify.*` host with `/api/v1/` path "
        f"(this is the NFM-4070/NFM-4071 regression class): {violations}. "
        "Either re-point the URL at the correct host (the NFM API lives at "
        "nucpot.dpdns.org) or use the host's native unversioned route "
        "(verify.nucpot.dpdns.org exposes /api/*, not /api/v1/*)."
    )


@pytest.mark.parametrize(
    "url,expected",
    [
        # Known-bad combinations (the regression class).
        ("https://verify.nucpot.dpdns.org/api/v1/health", True),
        ("https://verify.nucpot.dpdns.org/api/v1/potentials", True),
        ("https://verify.nucpot.dpdns.org/api/v1/reference-values/pending-review", True),
        # Other `verify.` subdomains would also be caught (future-proofing).
        ("https://verify.example.com/api/v1/health", True),
        ("https://verify.staging.example.com/api/v1/health", True),
        # Correct combinations (the fix).
        ("https://nucpot.dpdns.org/api/v1/health", False),
        ("https://nucpot.dpdns.org/api/v1/potentials", False),
        ("https://nucpot.dpdns.org/browse", False),
        # Correct AutoVC target (the optional AC-1 follow-up).
        ("https://verify.nucpot.dpdns.org/api/health", False),
        ("https://verify.nucpot.dpdns.org/api/potentials", False),
        # Look-alike paths that must NOT be flagged (avoid blind-sed class).
        ("https://verify.nucpot.dpdns.org/api/v1beta/health", False),
        ("https://verify.nucpot.dpdns.org/api/v10/health", False),
        ("https://verify.nucpot.dpdns.org/api/", False),
        # Hostnames with `verify` as a non-prefix label must NOT be flagged.
        # `verify-staging` is a different first label, not a `verify.` subdomain.
        ("https://verify-staging.nucpot.dpdns.org/api/v1/health", False),
        # Bare `verify` has no trailing dot, so it is not a `verify.` host.
        ("https://verify/api/v1/health", False),
        # Hostname that merely *contains* "verify" must NOT be flagged.
        ("https://notverify.nucpot.dpdns.org/api/v1/health", False),
        ("https://myverify.example.com/api/v1/health", False),
    ],
)
def test_is_verify_api_v1_classifier(url: str, expected: bool) -> None:
    """The classifier itself matches the spec — no false positives or negatives."""
    assert _is_verify_api_v1(url) is expected
