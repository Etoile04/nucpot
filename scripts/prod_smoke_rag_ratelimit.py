#!/usr/bin/env python3
"""NFM-4681 AC-3 field re-verification — ``/api/v1/lightrag/query`` 6/60s probe.

Posts six anonymous ``/api/v1/lightrag/query`` requests as fast as
possible from a single client IP and asserts the 6th is throttled with
HTTP 429 and a ``Retry-After`` header.  The probe proves the spec's
``5/minute/IP`` quota (NFM-4539 RAG-A, NFM-4681 AC-2) is enforced
through the shared storage backend on the 4-worker prod API.

Tier-2 queries take ~11s end-to-end, so the probe keeps the
``query`` string short and lets the response body fall through to
the rule-based fallback rather than blocking on the sidecar.
``fallback=True`` is the success signal that the API itself accepted
the request and produced an envelope.

Usage::

    python scripts/prod_smoke_rag_ratelimit.py \\
        --base-url https://nucpot.dpdns.org

    python scripts/prod_smoke_rag_ratelimit.py \\
        --base-url http://localhost:8001 \\
        --budget 5 --window-seconds 60

Exit codes::

    0  AC-3 satisfied: requests 1..N pass, request N+1 returns 429
       with a Retry-After header
    1  probe configuration error (bad URL, missing dep, …)
    2  probe failed: 6th request was NOT throttled (the quota
       regressed; investigate the slowapi storage backend wiring)
    3  probe failed: 429 was returned without a Retry-After header
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
from typing import NamedTuple
from urllib.parse import urljoin

import urllib.error
import urllib.request


DEFAULT_PATH = "/api/v1/lightrag/query"
DEFAULT_BUDGET = 5
DEFAULT_WINDOW_SECONDS = 60
PROBE_QUERY = "UO2 density"
REQUEST_TIMEOUT_S = 15


class ProbeResult(NamedTuple):
    index: int
    status: int
    remaining: str | None
    reset_epoch: str | None
    retry_after: str | None
    body_excerpt: str


def _post_query(base_url: str, index: int) -> ProbeResult:
    url = urljoin(base_url.rstrip("/") + "/", DEFAULT_PATH.lstrip("/"))
    payload = json.dumps({"query": PROBE_QUERY}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.time()
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        status = resp.status
        headers = {k.lower(): v for k, v in resp.headers.items()}
        body = resp.read(200).decode("utf-8", errors="replace")
    elapsed = time.time() - start
    return ProbeResult(
        index=index,
        status=status,
        remaining=headers.get("x-ratelimit-remaining"),
        reset_epoch=headers.get("x-ratelimit-reset"),
        retry_after=headers.get("retry-after"),
        body_excerpt=f"{elapsed:0.2f}s status={status} body={body[:80]!r}",
    )


def _dispatch_concurrent(base_url: str, total: int) -> list[ProbeResult]:
    """Fire ``total`` requests in parallel so they fall inside one window.

    Tier-2 latency is ~11s per request, so a sequential probe takes ~70s
    for six calls — that pushes the 6th call into the *next* minute
    window.  Concurrency keeps the entire probe inside the same 60s
    fixed window so the 5/min/IP quota (NFM-4539 RAG-A) is the only
    throttle in play.
    """
    results: list[ProbeResult] = []
    with cf.ThreadPoolExecutor(max_workers=total) as pool:
        futures = [pool.submit(_post_query, base_url, i) for i in range(total)]
        for fut in cf.as_completed(futures):
            results.append(fut.result())
    results.sort(key=lambda r: r.index)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base-url",
        default=os.environ.get("NFM_API_BASE_URL", "http://localhost:8001"),
        help="API base URL (default: %(default)s or $NFM_API_BASE_URL)",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_BUDGET,
        help="Requests to allow inside the window before expecting 429 (default: %(default)s)",
    )
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=DEFAULT_WINDOW_SECONDS,
        help="Window length to assert against (default: %(default)s)",
    )
    args = parser.parse_args()

    if args.budget < 1:
        sys.stderr.write("prod_smoke_rag_ratelimit: --budget must be >= 1\n")
        return 1

    total = args.budget + 1
    print(
        f"prod_smoke_rag_ratelimit: posting {total} calls to "
        f"{args.base_url}{DEFAULT_PATH} (budget={args.budget}/"
        f"{args.window_seconds}s)"
    )

    try:
        results = _dispatch_concurrent(args.base_url, total)
    except urllib.error.HTTPError as exc:
        # A 429 here means the budget was already exhausted by an
        # earlier probe run; surface the verdict but treat it as
        # AC-3-passing only if every ``budget`` requests succeeded.
        sys.stderr.write(
            f"prod_smoke_rag_ratelimit: HTTP {exc.code} from "
            f"{args.base_url}{DEFAULT_PATH}: {exc.reason}\n"
        )
        if exc.code == 429:
            print(
                "prod_smoke_rag_ratelimit: bucket was already empty at "
                "probe start — wait one window and re-run."
            )
            return 2
        return 1
    except urllib.error.URLError as exc:
        sys.stderr.write(
            f"prod_smoke_rag_ratelimit: connection error to {args.base_url}: {exc.reason}\n"
        )
        return 1

    print("prod_smoke_rag_ratelimit: results:")
    for r in results:
        print(f"  R{r.index + 1} {r.body_excerpt}")

    in_window = [r for r in results if r.status == 200]
    over_limit = [r for r in results if r.status == 429]

    if len(over_limit) != 1:
        sys.stderr.write(
            "prod_smoke_rag_ratelimit: AC-3 FAIL — expected exactly one "
            f"429 response, got {len(over_limit)} (passed={len(in_window)}/"
            f"{args.budget}).\n"
        )
        return 2

    if len(in_window) != args.budget:
        sys.stderr.write(
            "prod_smoke_rag_ratelimit: AC-3 FAIL — fewer than "
            f"{args.budget} requests returned 200 before the throttle "
            f"({len(in_window)}). Investigate slowapi storage backend "
            "wiring (NFM-4681 AC-2).\n"
        )
        return 2

    throttled = over_limit[0]
    if not throttled.retry_after:
        sys.stderr.write(
            "prod_smoke_rag_ratelimit: AC-3 FAIL — 429 response is "
            "missing the Retry-After header.\n"
        )
        return 3

    print(
        f"prod_smoke_rag_ratelimit: OK — {len(in_window)}/{args.budget} "
        f"passed, R{args.budget + 1} throttled with Retry-After="
        f"{throttled.retry_after}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
