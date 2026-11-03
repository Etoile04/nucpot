"""GitHub-hosted diversion guards for the site-monitor watchdog lane.

NFM-5798 P2 Leg A (operator card ``ce3c180b``, option ``p2-pilot-watchdog``;
design ``4f469b4f``, review ``8fecf232``). The self-hosted watchdog's only
failing step was its checkout: the hand-rolled bounded git fetch of two
self-contained scripts through the host's flaky egress lane (NFM-5548 →
5556 → 5796 → 5798 history; occurrence #8 minted 2/3 rc=124 streak members
in one trough at the widened 960s envelope, and dead troughs still hard-fail
every fetch by construction). The checks themselves are read-only docker /
host probes that need nothing from the repo at runtime.

The diversion moves the FETCH, not the job:

- ``site-monitor-watchdog-orchestrator.yml`` (hosted, ubuntu-latest) —
  checks out at the fire's pinned SHA on GitHub's healthy egress, stages
  the two payload scripts plus a sha256 manifest as a workflow artifact,
  then ``repository_dispatch``es the executor with (run-id, sha)
  provenance. The 6h cron moves here verbatim from ``site-monitor.yml``.
- ``site-monitor-watchdog-executor.yml`` (self-hosted) — downloads the
  artifact over the runner's own job channel and runs the scripts with
  host env/secrets. It performs ZERO git fetches, so the checkout
  blackhole class cannot fail the watchdog lane by construction. It is
  dispatch-only: the literal-cron ``if:`` guard class that silently
  skipped the watchdog leg on dispatch (RE smoke 41465022940) is
  structurally impossible with a repository_dispatch-only trigger.
- ``site-monitor.yml`` — keeps the hourly hosted monitor, which now also
  reads the Actions runs API for the executor workflow's last success
  (the run record IS the heartbeat — no write channel, review pt 2) and
  alerts when it goes stale (> 2x the 6h nominal cadence).

P1's widened bounded-fetch bounds (merged ``984af749fb``) remain on the
two collector workflows as defense-in-depth; this file additionally pins
that no OTHER workflow grew a bounded fetch.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]

SITE_MONITOR = REPO_ROOT / ".github/workflows/site-monitor.yml"
ORCHESTRATOR = REPO_ROOT / ".github/workflows/site-monitor-watchdog-orchestrator.yml"
EXECUTOR = REPO_ROOT / ".github/workflows/site-monitor-watchdog-executor.yml"

WORKFLOW_DIR = REPO_ROOT / ".github/workflows"

MONITOR_CRON = "23 * * * *"
WATCHDOG_CRON = "17 */6 * * *"
DISPATCH_EVENT_TYPE = "nfmd-watchdog-exec"
ARTIFACT_NAME = "watchdog-payload"
VERIFY_STEP_ID = "verify-payload"
EXECUTOR_WORKFLOW_FILENAME = EXECUTOR.name
STALE_THRESHOLD_SECS = 43200  # 2x the 6h nominal cadence (observed max fire gap ~9h)
EXECUTOR_TIMEOUT_MINUTES = 18  # carried from the pre-split watchdog job (P1)

PAYLOAD_SCRIPTS = (
    "tools/post-deploy-cutover-watchdog/watchdog.sh",
    "tools/post-deploy-cutover-watchdog/container_sanity_check.sh",
)

FETCH_NEEDLE = "fetch --no-tags --depth=1"
LITERAL_CRON_GUARD = "github.event.schedule == '17 */6 * * *'"
# The only collector workflows may carry the bounded-fetch checkout body.
EXPECTED_FETCH_CARRIERS = frozenset(
    {
        "collect-prod-deploy-events.yml",
        "prod-deploy-event-collector.yml",
    }
)


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


# ---------------------------------------------------------------------------
# The diversion files exist at all
# ---------------------------------------------------------------------------


def test_diversion_files_exist() -> None:
    for path in (SITE_MONITOR, ORCHESTRATOR, EXECUTOR):
        assert path.is_file(), f"{path.name} missing — the Leg A split is incomplete"


# ---------------------------------------------------------------------------
# site-monitor.yml: the on-host fetch class is gone
# ---------------------------------------------------------------------------


def test_site_monitor_has_no_on_host_fetch() -> None:
    text = SITE_MONITOR.read_text(encoding="utf-8")
    assert FETCH_NEEDLE not in text, (
        "site-monitor.yml still carries a bounded git fetch; the watchdog "
        "checkout must move to the hosted orchestrator (NFM-5798 P2)"
    )
    doc = _load_yaml(SITE_MONITOR)
    for job_key, job in doc.get("jobs", {}).items():
        for body in _run_bodies(_steps(job)):
            assert "mktemp -d" not in body, (
                f"job {job_key} still performs a private-workdir checkout; "
                "no site-monitor job may fetch on-host after the diversion"
            )
            assert "NFMD_FETCH_PER_ATTEMPT_SECS" not in body, (
                f"job {job_key} still carries bounded-fetch knobs; they belong "
                "only to the two collector carriers now"
            )


def test_site_monitor_has_no_self_hosted_watchdog_job() -> None:
    doc = _load_yaml(SITE_MONITOR)
    for job_key, job in doc.get("jobs", {}).items():
        runs_on = job.get("runs-on")
        if isinstance(runs_on, list):
            assert "self-hosted" not in runs_on and "production" not in runs_on, (
                f"job {job_key} still targets the self-hosted production "
                "runner; the watchdog leg moved to the executor workflow"
            )


def test_literal_cron_guard_removed_from_site_monitor() -> None:
    """The literal-cron ``if:`` skipped the watchdog leg on dispatch (RE smoke
    41465022940 — the proven trap). Post-split it must not exist anywhere in
    site-monitor.yml, and the hourly entry must be the only schedule left."""
    text = SITE_MONITOR.read_text(encoding="utf-8")
    assert LITERAL_CRON_GUARD not in text, (
        "the literal-cron guard is the dispatch-blackhole trap; the split "
        "must remove it, not re-wire it"
    )
    doc = _load_yaml(SITE_MONITOR)
    triggers = doc.get("on") or doc.get(True) or {}
    crons = [entry.get("cron") for entry in (triggers.get("schedule") or [])]
    assert crons == [MONITOR_CRON], (
        f"site-monitor.yml schedule must be hourly-only {crons!r}; the 6h "
        f"watchdog cron moved to the orchestrator ({WATCHDOG_CRON})"
    )


def test_bounded_fetch_carriers_are_exactly_two_collectors() -> None:
    """P1's widened bounds stay on the two collectors as defense-in-depth —
    and on NOTHING else (a silent re-growth of the on-host fetch class in a
    third workflow would re-open the blackhole lane without a guard)."""
    carriers: set[str] = set()
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        if FETCH_NEEDLE in path.read_text(encoding="utf-8"):
            carriers.add(path.name)
    assert carriers == set(EXPECTED_FETCH_CARRIERS), (
        f"bounded-fetch carriers are {sorted(carriers)}, expected exactly "
        f"{sorted(EXPECTED_FETCH_CARRIERS)}"
    )


# ---------------------------------------------------------------------------
# Orchestrator: hosted checkout, verbatim cron, artifact carry, dispatch
# ---------------------------------------------------------------------------


def test_orchestrator_schedule_preserves_literal_cron_and_dispatch() -> None:
    doc = _load_yaml(ORCHESTRATOR)
    triggers = doc.get("on") or doc.get(True) or {}
    crons = [entry.get("cron") for entry in (triggers.get("schedule") or [])]
    assert WATCHDOG_CRON in crons, (
        f"the 6h watchdog cron {WATCHDOG_CRON!r} must move verbatim to the "
        "orchestrator — dropping it silently retires the 6h cadence"
    )
    assert "workflow_dispatch" in triggers, (
        "workflow_dispatch is the Leg A canary door (3x dispatch across a "
        "flap window) — it must exist on the orchestrator"
    )


def test_orchestrator_is_hosted_and_free_of_bounded_fetch() -> None:
    doc = _load_yaml(ORCHESTRATOR)
    jobs = doc.get("jobs", {})
    assert jobs, "orchestrator has no jobs"
    for job_key, job in jobs.items():
        assert job.get("runs-on") == "ubuntu-latest", (
            f"orchestrator job {job_key} must run on the hosted lane; the "
            "whole point is fetching on GitHub's healthy egress"
        )
    text = ORCHESTRATOR.read_text(encoding="utf-8")
    assert FETCH_NEEDLE not in text, "orchestrator must use plain actions/checkout"
    used_checkout = any(
        (step.get("uses") or "").startswith("actions/checkout")
        for job in jobs.values()
        for step in _steps(job)
    )
    assert used_checkout, "orchestrator must check out via actions/checkout"


def test_orchestrator_stages_and_uploads_both_payload_scripts() -> None:
    doc = _load_yaml(ORCHESTRATOR)
    commands = _job_commands(doc)
    for script in PAYLOAD_SCRIPTS:
        assert Path(script).is_file(), f"payload script {script} missing from the repo"
        assert any(re.search(r"\bcp\b", cmd) and script in cmd for cmd in commands), (
            f"staging step must copy {script} into the payload; the executor "
            "runs exactly what the orchestrator checked out"
        )
    assert any(
        re.search(r"\bsha256sum\b", cmd)
        and "MANIFEST.sha256" in cmd
        and all(Path(script).name in cmd for script in PAYLOAD_SCRIPTS)
        for cmd in commands
    ), (
        "the payload must carry a sha256 manifest over both payload scripts "
        "so the executor can verify the artifact before executing it"
    )
    uploads = [
        step
        for job in doc.get("jobs", {}).values()
        for step in _steps(job)
        if (step.get("uses") or "").startswith("actions/upload-artifact")
    ]
    assert uploads, "orchestrator must upload the payload as a workflow artifact"
    with_name = [step for step in uploads if step.get("with", {}).get("name") == ARTIFACT_NAME]
    assert with_name, f"the artifact must be named {ARTIFACT_NAME!r} (executor pins it)"


def test_orchestrator_dispatches_executor_with_provenance() -> None:
    doc = _load_yaml(ORCHESTRATOR)
    dispatch_cmds = [
        cmd
        for cmd in _job_commands(doc)
        if re.search(r"\bgh\s+api\b", cmd) and "/dispatches" in cmd
    ]
    assert dispatch_cmds, "orchestrator must POST a repository dispatch via gh api"
    assert all(f"-f event_type={DISPATCH_EVENT_TYPE}" in cmd for cmd in dispatch_cmds), (
        f"dispatch event_type must be {DISPATCH_EVENT_TYPE!r} (the executor's only door)"
    )
    assert all("client_payload[orchestrator_run_id]" in cmd for cmd in dispatch_cmds), (
        "the executor needs the orchestrator run-id to download the artifact"
    )
    permissions = doc.get("permissions") or {}
    assert "contents" in permissions, (
        "dispatch POST needs explicit contents permission (review 8fecf232 pt 3)"
    )
    assert permissions["contents"] == "write", (
        "the dispatch POST requires contents: write on the workflow token"
    )


# ---------------------------------------------------------------------------
# Executor: dispatch-only, zero git, artifact-fed, serialized
# ---------------------------------------------------------------------------


def test_executor_receives_only_the_dispatch() -> None:
    doc = _load_yaml(EXECUTOR)
    triggers = doc.get("on") or doc.get(True) or {}
    assert set(triggers) == {"repository_dispatch"}, (
        f"executor triggers must be repository_dispatch only, got {sorted(triggers)}; "
        "a schedule trigger would re-open the literal-cron guard class and a "
        "direct workflow_dispatch would have no payload artifact"
    )
    types = triggers["repository_dispatch"].get("types") or []
    assert DISPATCH_EVENT_TYPE in types, f"executor must listen for {DISPATCH_EVENT_TYPE!r}"


def test_executor_does_zero_git_operations() -> None:
    """The core invariant: no checkout action, no git subcommand anywhere in
    a run body — the executor runs ONLY what the artifact carries, so the
    checkout blackhole class cannot fail this lane by construction."""
    doc = _load_yaml(EXECUTOR)
    for job_key, job in doc.get("jobs", {}).items():
        for step in _steps(job):
            uses = step.get("uses") or ""
            assert not uses.startswith("actions/checkout"), (
                f"executor step in {job_key} uses actions/checkout; the "
                "executor must never touch git"
            )
            body = step.get("run") or ""
            assert not re.search(r"\bgit\b", body), (
                f"executor step in {job_key} shells out to git:\n{body}"
            )


def test_executor_runs_on_production_runner_with_p1_budget() -> None:
    doc = _load_yaml(EXECUTOR)
    jobs = doc.get("jobs", {})
    assert jobs, "executor has no jobs"
    job = next(iter(jobs.values()))
    assert job.get("runs-on") == ["self-hosted", "production"], (
        "the executor must keep the pre-split watchdog's runner placement "
        "(docker + ~/.nfmd ledger live on that host)"
    )
    assert int(job.get("timeout-minutes", 0)) == EXECUTOR_TIMEOUT_MINUTES, (
        f"executor timeout-minutes must carry the P1 budget ({EXECUTOR_TIMEOUT_MINUTES})"
    )


def test_executor_downloads_and_verifies_artifact_before_running() -> None:
    doc = _load_yaml(EXECUTOR)
    steps = [step for job in doc.get("jobs", {}).values() for step in _steps(job)]
    downloads = [
        step for step in steps if (step.get("uses") or "").startswith("actions/download-artifact")
    ]
    assert downloads, "executor must download the orchestrator's payload artifact"
    with_block = downloads[0].get("with", {})
    assert with_block.get("name") == ARTIFACT_NAME
    assert "github.event.client_payload.orchestrator_run_id" in str(with_block.get("run-id")), (
        "cross-run artifact download must pin the orchestrator run-id"
    )
    assert with_block.get("github-token"), (
        "cross-run artifact download requires an explicit github-token (actions: read)"
    )

    def _verifies_manifest(step: dict[str, Any]) -> bool:
        return any(_is_manifest_checksum(cmd) for cmd in _shell_commands(step.get("run") or ""))

    verify_steps = [step for step in steps if _verifies_manifest(step)]
    assert verify_steps, (
        "the payload manifest must be verified in checksum -c mode before execution"
    )
    assert verify_steps[0].get("id") == VERIFY_STEP_ID, (
        f"the verify step's id must be {VERIFY_STEP_ID!r}: the sanity step's "
        "gate references steps.<id>.outcome, and a mismatched rename would "
        "silently disable the sanity check"
    )
    download_idx = next(
        i
        for i, step in enumerate(steps)
        if (step.get("uses") or "").startswith("actions/download-artifact")
    )
    verify_idx = next(i for i, step in enumerate(steps) if _verifies_manifest(step))
    assert verify_idx > download_idx, "manifest verification must run after the download"


def test_executor_serializes_and_never_cancels() -> None:
    doc = _load_yaml(EXECUTOR)
    concurrency = doc.get("concurrency") or {}
    assert concurrency.get("group"), "executor needs a concurrency group"
    assert concurrency.get("cancel-in-progress") is False, (
        "cancel-in-progress must be false: cancelling a host-side check "
        "mid-flight tears the run (review 8fecf232 pt 5); queue instead"
    )


def test_executor_runs_both_scripts_with_webhook_secret() -> None:
    doc = _load_yaml(EXECUTOR)
    steps = [step for job in doc.get("jobs", {}).values() for step in _steps(job)]
    watchdog_bodies = [s.get("run") or "" for s in steps if "watchdog.sh" in (s.get("run") or "")]
    sanity_bodies = [
        s.get("run") or "" for s in steps if "container_sanity_check.sh" in (s.get("run") or "")
    ]
    assert watchdog_bodies, "executor must run the stale-container watchdog script"
    assert sanity_bodies, "executor must run the container sanity check script"
    for step in steps:
        body = step.get("run") or ""
        if "watchdog.sh" in body or "container_sanity_check.sh" in body:
            env = step.get("env") or {}
            assert env.get("ALERT_WEBHOOK") == "${{ secrets.ALERT_WEBHOOK }}", (
                "both payload scripts alert via ALERT_WEBHOOK; the executor "
                "must pass the host secret through unchanged"
            )
    sanity_steps = [s for s in steps if "container_sanity_check.sh" in (s.get("run") or "")]
    sanity_if = (sanity_steps[0].get("if") or "") if sanity_steps else ""
    assert sanity_steps and "always()" not in sanity_if, (
        "the sanity check must never execute unverified payload bytes"
    )
    assert f"steps.{VERIFY_STEP_ID}.outcome == 'success'" in sanity_if, (
        "the sanity check keeps its pre-split contract — it runs even when "
        "the stale-image check failed — but only after the manifest verified"
    )


# ---------------------------------------------------------------------------
# Monitor: hosted staleness eye over the executor (the blind-spot closure)
# ---------------------------------------------------------------------------


def test_monitor_reads_actions_api_for_executor_health() -> None:
    doc = _load_yaml(SITE_MONITOR)
    monitor = (doc.get("jobs") or {}).get("monitor")
    assert monitor, "site-monitor.yml must keep its hosted monitor job"
    permissions = monitor.get("permissions") or {}
    assert permissions.get("actions") == "read", (
        "the monitor job needs actions: read to query the executor's runs "
        "(review 8fecf232 pt 2 — no write channel, read-only heartbeat)"
    )
    commands = _shell_commands("\n".join(_run_bodies(_steps(monitor))))
    assert any(
        re.search(r"^\w+=", cmd) and EXECUTOR_WORKFLOW_FILENAME in cmd for cmd in commands
    ), "the staleness check must resolve the executor workflow via the Actions API"
    stale_needle = "${NFMD_WATCHDOG_STALE_SECS:-" + str(STALE_THRESHOLD_SECS) + "}"
    assert any(re.search(r"^\w+=", cmd) and stale_needle in cmd for cmd in commands), (
        f"staleness threshold must default to {STALE_THRESHOLD_SECS}s "
        "(2x the 6h nominal cadence, above the observed ~9h max fire gap) "
        "and stay env-tunable like the fetch knobs"
    )
    assert any(re.search(r"\bgh\s+api\b", cmd) and "status=success" in cmd for cmd in commands), (
        "the heartbeat is the executor's last SUCCESSFUL run — failures "
        "must not reset the staleness clock"
    )


def test_monitor_alerts_via_dedicated_label_on_staleness() -> None:
    doc = _load_yaml(SITE_MONITOR)
    monitor = (doc.get("jobs") or {}).get("monitor")
    steps = _steps(monitor)
    alert_steps = [
        step
        for step in steps
        if (step.get("if") or "").startswith("steps.exec_stale.outputs.stale")
    ]
    assert alert_steps, (
        "an alert step gated on the staleness output must exist — a stale "
        "executor with no alert recreates the 3-streak blind spot"
    )
    script = "\n".join((step.get("with") or {}).get("script", "") for step in alert_steps)
    assert "watchdog-stale" in script, (
        "staleness alerts must carry the watchdog-stale label so they "
        "never collide with the P0 site-down lane"
    )
    assert "issues.create" in script or "createComment" in script, (
        "the alert must land as a GitHub issue (the workflow's alerting convention)"
    )
