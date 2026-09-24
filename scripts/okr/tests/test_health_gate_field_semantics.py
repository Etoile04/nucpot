"""Guards on the frozen `health_gate_first_poll_passed` schema field (NFM-5208).

NFM-5208 AC2 considered renaming the §3.1 schema field after PR #1416's
bounded retry drifted its prod meaning from "literal first attempt" to
"passed within budget". Rename was REJECTED (ADR-KR3 amendment C6.4): the
schema is frozen by spec §3.1 and strictly enforced by the collector, the
JSONL is append-only so a rename buys dual-name parsing forever, and the
name is still literally accurate for staging — the majority producer.

The contract these tests pin:

* the field name is IDENTICAL in all three schema definitions (bash
  emitter, python emitter, collector) — a rename that does not supersede
  ADR-KR3 C6.4 in the same change cannot land silently;
* the per-environment semantics are documented at every producer surface
  that computes or passes the value, because the name alone no longer
  carries them.

Static assertions, same rationale as test_prod_deploy_event_job.py
(NFM-2119): a schema rename is invisible until a collector quarantines a
real event or a reader KeyError's on real data, and by then the production
measurement pipeline has already lost events.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BASH_LIB = REPO_ROOT / "scripts" / "lib" / "deploy_event.sh"
EMITTER = REPO_ROOT / "scripts" / "lib" / "deploy_event_emitter.py"
COLLECTOR = REPO_ROOT / "scripts" / "lib" / "collect_prod_events.py"
STAGING = REPO_ROOT / "scripts" / "staging_deploy.sh"
DEPLOY_PROD = REPO_ROOT / "scripts" / "deploy_prod.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production-deployment.yml"
ADR = REPO_ROOT / "docs" / "architecture" / "ADR-KR3-deploy-events.md"

FIELD = "health_gate_first_poll_passed"
FLAG = "--health-gate-first-poll-passed"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# The field name is frozen identically everywhere §3.1 is defined
# --------------------------------------------------------------------------


def test_bash_emitter_json_key_is_frozen() -> None:
    text = BASH_LIB.read_text(encoding="utf-8")
    assert f'"{FIELD}":%s' in text, (
        f"the bash emitter no longer writes {FIELD!r} verbatim. The §3.1 "
        "schema is frozen (ADR-KR3 C6.4): renaming requires superseding that "
        "amendment AND updating the collector + all readers in the same change."
    )
    assert FLAG in text, "the bash emitter CLI flag must stay in sync with the JSON key"


def test_python_emitter_schema_field_is_frozen() -> None:
    emitter = _load_module(EMITTER, "nfm5208_deploy_event_emitter")
    assert FIELD in emitter.SCHEMA_FIELDS
    assert FIELD in emitter.build_event(
        environment="production",
        triggered_by="t",
        commit_sha="c",
        first_pass_success=True,
        health_gate_first_poll_passed=True,
        rollback_triggered=False,
        skip_flag_used=False,
        duration_ms=1,
    )


def test_collector_schema_field_is_frozen() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
    try:
        collector = sys.modules.get("collect_prod_events") or _load_module(
            COLLECTOR, "collect_prod_events"
        )
        assert FIELD in collector.SCHEMA_FIELDS
    finally:
        sys.path.pop(0)


def test_call_sites_still_pass_the_flag() -> None:
    # Both producers' callers (staging trap, prod fragment step, hosted
    # producer job) must keep passing the flag the schema defines.
    for path, why in ((STAGING, "staging deploy trap"), (WORKFLOW, "prod workflow")):
        assert FLAG in path.read_text(encoding="utf-8"), f"{why} no longer passes {FLAG}"


# --------------------------------------------------------------------------
# Per-environment semantics are documented where the value is produced
# --------------------------------------------------------------------------


def test_bash_lib_documents_per_environment_semantics() -> None:
    text = BASH_LIB.read_text(encoding="utf-8")
    assert "WITHIN BUDGET" in text and "first probe" in text, (
        "deploy_event.sh's usage header must carry the C6.4 per-environment "
        "semantics — the field name alone no longer tells a reader what "
        "'true' means"
    )


def test_emitter_help_documents_per_environment_semantics() -> None:
    text = EMITTER.read_text(encoding="utf-8")
    assert "C6.4" in text and "within" in text and "bounded retry budget" in text


def test_workflow_fragment_documents_within_budget_semantics() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    # The fragment step computes FIRST_POLL from the marker; its comment must
    # state the marker means within-budget, not literal first attempt.
    assert "WITHIN" in text and "BUDGET" in text and "NFM-5208" in text


def test_staging_keeps_literal_first_probe_semantics() -> None:
    text = STAGING.read_text(encoding="utf-8")
    assert 'poll_count" -eq 0' in text or "poll_count -eq 0" in text, (
        "staging's C3 probe-counter derivation (first check_health_once "
        "returned 0) must stay literal — it is the environment the frozen "
        "field name still describes exactly"
    )


def test_adr_c64_amendment_records_the_decision() -> None:
    text = ADR.read_text(encoding="utf-8")
    assert "Amendment C6.4" in text, (
        "ADR-KR3 must carry the C6.4 amendment recording why the rename was "
        "rejected and what the field means per environment"
    )
    assert "passed within budget" in text.lower()
    assert "NFM-5208" in text


def test_deploy_prod_gate_comment_matches_marker_semantics() -> None:
    text = DEPLOY_PROD.read_text(encoding="utf-8")
    assert "WITHIN BUDGET" in text and "C6.4" in text, (
        "deploy_prod.sh's gate comment must tie the marker to the C6.4 "
        "within-budget semantics"
    )
