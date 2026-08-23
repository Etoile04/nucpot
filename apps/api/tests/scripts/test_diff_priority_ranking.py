"""Tests for apps/api/scripts/diff_priority_ranking.py (NFM-3578).

Covers:
- Ranking arithmetic: sort, delta_rank computation
- Spearman rank correlation: identity, reverse, partial
- Top-N move accounting: moves into / out of top-N
- Markdown rendering: column order, summary line
- CLI smoke test: --limit / --weights / --out
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# Make the script importable as a module so we can unit-test its library surface.
_SCRIPT_DIR = (
    Path(__file__).resolve().parent.parent.parent / "scripts"
)
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import diff_priority_ranking as dpr  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tiny_candidates() -> list[dict[str, Any]]:
    """Three candidates with known old/new scores for deterministic tests."""
    return [
        {
            "candidate_id": "cand-A",
            "score_old": 0.10,
            "signals": {"ontology": 1.0, "atf": 1.0, "citation": 1.0},
        },
        {
            "candidate_id": "cand-B",
            "score_old": 0.30,
            "signals": {"ontology": 0.5, "atf": 0.5, "citation": 0.5},
        },
        {
            "candidate_id": "cand-C",
            "score_old": 0.20,
            "signals": {"ontology": 0.0, "atf": 0.0, "citation": 0.0},
        },
    ]


@pytest.fixture
def swap_candidates() -> list[dict[str, Any]]:
    """Two candidates: old rank [1,2], new rank [2,1] — used for correlation."""
    return [
        {
            "candidate_id": "cand-X",
            "score_old": 0.90,
            "signals": {"ontology": 0.0, "atf": 0.0, "citation": 0.0},
        },
        {
            "candidate_id": "cand-Y",
            "score_old": 0.10,
            "signals": {"ontology": 1.0, "atf": 1.0, "citation": 1.0},
        },
    ]


# ---------------------------------------------------------------------------
# parse_candidates
# ---------------------------------------------------------------------------


class TestParseCandidates:
    def test_round_trip(self, tmp_path: Path) -> None:
        src = tmp_path / "cands.json"
        src.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "candidate_id": "x",
                            "score_old": 0.5,
                            "signals": {"ontology": 1.0, "atf": 1.0, "citation": 1.0},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        rows = dpr.parse_candidates(src)
        assert len(rows) == 1
        assert rows[0].candidate_id == "x"
        assert rows[0].score_old == 0.5

    def test_rejects_missing_signals(self, tmp_path: Path) -> None:
        src = tmp_path / "bad.json"
        # No 'signals' key at all — from_dict() should refuse.
        src.write_text(
            json.dumps(
                {
                    "candidates": [
                        {"candidate_id": "x", "score_old": 0.5}
                    ]
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="signals"):
            dpr.parse_candidates(src)

    def test_partial_signals_accepted(self, tmp_path: Path) -> None:
        """Missing sub-keys inside signals are tolerated; combine() ignores them."""
        src = tmp_path / "partial.json"
        src.write_text(
            json.dumps(
                {
                    "candidates": [
                        {"candidate_id": "x", "score_old": 0.5, "signals": {"ontology": 1.0}}
                    ]
                }
            ),
            encoding="utf-8",
        )
        rows = dpr.parse_candidates(src)
        assert len(rows) == 1
        assert rows[0].signals == {"ontology": 1.0}

    def test_rejects_missing_candidates_key(self, tmp_path: Path) -> None:
        src = tmp_path / "bad.json"
        src.write_text(json.dumps({"rows": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="candidates"):
            dpr.parse_candidates(src)


# ---------------------------------------------------------------------------
# score_candidates
# ---------------------------------------------------------------------------


class TestScoreCandidates:
    def test_default_weights_match_priority_combine(
        self, tiny_candidates: list[dict[str, Any]]
    ) -> None:
        from nfm_db.services.priority import combine

        rows = [dpr.CandidateRecord.from_dict(c) for c in tiny_candidates]
        scored = dpr.score_candidates(rows, weights=None)
        # cand-A signals are all 1.0 → score is the sum of default weights = 1.0
        cand_a = next(s for s in scored if s.candidate_id == "cand-A")
        assert math.isclose(cand_a.score_new, 1.0, rel_tol=1e-9)
        # Must equal priority.combine({...}, None) for each row
        for s in scored:
            assert math.isclose(
                s.score_new,
                combine(
                    {
                        "ontology": s.signals["ontology"],
                        "atf": s.signals["atf"],
                        "citation": s.signals["citation"],
                    }
                ),
                rel_tol=1e-9,
            )

    def test_custom_weights_respected(self) -> None:
        rows = [
            dpr.CandidateRecord(
                candidate_id="x",
                score_old=0.0,
                signals={"ontology": 1.0, "atf": 0.0, "citation": 0.0},
            )
        ]
        custom = {"ontology": 1.0, "atf": 0.0, "citation": 0.0}
        scored = dpr.score_candidates(rows, weights=custom)
        assert scored[0].score_new == 1.0


# ---------------------------------------------------------------------------
# rank_candidates
# ---------------------------------------------------------------------------


class TestRankCandidates:
    def test_descending_score_order(self, tiny_candidates: list[dict[str, Any]]) -> None:
        rows = [dpr.CandidateRecord.from_dict(c) for c in tiny_candidates]
        scored = dpr.score_candidates(rows, weights=None)
        ranked_new = dpr.rank_candidates(scored, key="score_new")
        # Returns list of (candidate_id, rank) tuples in sort order.
        ids = [cid for cid, _ in ranked_new]
        ranks = [rank for _, rank in ranked_new]
        assert ids == ["cand-A", "cand-B", "cand-C"]
        assert ranks == [1, 2, 3]

    def test_ties_get_average_rank(self) -> None:
        rows = [
            dpr.CandidateRecord(
                candidate_id=f"x{i}",
                score_old=0.0,
                signals={"ontology": 1.0, "atf": 0.0, "citation": 0.0},
            )
            for i in range(4)
        ]
        scored = dpr.score_candidates(rows, weights=None)
        ranked = dpr.rank_candidates(scored, key="score_new")
        # All 4 tied at the top — average rank of (1,2,3,4) is 2.5.
        ranks = [rank for _, rank in ranked]
        assert ranks == [2.5, 2.5, 2.5, 2.5]

    def test_old_score_order_independent(self, tiny_candidates: list[dict[str, Any]]) -> None:
        rows = [dpr.CandidateRecord.from_dict(c) for c in tiny_candidates]
        scored = dpr.score_candidates(rows, weights=None)
        ranked_old = dpr.rank_candidates(scored, key="score_old")
        # Old: cand-B(0.30) > cand-C(0.20) > cand-A(0.10)
        ids = [cid for cid, _ in ranked_old]
        assert ids == ["cand-B", "cand-C", "cand-A"]


# ---------------------------------------------------------------------------
# spearman
# ---------------------------------------------------------------------------


class TestSpearman:
    def test_bounds(self, tiny_candidates: list[dict[str, Any]]) -> None:
        rows = [dpr.CandidateRecord.from_dict(c) for c in tiny_candidates]
        scored = dpr.score_candidates(rows, weights=None)
        ranked_old = dpr.rank_candidates(scored, key="score_old")
        ranked_new = dpr.rank_candidates(scored, key="score_new")
        r_old = {cid: rank for cid, rank in ranked_old}
        r_new = {cid: rank for cid, rank in ranked_new}
        rho = dpr.spearman(r_old, r_new)
        assert -1.0 <= rho <= 1.0

    def test_identity_is_one(self) -> None:
        ranks = {"a": 1, "b": 2, "c": 3}
        assert math.isclose(dpr.spearman(ranks, ranks), 1.0, rel_tol=1e-9)

    def test_reverse_is_minus_one(self) -> None:
        ranks_old = {"a": 1, "b": 2, "c": 3}
        ranks_new = {"a": 3, "b": 2, "c": 1}
        assert math.isclose(dpr.spearman(ranks_old, ranks_new), -1.0, rel_tol=1e-9)

    def test_swap_pair_near_minus_one(
        self, swap_candidates: list[dict[str, Any]]
    ) -> None:
        rows = [dpr.CandidateRecord.from_dict(c) for c in swap_candidates]
        scored = dpr.score_candidates(rows, weights=None)
        ranked_old = dpr.rank_candidates(scored, key="score_old")
        ranked_new = dpr.rank_candidates(scored, key="score_new")
        r_old = {cid: rank for cid, rank in ranked_old}
        r_new = {cid: rank for cid, rank in ranked_new}
        rho = dpr.spearman(r_old, r_new)
        assert math.isclose(rho, -1.0, rel_tol=1e-9)

    def test_empty_returns_nan(self) -> None:
        assert math.isnan(dpr.spearman({}, {}))

    def test_single_item_returns_nan(self) -> None:
        assert math.isnan(dpr.spearman({"a": 1}, {"a": 1}))


# ---------------------------------------------------------------------------
# top_n_moves
# ---------------------------------------------------------------------------


class TestTopNMoves:
    def test_swap_pair_yields_one_in_one_out(self) -> None:
        ranks_old = {"a": 1, "b": 2, "c": 3}
        ranks_new = {"a": 3, "b": 2, "c": 1}
        into, out_of = dpr.top_n_moves(ranks_old, ranks_new, n=2)
        # top-2 old = {a,b}; new top-2 = {b,c}; a left (out=1), c entered (in=1)
        assert (into, out_of) == (1, 1)

    def test_stable_top_n_yields_zero_moves(self) -> None:
        ranks = {"a": 1, "b": 2, "c": 3, "d": 4}
        into, out_of = dpr.top_n_moves(ranks, ranks, n=2)
        assert (into, out_of) == (0, 0)


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def test_contains_required_columns(self) -> None:
        ranked = [
            dpr.RankedCandidate(
                candidate_id="c1",
                score_old=0.5,
                score_new=0.7,
                rank_old=2,
                rank_new=1,
                delta_rank=-1,
            ),
        ]
        md = dpr.render_markdown(
            ranked=ranked,
            limit=10,
            spearman_rho=0.42,
            moves_in=1,
            moves_out=2,
            weights={"ontology": 0.4, "atf": 0.3, "citation": 0.3},
        )
        for col in (
            "rank_old",
            "rank_new",
            "candidate_id",
            "score_old",
            "score_new",
            "delta_rank",
        ):
            assert col in md, f"missing column header: {col}"
        # Summary line present (case-insensitive — the renderer capitalizes).
        md_lower = md.lower()
        assert "moves into" in md_lower
        assert "moves out" in md_lower
        assert "Spearman".lower() in md_lower

    def test_limit_truncates_table(self) -> None:
        ranked = [
            dpr.RankedCandidate(
                candidate_id=f"c{i}",
                score_old=0.0,
                score_new=1.0 / (i + 1),
                rank_old=i + 1,
                rank_new=i + 1,
                delta_rank=0,
            )
            for i in range(20)
        ]
        md = dpr.render_markdown(
            ranked=ranked,
            limit=5,
            spearman_rho=1.0,
            moves_in=0,
            moves_out=0,
            weights={"ontology": 0.4, "atf": 0.3, "citation": 0.3},
        )
        # Body rows contain " | c" (the candidate_id column) AND start with
        # "|<digit>" (i.e. the row, not the header "rank_old ..."). limit=5 yields 5.
        body_rows = [
            line
            for line in md.splitlines()
            if line.startswith("|") and " | c" in line and line.split("|")[1].strip().isdigit()
        ]
        assert len(body_rows) == 5


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_end_to_end(self, tmp_path: Path) -> None:
        input_path = tmp_path / "cands.json"
        out_path = tmp_path / "report.md"
        input_path.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "candidate_id": "cand-A",
                            "score_old": 0.10,
                            "signals": {"ontology": 1.0, "atf": 1.0, "citation": 1.0},
                        },
                        {
                            "candidate_id": "cand-B",
                            "score_old": 0.30,
                            "signals": {"ontology": 0.0, "atf": 0.0, "citation": 0.0},
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        repo_root = Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo_root / "src")
        result = subprocess.run(
            [
                sys.executable,
                str(repo_root / "scripts" / "diff_priority_ranking.py"),
                "--input",
                str(input_path),
                "--limit",
                "5",
                "--out",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        text = out_path.read_text(encoding="utf-8")
        assert "rank_old" in text
        assert "rank_new" in text
        assert "Spearman" in text

    def test_cli_idempotent(self, tmp_path: Path) -> None:
        """Re-running with identical args produces identical bytes."""
        input_path = tmp_path / "cands.json"
        input_path.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "candidate_id": "x",
                            "score_old": 0.5,
                            "signals": {"ontology": 1.0, "atf": 1.0, "citation": 1.0},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        repo_root = Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo_root / "src")
        cmd = [
            sys.executable,
            str(repo_root / "scripts" / "diff_priority_ranking.py"),
            "--input",
            str(input_path),
            "--limit",
            "5",
        ]
        r1 = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
        r2 = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
        assert r1.stdout == r2.stdout
