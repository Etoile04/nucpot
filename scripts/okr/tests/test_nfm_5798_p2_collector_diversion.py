"""GitHub-hosted diversion guards for the two collector lanes.

NFM-5798 P2 Leg B (operator card ``3e23aa54``, option ``p2-legB-collectors``;
staged-legs plan and design review in ``8fecf232``; Leg A pattern ratified in
``229a46a7`` + canary gate ``6d5eec2f``). The collectors' only failing step
was ever their checkout: the hand-rolled bounded git fetch of their payload
scripts through the host's flaky egress lane (NFM-5548 → 5556 → 5796 → 5798;
the 11-04 runner-outage window starved this lane 12h+ with four runs stacked
behind an offline host). The collector work itself is host-bound — the
master/processed JSONL ledgers, sync state, the loopback SSH hop, and the
``gh`` artifact queries all live on the Mac Studio — so Leg B moves the
FETCH, not the job, exactly like Leg A:

- ``prod-deploy-event-collector-orchestrator.yml`` (hosted, ubuntu-latest) —
  owns ALL three original triggers moved verbatim: the ``workflow_run``
  fast-path off ``Production Deployment`` (types ``[completed]``), the
  ``*/15`` cron backstop, and the ``workflow_dispatch`` door with its
  ``dry_run`` input. Checks out at the fire's SHA on GitHub's healthy
  egress, stages ``scripts/okr/prod_event_collector.py`` plus a sha256
  manifest as the ``prod-collector-payload`` artifact, then
  ``repository_dispatch``es the executor with (run-id, sha, dry_run)
  provenance.
- ``prod-deploy-event-collector-executor.yml`` (self-hosted) — downloads
  the artifact over the runner's own job channel, verifies the manifest,
  and runs the collector with the host ledger paths and secrets. ZERO git
  operations by construction; ``cancel-in-progress: false`` is load-bearing
  (a cancelled run could tear a host-local ledger append mid-flight — the
  11-04 queue-timeout evictions 41866246466/41866273808 prove queued runs
  do get cancelled externally).
- ``collect-prod-deploy-events-orchestrator.yml`` (hosted) — owns the
  ``*/5`` schedule and ``workflow_dispatch`` verbatim; stages
  ``scripts/lib/collect_prod_run.sh`` + ``scripts/lib/collect_prod_events.py``
  PRESERVING the ``scripts/lib/`` layout (the bash glue resolves its repo
  root two directories above itself), manifest-gated, as the
  ``collect-events-payload`` artifact.
- ``collect-prod-deploy-events-executor.yml`` (self-hosted) — the executor
  half, dispatch-only, zero git, serialized, with the
  ``NFMD_DEPLOY_EVENTS_PATH``/``NFMD_DEPLOY_EVENTS_PROCESSED_PATH`` repo
  vars flowing through unchanged.

The host-local ledger stays host-side: nothing in the orchestrators may
reference ``~/.nfmd`` paths, the sync-state/master JSONL, the loopback SSH
target, or the deploy-events repo vars — only the executors touch host
resources (design ``4f469b4f``).

The old flat collector workflows are deleted: their triggers moved to the
orchestrators, and no other workflow consumes their names via
``workflow_run`` (census at implementation time), so deletion breaks no
trigger wiring. With them goes the last bounded-fetch carrier in the repo —
the global "carrier set is empty" invariant is pinned by
``test_nfm_5798_p2_watchdog_diversion.py``; P1's widened bounds remain
defense-in-depth in git history (revert = restore), and the retired
NFM-5796/5548/5556 contract modules leave with their subjects.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / ".github/workflows"

# Helpers below mirror test_nfm_5798_p2_watchdog_diversion.py verbatim; this
# module stays import-standalone because sibling imports are invocation-mode
# dependent (bare `pytest` from the repo root vs explicit file args — the
# Leg A module carries the same helpers for the same reason).

FETCH_NEEDLE = "fetch --no-tags --depth=1"
VERIFY_STEP_ID = "verify-payload"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return job.get("steps") or []


def _run_bodies(steps: list[dict[str, Any]]) -> list[str]:
    return [step.get("run") or "" for step in steps]


_ECHO_ARGS = re.compile(r"\b(?:echo|printf)\s+(?:\"[^\"]*\"|'[^']*')+")


def _shell_commands(body: str) -> list[str]:
    """Logical shell commands of a run body: backslash continuations joined,
    full-line comments dropped, and the quoted argument spans of ``echo`` /
    ``printf`` erased (so a logged copy of a command — bare, in a subshell,
    or inside ``$( )`` — reads as nothing). Positive assertions run against
    these so a comment or an ``echo`` of a phrase can never satisfy them."""
    commands: list[str] = []
    pending = ""
    for raw in body.splitlines():
        stripped = raw.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            pending += (" " if pending else "") + stripped[:-1].rstrip()
            continue
        commands.append(f"{pending} {stripped}".strip() if pending else stripped)
        pending = ""
    if pending:
        commands.append(pending.strip())
    return [cmd for cmd in (_ECHO_ARGS.sub("", c) for c in commands) if cmd]


def _job_commands(doc: dict[str, Any]) -> list[str]:
    return [
        cmd
        for job in doc.get("jobs", {}).values()
        for body in _run_bodies(_steps(job))
        for cmd in _shell_commands(body)
    ]


def _is_manifest_checksum(cmd: str) -> bool:
    return bool(re.search(r"\bsha(256sum|sum)\b.*\s-c\s+MANIFEST\.sha256\b", cmd))


ORCH1 = WORKFLOW_DIR / "prod-deploy-event-collector-orchestrator.yml"
EXEC1 = WORKFLOW_DIR / "prod-deploy-event-collector-executor.yml"
ORCH2 = WORKFLOW_DIR / "collect-prod-deploy-events-orchestrator.yml"
EXEC2 = WORKFLOW_DIR / "collect-prod-deploy-events-executor.yml"
OLD1 = WORKFLOW_DIR / "prod-deploy-event-collector.yml"
OLD2 = WORKFLOW_DIR / "collect-prod-deploy-events.yml"

DEPLOY_WORKFLOW_NAME = "Production Deployment"
PROD_COLLECTOR_CRON = "*/15 * * * *"
COLLECT_EVENTS_CRON = "*/5 * * * *"
DISPATCH_TYPE_1 = "nfmd-prod-collector-exec"
DISPATCH_TYPE_2 = "nfmd-collect-events-exec"
ARTIFACT_1 = "prod-collector-payload"
ARTIFACT_2 = "collect-events-payload"
EXECUTOR_TIMEOUT_MINUTES = 18  # carried from the pre-split collector jobs (P1)

PAYLOAD_1 = ("scripts/okr/prod_event_collector.py",)
PAYLOAD_2 = (
    "scripts/lib/collect_prod_run.sh",
    "scripts/lib/collect_prod_events.py",
)

# Host-bound resources that must appear ONLY in executors, never in the
# hosted orchestrators (design 4f469b4f: the ledger is a host-local lane).
HOST_LEDGER_NEEDLES = (
    "~/.nfmd",
    "/Users/lwj04/.nfmd",
    "master-deploy-events",
    "prod-event-sync-state",
    "NFMD_DEPLOY_EVENTS_PATH",
    "NFMD_DEPLOY_EVENTS_PROCESSED_PATH",
    "lwj04@127.0.0.1",
)


def _triggers(doc: dict[str, Any]) -> dict[str, Any]:
    return doc.get("on") or doc.get(True) or {}


# ---------------------------------------------------------------------------
# The split exists; the old flat workflows are gone
# ---------------------------------------------------------------------------


def test_diversion_files_exist_and_old_workflows_deleted() -> None:
    for path in (ORCH1, EXEC1, ORCH2, EXEC2):
        assert path.is_file(), f"{path.name} missing — the Leg B split is incomplete"
    for path in (OLD1, OLD2):
        assert not path.exists(), (
            f"{path.name} still exists — its triggers moved to the "
            "orchestrator; leaving the self-hosted checkout in place would "
            "keep the blackhole lane alive"
        )


# ---------------------------------------------------------------------------
# Orchestrator triggers: moved verbatim (the workflow_run chain trap)
# ---------------------------------------------------------------------------


def test_orchestrator1_preserves_all_three_triggers() -> None:
    """RE trap 1: the ``workflow_run`` chain off ``Production Deployment`` is
    the seconds-after-deploy fast path. It must move to the orchestrator
    VERBATIM (name + ``[completed]`` types) or the chain silently no-ops and
    only the */15 cron backstop survives."""
    doc = _load_yaml(ORCH1)
    triggers = _triggers(doc)
    wf_run = triggers.get("workflow_run") or {}
    assert DEPLOY_WORKFLOW_NAME in (wf_run.get("workflows") or []), (
        "the workflow_run fast-path off Production Deployment must move to "
        "the orchestrator verbatim; dropping it silently retires the "
        "seconds-after-deploy pickup (the Leg A literal-if trap class)"
    )
    assert (wf_run.get("types") or []) == ["completed"]
    crons = [entry.get("cron") for entry in (triggers.get("schedule") or [])]
    assert PROD_COLLECTOR_CRON in crons, (
        "the */15 backstop cron must move verbatim to the orchestrator"
    )
    dispatch = triggers.get("workflow_dispatch") or {}
    dry_run = (dispatch.get("inputs") or {}).get("dry_run") or {}
    assert dry_run.get("type") == "boolean", (
        "the manual dry_run input must survive the move (validate-only fires)"
    )


def test_orchestrator2_preserves_schedule_and_dispatch() -> None:
    doc = _load_yaml(ORCH2)
    triggers = _triggers(doc)
    crons = [entry.get("cron") for entry in (triggers.get("schedule") or [])]
    assert COLLECT_EVENTS_CRON in crons, "the */5 schedule must move verbatim"
    assert "workflow_dispatch" in triggers, "the manual dispatch door must move verbatim"
    assert "workflow_run" not in triggers, (
        "collect-prod-deploy-events has never been chained via workflow_run; "
        "adding one here would re-wire the lane for no stated reason"
    )


def test_executors_are_dispatch_only() -> None:
    for exec_path, dispatch_type in ((EXEC1, DISPATCH_TYPE_1), (EXEC2, DISPATCH_TYPE_2)):
        doc = _load_yaml(exec_path)
        triggers = _triggers(doc)
        assert set(triggers) == {"repository_dispatch"}, (
            f"{exec_path.name} triggers must be repository_dispatch only, got "
            f"{sorted(triggers)}; a schedule trigger would re-open the "
            "literal-cron guard class and a direct workflow_dispatch would "
            "have no payload artifact"
        )
        types = triggers["repository_dispatch"].get("types") or []
        assert dispatch_type in types, f"{exec_path.name} must listen for {dispatch_type!r}"


def test_orchestrators_and_executors_use_matching_event_types() -> None:
    """The orchestrator's dispatch event_type and the executor's listening
    type are a hand-maintained pair — a silent rename on one side no-ops the
    whole chain."""
    for orch_path, _exec_path, dispatch_type in (
        (ORCH1, EXEC1, DISPATCH_TYPE_1),
        (ORCH2, EXEC2, DISPATCH_TYPE_2),
    ):
        orch_cmds = _job_commands(_load_yaml(orch_path))
        dispatch_cmds = [
            cmd for cmd in orch_cmds if re.search(r"\bgh\s+api\b", cmd) and "/dispatches" in cmd
        ]
        assert dispatch_cmds, f"{orch_path.name} must POST a repository dispatch"
        assert all(f"event_type={dispatch_type}" in cmd for cmd in dispatch_cmds), (
            f"{orch_path.name} dispatch event_type must be {dispatch_type!r}"
        )


# ---------------------------------------------------------------------------
# Placement, budgets, serialization
# ---------------------------------------------------------------------------


def test_orchestrators_are_hosted_and_free_of_bounded_fetch() -> None:
    for orch_path in (ORCH1, ORCH2):
        doc = _load_yaml(orch_path)
        jobs = doc.get("jobs", {})
        assert jobs, f"{orch_path.name} has no jobs"
        for job_key, job in jobs.items():
            assert job.get("runs-on") == "ubuntu-latest", (
                f"{orch_path.name} job {job_key} must run on the hosted lane; "
                "the whole point is fetching on GitHub's healthy egress"
            )
        text = orch_path.read_text(encoding="utf-8")
        assert FETCH_NEEDLE not in text, f"{orch_path.name} must use plain actions/checkout"
        used_checkout = any(
            (step.get("uses") or "").startswith("actions/checkout")
            for job in jobs.values()
            for step in _steps(job)
        )
        assert used_checkout, f"{orch_path.name} must check out via actions/checkout"


def test_executors_run_on_production_runner_with_p1_budget() -> None:
    for exec_path in (EXEC1, EXEC2):
        doc = _load_yaml(exec_path)
        jobs = doc.get("jobs", {})
        assert jobs, f"{exec_path.name} has no jobs"
        job = next(iter(jobs.values()))
        assert job.get("runs-on") == ["self-hosted", "production"], (
            f"{exec_path.name} must keep the pre-split collector's runner "
            "placement (the host-local ledger + loopback SSH hop live there)"
        )
        assert int(job.get("timeout-minutes", 0)) == EXECUTOR_TIMEOUT_MINUTES, (
            f"{exec_path.name} timeout-minutes must carry the P1 budget "
            f"({EXECUTOR_TIMEOUT_MINUTES})"
        )


def test_executors_serialize_and_never_cancel() -> None:
    """RE trap 2: the executors append host-local ledgers — a cancelled run
    tears an append mid-flight. The 11-04 queue-timeout evictions
    (41866246466 / 41866273808) prove queued runs DO get cancelled
    externally, so cancel-in-progress: false is load-bearing, inherited from
    the pre-split collector concurrency groups."""
    for exec_path in (EXEC1, EXEC2):
        concurrency = _load_yaml(exec_path).get("concurrency") or {}
        assert concurrency.get("group"), f"{exec_path.name} needs a concurrency group"
        assert concurrency.get("cancel-in-progress") is False, (
            f"{exec_path.name} cancel-in-progress must be false: cancelling a "
            "host-side ledger append mid-flight tears the run; queue instead"
        )


def test_executors_do_zero_git_operations() -> None:
    """The core invariant: no checkout action, no git subcommand anywhere in
    a run body — the executors run ONLY what the artifact carries, so the
    checkout blackhole class cannot fail these lanes by construction."""
    for exec_path in (EXEC1, EXEC2):
        doc = _load_yaml(exec_path)
        for job_key, job in doc.get("jobs", {}).items():
            for step in _steps(job):
                uses = step.get("uses") or ""
                assert not uses.startswith("actions/checkout"), (
                    f"{exec_path.name} step in {job_key} uses actions/checkout; "
                    "the executor must never touch git"
                )
                body = step.get("run") or ""
                assert not re.search(r"\bgit\b", body), (
                    f"{exec_path.name} step in {job_key} shells out to git:\n{body}"
                )


# ---------------------------------------------------------------------------
# Host-local resources stay host-side
# ---------------------------------------------------------------------------


def test_host_local_ledger_stays_out_of_orchestrators() -> None:
    """Design 4f469b4f: only the checkout moves off-host. The orchestrators
    must not reference the host ledger paths, sync state, loopback SSH
    target, or the deploy-events repo vars — a hosted runner has neither the
    file nor the key, and such a reference would mean ledger IO moved."""
    for orch_path in (ORCH1, ORCH2):
        text = orch_path.read_text(encoding="utf-8")
        for needle in HOST_LEDGER_NEEDLES:
            assert needle not in text, (
                f"{orch_path.name} references host-bound resource {needle!r}; "
                "the host-local ledger stays in the executor (NFM-5798 P2 "
                "Leg B design)"
            )


def test_executors_carry_the_host_resources() -> None:
    """The mirror pin: each executor must keep its pre-split job's host
    resources, so the diversion cannot silently strand the ledger lanes."""
    exec1_text = EXEC1.read_text(encoding="utf-8")
    for needle in (
        "master-deploy-events.jsonl",
        "prod-event-sync-state.json",
        "lwj04@127.0.0.1",
        "ALERT_WEBHOOK",
    ):
        assert needle in exec1_text, f"executor 1 lost host resource {needle!r}"
    exec2_text = EXEC2.read_text(encoding="utf-8")
    for needle in (
        "NFMD_DEPLOY_EVENTS_PATH",
        "NFMD_DEPLOY_EVENTS_PROCESSED_PATH",
        "collect_prod_run.sh",
    ):
        assert needle in exec2_text, f"executor 2 lost host resource {needle!r}"


# ---------------------------------------------------------------------------
# Artifact carry: staging, manifest, provenance dispatch
# ---------------------------------------------------------------------------


def test_orchestrators_stage_payload_with_manifest_and_upload() -> None:
    for orch_path, payload, artifact_name in (
        (ORCH1, PAYLOAD_1, ARTIFACT_1),
        (ORCH2, PAYLOAD_2, ARTIFACT_2),
    ):
        doc = _load_yaml(orch_path)
        commands = _job_commands(doc)
        for script in payload:
            assert (REPO_ROOT / script).is_file(), f"payload script {script} missing from the repo"
            assert any(re.search(r"\bcp\b", cmd) and script in cmd for cmd in commands), (
                f"{orch_path.name} staging step must copy {script} into the "
                "payload; the executor runs exactly what the orchestrator "
                "checked out"
            )
        assert any(
            re.search(r"\bsha256sum\b", cmd)
            and "MANIFEST.sha256" in cmd
            and all(Path(script).name in cmd for script in payload)
            for cmd in commands
        ), (
            f"{orch_path.name} payload must carry a sha256 manifest over all "
            "payload scripts so the executor can verify the artifact before "
            "executing it"
        )
        uploads = [
            step
            for job in doc.get("jobs", {}).values()
            for step in _steps(job)
            if (step.get("uses") or "").startswith("actions/upload-artifact")
        ]
        with_name = [step for step in uploads if step.get("with", {}).get("name") == artifact_name]
        assert with_name, (
            f"{orch_path.name} must upload the payload as artifact {artifact_name!r} "
            "(the executor pins that name)"
        )


def test_orchestrator2_staging_preserves_scripts_lib_layout() -> None:
    """collect_prod_run.sh resolves its repo root two directories above
    itself and calls ``$REPO_ROOT/scripts/lib/collect_prod_events.py`` — a
    FLAT payload would break that resolution and strand collector 2 with a
    missing-collector error on every fire."""
    doc = _load_yaml(ORCH2)
    commands = _job_commands(doc)
    for script in PAYLOAD_2:
        assert any(re.search(r"\bcp\b", cmd) and f"payload/{script}" in cmd for cmd in commands), (
            f"staging must preserve {script} under payload/ (repo-relative "
            "layout) — collect_prod_run.sh derives REPO_ROOT from its own "
            "location"
        )


def test_orchestrators_dispatch_with_run_and_sha_provenance() -> None:
    for orch_path in (ORCH1, ORCH2):
        doc = _load_yaml(orch_path)
        dispatch_cmds = [
            cmd
            for cmd in _job_commands(doc)
            if re.search(r"\bgh\s+api\b", cmd) and "/dispatches" in cmd
        ]
        assert dispatch_cmds, f"{orch_path.name} must POST a repository dispatch"
        assert all("client_payload[orchestrator_run_id]" in cmd for cmd in dispatch_cmds), (
            f"{orch_path.name}: the executor needs the orchestrator run-id to "
            "download the artifact cross-run"
        )
        assert all("client_payload[sha]" in cmd for cmd in dispatch_cmds), (
            f"{orch_path.name}: the dispatch must carry the checkout SHA for payload provenance"
        )
        permissions = doc.get("permissions") or {}
        assert permissions.get("contents") == "write", (
            f"{orch_path.name}: the dispatch POST requires contents: write (review 8fecf232 pt 3)"
        )


def test_orchestrator1_carries_dry_run_through_the_dispatch() -> None:
    doc = _load_yaml(ORCH1)
    dispatch_cmds = [
        cmd
        for cmd in _job_commands(doc)
        if re.search(r"\bgh\s+api\b", cmd) and "/dispatches" in cmd and "client_payload" in cmd
    ]
    assert dispatch_cmds, "orchestrator 1 must dispatch"
    assert all("client_payload[dry_run]" in cmd for cmd in dispatch_cmds), (
        "the manual dry_run input must ride the dispatch — a dry_run "
        "workflow_dispatch that appends to the master ledger would corrupt "
        "sync state"
    )


# ---------------------------------------------------------------------------
# Executor half: download, verify, then run gated on the manifest
# ---------------------------------------------------------------------------


def test_executors_download_and_verify_artifact_before_running() -> None:
    for exec_path, _payload, artifact_name in (
        (EXEC1, PAYLOAD_1, ARTIFACT_1),
        (EXEC2, PAYLOAD_2, ARTIFACT_2),
    ):
        doc = _load_yaml(exec_path)
        steps = [step for job in doc.get("jobs", {}).values() for step in _steps(job)]
        downloads = [
            step
            for step in steps
            if (step.get("uses") or "").startswith("actions/download-artifact")
        ]
        assert downloads, f"{exec_path.name} must download the orchestrator's payload artifact"
        with_block = downloads[0].get("with", {})
        assert with_block.get("name") == artifact_name
        assert "github.event.client_payload.orchestrator_run_id" in str(with_block.get("run-id")), (
            f"{exec_path.name}: cross-run download must pin the orchestrator run-id"
        )
        assert with_block.get("github-token"), (
            f"{exec_path.name}: cross-run download requires an explicit "
            "github-token (actions: read)"
        )

        def _verifies(step: dict[str, Any]) -> bool:
            return any(_is_manifest_checksum(cmd) for cmd in _shell_commands(step.get("run") or ""))

        verify_steps = [step for step in steps if _verifies(step)]
        assert verify_steps, (
            f"{exec_path.name}: the payload manifest must be verified in "
            "checksum -c mode before execution"
        )
        assert verify_steps[0].get("id") == VERIFY_STEP_ID, (
            f"{exec_path.name}: the verify step's id must be {VERIFY_STEP_ID!r}: "
            "the payload step's gate references steps.<id>.outcome, and a "
            "mismatched rename would silently disable the manifest gate"
        )
        download_idx = next(
            i
            for i, step in enumerate(steps)
            if (step.get("uses") or "").startswith("actions/download-artifact")
        )
        verify_idx = next(i for i, step in enumerate(steps) if _verifies(step))
        assert verify_idx > download_idx, "manifest verification must run after the download"


def test_executor_payload_steps_run_only_verified_bytes() -> None:
    """The Leg A provenance invariant (gate round-1 finding): no payload
    script may execute on unverified bytes — every step that runs payload
    content must be gated on the manifest verify step's outcome, and no
    ``always()`` may override that gate."""
    for exec_path, payload_run_needle in (
        (EXEC1, "prod_event_collector.py"),
        (EXEC2, "collect_prod_run.sh"),
    ):
        doc = _load_yaml(exec_path)
        steps = [step for job in doc.get("jobs", {}).values() for step in _steps(job)]
        payload_steps = [step for step in steps if payload_run_needle in (step.get("run") or "")]
        assert payload_steps, f"{exec_path.name} must run {payload_run_needle}"
        for step in payload_steps:
            gate = step.get("if") or ""
            assert "always()" not in gate, (
                f"{exec_path.name}: {payload_run_needle} must never run on unverified payload bytes"
            )
            assert f"steps.{VERIFY_STEP_ID}.outcome == 'success'" in gate, (
                f"{exec_path.name}: {payload_run_needle} must be gated on the "
                "manifest verify step's outcome"
            )


def test_executor1_keeps_collector_flags_and_dry_run_contract() -> None:
    doc = _load_yaml(EXEC1)
    steps = [step for job in doc.get("jobs", {}).values() for step in _steps(job)]
    run_steps = [s for s in steps if "prod_event_collector.py" in (s.get("run") or "")]
    assert run_steps, "executor 1 must run the collector"
    body = run_steps[0].get("run") or ""
    for flag in (
        "--sync-state /Users/lwj04/.nfmd/prod-event-sync-state.json",
        "--master-jsonl /Users/lwj04/.nfmd/master-deploy-events.jsonl",
        "--repo",
        "--ssh-target lwj04@127.0.0.1",
    ):
        assert flag in body, f"executor 1 collector invocation lost {flag!r}"
    env = run_steps[0].get("env") or {}
    assert env.get("GH_TOKEN") == "${{ secrets.GITHUB_TOKEN }}", (
        "executor 1 must keep the collector's GH_TOKEN env (gh api + gh run download)"
    )
    assert "client_payload.dry_run" in str(env.get("DRY_RUN")), (
        "dry_run must reach the collector through an env var, not inline "
        "shell interpolation of client_payload"
    )
    assert '[ "$DRY_RUN" = "true" ]' in body, (
        "the --dry-run flag must be constructed from the DRY_RUN env var"
    )


def test_executor2_keeps_gh_token_and_deploy_events_vars() -> None:
    doc = _load_yaml(EXEC2)
    jobs = doc.get("jobs", {})
    job = next(iter(jobs.values()))
    env = job.get("env") or {}
    assert env.get("GH_TOKEN") == "${{ secrets.GH_TOKEN || secrets.GITHUB_TOKEN }}", (
        "executor 2 must keep the fine-grained-PAT-first GH_TOKEN env"
    )
    assert env.get("NFMD_DEPLOY_EVENTS_PATH") == "${{ vars.NFMD_DEPLOY_EVENTS_PATH || '' }}", (
        "executor 2 must keep the host-absolute deploy-events path var"
    )
    assert (
        env.get("NFMD_DEPLOY_EVENTS_PROCESSED_PATH")
        == "${{ vars.NFMD_DEPLOY_EVENTS_PROCESSED_PATH || '' }}"
    ), "executor 2 must keep the processed-ledger path var"
    steps = [step for j in jobs.values() for step in _steps(j)]
    run_steps = [s for s in steps if "collect_prod_run.sh" in (s.get("run") or "")]
    assert run_steps, "executor 2 must run the bash glue"
    assert "RUNNER_TEMP}/payload-${GITHUB_RUN_ID}/scripts/lib/collect_prod_run.sh" in (
        run_steps[0].get("run") or ""
    ), (
        "executor 2 must invoke the glue at its staged repo-relative layout "
        "under the per-run payload dir (collect_prod_run.sh resolves REPO_ROOT "
        "from its own location)"
    )


def test_executor_downloads_require_actions_read_permission() -> None:
    for exec_path in (EXEC1, EXEC2):
        permissions = _load_yaml(exec_path).get("permissions") or {}
        assert permissions.get("actions") == "read", (
            f"{exec_path.name}: cross-run artifact download needs actions: read"
        )


# ---------------------------------------------------------------------------
# Per-run payload staging (NFM-5556 concurrent-workspace isolation)
# ---------------------------------------------------------------------------


def test_executors_stage_payload_in_a_per_run_dir() -> None:
    """Several independently-grouped jobs write the one persisted
    ``GITHUB_WORKSPACE`` on this runner; a shared ``payload/`` staging dir
    lets one executor's download/cleanup race another's gated run step
    (NFM-5556: concurrent jobs against the shared workspace). The payload
    must live in a per-run dir under ``runner.temp`` for its whole
    lifecycle: download target, every step that touches it, and the
    ``if: always()`` cleanup."""
    for exec_path in (EXEC1, EXEC2):
        doc = _load_yaml(exec_path)
        steps = [step for job in doc.get("jobs", {}).values() for step in _steps(job)]
        downloads = [
            step
            for step in steps
            if (step.get("uses") or "").startswith("actions/download-artifact")
        ]
        assert downloads, f"{exec_path.name} must download the payload artifact"
        dl_path = str(downloads[0].get("with", {}).get("path") or "")
        assert dl_path == "${{ runner.temp }}/payload-${{ github.run_id }}", (
            f"{exec_path.name}: payload must stage under a per-run dir in "
            "runner.temp, not the shared GITHUB_WORKSPACE (NFM-5556 race)"
        )
        for step in steps:
            body = step.get("run") or ""
            # client_payload is the dispatch envelope, not the staged dir.
            if "payload" in body.replace("client_payload", ""):
                assert "${GITHUB_WORKSPACE}" not in body, (
                    f"{exec_path.name} step {step.get('name')!r} must not "
                    "touch the payload via the shared GITHUB_WORKSPACE"
                )
                if "rm -rf" in body:
                    assert 'rm -rf "${RUNNER_TEMP}/payload-${GITHUB_RUN_ID}"' in body, (
                        f"{exec_path.name} cleanup must remove the per-run "
                        "payload dir (and only it)"
                    )
                else:
                    assert "${RUNNER_TEMP}/payload-${GITHUB_RUN_ID}" in body, (
                        f"{exec_path.name} step {step.get('name')!r} must "
                        "reference the payload by its per-run absolute path"
                    )


# ---------------------------------------------------------------------------
# Cleanup hygiene (NFM-5556: the host is disk-pressured)
# ---------------------------------------------------------------------------


def test_executors_clean_the_payload_every_fire() -> None:
    for exec_path in (EXEC1, EXEC2):
        doc = _load_yaml(exec_path)
        steps = [step for job in doc.get("jobs", {}).values() for step in _steps(job)]
        clean_steps = [
            step
            for step in steps
            if "rm -rf" in (step.get("run") or "") and "payload" in (step.get("run") or "")
        ]
        assert clean_steps, f"{exec_path.name} must remove the payload dir after the run"
        assert clean_steps[-1].get("if") == "always()", (
            f"{exec_path.name}: payload cleanup must run even on failure "
            "(per-run dirs cannot accumulate on the disk-pressured host)"
        )
