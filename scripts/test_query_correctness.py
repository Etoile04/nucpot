"""Test query correctness against a fixture knowledge graph.

Runs predefined queries against a test KG (loaded from fixtures) and
asserts the results match expected ground truth.

Usage:
    python scripts/test_query_correctness.py \
        --fixtures-dir apps/api/tests/fixtures/query_correctness

Exit codes:
    0 — all queries produce correct results
    1 — one or more queries returned incorrect results

TODO(Phase 2 follow-up, NFM-860 review finding H2):
This script runs canned traversal logic against a static JSON fixture,
not the B2.4 KG query API (api/v1/kg.py / NFM-858). Per the reviewer:

  > "AC says 'Run entity linking against test corpus' — that requires
  > wiring the scripts to the production services once B2.2/B2.4 land."

When NFM-856 (entity linker) and NFM-858 (query API) land, replace
``run_queries()`` with a thin client that calls the FastAPI routes
(GET /api/v1/kg/nodes, /api/v1/kg/edges, etc.) against a live
test database — the fixture stays as offline validation, but CI will
exercise the actual query layer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_fixture(fixture_dir: Path) -> dict[str, Any]:
    """Load the query correctness test fixture."""
    ground_truth_path = fixture_dir / "ground_truth.json"
    if not ground_truth_path.exists():
        raise FileNotFoundError(
            f"Missing ground_truth.json in {fixture_dir}"
        )
    with open(ground_truth_path, encoding="utf-8") as f:
        return json.load(f)


def build_graph(fixture: dict[str, Any]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Build adjacency lists from the fixture nodes and edges.

    Returns:
        nodes: dict mapping node_id to node data
        outgoing: dict mapping node_id to list of edge dicts
    """
    nodes: dict[str, dict] = {}
    outgoing: dict[str, list[dict]] = {}

    for node in fixture.get("nodes", []):
        nid = node["id"]
        nodes[nid] = node
        outgoing[nid] = []

    for edge in fixture.get("edges", []):
        outgoing.setdefault(edge["source"], []).append(edge)

    return nodes, outgoing


def build_reverse_graph(outgoing: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Build a reverse adjacency list (incoming edges)."""
    reverse: dict[str, list[dict]] = {}
    for source, edges in outgoing.items():
        for edge in edges:
            reverse.setdefault(edge["target"], []).append(edge)
    return reverse


def execute_query(
    query: dict[str, Any],
    nodes: dict[str, dict],
    outgoing: dict[str, list[dict]],
    reverse: dict[str, list[dict]],
) -> dict[str, Any]:
    """Execute a single query against the in-memory graph."""
    qtype = query["type"]
    start = query["start_node"]
    relation_filter = query.get("relation_filter")

    if qtype == "neighbors":
        edges = outgoing.get(start, [])
        if relation_filter:
            edges = [e for e in edges if e["relation_type"] == relation_filter]
        targets = sorted([e["target"] for e in edges])
        return {"targets": targets, "count": len(targets)}

    if qtype == "reverse_neighbors":
        edges = reverse.get(start, [])
        if relation_filter:
            edges = [e for e in edges if e["relation_type"] == relation_filter]
        targets = sorted([e["source"] for e in edges])
        return {"targets": targets, "count": len(targets)}

    if qtype == "outgoing_count":
        edges = outgoing.get(start, [])
        return {"targets": [], "count": len(edges)}

    raise ValueError(f"Unknown query type: {qtype}")


def run_queries(
    fixture: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run all predefined queries and return per-query results."""
    nodes, outgoing = build_graph(fixture)
    reverse = build_reverse_graph(outgoing)
    results: list[dict[str, Any]] = []

    for query in fixture.get("queries", []):
        result = execute_query(query, nodes, outgoing, reverse)
        passed = True
        failures: list[str] = []

        expected_targets = sorted(query.get("expected_targets", []))
        expected_count = query.get("expected_count")

        if expected_count is not None and result["count"] != expected_count:
            passed = False
            failures.append(
                f"count: expected {expected_count}, got {result['count']}"
            )

        if expected_targets is not None and result["targets"] != expected_targets:
            passed = False
            failures.append(
                f"targets: expected {expected_targets}, got {result['targets']}"
            )

        results.append({
            "query_id": query["id"],
            "description": query["description"],
            "passed": passed,
            "expected_count": expected_count,
            "actual_count": result["count"],
            "expected_targets": expected_targets,
            "actual_targets": result["targets"],
            "failures": failures,
        })

    return results


def print_report(results: list[dict[str, Any]]) -> None:
    """Print a human-readable query correctness report."""
    print("=" * 60)
    print("Query Correctness Test Report")
    print("=" * 60)

    passed_count = sum(1 for r in results if r["passed"])
    total_count = len(results)

    for result in results:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"\n[{status}] {result['query_id']}: {result['description']}")
        print(f"       Expected count: {result['expected_count']} | Actual: {result['actual_count']}")
        if result["failures"]:
            for failure in result["failures"]:
                print(f"       - {failure}")

    print("")
    print("=" * 60)
    print(f"Results: {passed_count}/{total_count} queries passed")
    print("=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test query correctness against fixture KG"
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        required=True,
        help="Directory containing query_correctness ground truth fixtures",
    )
    args = parser.parse_args()

    try:
        fixture = load_fixture(args.fixtures_dir)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    results = run_queries(fixture)
    print_report(results)

    all_passed = all(r["passed"] for r in results)
    if not all_passed:
        failed = [r for r in results if not r["passed"]]
        print(
            f"\nFAIL: {len(failed)} query/queries returned incorrect results",
            file=sys.stderr,
        )
        return 1

    print(f"\nPASS: All {len(results)} queries produced correct results")
    return 0


if __name__ == "__main__":
    sys.exit(main())
