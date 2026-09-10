#!/usr/bin/env/python3
"""prod_smoke_rag_anonymous.py — Post-deploy smoke for /lightrag/query.

NFM-4593 closed the "QA 全绿生产挂" gap on /lightrag/query by:

1. Adding a regression unit test that exercises the real
   ``RuleBasedFallbackProvider.query()`` path (no method-level mock),
   so the bug pattern ``TypeError: object MappingResult can't be used
   in 'await' expression`` cannot return without a RED test failure.
2. Adding THIS post-deploy smoke that hits the live prod endpoint
   anonymously (the §3 RAG-A de-wall posture) and fails on either
   ``success=false`` with a ``Query failed:`` error envelope or an
   unexpected latency spike (proxy for silent fallback-degradation
   regressions).

The smoke runs against the live prod API at ``http://127.0.0.1:8001``
(matching the host-port mapping in ``.env.prod``); the deploy runbook
calls it after every prod deploy.

Why a separate file (not folded into ``prod_smoke_test.py``):
  * The existing smoke queries ``/api/v1/rag/query`` which predates the
    NFM-4539 RAG-A rename to ``/api/v1/lightrag/query``; consolidating
    would require coordinating with whoever owns the RAG-D Celery beat
    harness + the named-volume check.
  * RAG-A made the endpoint anonymous; the existing smoke still gates
    behind ``PROD_SMOKE_USER``/``PROD_SMOKE_PASS``, which means a
    misconfigured credential set would silently skip the most
    important post-deploy assertion.  This file intentionally has NO
    credential requirement — anonymous is the most-exposed surface.

Exit code 0 = pass, 1 = fail, 2 = skip (no API reachable).

Usage:
  python3 scripts/prod_smoke_rag_anonymous.py
  python3 scripts/prod_smoke_rag_anonymous.py --api-base http://127.0.0.1:8001
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_API_BASE = "http://127.0.0.1:8001"
QUERY_PATH = "/api/v1/lightrag/query"

# Query modes covered by the smoke.  NFM-4539 round-2 bug reproduced in
# both naive and hybrid; mirror the post-merge QA matrix.
QUERY_MODES = ("naive", "hybrid", "mix")

# Latency ceiling in seconds.  Pre-NFM-4525 cold queries took ~5-8s;
# post-fix the §3.3 budget is <10s end-to-end.  Anything north of 15s
# means the sidecar either fell back to the rule-based path without
# setting ``fallback.used`` (the §3.2 lie) or got stuck — both should
# trip the post-deploy alarm.
LATENCY_BUDGET_S = 15.0


def _hit_endpoint(api_base: str, mode: str) -> dict[str, Any]:
    """POST one anonymous query and return the parsed envelope.

    Raises ``RuntimeError`` on transport failure so the caller can
    distinguish "API unreachable" from "API returned a broken envelope".
    """
    payload = json.dumps(
        {
            "query": "UO2 thermal conductivity at 300 K",
            "mode": mode,
            "include_references": True,
            "top_k": 5,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base}{QUERY_PATH}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=LATENCY_BUDGET_S + 5) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:  # 429, 5xx, etc.
        body = exc.read().decode("utf-8", errors="replace")
        return {"_http_status": exc.code, "_body": body}
    except urllib.error.URLError as exc:
        raise RuntimeError(f"API unreachable at {api_base}: {exc}") from exc


def check_no_mapping_result_await_error(envelope: dict[str, Any]) -> bool:
    """Fail loudly on the NFM-4593 production error signature.

    The bug surfaced as::

        {"success":false,"data":null,"error":"Query failed: object MappingResult can't be used in 'await' expression"}

    Any envelope whose ``error`` mentions the forbidden ``await``
    expression on a ``MappingResult`` is treated as a hard fail — the
    fix is either missing from the deployed image, or a new instance
    of the same class of bug has resurfaced.
    """
    if envelope.get("success") is True:
        return True
    error = (envelope.get("error") or "").lower()
    if "mappingresult" in error and "await" in error:
        print(
            f"FAIL: NFM-4593 regression — envelope error mentions "
            f"MappingResult/await: {envelope.get('error')!r}"
        )
        return False
    if error.startswith("query failed"):
        # Generic Query failed — the original symptom.  Surface the
        # exact text so the operator can grep the api logs.
        print(f"FAIL: Query failed envelope: {envelope.get('error')!r}")
        return False
    return True


def check_response_shape(envelope: dict[str, Any], mode: str) -> bool:
    """Verify the §3.2 AC-4 envelope landed: ``data.fallback`` must be
    an object (not absent) so the frontend ``RagFallbackBadge`` can
    render.  Pre-NFM-4539 responses omitted ``fallback`` entirely;
    absence here is a regression to that pre-envelope shape.
    """
    if envelope.get("success") is not True:
        # Already flagged by the prior check; don't double-report.
        return False
    data = envelope.get("data") or {}
    if not isinstance(data, dict):
        print(f"FAIL [{mode}]: data envelope is not an object: {data!r}")
        return False
    if "fallback" not in data:
        print(
            f"FAIL [{mode}]: data.fallback missing — frontend §3.2 "
            f"RagFallbackBadge cannot render. envelope keys: {list(data.keys())}"
        )
        return False
    fallback = data["fallback"]
    if not isinstance(fallback, dict) or "used" not in fallback:
        print(f"FAIL [{mode}]: data.fallback malformed: {fallback!r}")
        return False
    if not isinstance(data.get("references", []), list):
        print(f"FAIL [{mode}]: data.references is not a list")
        return False
    return True


def run_smoke(api_base: str) -> int:
    """Run the anonymous-mode smoke against the live API. Returns exit code."""
    print(f"=== NFM-4593 post-deploy smoke ({api_base}{QUERY_PATH}) ===")
    print()

    failed = False

    for mode in QUERY_MODES:
        print(f"[mode={mode}] sending anonymous query...")
        start = time.monotonic()
        envelope = _hit_endpoint(api_base, mode)
        elapsed = time.monotonic() - start

        # Latency ceiling — silent fallback-degradation proxy.
        if elapsed > LATENCY_BUDGET_S:
            print(
                f"FAIL [{mode}]: latency {elapsed:.2f}s exceeds "
                f"{LATENCY_BUDGET_S}s budget — fallback path likely engaged "
                f"without setting fallback.used=true"
            )
            failed = True

        # NFM-4593 hard fail.
        if not check_no_mapping_result_await_error(envelope):
            failed = True

        # Envelope shape contract.
        if envelope.get("_http_status"):
            print(
                f"FAIL [{mode}]: HTTP {envelope['_http_status']}: "
                f"{envelope.get('_body', '')[:200]!r}"
            )
            failed = True
        elif not check_response_shape(envelope, mode):
            failed = True
        else:
            used = envelope["data"]["fallback"].get("used", False)
            ref_count = len(envelope["data"].get("references", []))
            print(
                f"PASS [{mode}]: {elapsed:.2f}s fallback.used={used} "
                f"references={ref_count}"
            )

    print()
    if failed:
        print("RESULT: FAIL — NFM-4593 smoke detected a regression")
        return 1
    print("RESULT: ALL PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NFM-4593 post-deploy smoke for /lightrag/query (anonymous)"
    )
    parser.add_argument(
        "--api-base",
        default=DEFAULT_API_BASE,
        help=f"API base URL (default: {DEFAULT_API_BASE})",
    )
    args = parser.parse_args()

    try:
        return run_smoke(args.api_base)
    except RuntimeError as exc:
        # API unreachable — skip (not a fail) so the deploy runbook
        # doesn't false-alarm when the smoke target host is wrong.
        print(f"SKIP: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
