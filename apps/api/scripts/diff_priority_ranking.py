#!/usr/bin/env python3
"""Re-rank diff report generator (NFM-3578 / NFM-3548-D).

Compares the existing extraction-candidate heuristic score against the new
weighted-priority score produced by ``nfm_db.services.priority.combine(...)``.
Reads a JSON snapshot of the candidate queue, computes the new score for
each row, sorts both rankings, and emits a markdown report with the
top-N delta table, Spearman rank correlation, and move accounting.

The script is idempotent: identical input + flags -> identical output bytes
(no timestamps in the body; only the ISO date in the H1 title).

CLI
---
    python apps/api/scripts/diff_priority_ranking.py \\
        --input candidates.json \\
        --limit 50 \\
        --weights '{"ontology":0.4,"atf":0.3,"citation":0.3}' \\
        --out docs/priority_v2/diff_report_YYYY-MM-DD.md

Input JSON shape
----------------
    {
        "candidates": [
            {
                "candidate_id": "<id>",
                "score_old": <float>,
                "signals": {"ontology": <float>, "atf": <float>, "citation": <float>}
            },
            ...
        ]
    }

Output markdown shape
---------------------
    # Priority diff report -- <ISO date>
    ...
    ## Summary
    - Candidates: <n>
    - Moves into top-<limit>: <n>
    - Moves out of top-<limit>: <n>
    - Spearman rank correlation: <rho>
    ...
    ## Top-<limit> delta
    | rank_old | rank_new | candidate_id | score_old | score_new | delta_rank |
    ...
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap -- make ``nfm_db.services.priority`` importable when run as
# a script outside the pytest harness.
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from nfm_db.services.priority import combine as priority_combine  # noqa: E402

# ---------------------------------------------------------------------------
# Pure data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateRecord:
    """One candidate row read from the input snapshot."""

    candidate_id: str
    score_old: float
    signals: Mapping[str, float]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CandidateRecord:
        if "signals" not in raw:
            raise ValueError(
                f"candidate {raw.get('candidate_id')!r} missing 'signals' key"
            )
        return cls(
            candidate_id=str(raw["candidate_id"]),
            score_old=float(raw["score_old"]),
            signals={k: float(v) for k, v in raw["signals"].items()},
        )


@dataclass(frozen=True)
class ScoredCandidate:
    """A candidate with both old + new scores populated."""

    candidate_id: str
    score_old: float
    score_new: float
    signals: Mapping[str, float]


@dataclass(frozen=True)
class RankedCandidate:
    """A candidate with both old and new ranks and their delta."""

    candidate_id: str
    score_old: float
    score_new: float
    rank_old: float  # float because ties share an average rank
    rank_new: float
    delta_rank: float  # rank_old - rank_new; negative = improved


# ---------------------------------------------------------------------------
# Library surface
# ---------------------------------------------------------------------------


def parse_candidates(input_path: Path) -> list[CandidateRecord]:
    """Read the JSON input snapshot and return validated CandidateRecords."""
    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("input JSON must be an object with a 'candidates' key")
    if "candidates" not in payload:
        raise ValueError("input JSON must have a top-level 'candidates' key")
    return [CandidateRecord.from_dict(c) for c in payload["candidates"]]


def score_candidates(
    rows: Iterable[CandidateRecord],
    weights: Mapping[str, float] | None = None,
) -> list[ScoredCandidate]:
    """Compute score_new via priority.combine(...) for each row."""
    return [
        ScoredCandidate(
            candidate_id=row.candidate_id,
            score_old=row.score_old,
            score_new=priority_combine(row.signals, weights),
            signals=row.signals,
        )
        for row in rows
    ]


def _competition_ranks(
    sorted_pairs: list[tuple[str, float]],
) -> list[tuple[str, float]]:
    """Assign competition (1, 2, 2, 4) ranks; ties share the average position.

    Returns ``[(candidate_id, rank), ...]`` in the input's sort order.
    """
    ranks: list[tuple[str, float]] = []
    i = 0
    n = len(sorted_pairs)
    while i < n:
        j = i + 1
        while j < n and sorted_pairs[j][1] == sorted_pairs[i][1]:
            j += 1
        # Positions i+1..j are tied -> average rank = (i + 1 + j) / 2
        rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks.append((sorted_pairs[k][0], rank))
        i = j
    return ranks


def rank_candidates(
    scored: Iterable[ScoredCandidate],
    key: str,
) -> list[tuple[str, float]]:
    """Return ``[(candidate_id, rank), ...]`` sorted by ``key`` descending.

    ``key`` must be ``"score_old"`` or ``"score_new"``.
    """
    if key not in ("score_old", "score_new"):
        raise ValueError(f"unknown rank key: {key!r} (expected score_old|score_new)")
    scored_list = list(scored)
    pairs = [(s.candidate_id, getattr(s, key)) for s in scored_list]
    pairs_sorted = sorted(pairs, key=lambda x: x[1], reverse=True)
    return _competition_ranks(pairs_sorted)


def compute_ranked(scored: Iterable[ScoredCandidate]) -> list[RankedCandidate]:
    """Build the full RankedCandidate list with both ranks and delta_rank.

    Output is sorted by ``rank_new`` ascending (best new rank first).
    """
    scored_list = list(scored)
    by_id = {s.candidate_id: s for s in scored_list}

    old_ranks = dict(rank_candidates(scored_list, key="score_old"))
    new_ranks = dict(rank_candidates(scored_list, key="score_new"))

    rows: list[RankedCandidate] = []
    for cid, s in by_id.items():
        r_old = old_ranks[cid]
        r_new = new_ranks[cid]
        rows.append(
            RankedCandidate(
                candidate_id=cid,
                score_old=s.score_old,
                score_new=s.score_new,
                rank_old=r_old,
                rank_new=r_new,
                delta_rank=r_old - r_new,
            )
        )
    rows.sort(key=lambda r: (r.rank_new, r.candidate_id))
    return rows


def spearman(
    ranks_old: Mapping[str, float],
    ranks_new: Mapping[str, float],
) -> float:
    """Compute Spearman rank correlation (Pearson over ranks).

    Returns NaN when fewer than two candidates are present -- a single
    ranking is not informative.
    """
    common = sorted(set(ranks_old) & set(ranks_new))
    if len(common) < 2:
        return float("nan")
    a = [ranks_old[k] for k in common]
    b = [ranks_new[k] for k in common]
    mean_a = statistics.fmean(a)
    mean_b = statistics.fmean(b)
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    den_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    den_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    if den_a == 0 or den_b == 0:
        return float("nan")
    return num / (den_a * den_b)


def top_n_moves(
    ranks_old: Mapping[str, float],
    ranks_new: Mapping[str, float],
    n: int,
) -> tuple[int, int]:
    """Count candidates that entered or exited the top-N.

    Returns ``(moves_in, moves_out)`` -- items present in the new top-N
    but not the old top-N, and vice versa.
    """
    top_old = {k for k, r in ranks_old.items() if r <= n}
    top_new = {k for k, r in ranks_new.items() if r <= n}
    moves_in = len(top_new - top_old)
    moves_out = len(top_old - top_new)
    return moves_in, moves_out


def render_markdown(
    ranked: list[RankedCandidate],
    limit: int,
    spearman_rho: float,
    moves_in: int,
    moves_out: int,
    weights: Mapping[str, float],
    today: date | None = None,
) -> str:
    """Render the markdown report body."""
    iso_date = (today or date.today()).isoformat()
    lines: list[str] = []
    lines.append(f"# Priority diff report -- {iso_date}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Candidates: {len(ranked)}")
    lines.append(f"- Moves into top-{limit}: {moves_in}")
    lines.append(f"- Moves out of top-{limit}: {moves_out}")
    if math.isnan(spearman_rho):
        lines.append("- Spearman rank correlation: n/a (<2 candidates)")
    else:
        lines.append(f"- Spearman rank correlation: {spearman_rho:.4f}")
    lines.append(
        "- Weights (ontology / atf / citation): "
        f"{weights.get('ontology', 0):.2f} / "
        f"{weights.get('atf', 0):.2f} / "
        f"{weights.get('citation', 0):.2f}"
    )
    lines.append("")
    lines.append(f"## Top-{limit} delta")
    lines.append("")
    lines.append("| rank_old | rank_new | candidate_id | score_old | score_new | delta_rank |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    body = ranked[:limit]
    for r in body:
        lines.append(
            "| "
            + " | ".join(
                [
                    _fmt_rank(r.rank_old),
                    _fmt_rank(r.rank_new),
                    r.candidate_id,
                    _fmt_score(r.score_old),
                    _fmt_score(r.score_new),
                    _fmt_signed(r.delta_rank),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _fmt_rank(rank: float) -> str:
    if rank == int(rank):
        return str(int(rank))
    return f"{rank:.1f}"


def _fmt_score(score: float) -> str:
    return f"{score:.4f}"


def _fmt_signed(value: float) -> str:
    if value > 0:
        return f"+{value:.1f}"
    return f"{value:.1f}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="diff_priority_ranking.py",
        description=(
            "Re-rank extraction candidates under the new priority formula and "
            "emit a markdown diff report comparing the top-N rankings."
        ),
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to JSON snapshot of extraction candidates (see module docstring).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Top-N cutoff for the delta table and move accounting (default: 50).",
    )
    parser.add_argument(
        "--weights",
        type=str,
        default=None,
        help=(
            "JSON object overriding the default weights, "
            'e.g. \'{"ontology":0.4,"atf":0.3,"citation":0.3}\'. '
            "Falls back to priority.DEFAULT_WEIGHTS when omitted."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Path to write the markdown report (default: stdout).",
    )
    return parser


def _resolve_weights(raw: str | None) -> Mapping[str, float] | None:
    if raw is None:
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("--weights must be a JSON object")
    return {k: float(v) for k, v in parsed.items()}


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    weights = _resolve_weights(args.weights)

    rows = parse_candidates(args.input)
    scored = score_candidates(rows, weights=weights)
    ranked = compute_ranked(scored)

    old_rank_map = {r.candidate_id: r.rank_old for r in ranked}
    new_rank_map = {r.candidate_id: r.rank_new for r in ranked}
    rho = spearman(old_rank_map, new_rank_map)
    moves_in, moves_out = top_n_moves(old_rank_map, new_rank_map, args.limit)

    effective_weights: Mapping[str, float]
    if weights is None:
        from nfm_db.services.priority import DEFAULT_WEIGHTS
        effective_weights = DEFAULT_WEIGHTS
    else:
        effective_weights = weights

    md = render_markdown(
        ranked=ranked,
        limit=args.limit,
        spearman_rho=rho,
        moves_in=moves_in,
        moves_out=moves_out,
        weights=effective_weights,
    )

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
    else:
        # stdout output -- no trailing noise so the result is byte-stable.
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
