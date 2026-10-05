"""Unit tests for check_prereg.py (NFM-5312 Phase 1 — NDE [PREREG-GATE-RECONFIRMED]).

Offline, monkeypatched — no network, no Paperclip access. Covers the surfaces
the NDE review bound for Phase 1:

- signal taxonomy (strong / weak, ≥2-weak default threshold, ``--strict``)
- block parser: fenced + Python-comment lexers, full / short-form / minimal
  variants, invalid-block rules (missing or empty required key)
- the three outcome branches + the recorded hard-fail boundary
  (``[CONFIRMATORY-RUN]`` with no parseable block)
- Q2 sharpening: strong signal + only an EXPLORATORY minimal block ⇒ WARN
  with automatic NDE routing
- ``--verify`` against a mocked carrier thread (approved / rejected /
  config-error / not applicable to unlabeled artifacts)
- scan-surface path filter exercised against the surfaces that actually exist
  today (ml/**, apps/api/tests/**, docs/analysis/**, docs/reports/**) per NDE
  factual correction #2 — experiments/** stays a reserved path only.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import check_prereg as cp  # noqa: E402

FULL_BLOCK = textwrap.dedent(
    """\
    ```prereg
    carrier: NFM-5305
    label: CONFIRMATORY
    objectives: max rho_U; max T_stable
    constraints: rho_U >= 16.0 g/cm3; T_stable >= 500C
    algorithm: NSGA-II (pymoo); population=200; generations=100
    seed: 42
    convergence: hypervolume delta < 1e-3 over final 10 generations
    acceptance: >= 10 non-dominated solutions
    ```
    """
)

ML_REL = "apps/api/src/nfm_db/ml/promotion_run.py"
ANALYSIS_REL = "docs/analysis/2026-10-05-front.md"
REPORTS_REL = "docs/reports/2026-10-05-run.md"
TESTS_REL = "apps/api/tests/test_promotion.py"


def write(root: Path, rel: str, text: str) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return rel


# ---------------------------------------------------------------------------
# Block parser — fenced lexer
# ---------------------------------------------------------------------------


def test_fenced_full_block_valid():
    blocks = cp.parse_blocks(FULL_BLOCK)
    assert [b.variant for b in blocks] == [cp.VARIANT_FULL]
    assert blocks[0].carrier == "NFM-5305"
    assert blocks[0].label == "CONFIRMATORY"


def test_fenced_block_missing_required_key_is_invalid():
    text = FULL_BLOCK.replace("seed: 42\n", "")
    assert cp.parse_blocks(text)[0].variant == cp.VARIANT_INVALID


def test_fenced_block_empty_required_value_is_invalid():
    text = FULL_BLOCK.replace("seed: 42", "seed:")
    assert cp.parse_blocks(text)[0].variant == cp.VARIANT_INVALID


def test_short_form_block_with_only_ref_key():
    text = "```prereg\nref: NFM-5305\n```\n"
    block = cp.parse_blocks(text)[0]
    assert block.variant == cp.VARIANT_SHORT
    assert block.carrier == "NFM-5305"


def test_minimal_block_exactly_two_keys():
    text = "```prereg\nlabel: EXPLORATORY\ncarrier: NFM-5312\n```\n"
    block = cp.parse_blocks(text)[0]
    assert block.variant == cp.VARIANT_MINIMAL
    assert block.carrier == "NFM-5312"


def test_minimal_block_with_extra_key_is_not_minimal():
    text = "```prereg\nlabel: EXPLORATORY\ncarrier: NFM-5312\nseed: 42\n```\n"
    # three keys ⇒ neither minimal (exactly two) nor full (missing five) ⇒ invalid
    assert cp.parse_blocks(text)[0].variant == cp.VARIANT_INVALID


def test_minimal_block_requires_exploratory_label():
    text = "```prereg\nlabel: CONFIRMATORY\ncarrier: NFM-5312\n```\n"
    assert cp.parse_blocks(text)[0].variant == cp.VARIANT_INVALID


def test_fenced_block_indented_in_docstring_is_lexed():
    text = textwrap.dedent(
        '''\
        def run():
            """Promotion run.

            ```prereg
            carrier: NFM-5305
            label: CONFIRMATORY
            objectives: max rho_U
            constraints: rho_U >= 16.0
            algorithm: NSGA-II
            seed: 7
            convergence: GD < 0.01
            acceptance: front >= 10
            ```
            """
        '''
    )
    assert cp.parse_blocks(text)[0].variant == cp.VARIANT_FULL


# ---------------------------------------------------------------------------
# Block parser — Python comment lexer
# ---------------------------------------------------------------------------


COMMENT_FULL = textwrap.dedent(
    """\
    # prereg
    # carrier: NFM-5305
    # label: CONFIRMATORY
    # objectives: max rho_U
    # constraints: rho_U >= 16.0
    # algorithm: NSGA-II
    # seed: 42
    # convergence: GD < 0.01
    # acceptance: front >= 10
    """
)


def test_comment_block_full():
    block = cp.parse_blocks(COMMENT_FULL)[0]
    assert block.variant == cp.VARIANT_FULL
    assert block.carrier == "NFM-5305"


def test_comment_block_ends_at_free_form_comment():
    text = COMMENT_FULL + "# free-form commentary after the block\n"
    assert cp.parse_blocks(text)[0].variant == cp.VARIANT_FULL


def test_comment_block_header_without_keys_is_invalid():
    assert cp.parse_blocks("# prereg\n# just prose, no keys\n")[0].variant == cp.VARIANT_INVALID


def test_comment_block_minimal_form():
    text = "# prereg\n# label: exploratory\n# carrier: NFM-5312\n"
    block = cp.parse_blocks(text)[0]
    assert block.variant == cp.VARIANT_MINIMAL
    assert block.label == "EXPLORATORY"


def test_best_block_prefers_full_over_minimal():
    text = COMMENT_FULL + "\n```prereg\nlabel: EXPLORATORY\ncarrier: NFM-5312\n```\n"
    assert cp.best_block(cp.parse_blocks(text)).variant == cp.VARIANT_FULL


# ---------------------------------------------------------------------------
# Signal taxonomy
# ---------------------------------------------------------------------------


def test_strong_signals_fire_alone():
    for text in ("[CONFIRMATORY] run", "[CONFIRMATORY-RUN] v3.3", "this is a promotion run", "dispatch-candidate"):
        signals = cp.classify_signals(text)
        assert signals.strong, text
        assert signals.triggered(cp.DEFAULT_WEAK_THRESHOLD)


def test_proposal_reproduction_claim_needs_marker_pair():
    assert "proposal-reproduction" in cp.classify_signals("申报书 最优成分 复现").strong
    assert "proposal-reproduction" in cp.classify_signals("reproduce 申报书 composition").strong
    assert not cp.classify_signals("申报书 background section").strong


def test_weak_signals():
    assert "pinned-seed" in cp.classify_signals("config with seed: 42").weak
    assert "pareto-front-dump" in cp.classify_signals("final Pareto front table").weak
    assert "convergence-metrics" in cp.classify_signals("generational distance per gen").weak


def test_pop_gen_signal_needs_both_parameters():
    assert not cp.classify_signals("population=200 only").weak
    assert "pop-gen-200x100" in cp.classify_signals("population=200\ngenerations=100").weak


def test_single_weak_does_not_trigger_by_default():
    signals = cp.classify_signals("seed: 42")
    assert not signals.triggered(cp.DEFAULT_WEAK_THRESHOLD)
    assert signals.triggered(cp.STRICT_WEAK_THRESHOLD)


# ---------------------------------------------------------------------------
# Outcome branches (three + hard-fail boundary + Q2 sharpening)
# ---------------------------------------------------------------------------


def test_no_signals_ok():
    finding = cp.evaluate_text(ML_REL, "import numpy\n", cp.DEFAULT_WEAK_THRESHOLD)
    assert finding.outcome == cp.OUTCOME_OK


def test_signals_without_block_warn():
    text = "seed: 42\nfinal Pareto front dump\n"
    finding = cp.evaluate_text(ML_REL, text, cp.DEFAULT_WEAK_THRESHOLD)
    assert finding.outcome == cp.OUTCOME_WARN
    assert "NDE sign-off" in finding.reason


def test_confirmatory_run_without_block_is_hard_fail():
    finding = cp.evaluate_text(ML_REL, "[CONFIRMATORY-RUN] v3.3 results\n", cp.DEFAULT_WEAK_THRESHOLD)
    assert finding.outcome == cp.OUTCOME_FAIL


def test_confirmatory_plain_label_without_block_only_warns():
    finding = cp.evaluate_text(ML_REL, "[CONFIRMATORY] v3.3 results\n", cp.DEFAULT_WEAK_THRESHOLD)
    assert finding.outcome == cp.OUTCOME_WARN


def test_signals_with_full_block_ok():
    finding = cp.evaluate_text(ML_REL, "[CONFIRMATORY-RUN]\n" + FULL_BLOCK, cp.DEFAULT_WEAK_THRESHOLD)
    assert finding.outcome == cp.OUTCOME_OK
    assert finding.confirmatory_labeled
    assert finding.carrier == "NFM-5305"


def test_strong_signal_with_only_minimal_block_warns_with_nde_routing():
    text = "[CONFIRMATORY] promotion run\n```prereg\nlabel: EXPLORATORY\ncarrier: NFM-5312\n```\n"
    finding = cp.evaluate_text(ML_REL, text, cp.DEFAULT_WEAK_THRESHOLD)
    assert finding.outcome == cp.OUTCOME_WARN
    assert "automatic NDE routing" in finding.reason


# ---------------------------------------------------------------------------
# Scan surface (NDE correction #2 — surfaces that exist today)
# ---------------------------------------------------------------------------


def test_scan_surface_filter():
    inside = {ML_REL: "seed: 42 pareto front", ANALYSIS_REL: "x", REPORTS_REL: "x", TESTS_REL: "x"}
    outside = {"apps/web/src/app/design/page.tsx": "x", "scripts/check_prereg.py": "seed: 42"}
    findings = cp.scan_texts({**inside, **outside})
    assert {f.path for f in findings} == set(inside)
    # untriggered in-surface files still count as scanned-and-pass
    assert len(findings) == 4


def test_experiments_is_a_reserved_surface_path():
    assert cp.in_scan_surface("experiments/nsga2_skeleton.py")


def test_collect_paths_expands_dirs_and_counts_skipped(tmp_path):
    write(tmp_path, ML_REL, "x")
    write(tmp_path, "apps/web/src/page.tsx", "x")
    files, skipped = cp.collect_paths(tmp_path, ["apps", "docs/handoffs/missing.md"])
    assert files == [ML_REL]
    assert skipped == 2


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------


def test_cli_requires_paths_or_base(capsys):
    assert cp.main([]) == 2
    assert cp.main(["--base", "HEAD", "some/path"]) == 2


def test_cli_warn_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cp, "REPO_ROOT", tmp_path)
    write(tmp_path, ML_REL, "seed: 42\npareto front\n")
    assert cp.main([ML_REL]) == 0
    assert "WARN" in capsys.readouterr().out


def test_cli_hard_fail_exits_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cp, "REPO_ROOT", tmp_path)
    write(tmp_path, ML_REL, "[CONFIRMATORY-RUN] no marker here\n")
    assert cp.main([ML_REL]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_cli_strict_downgrades_threshold(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cp, "REPO_ROOT", tmp_path)
    write(tmp_path, ANALYSIS_REL, "notes with seed: 7 pinned\n")
    assert cp.main([ANALYSIS_REL]) == 0
    assert "WARN" not in capsys.readouterr().out
    assert cp.main(["--strict", ANALYSIS_REL]) == 0
    assert "WARN" in capsys.readouterr().out


def test_cli_base_uses_git_diff_and_surface_filter(monkeypatch):
    class FakeCompleted:
        stdout = f"{ML_REL}\napps/web/src/page.tsx\n"

    monkeypatch.setattr(
        cp.subprocess, "run", lambda *a, **k: FakeCompleted()
    )
    assert cp.git_diff_paths("HEAD~1") == [ML_REL]


# ---------------------------------------------------------------------------
# --verify (mocked carrier thread)
# ---------------------------------------------------------------------------


LABELED_WITH_BLOCK = "[CONFIRMATORY-RUN] v3.3\n" + FULL_BLOCK


def test_verify_approved_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "REPO_ROOT", tmp_path)
    write(tmp_path, ML_REL, LABELED_WITH_BLOCK)
    monkeypatch.setattr(cp, "fetch_carrier_approval", lambda carrier, approver: True)
    assert cp.main(["--verify", ML_REL]) == 0


def test_verify_rejection_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cp, "REPO_ROOT", tmp_path)
    write(tmp_path, ML_REL, LABELED_WITH_BLOCK)
    monkeypatch.setattr(cp, "fetch_carrier_approval", lambda carrier, approver: False)
    assert cp.main(["--verify", ML_REL]) == 1
    assert cp.PREREG_APPROVED_MARK in capsys.readouterr().out


def test_verify_config_error_exits_two(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "REPO_ROOT", tmp_path)
    write(tmp_path, ML_REL, LABELED_WITH_BLOCK)

    def boom(carrier, approver):
        raise cp.ConfigError("no api key")

    monkeypatch.setattr(cp, "fetch_carrier_approval", boom)
    assert cp.main(["--verify", ML_REL]) == 2


def test_verify_ignores_unlabeled_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "REPO_ROOT", tmp_path)
    # weak signals + block, but no [CONFIRMATORY...] label ⇒ no verify call
    write(tmp_path, ML_REL, FULL_BLOCK)
    calls: list[str] = []
    monkeypatch.setattr(cp, "fetch_carrier_approval", lambda carrier, approver: calls.append(carrier) or True)
    assert cp.main(["--verify", ML_REL]) == 0
    assert calls == []


def test_verify_notfound_carrier_is_rejection_not_config_error(monkeypatch):
    from paperclip_issue_lookup import NotFound

    monkeypatch.setenv("PAPERCLIP_API_KEY", "k")
    monkeypatch.setenv("PAPERCLIP_API_URL", "http://paperclip.invalid")

    def fake_lookup(identifier, max_pages=5):
        return NotFound(identifier=identifier)

    monkeypatch.setattr(cp, "lookup_issue", fake_lookup, raising=False)
    # fetch_carrier_approval imports lookup_issue lazily from the module —
    # patch the source module's symbol too
    import paperclip_issue_lookup as pil

    monkeypatch.setattr(pil, "lookup_issue", fake_lookup)
    assert cp.fetch_carrier_approval("NFM-9999", None) is False


# ---------------------------------------------------------------------------
# Approval-marker authorship (calibrated against the live NFM-5305 thread:
# the [PREREG-APPROVED] marker was authored by the run owner relaying the
# NDE gate verdict, not by NDE's own agent identity)
# ---------------------------------------------------------------------------


def _comment(body: str, author_type: str = "agent", agent_id: str = "petrov-uuid") -> dict:
    return {"body": body, "authorType": author_type, "authorAgentId": agent_id}


def test_approved_marker_by_any_agent_satisfies_default_verify():
    comments = [_comment("results posted", agent_id="run-owner"), _comment(f"relayed {cp.PREREG_APPROVED_MARK} verdict")]
    assert cp.has_approved_comment(comments, None) is True


def test_approved_marker_with_strict_approver_rejects_relayed_marker():
    comments = [_comment(f"{cp.PREREG_APPROVED_MARK} relayed by run owner", agent_id="run-owner")]
    assert cp.has_approved_comment(comments, cp.NDE_AGENT_ID) is False
    assert cp.has_approved_comment(
        [*comments, _comment(f"{cp.PREREG_APPROVED_MARK} direct", agent_id=cp.NDE_AGENT_ID)],
        cp.NDE_AGENT_ID,
    ) is True


def test_approved_marker_by_human_author_does_not_satisfy_verify():
    comments = [_comment(f"{cp.PREREG_APPROVED_MARK} from a user", author_type="user", agent_id=None)]
    assert cp.has_approved_comment(comments, None) is False


def test_no_marker_no_approval():
    assert cp.has_approved_comment([_comment("ordinary comment")], None) is False


if __name__ == "__main__":
    pytest.main([__file__])
