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
``beat`` command, or rebinds the service to a different image, this test
fails on the matching compose file with the offending value quoted
verbatim. CI runs the test on every PR touching ``docker-compose.prod.yml``
(NFM-4481 review pattern).
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


# ---------------------------------------------------------------------------
# Core invariant: the prod compose declares a beat service that schedules
# Celery against the same app module the worker uses.
# ---------------------------------------------------------------------------


class TestProdComposeCeleryBeat:
    """docker-compose.prod.yml MUST declare a `beat` Celery scheduler."""

    def test_beat_service_is_declared(self) -> None:
        """The prod compose must declare a top-level ``beat:`` service.

        NFM-4539 AC-3 requires the daily ``rag_audit_index_coverage`` audit
        to actually fire. Without a ``beat`` scheduler container, the
        registered ``beat_schedule`` is silently inert and the metric
        ``lit_indexed_total`` stays at zero (NFM-4617-B2 evidence:
        ``lit_completed_total=15``, ``lit_indexed_total=0``).
        """
        beat = _beat_service(PROD_COMPOSE)
        assert beat is not None, (
            f"{PROD_COMPOSE}: `services.beat` is missing entirely. "
            f"NFM-4539 AC-3 requires a Celery `beat` scheduler container "
            f"running `celery -A nfm_db.services.celery_app:celery_app "
            f"beat` against the same broker (redis) the worker uses. Add "
            f"a `beat:` service to this compose file."
        )

    def test_beat_command_runs_celery_beat(self) -> None:
        """The ``beat`` command must invoke ``celery ... beat``.

        Distinguishes a real scheduler from a misconfigured worker (the
        current prod shape — a single ``worker`` service that never
        schedules). Anything that does not include the literal ``beat``
        token in the command is treated as a regression.
        """
        beat = _beat_service(PROD_COMPOSE)
        if beat is None:
            pytest.fail(
                f"{PROD_COMPOSE}: no `beat` service declared; cannot "
                f"inspect its command. See test_beat_service_is_declared."
            )
        rendered = _render_command(beat.get("command"))
        assert rendered, (
            f"{PROD_COMPOSE}: `services.beat` has no `command:` — the "
            f"scheduler image defaults to the Dockerfile CMD, which "
            f"starts the API. Set `command:` to "
            f"`celery -A nfm_db.services.celery_app:celery_app beat "
            f"--loglevel=info`."
        )
        assert "celery" in rendered, (
            f"{PROD_COMPOSE}: `services.beat.command` does not invoke "
            f"`celery`: {rendered!r}. NFM-4617-B2 requires the Celery "
            f"beat scheduler against nfm_db.services.celery_app:celery_app."
        )
        assert "beat" in rendered, (
            f"{PROD_COMPOSE}: `services.beat.command` does not include "
            f"the `beat` subcommand: {rendered!r}. A beat-shaped command "
            f"without the `beat` token is a worker, not a scheduler."
        )

    def test_beat_targets_the_same_celery_app_as_worker(self) -> None:
        """The ``-A`` module path must point at the worker's celery_app.

        ``apps/api/src/nfm_db/services/celery_app.py`` registers
        ``beat_schedule`` at module import. If beat targets a different
        app object (e.g. ``nfm_db.celery_app`` — the example string in
        the NFM-4617 issue is wrong), the scheduled tasks never fire
        because the worker reads schedules off its own ``celery_app``.
        """
        beat = _beat_service(PROD_COMPOSE)
        if beat is None:
            pytest.fail(
                f"{PROD_COMPOSE}: no `beat` service declared; cannot "
                f"verify celery_app target."
            )
        rendered = _render_command(beat.get("command"))
        assert _CELERY_APP_TARGET in rendered, (
            f"{PROD_COMPOSE}: `services.beat.command` does not target "
            f"{_CELERY_APP_TARGET!r}: got {rendered!r}. The worker uses "
            f"this exact module; beat must use the same one or the "
            f"`beat_schedule` registered at import time is invisible to "
            f"the scheduler."
        )

    def test_beat_reuses_worker_image(self) -> None:
        """The ``beat`` service must reuse the worker's image, not build a new one.

        Per NFM-4617-B2, beat is a deployment-config edit, not a build
        change. Adding a parallel Dockerfile would be a governance
        signal — ADR-013 G2 forbids weakening controls, and a new image
        target is the kind of change SRE must gate. Mirror
        ``nucpot-prod-api:${PROD_IMAGE_TAG:-latest}`` so the deploy stays
        atomic.
        """
        beat = _beat_service(PROD_COMPOSE)
        if beat is None:
            pytest.fail(
                f"{PROD_COMPOSE}: no `beat` service declared; cannot "
                f"verify image reuse."
            )
        image = beat.get("image")
        assert image == _WORKER_IMAGE, (
            f"{PROD_COMPOSE}: `services.beat.image` must reuse the "
            f"worker image ({_WORKER_IMAGE!r}) to avoid introducing a "
            f"parallel build target. Got {image!r}."
        )

    def test_beat_has_container_name(self) -> None:
        """The ``beat`` service must declare ``container_name: nucpot-prod-beat``.

        Compose fails loudly when two services share a container_name on
        the same network. Pinning the name protects against accidental
        duplication and makes ``docker ps | grep beat`` deterministic for
        the NFM-4539 AC-3 acceptance evidence.
        """
        beat = _beat_service(PROD_COMPOSE)
        if beat is None:
            pytest.fail(
                f"{PROD_COMPOSE}: no `beat` service declared; cannot "
                f"verify container_name."
            )
        name = beat.get("container_name")
        assert name == "nucpot-prod-beat", (
            f"{PROD_COMPOSE}: `services.beat.container_name` must be "
            f"`nucpot-prod-beat` so the prod stack has a single "
            f"naming root (api/worker/redis/db/lightrag/web/beat). "
            f"Got {name!r}."
        )

    def test_beat_depends_on_redis(self) -> None:
        """The ``beat`` service must depend on ``redis`` (Celery broker).

        Without a healthy broker the scheduler queues messages into the
        void and ``rag_audit_index_coverage`` silently never runs.
        ``depends_on: redis: { condition: service_healthy }`` matches the
        worker service's existing declaration.
        """
        beat = _beat_service(PROD_COMPOSE)
        if beat is None:
            pytest.fail(
                f"{PROD_COMPOSE}: no `beat` service declared; cannot "
                f"verify depends_on."
            )
        depends = beat.get("depends_on")
        if depends is None:
            pytest.fail(
                f"{PROD_COMPOSE}: `services.beat.depends_on` is missing. "
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
                f"{PROD_COMPOSE}: `services.beat.depends_on` must be a "
                f"list or mapping, got {type(depends).__name__}."
            )
        assert "redis" in keys, (
            f"{PROD_COMPOSE}: `services.beat.depends_on` must include "
            f"`redis` (the Celery broker). Got keys {keys!r}."
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


# ---------------------------------------------------------------------------
# Regression fixture: prove the test CATCHES a missing beat service
# ---------------------------------------------------------------------------


class TestRegressionFixtures:
    """Inject a missing-beat compose into a tmp_path and confirm the test fires.

    These run only against synthetic in-memory files; the real prod
    compose file is left untouched. They exist so a future refactor that
    silently weakens the assertion (e.g. by switching to a permissive
    ``in`` check) is caught by CI before it lands.
    """

    @pytest.fixture
    def no_beat_compose(self, tmp_path: Path) -> Path:
        """Synthetic prod compose with a worker service but no beat service."""
        path = tmp_path / "docker-compose.prod.yml"
        path.write_text(
            "services:\n"
            "  worker:\n"
            '    image: "nucpot-prod-api:1"\n'
            '    command: "celery -A nfm_db.services.celery_app:celery_app worker"\n',
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
            '    image: "nucpot-prod-api:1"\n'
            "  beat:\n"
            '    image: "nucpot-prod-api:1"\n'
            '    container_name: "nucpot-prod-beat"\n'
            "    depends_on:\n"
            '      redis: { condition: service_healthy }\n'
            '    command: "celery -A nfm_db.services.celery_app:celery_app beat --loglevel=info"\n',
            encoding="utf-8",
        )
        return path

    def test_missing_beat_is_rejected(self, no_beat_compose: Path) -> None:
        """A compose without a ``beat`` service fails the assertion."""
        beat = _beat_service(no_beat_compose)
        assert beat is None, (
            "fixture must omit the `beat` service to be a valid "
            f"regression case; got {beat!r}"
        )

    def test_correct_beat_passes(self, good_beat_compose: Path) -> None:
        """A compose with a correctly-shaped ``beat`` service passes every check."""
        beat = _beat_service(good_beat_compose)
        assert beat is not None
        rendered = _render_command(beat.get("command"))
        assert "celery" in rendered and "beat" in rendered
        assert _CELERY_APP_TARGET in rendered
        assert beat.get("image") == "nucpot-prod-api:1"
        assert beat.get("container_name") == "nucpot-prod-beat"
        assert "redis" in (beat.get("depends_on") or {})
