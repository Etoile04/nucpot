"""Static guard: docker-compose.prod.yml declares a Celery ``beat`` service (NFM-4617-B2).

Background
----------
NFM-4539 AC-3 requires ``rag_audit_index_coverage`` to run daily at 03:30 UTC
via Celery beat. The task is registered in
``apps/api/src/nfm_db/services/celery_app.py`` via
``celery_app.conf.beat_schedule.update({...})`` (crontab(hour=3, minute=30)),
but the **scheduler process** itself (``celery ... beat``) was never added
to ``docker-compose.prod.yml``. Only the ``worker`` service exists, so
``beat_schedule`` is silently inert on the prod host. Production telemetry
confirmed the gap: ``GET /api/v1/lightrag/metrics`` reported
``lit_indexed_total=0`` while ``lit_completed_total=15`` — the diff job had
never executed.

What this test enforces
-----------------------
``docker-compose.prod.yml`` MUST declare a ``beat:`` service that runs the
Celery beat scheduler against the same ``nfm_db.services.celery_app:celery_app``
module the worker uses. Concretely:

  * ``services.beat`` is present (top-level mapping).
  * The service ``command:`` invokes ``celery ... beat`` (so it is the
    scheduler process, not another worker).
  * The service shares the same base image as the ``worker`` service
    (``nucpot-prod-api``); beat is a scheduler, not a new build target.
  * The service declares a ``container_name: nucpot-prod-beat`` so the
    duplicate-container guard rejects a future re-introduction.
  * The service depends on ``redis`` (Celery broker) so the scheduler has
    a live broker to publish scheduled task messages into.
  * Every task in ``celery_app.conf.beat_schedule`` resolves (via
    ``task_routes``, per-entry ``options.queue``, or the app's default
    queue) to a queue listed in the worker's ``--queues`` flag — beat
    publishing onto a queue nobody consumes is the same silent failure
    as having no scheduler at all.

Why YAML and not regex
----------------------
Compose accepts command-as-string and command-as-list-of-strings. PyYAML
gives a single canonical view of both shapes; regex is brittle to quoting.
The same ``!override`` / ``!reset`` / ``!merge`` compose-spec custom tags
used by ``docker-compose.preview.yml`` are not used in the prod file, but
the tag-stripping helper is borrowed for symmetry with the sibling
``test_lightrag_no_host_port.py`` loader so a future fork that adds an
override does not silently break the loader.

Failure modes
-------------
If a future change removes or renames the ``beat`` service, drops the
``beat`` command, rebinds the service to a different image, or drops a
scheduled task's queue from the worker's ``--queues`` list, this test
fails on the matching compose file with the offending value quoted
verbatim. CI runs the test on every PR touching ``docker-compose.prod.yml``
(NFM-4481 review pattern). The regression fixtures in
``TestRegressionFixtures`` invoke these same guard methods against
synthetic compose files, so weakening any guard assertion trips CI before
it lands.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# Compose-spec custom tags that PyYAML's safe_load cannot resolve. The prod
# compose does not use them, but the loader strips them for symmetry with
# the sibling NFM-4481 test.
_COMPOSE_TAG_RE = re.compile(r"!(?:override|reset|merge)\b")

# The ``--queues`` flag of the prod worker command, in any of the shapes
# ``_render_command`` flattens (string / list / block scalar).
_QUEUES_FLAG_RE = re.compile(r"--queues[=\s]+(\S+)")

# Repo-rooted absolute paths so the test is independent of CWD.
# Layout: <repo>/apps/api/tests/compose/test_prod_celery_beat_service.py
# parents[0] = tests/compose, [1] = tests, [2] = api, [3] = apps, [4] = repo
REPO_ROOT = Path(__file__).resolve().parents[4]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"

# The worker image is the canonical build target; beat reuses it so we do
# not introduce a parallel Dockerfile. Mirror NFM-4257's prune pattern: a
# new image per role (worker, beat) is a governance signal that the change
# has drifted beyond a deployment-config edit.
_WORKER_IMAGE = "nucpot-prod-api:${PROD_IMAGE_TAG:-latest}"

# The Celery app object the worker also uses — beat must target the same
# module so the ``beat_schedule`` registered at import time actually fires.
# The string form (``nfm_db.services.celery_app:celery_app``) is what the
# prod worker command passes via ``-A``; we accept either the literal form
# or a one-token fragment that contains it.
_CELERY_APP_TARGET = "nfm_db.services.celery_app:celery_app"


@pytest.fixture
def prod_compose() -> Path:
    """The real prod compose file the guards run against by default.

    Guards take the compose path as a parameter so the regression fixtures
    can invoke the same guard methods against synthetic files.
    """
    return PROD_COMPOSE


def _load_yaml(path: Path) -> dict:
    """Load a compose YAML and return the top-level mapping.

    Mirrors the NFM-4481 helper. Stripping ``!override`` / ``!reset`` /
    ``!merge`` is a no-op for the prod file (it never uses them) but keeps
    the loader robust if a future change adds one. ``pytest.skip`` on a
    missing file keeps the test independent of partial CI checkouts.
    """
    if not path.exists():
        pytest.skip(f"{path} not present in this checkout")
    text = path.read_text(encoding="utf-8")
    stripped = _COMPOSE_TAG_RE.sub("", text)
    loaded = yaml.safe_load(stripped)
    assert isinstance(loaded, dict), (
        f"{path}: top-level YAML must be a mapping (services:), got {type(loaded).__name__}"
    )
    return loaded


def _render_command(cmd: object) -> str:
    """Flatten a compose ``command:`` value to a single string for substring checks.

    Compose accepts command-as-string (``command: "celery -A ... beat"``),
    command-as-list (``command: ["celery", "-A", "...", "beat"]``), and
    command-as-multi-line block scalar (the ``command: >`` style used by
    the existing worker service). All three are reduced to a single
    whitespace-joined string so the substring check below catches any
    shape.
    """
    if cmd is None:
        return ""
    if isinstance(cmd, str):
        return cmd
    if isinstance(cmd, list):
        return " ".join(str(token) for token in cmd)
    return str(cmd)


def _services(doc: dict) -> dict:
    services = doc.get("services") or {}
    assert isinstance(services, dict), (
        f"top-level `services:` must be a mapping, got {type(services).__name__}"
    )
    return services


def _beat_service(path: Path) -> dict | None:
    """Return the ``beat`` service mapping, or ``None`` if absent.

    Mirrors the three-way contract used by ``_lightrag_service`` in the
    sibling test: missing service (None) vs. present service without
    ``command:`` (a dict) is the failure mode this test catches.
    """
    doc = _load_yaml(path)
    beat = _services(doc).get("beat")
    if beat is None:
        return None
    assert isinstance(beat, dict), (
        f"{path}: `services.beat` must be a mapping, got {type(beat).__name__}"
    )
    return beat


def _worker_queues(path: Path) -> set[str]:
    """Return the queue names the ``worker`` service subscribes to.

    Parses the worker ``command:`` for ``--queues=q1,q2,...`` (any command
    shape ``_render_command`` flattens). A worker without the flag consumes
    only Celery's default queue, which is a routing regression the caller
    reports.
    """
    worker = _services(_load_yaml(path)).get("worker")
    assert isinstance(worker, dict), (
        f"{path}: `services.worker` must be a mapping, got {type(worker).__name__}"
    )
    rendered = _render_command(worker.get("command"))
    match = _QUEUES_FLAG_RE.search(rendered)
    assert match, (
        f"{path}: `services.worker.command` must declare `--queues=...` so "
        f"beat-scheduled tasks are guaranteed a consumer. Got {rendered!r}."
    )
    return {queue for queue in match.group(1).split(",") if queue}


def _entry_queue(task_name: str, entry: dict, celery_conf) -> str:
    """Resolve the broker queue a beat-scheduled task is published to.

    Precedence mirrors Celery's own routing: per-entry ``options.queue``,
    then ``task_routes``, then the app's default queue (``celery`` unless
    ``task_default_queue`` overrides it).
    """
    options = entry.get("options")
    if isinstance(options, dict) and options.get("queue"):
        return str(options["queue"])
    routes = celery_conf.task_routes
    if isinstance(routes, dict):
        route = routes.get(task_name)
        if isinstance(route, dict) and route.get("queue"):
            return str(route["queue"])
    return str(celery_conf.task_default_queue or "celery")


# ---------------------------------------------------------------------------
# Core invariant: the prod compose declares a beat service that schedules
# Celery against the same app module the worker uses.
# ---------------------------------------------------------------------------


class TestProdComposeCeleryBeat:
    """docker-compose.prod.yml MUST declare a `beat` Celery scheduler.

    Each guard takes the compose path as its ``prod_compose`` fixture
    parameter so ``TestRegressionFixtures`` can run the exact same guard
    logic against synthetic files.
    """

    def test_beat_service_is_declared(self, prod_compose: Path) -> None:
        """The prod compose must declare a top-level ``beat:`` service.

        NFM-4539 AC-3 requires the daily ``rag_audit_index_coverage`` audit
        to actually fire. Without a ``beat`` scheduler container, the
        registered ``beat_schedule`` is silently inert and the metric
        ``lit_indexed_total`` stays at zero (NFM-4617-B2 evidence:
        ``lit_completed_total=15``, ``lit_indexed_total=0``).
        """
        beat = _beat_service(prod_compose)
        assert beat is not None, (
            f"{prod_compose}: `services.beat` is missing entirely. "
            f"NFM-4539 AC-3 requires a Celery `beat` scheduler container "
            f"running `celery -A nfm_db.services.celery_app:celery_app "
            f"beat` against the same broker (redis) the worker uses. Add "
            f"a `beat:` service to this compose file."
        )

    def test_beat_command_runs_celery_beat(self, prod_compose: Path) -> None:
        """The ``beat`` command must invoke ``celery ... beat``.

        Distinguishes a real scheduler from a misconfigured worker (the
        current prod shape — a single ``worker`` service that never
        schedules). Anything that does not include the literal ``beat``
        token in the command is treated as a regression.
        """
        beat = _beat_service(prod_compose)
        if beat is None:
            pytest.fail(
                f"{prod_compose}: no `beat` service declared; cannot "
                f"inspect its command. See test_beat_service_is_declared."
            )
        rendered = _render_command(beat.get("command"))
        assert rendered, (
            f"{prod_compose}: `services.beat` has no `command:` — the "
            f"scheduler image defaults to the Dockerfile CMD, which "
            f"starts the API. Set `command:` to "
            f"`celery -A nfm_db.services.celery_app:celery_app beat "
            f"--loglevel=info`."
        )
        assert "celery" in rendered, (
            f"{prod_compose}: `services.beat.command` does not invoke "
            f"`celery`: {rendered!r}. NFM-4617-B2 requires the Celery "
            f"beat scheduler against nfm_db.services.celery_app:celery_app."
        )
        assert "beat" in rendered, (
            f"{prod_compose}: `services.beat.command` does not include "
            f"the `beat` subcommand: {rendered!r}. A beat-shaped command "
            f"without the `beat` token is a worker, not a scheduler."
        )

    def test_beat_targets_the_same_celery_app_as_worker(self, prod_compose: Path) -> None:
        """The ``-A`` module path must point at the worker's celery_app.

        ``apps/api/src/nfm_db/services/celery_app.py`` registers
        ``beat_schedule`` at module import. If beat targets a different
        app object (e.g. ``nfm_db.celery_app`` — the example string in
        the NFM-4617 issue is wrong), the scheduled tasks never fire
        because the worker reads schedules off its own ``celery_app``.
        """
        beat = _beat_service(prod_compose)
        if beat is None:
            pytest.fail(
                f"{prod_compose}: no `beat` service declared; cannot verify celery_app target."
            )
        rendered = _render_command(beat.get("command"))
        assert _CELERY_APP_TARGET in rendered, (
            f"{prod_compose}: `services.beat.command` does not target "
            f"{_CELERY_APP_TARGET!r}: got {rendered!r}. The worker uses "
            f"this exact module; beat must use the same one or the "
            f"`beat_schedule` registered at import time is invisible to "
            f"the scheduler."
        )

    def test_beat_reuses_worker_image(self, prod_compose: Path) -> None:
        """The ``beat`` service must reuse the worker's image, not build a new one.

        Per NFM-4617-B2, beat is a deployment-config edit, not a build
        change. Adding a parallel Dockerfile would be a governance
        signal — ADR-013 G2 forbids weakening controls, and a new image
        target is the kind of change SRE must gate. Mirror
        ``nucpot-prod-api:${PROD_IMAGE_TAG:-latest}`` so the deploy stays
        atomic.
        """
        beat = _beat_service(prod_compose)
        if beat is None:
            pytest.fail(f"{prod_compose}: no `beat` service declared; cannot verify image reuse.")
        image = beat.get("image")
        assert image == _WORKER_IMAGE, (
            f"{prod_compose}: `services.beat.image` must reuse the "
            f"worker image ({_WORKER_IMAGE!r}) to avoid introducing a "
            f"parallel build target. Got {image!r}."
        )

    def test_beat_has_container_name(self, prod_compose: Path) -> None:
        """The ``beat`` service must declare ``container_name: nucpot-prod-beat``.

        Compose fails loudly when two services share a container_name on
        the same network. Pinning the name protects against accidental
        duplication and makes ``docker ps | grep beat`` deterministic for
        the NFM-4539 AC-3 acceptance evidence.
        """
        beat = _beat_service(prod_compose)
        if beat is None:
            pytest.fail(
                f"{prod_compose}: no `beat` service declared; cannot verify container_name."
            )
        name = beat.get("container_name")
        assert name == "nucpot-prod-beat", (
            f"{prod_compose}: `services.beat.container_name` must be "
            f"`nucpot-prod-beat` so the prod stack has a single "
            f"naming root (api/worker/redis/db/lightrag/web/beat). "
            f"Got {name!r}."
        )

    def test_beat_depends_on_redis(self, prod_compose: Path) -> None:
        """The ``beat`` service must depend on ``redis`` (Celery broker).

        Without a healthy broker the scheduler queues messages into the
        void and ``rag_audit_index_coverage`` silently never runs.
        ``depends_on: redis: { condition: service_healthy }`` matches the
        worker service's existing declaration.
        """
        beat = _beat_service(prod_compose)
        if beat is None:
            pytest.fail(f"{prod_compose}: no `beat` service declared; cannot verify depends_on.")
        depends = beat.get("depends_on")
        if depends is None:
            pytest.fail(
                f"{prod_compose}: `services.beat.depends_on` is missing. "
                f"Beat must declare `depends_on: redis: {{ condition: "
                f"service_healthy }}` so the scheduler has a live broker."
            )
        # Compose accepts both list-of-strings and mapping form. We only
        # need to confirm ``redis`` is in there.
        keys: list[str]
        if isinstance(depends, list):
            keys = [str(d) for d in depends]
        elif isinstance(depends, dict):
            keys = [str(k) for k in depends]
        else:
            pytest.fail(
                f"{prod_compose}: `services.beat.depends_on` must be a "
                f"list or mapping, got {type(depends).__name__}."
            )
        assert "redis" in keys, (
            f"{prod_compose}: `services.beat.depends_on` must include "
            f"`redis` (the Celery broker). Got keys {keys!r}."
        )

    def test_beat_healthcheck_is_valid(self, prod_compose: Path) -> None:
        """The ``beat`` healthcheck must be a valid docker-compose shape.

        NFM-4617-B2 review caught a copy-pasted ``celery inspect ping``
        probe that targets worker control nodes — beat (a scheduler, not
        a worker) would never receive a reply, so the container would
        flap to (unhealthy) every interval. This test asserts the parsed
        healthcheck mapping exists, its ``test`` field is a non-empty
        CMD-SHELL list, and the probe body is **not** the broken worker
        pattern (which would silently re-regress).

        The check is semantic (parsed YAML structure + the token set the
        probe actually executes), not a substring match on the raw file
        text, so a behavior-preserving refactor that swaps the command
        for an equivalent broker ping still passes.
        """
        beat = _beat_service(prod_compose)
        if beat is None:
            pytest.fail(f"{prod_compose}: no `beat` service declared; cannot verify healthcheck.")
        health = beat.get("healthcheck")
        if health is None:
            pytest.fail(
                f"{prod_compose}: `services.beat.healthcheck` is missing. "
                f"Beat must declare a healthcheck so an unreachable "
                f"broker is surfaced before scheduled tasks go silent."
            )
        assert isinstance(health, dict), (
            f"{prod_compose}: `services.beat.healthcheck` must be a "
            f"mapping, got {type(health).__name__}."
        )
        test = health.get("test")
        assert isinstance(test, list) and test, (
            f"{prod_compose}: `services.beat.healthcheck.test` must be "
            f"a non-empty list (CMD-SHELL form), got {test!r}."
        )
        assert test[0] == "CMD-SHELL", (
            f"{prod_compose}: `services.beat.healthcheck.test[0]` must "
            f"be `CMD-SHELL` so the shell expands $$HOSTNAME and other "
            f"variables; got {test[0]!r}."
        )
        # Render the full probe into a single string and reject the
        # known-broken worker-targeted pattern: ``celery inspect ping``.
        # The probe may legitimately contain the substring ``beat`` (e.g.
        # ``pgrep -f 'celery.*beat'``) — only the broken worker pattern
        # is rejected here.
        probe = " ".join(str(token) for token in test)
        assert "inspect ping" not in probe, (
            f"{prod_compose}: `services.beat.healthcheck.test` runs "
            f"`celery ... inspect ping`, which targets worker control "
            f"nodes. Beat is a scheduler and never receives a reply, so "
            f"the container is flagged (unhealthy) every interval. "
            f"Replace with a broker ping (e.g. "
            f"`python -c \"import redis; redis.Redis(host='nucpot-prod-"
            f"redis', port=6379).ping()\"`) or a process liveness probe."
        )
        # Required compose fields for a working healthcheck.
        assert health.get("interval"), (
            f"{prod_compose}: `services.beat.healthcheck.interval` is missing or empty."
        )
        assert health.get("retries"), (
            f"{prod_compose}: `services.beat.healthcheck.retries` is missing or empty."
        )


# ---------------------------------------------------------------------------
# Routing invariant: every beat-scheduled task lands on a queue the prod
# worker actually consumes.
# ---------------------------------------------------------------------------


class TestBeatTaskQueueRouting:
    """Beat messages must land on a queue listed in the worker's ``--queues``.

    A beat container publishing onto an unconsumed queue reproduces the
    exact silent failure NFM-4617-B2 set out to fix (``lit_indexed_total``
    stays 0) while the broker accumulates the scheduled messages
    unboundedly. The guard resolves each ``beat_schedule`` entry's queue
    the same way Celery does and checks it against the worker's parsed
    ``--queues`` flag.
    """

    def test_beat_scheduled_tasks_land_on_consumed_queues(self, prod_compose: Path) -> None:
        try:
            from nfm_db.services.celery_app import celery_app
        except Exception:
            pytest.skip("Celery app not available in test environment")

        schedule = celery_app.conf.beat_schedule
        assert schedule, (
            "celery_app.conf.beat_schedule must not be empty — NFM-4539 "
            "AC-3 and the 30s HPC sync both depend on it."
        )
        worker_queues = _worker_queues(prod_compose)
        for name, entry in schedule.items():
            assert isinstance(entry, dict), (
                f"beat_schedule[{name!r}] must be a mapping, got {type(entry).__name__}."
            )
            task_name = str(entry.get("task", ""))
            queue = _entry_queue(task_name, entry, celery_app.conf)
            assert queue in worker_queues, (
                f"{prod_compose}: beat schedules {name!r} "
                f"(task {task_name!r}) onto queue {queue!r}, but the "
                f"worker only consumes {sorted(worker_queues)!r}. The "
                f"messages accumulate on the broker with no consumer — "
                f"add {queue!r} to the worker's `--queues` flag or route "
                f"the task to a consumed queue."
            )


# ---------------------------------------------------------------------------
# Defensive: missing file / empty file / unknown shape / tag-stripping
# ---------------------------------------------------------------------------


class TestDefensiveLoadHelpers:
    """Cover the loader's defensive branches so a future regression does not
    silently change error semantics (NFM-4617-B2 review checklist).
    """

    def test_missing_compose_file_is_skipped(self, tmp_path: Path) -> None:
        """A non-existent path short-circuits with ``pytest.skip``."""
        with pytest.raises(pytest.skip.Exception):
            _load_yaml(tmp_path / "does-not-exist.yml")

    def test_empty_compose_file_loads_to_none(self, tmp_path: Path) -> None:
        """An empty YAML file parses to ``None`` — surfaced as a hard fail."""
        empty = tmp_path / "empty.yml"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(AssertionError, match="top-level YAML must be a mapping"):
            _load_yaml(empty)

    def test_top_level_non_mapping_is_rejected(self, tmp_path: Path) -> None:
        """A YAML whose top-level is a list (not a mapping) fails loudly."""
        bad = tmp_path / "list-root.yml"
        bad.write_text("- one\n- two\n", encoding="utf-8")
        with pytest.raises(AssertionError, match="top-level YAML must be a mapping"):
            _load_yaml(bad)

    def test_render_command_handles_string_form(self) -> None:
        """Command-as-string round-trips unchanged."""
        assert _render_command("celery -A x:y beat") == "celery -A x:y beat"

    def test_render_command_handles_list_form(self) -> None:
        """Command-as-list joins tokens with a single space."""
        assert _render_command(["celery", "-A", "x:y", "beat"]) == "celery -A x:y beat"

    def test_render_command_handles_none(self) -> None:
        """A missing ``command:`` key renders to empty string."""
        assert _render_command(None) == ""

    def test_worker_queues_parses_flag(self, tmp_path: Path) -> None:
        """The ``--queues`` flag parses into a queue-name set."""
        path = tmp_path / "queues.yml"
        path.write_text(
            "services:\n"
            "  worker:\n"
            '    command: "python -m celery -A x:y worker --queues=a,b --loglevel=INFO"\n',
            encoding="utf-8",
        )
        assert _worker_queues(path) == {"a", "b"}

    def test_worker_queues_without_flag_is_rejected(self, tmp_path: Path) -> None:
        """A worker command without ``--queues`` fails the loader."""
        path = tmp_path / "no-queues.yml"
        path.write_text(
            'services:\n  worker:\n    command: "python -m celery -A x:y worker --loglevel=INFO"\n',
            encoding="utf-8",
        )
        with pytest.raises(AssertionError, match="must declare `--queues="):
            _worker_queues(path)


# ---------------------------------------------------------------------------
# Regression fixtures: prove the guards above CATCH the failures they
# claim to catch, by invoking the real guard methods against synthetic
# compose files.
# ---------------------------------------------------------------------------


class TestRegressionFixtures:
    """Run the real guard methods against synthetic compose files.

    These tests call ``TestProdComposeCeleryBeat`` and
    ``TestBeatTaskQueueRouting`` methods directly (they only take the
    compose path), so a future weakening of any guard assertion — e.g.
    switching to a permissive ``in`` check or dropping a queue from the
    routing resolution — trips CI here before it lands. The synthetic
    files live under ``tmp_path``; the real prod compose is untouched.
    """

    @pytest.fixture
    def no_beat_compose(self, tmp_path: Path) -> Path:
        """Synthetic prod compose with a worker service but no beat service."""
        path = tmp_path / "docker-compose.prod.yml"
        path.write_text(
            "services:\n"
            "  worker:\n"
            f'    image: "{_WORKER_IMAGE}"\n'
            '    command: "python -m celery -A nfm_db.services.celery_app:celery_app worker '
            '--queues=md_verification,literature_processing,default,celery --loglevel=INFO"\n',
            encoding="utf-8",
        )
        return path

    @pytest.fixture
    def good_beat_compose(self, tmp_path: Path) -> Path:
        """Synthetic prod compose that declares a beat service correctly."""
        path = tmp_path / "docker-compose.prod.yml"
        path.write_text(
            "services:\n"
            "  worker:\n"
            f'    image: "{_WORKER_IMAGE}"\n'
            '    command: "python -m celery -A nfm_db.services.celery_app:celery_app worker '
            '--queues=md_verification,literature_processing,default,celery --loglevel=INFO"\n'
            "  beat:\n"
            f'    image: "{_WORKER_IMAGE}"\n'
            '    container_name: "nucpot-prod-beat"\n'
            "    depends_on:\n"
            "      redis: { condition: service_healthy }\n"
            '    command: "celery -A nfm_db.services.celery_app:celery_app beat --loglevel=info"\n'
            "    healthcheck:\n"
            '      test: ["CMD-SHELL", "python -c \\"import redis; redis.Redis(host=\'nucpot-prod-redis\', port=6379).ping()\\" || exit 1"]\n'
            "      interval: 60s\n"
            "      retries: 3\n",
            encoding="utf-8",
        )
        return path

    @pytest.fixture
    def broken_healthcheck_compose(self, tmp_path: Path) -> Path:
        """Synthetic prod compose that uses the broken worker-targeted probe.

        Mirrors the original NFM-4617-B2 healthcheck that copy-pasted the
        worker's ``celery inspect ping`` probe onto beat. Every other
        field is well-formed, so the healthcheck guard is the only one
        that fails — proving the failure comes from the probe itself.
        """
        path = tmp_path / "docker-compose.prod.yml"
        path.write_text(
            "services:\n"
            "  worker:\n"
            f'    image: "{_WORKER_IMAGE}"\n'
            '    command: "python -m celery -A nfm_db.services.celery_app:celery_app worker '
            '--queues=md_verification,literature_processing,default,celery --loglevel=INFO"\n'
            "  beat:\n"
            f'    image: "{_WORKER_IMAGE}"\n'
            '    container_name: "nucpot-prod-beat"\n'
            "    depends_on:\n"
            "      redis: { condition: service_healthy }\n"
            '    command: "celery -A nfm_db.services.celery_app:celery_app beat --loglevel=info"\n'
            "    healthcheck:\n"
            '      test: ["CMD-SHELL", "python -m celery -A nfm_db.services.celery_app:celery_app '
            'inspect ping -d celery@$$HOSTNAME || exit 1"]\n'
            "      interval: 60s\n"
            "      retries: 3\n",
            encoding="utf-8",
        )
        return path

    @pytest.fixture
    def unconsumed_queue_compose(self, tmp_path: Path) -> Path:
        """Synthetic prod compose whose worker omits a scheduled task's queue.

        The real ``beat_schedule`` routes the daily audit onto ``default``
        and leaves the 30s HPC sync on Celery's ``celery`` queue, so a
        worker consuming only ``md_verification`` starves both — the
        exact regression this fixture pins.
        """
        path = tmp_path / "docker-compose.prod.yml"
        path.write_text(
            "services:\n"
            "  worker:\n"
            f'    image: "{_WORKER_IMAGE}"\n'
            '    command: "python -m celery -A nfm_db.services.celery_app:celery_app worker '
            '--queues=md_verification --loglevel=INFO"\n',
            encoding="utf-8",
        )
        return path

    def test_missing_beat_is_rejected(self, no_beat_compose: Path) -> None:
        """A compose without a ``beat`` service fails the declaration guard."""
        guards = TestProdComposeCeleryBeat()
        with pytest.raises((AssertionError, pytest.fail.Exception)):
            guards.test_beat_service_is_declared(no_beat_compose)
        with pytest.raises((AssertionError, pytest.fail.Exception)):
            guards.test_beat_command_runs_celery_beat(no_beat_compose)

    def test_correct_beat_passes(self, good_beat_compose: Path) -> None:
        """A compose with a correctly-shaped ``beat`` service passes every guard."""
        guards = TestProdComposeCeleryBeat()
        guards.test_beat_service_is_declared(good_beat_compose)
        guards.test_beat_command_runs_celery_beat(good_beat_compose)
        guards.test_beat_targets_the_same_celery_app_as_worker(good_beat_compose)
        guards.test_beat_reuses_worker_image(good_beat_compose)
        guards.test_beat_has_container_name(good_beat_compose)
        guards.test_beat_depends_on_redis(good_beat_compose)
        guards.test_beat_healthcheck_is_valid(good_beat_compose)

    def test_broken_healthcheck_is_rejected(self, broken_healthcheck_compose: Path) -> None:
        """A beat with a worker-targeted probe fails the healthcheck guard.

        Pins the original NFM-4617-B2 regression: a future re-introduction
        of the copy-pasted ``celery inspect ping`` probe must trip the
        real ``test_beat_healthcheck_is_valid`` guard. All sibling guards
        still pass on this fixture, so the failure is attributable to the
        probe alone.
        """
        guards = TestProdComposeCeleryBeat()
        guards.test_beat_service_is_declared(broken_healthcheck_compose)
        guards.test_beat_command_runs_celery_beat(broken_healthcheck_compose)
        guards.test_beat_targets_the_same_celery_app_as_worker(broken_healthcheck_compose)
        guards.test_beat_reuses_worker_image(broken_healthcheck_compose)
        guards.test_beat_has_container_name(broken_healthcheck_compose)
        guards.test_beat_depends_on_redis(broken_healthcheck_compose)
        with pytest.raises(AssertionError, match="inspect ping"):
            guards.test_beat_healthcheck_is_valid(broken_healthcheck_compose)

    def test_worker_missing_scheduled_queue_is_rejected(
        self, unconsumed_queue_compose: Path
    ) -> None:
        """A worker that drops a scheduled task's queue fails the routing guard.

        Pins the routing regression where beat published onto queues the
        sole worker never consumed: the real
        ``test_beat_scheduled_tasks_land_on_consumed_queues`` guard must
        reject a ``--queues`` list that starves any scheduled task.
        """
        try:
            from nfm_db.services.celery_app import celery_app  # noqa: F401
        except Exception:
            pytest.skip("Celery app not available in test environment")
        with pytest.raises(AssertionError, match="only consumes"):
            TestBeatTaskQueueRouting().test_beat_scheduled_tasks_land_on_consumed_queues(
                unconsumed_queue_compose
            )
