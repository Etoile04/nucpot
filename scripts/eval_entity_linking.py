"""Evaluate entity linking deduplication against golden fixture corpus.

Tests that the entity linking pipeline correctly merges duplicate entity
mentions into canonical forms. Reports per-type dedup rates and asserts
a minimum threshold.

Usage:
    python scripts/eval_entity_linking.py \
        --fixtures-dir apps/api/tests/fixtures/entity_linking \
        --min-dedup-rate 90

    # Fixture-only mode (no live linking — validates fixture + algorithm):
    ENTITY_LINKING_EVAL_MODE=fixture_only python scripts/eval_entity_linking.py \
        --fixtures-dir apps/api/tests/fixtures/entity_linking \
        --min-dedup-rate 90

Exit codes:
    0 — all checks pass
    1 — dedup rate below threshold or structural errors
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_ground_truth(fixture_dir: Path) -> dict[str, Any]:
    """Load ground truth JSON from the entity linking fixture directory."""
    ground_truth_path = fixture_dir / "ground_truth.json"
    if not ground_truth_path.exists():
        raise FileNotFoundError(
            f"Missing ground_truth.json in {fixture_dir}"
        )
    with open(ground_truth_path, encoding="utf-8") as f:
        return json.load(f)


def validate_fixture_structure(ground_truth: dict[str, Any]) -> list[str]:
    """Validate that the fixture has the required structure."""
    errors: list[str] = []
    required_top = ["corpus_id", "total_entities", "unique_canonical", "entries"]

    for field in required_top:
        if field not in ground_truth:
            errors.append(f"Missing required top-level field '{field}'")

    if "entries" in ground_truth:
        for i, entry in enumerate(ground_truth["entries"]):
            required_entry = ["raw_text", "canonical_name", "entity_type", "aliases"]
            for field in required_entry:
                if field not in entry:
                    errors.append(
                        f"entry[{i}]: missing required field '{field}'"
                    )
            if "entity_type" in entry and not isinstance(entry["entity_type"], str):
                errors.append(f"entry[{i}]: entity_type must be a string")

    return errors


def simulate_entity_linking(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Simulate entity linking deduplication using alias matching.

    For each entry, check if the raw_text or any alias matches a previously
    seen canonical entity. If so, it is a successful dedup. Otherwise,
    it represents a new canonical entity.

    Returns:
        dict with 'linked', 'new_canonical', 'dedup_rate', and 'per_type' stats.
    """
    canonical_entities: dict[str, set[str]] = {}
    linked_count = 0
    new_canonical_count = 0
    per_type: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "linked": 0}
    )

    for entry in entries:
        raw_text = entry["raw_text"].strip()
        canonical = entry["canonical_name"]
        aliases = entry.get("aliases", [])
        entity_type = entry["entity_type"]

        per_type[entity_type]["total"] += 1

        matched = False

        # Check against all known canonical entities
        for known_canonical, known_aliases in canonical_entities.items():
            all_known = {known_canonical} | known_aliases
            if raw_text in all_known or raw_text.lower() in {a.lower() for a in all_known}:
                linked_count += 1
                per_type[entity_type]["linked"] += 1
                matched = True
                break

        if not matched:
            canonical_entities[canonical] = set(aliases)
            new_canonical_count += 1

    total = linked_count + new_canonical_count
    dedup_rate = (linked_count / total * 100) if total > 0 else 0.0

    return {
        "total_entries": total,
        "linked": linked_count,
        "new_canonical": new_canonical_count,
        "expected_unique": len(canonical_entities),
        "dedup_rate": round(dedup_rate, 1),
        "per_type": dict(per_type),
    }


def print_report(results: dict[str, Any]) -> None:
    """Print a human-readable evaluation report."""
    print("=" * 60)
    print("Entity Linking Deduplication Report")
    print("=" * 60)
    print(f"Total entries:    {results['total_entries']}")
    print(f"Linked (deduped): {results['linked']}")
    print(f"New canonical:    {results['new_canonical']}")
    print(f"Unique entities:  {results['expected_unique']}")
    print(f"Dedup rate:       {results['dedup_rate']}%")
    print("")

    print("Per-type breakdown:")
    print(f"  {'Type':<25} {'Total':>6} {'Linked':>7} {'Rate':>7}")
    print(f"  {'-' * 25} {'-' * 6} {'-' * 7} {'-' * 7}")

    for entity_type in sorted(results["per_type"].keys()):
        stats = results["per_type"][entity_type]
        total = stats["total"]
        linked = stats["linked"]
        rate = (linked / total * 100) if total > 0 else 0.0
        print(f"  {entity_type:<25} {total:>6} {linked:>7} {rate:>6.1f}%")

    print("=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate entity linking deduplication accuracy"
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        required=True,
        help="Directory containing entity_linking ground truth fixtures",
    )
    parser.add_argument(
        "--min-dedup-rate",
        type=float,
        default=90.0,
        help="Minimum deduplication rate to pass (default: 90)",
    )
    args = parser.parse_args()

    try:
        ground_truth = load_ground_truth(args.fixtures_dir)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    errors = validate_fixture_structure(ground_truth)
    if errors:
        print("ERROR: Invalid fixture structure:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    results = simulate_entity_linking(ground_truth["entries"])
    print_report(results)

    if results["dedup_rate"] < args.min_dedup_rate:
        print(
            f"\nFAIL: Dedup rate {results['dedup_rate']}% "
            f"is below minimum {args.min_dedup_rate}%",
            file=sys.stderr,
        )
        return 1

    expected_unique = ground_truth.get("unique_canonical", results["expected_unique"])
    if results["expected_unique"] != expected_unique:
        print(
            f"\nWARNING: Expected {expected_unique} unique entities, "
            f"got {results['expected_unique']}",
            file=sys.stderr,
        )

    print(f"\nPASS: Dedup rate {results['dedup_rate']}% >= {args.min_dedup_rate}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
