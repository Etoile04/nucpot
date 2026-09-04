"""NFM-4269 — ADR-013 G1+G3 harness-layer prod-compose guard.

Tests the pure matcher (``tools/hermes-prod-guard/prod_guard.py``), the
Hermes plugin wrapper (``__init__.py``), and the config-belt globs
(``approvals_deny.yaml``) against the ADR-013 acceptance criteria.

The matcher and plugin are stdlib-only at import time (Hermes modules are
imported lazily inside functions) so these tests run in the plain nucpot
pytest environment without a Hermes install.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "tools" / "hermes-prod-guard"


def _load_prod_guard():
    sys.path.insert(0, str(PLUGIN_DIR))
    try:
        import prod_guard

        return prod_guard
    finally:
        sys.path.remove(str(PLUGIN_DIR))


def _load_plugin_package():
    """Load the plugin ``__init__.py`` the way the Hermes loader does."""
    pkg_name = "nfm_prod_guard_plugin_under_test"
    spec = importlib.util.spec_from_file_location(
        pkg_name, PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[pkg_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pg():
    return _load_prod_guard()


@pytest.fixture(scope="module")
def plugin():
    return _load_plugin_package()


# ---------------------------------------------------------------------------
# G1 — the incident command and its close variants must be refused
# ---------------------------------------------------------------------------

INCIDENT_COMMAND = (
    "docker compose --env-file docker/.env.prod up -d --build api web"
)

BLOCK_CASES = [
    # (label, command)
    ("AC1: exact NFM-4264 00:05 command", INCIDENT_COMMAND),
    ("legacy docker-compose spelling",
     "docker-compose --env-file docker/.env.prod up -d --build api web"),
    ("-f compose file, marker before verb",
     "docker compose -f docker-compose.prod.yml up -d"),
    ("-f compose file, verb before marker",
     "docker compose up -d --build -f docker-compose.prod.yml"),
    ("project name flag",
     "docker compose -p nucpot-prod up -d"),
    ("project name long form",
     "docker compose --project-name nucpot-prod up -d --build"),
    ("COMPOSE_PROJECT_NAME env assignment",
     "COMPOSE_PROJECT_NAME=nucpot-prod docker compose up -d"),
    ("PROD_IMAGE_TAG prefix assignment (drift-watchdog fix recipe)",
     "PROD_IMAGE_TAG=deadbeef docker compose -f docker-compose.prod.yml "
     "--env-file docker/.env.prod up -d"),
    ("chained after cd",
     "cd ~/Projects/nucpot && docker compose --env-file docker/.env.prod "
     "up -d --build api web"),
    ("sudo wrapper",
     "sudo docker compose -f docker-compose.prod.yml up -d --build"),
    ("env wrapper",
     "env PROD_IMAGE_TAG=x docker compose -f docker-compose.prod.yml up -d"),
    ("nohup wrapper",
     "nohup docker compose --env-file docker/.env.prod up -d &"),
    ("timeout-wrapped compose restart",
     "timeout 60 docker compose -f docker-compose.prod.yml restart api"),
    ("env -i wrapped compose up",
     "env -i docker compose -f docker-compose.prod.yml up -d"),
    ("nice-wrapped compose down",
     "nice -n 5 docker compose --env-file docker/.env.prod down"),
    ("stdbuf-wrapped bare docker stop",
     "stdbuf -oL sudo docker stop nucpot-prod-api"),
    ("stdbuf attached-value forms (regression: out[2:] mispairing)",
     "stdbuf -oL docker compose -f docker-compose.prod.yml up -d"),
    ("stdbuf separate-value forms",
     "stdbuf -o L docker compose -f docker-compose.prod.yml up -d"),
    ("stdbuf mixed attached forms",
     "stdbuf -i0 -oL -eL docker compose --env-file docker/.env.prod up -d"),
    # aecb57d3 deploy-identity marker contract (NFM-4274 SPEC-BINDING):
    # in-session sudo elevation is NOT a chokepoint; no env marker honored.
    ("sudo -u deploy-identity wrapper (aecb57d3, NFM-4274)",
     "sudo -u nucpot-deploy docker compose -f docker-compose.prod.yml "
     "up -d"),
    ("sudo --user= deploy-identity long form",
     "sudo --user nucpot-deploy docker compose --env-file docker/.env.prod "
     "up -d --build"),
    ("env-marker prefix is not a carve-out (no settable token)",
     "NFM_SANCTIONED=1 docker compose --env-file docker/.env.prod up -d"),
    ("NFM-1664-style marker prefix also refused",
     "NFM1664_RECOVERY=1 docker compose -f docker-compose.prod.yml "
     "up -d --build"),
    ("piped into tee",
     "docker compose -f docker-compose.prod.yml up -d | tee /tmp/deploy.log"),
    ("subshell-wrapped",
     "echo deploying && $(docker compose --env-file docker/.env.prod up -d)"),
    ("backtick-wrapped",
     "echo `docker compose --env-file docker/.env.prod up -d`"),
    ("mutation verb: down",
     "docker compose -f docker-compose.prod.yml down"),
    ("mutation verb: down with env-file",
     "docker compose --env-file docker/.env.prod down -v"),
    ("mutation verb: build",
     "docker compose -f docker-compose.prod.yml build api"),
    ("mutation verb: rebuild",
     "docker compose --env-file docker/.env.prod rebuild web"),
    ("mutation verb: restart",
     "docker compose -f docker-compose.prod.yml restart api"),
    ("mutation verb: stop",
     "docker compose --env-file docker/.env.prod stop"),
    ("mutation verb: start",
     "docker compose -f docker-compose.prod.yml start api"),
    ("mutation verb: rm",
     "docker compose -f docker-compose.prod.yml rm -f api"),
    ("mutation verb: kill",
     "docker compose --env-file docker/.env.prod kill api"),
    ("mutation verb: run",
     "docker compose -f docker-compose.prod.yml run --rm api ls"),
    ("mutation verb: exec",
     "docker compose --env-file docker/.env.prod exec api sh -c 'true'"),
    ("NFM-1664 recovery shape via Hermes terminal (must route elsewhere)",
     "docker compose -f docker-compose.prod.yml up -d --build"),
    # bare docker container mutations on prod containers
    ("bare docker stop on prod container",
     "docker stop nucpot-prod-api"),
    ("bare docker rm on prod container",
     "docker rm -f nucpot-prod-web"),
    ("bare docker restart on prod container",
     "docker restart nucpot-prod-worker"),
    ("bare docker kill on prod container",
     "docker kill nucpot-prod-db"),
    ("bare docker exec on prod container",
     "docker exec -it nucpot-prod-api sh -c 'true'"),
    ("bare docker container-stop spelling",
     "docker container stop nucpot-prod-api"),
    ("bare docker container-restart spelling",
     "docker container restart nucpot-prod-api"),
    ("bare docker container-rm spelling",
     "docker container rm nucpot-prod-web"),
    ("bare docker container-exec spelling",
     "docker container exec nucpot-prod-api ls"),
    ("bare docker stop, chained and sudoed",
     "cd ~ && sudo docker stop nucpot-prod-api nucpot-prod-web"),
    ("bare docker stop in subshell",
     "echo x && $(docker stop nucpot-prod-api)"),
    # terminal-vector writes to prod compose/env files
    ("redirect overwrite compose file",
     "echo 'services: {}' > docker-compose.prod.yml"),
    ("redirect append env file",
     "echo 'PROD_IMAGE_TAG=x' >> docker/.env.prod"),
    ("fd-redirect to compose file",
     "cat /tmp/new.yml 1> docker-compose.prod.yml"),
    ("tee compose file",
     "echo 'services: {}' | tee docker-compose.prod.yml"),
    ("tee -a env file",
     "echo 'X=1' | tee -a docker/.env.prod"),
    ("sed in-place compose file",
     "sed -i 's/5433/5434/' docker-compose.prod.yml"),
    ("sed -i.bak env file",
     "sed -i.bak 's/x/y/' docker/.env.prod"),
    ("cp destination is compose file",
     "cp /tmp/fixed-compose.yml docker-compose.prod.yml"),
    ("mv destination is env file",
     "mv /tmp/new-env docker/.env.prod"),
    ("absolute-path redirect",
     "echo x > /Users/lwj04/Projects/nucpot/docker-compose.prod.yml"),
    ("home-tilde redirect",
     "echo x > ~/Projects/nucpot/docker-compose.prod.yml"),
    ("clobber-redirect (>|) compose file",
     "echo 'services: {}' >| docker-compose.prod.yml"),
    ("zsh clobber-redirect (>!) compose file",
     "echo 'services: {}' >! docker-compose.prod.yml"),
    ("fd clobber-redirect (2>|) compose file",
     "docker compose config 2>| docker-compose.prod.yml"),
    # NFM-4284 N1 — backslash-escape obfuscation (CR bb0bbb95): an
    # unquoted escape (docker-compose\.prod\.yml) hides the marker from
    # raw-substring detection; shlex has already unescaped the segment
    # words, so marker detection must scan those words too.
    ("N1: CR probe — escaped compose marker",
     "docker compose -f docker-compose\\.prod\\.yml up -d"),
    ("N1: escaped marker, verb before marker",
     "docker compose up -d --build -f docker-compose\\.prod\\.yml"),
    ("N1: escaped marker, legacy docker-compose head",
     "docker-compose -f docker-compose\\.prod\\.yml up -d --build"),
    ("N1: partially escaped marker (first dot)",
     "docker compose -f docker-compose\\.prod.yml up -d"),
    ("N1: partially escaped marker (second dot)",
     "docker compose -f docker-compose.prod\\.yml down"),
    ("N1: escaped env-file marker",
     "docker compose --env-file docker/.env\\.prod up -d --build api web"),
    ("N1: escaped project name",
     "docker compose -p nucpot\\-prod up -d"),
    ("N1: escaped head spelling",
     "docker\\-compose -f docker-compose.prod.yml up -d"),
    ("N1: escaped head word",
     "\\docker compose -f docker-compose.prod.yml up -d"),
    ("N1: escaped verb (shlex unescapes; finite verb set)",
     "docker compose -f docker-compose.prod.yml u\\p -d"),
    ("N1: escaped redirect target (write vector)",
     "echo 'services: {}' > docker-compose\\.prod\\.yml"),
    ("N1: escaped tee target (write vector)",
     "echo 'X=1' | tee docker/.env\\.prod"),
    ("N1: escaped cp destination (write vector)",
     "cp /tmp/fixed.yml docker-compose\\.prod\\.yml"),
]


@pytest.mark.parametrize("label,command", BLOCK_CASES, ids=[c[0] for c in BLOCK_CASES])
def test_command_blocked(pg, label, command):
    verdict = pg.evaluate_command(command)
    assert verdict is not None, f"expected block for: {label!r}: {command!r}"
    assert "production-deployment.yml" in verdict.reason
    assert "deploy_prod.sh" in verdict.reason


# ---------------------------------------------------------------------------
# G1 — read-only and sanctioned paths must NOT be blocked
# ---------------------------------------------------------------------------

ALLOW_CASES = [
    ("AC2: compose config render", "docker compose -f docker-compose.prod.yml config"),
    ("compose config with env-file", "docker compose --env-file docker/.env.prod config"),
    ("compose config verb before marker",
     "docker compose config -f docker-compose.prod.yml"),
    ("compose ps", "docker compose -p nucpot-prod ps"),
    ("compose logs", "docker compose -f docker-compose.prod.yml logs --tail 50 api"),
    ("docker ps", "docker ps"),
    ("docker ps filtered on prod name", "docker ps --filter name=nucpot-prod-api"),
    ("docker inspect prod container", "docker inspect nucpot-prod-api"),
    ("docker logs prod container", "docker logs --tail 100 nucpot-prod-api"),
    ("docker stats", "docker stats nucpot-prod-api"),
    ("docker container ls", "docker container ls"),
    ("docker container inspect prod container",
     "docker container inspect nucpot-prod-api"),
    ("docker exec non-prod container", "docker exec nucpot-staging-api ls"),
    ("timeout-wrapped read-only compose config",
     "timeout 30 docker compose -f docker-compose.prod.yml config"),
    ("env -i wrapped docker ps", "env -i docker ps"),
    ("cat prod compose file", "cat docker-compose.prod.yml"),
    ("cat prod env file", "cat docker/.env.prod"),
    ("grep prod compose file",
     "grep -n 'prod-uploads' docker-compose.prod.yml | head -6"),
    ("AC3a: deploy_prod.sh invocation (sanctioned path)",
     "bash scripts/deploy_prod.sh"),
    ("AC3a: deploy_prod.sh with args", "sh scripts/deploy_prod.sh --skip-verify"),
    ("staging compose up (different stack)",
     "docker compose -f docker-compose.staging.yml --env-file docker/.env.staging "
     "up -d --build api web"),
    ("dev compose up at repo root", "docker compose up -d"),
    ("docker build prod image (no compose verb)",
     "docker build -f docker/prod-api.Dockerfile -t nucpot-prod-api:latest ."),
    ("docker image prune (NFM-4257 cron surface)",
     "docker image prune -af --filter until=72h"),
    ("docker stop non-prod container", "docker stop nucpot-staging-api"),
    ("docker rm non-prod container", "docker rm some-sandbox"),
    ("docker logs non-prod", "docker logs nucpot-staging-api"),
    ("read redirect FROM prod file (source is prod, dest is tmp)",
     "docker compose -f docker-compose.prod.yml config > /tmp/render.yml"),
    ("cp FROM prod file (prod is the source)",
     "cp docker-compose.prod.yml /tmp/backup.yml"),
    ("mv FROM prod file to tmp", "mv docker-compose.prod.yml /tmp/old.yml"),
    ("env.example read", "cat docker/.env.prod.example"),
    ("redirect to env.example template",
     "echo 'PROD_IMAGE_TAG=v2' > docker/.env.prod.example"),
    ("cp to env.example template", "cp /tmp/env docker/.env.prod.example"),
    ("tee env.example template",
     "echo 'X=1' | tee docker/.env.prod.example"),
    ("grep prose containing verb words (quoted data)",
     "grep 'docker compose up' docs/README.md"),
    ("git diff on prod compose file",
     "git diff HEAD -- docker-compose.prod.yml"),
    ("git log on prod compose file", "git log -3 -- docker-compose.prod.yml"),
    ("watchdog script invocation (reads only)",
     "bash ~/.hermes/scripts/prod-drift-watchdog.sh"),
    # NFM-4284 N2 — NFM-1664 AC3 recovery carve-out: bare `docker start` on
    # a prod container is allowed (`start` is a compose mutation verb but
    # not a bare-docker container verb); pin it so a future verb-scope
    # change cannot silently break the recovery path.
    ("N2/NFM-1664 AC3: bare docker start on prod container",
     "docker start nucpot-prod-api-1"),
    # NFM-4284 N1 — quote-literal backslashes name a DIFFERENT (nonexistent)
    # file: the argv keeps the backslashes, so this is not the prod stack
    # and must stay allowed (shell-accurate precision boundary).
    ("N1: quoted-literal escaped marker names a different file",
     "docker compose -f 'docker-compose\\.prod\\.yml' config"),
    ("N1: escaped staging marker stays allowed",
     "docker compose -f docker-compose\\.staging\\.yml up -d --build"),
    ("empty command", ""),
    ("plain ls", "ls -la"),
]


@pytest.mark.parametrize("label,command", ALLOW_CASES, ids=[c[0] for c in ALLOW_CASES])
def test_command_allowed(pg, label, command):
    verdict = pg.evaluate_command(command)
    assert verdict is None, (
        f"expected allow for: {label!r}: {command!r}, got block: "
        f"{getattr(verdict, 'reason', verdict)}"
    )


# ---------------------------------------------------------------------------
# G1 — patch/write_file targets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "docker-compose.prod.yml",
    "/Users/lwj04/Projects/nucpot/docker-compose.prod.yml",
    "~/Projects/nucpot/docker-compose.prod.yml",
    "docker/.env.prod",
    "/Users/lwj04/Projects/nucpot/docker/.env.prod",
    "./docker/.env.prod",
])
def test_write_target_blocked(pg, path):
    verdict = pg.evaluate_write_target(path)
    assert verdict is not None
    assert "production-deployment.yml" in verdict.reason


@pytest.mark.parametrize("path", [
    "docker-compose.staging.yml",
    "docker-compose.yml",
    "docker/.env.prod.example",
    "docker/.env.staging",
    "src/app/main.py",
    "docs/adr/ADR-013-NFM-4266-prod-mutation-guardrails.md",
])
def test_write_target_allowed(pg, path):
    assert pg.evaluate_write_target(path) is None


# ---------------------------------------------------------------------------
# G3 — prod-touching detection for success-path logging
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command,expected", [
    ("docker compose -f docker-compose.prod.yml config", True),
    ("docker inspect nucpot-prod-api", True),
    ("cat docker/.env.prod", True),
    (INCIDENT_COMMAND, True),
    ("docker compose -f docker-compose.staging.yml config", False),
    # NFM-4284 N1: escaped markers bound the G3 success log too (read-only
    # successes like `cat docker-compose\.prod\.yml` must be logged).
    ("cat docker-compose\\.prod\\.yml", True),
    ("docker compose -f docker-compose\\.staging\\.yml config", False),
    ("cat /etc/hosts", False),
    ("", False),
])
def test_is_prod_touching(pg, command, expected):
    assert pg.is_prod_touching(command) is expected


# ---------------------------------------------------------------------------
# Plugin wrapper — hook contract, fail-closed, logging
# ---------------------------------------------------------------------------

class _StubCtx:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, cb):
        self.hooks[name] = cb


def test_plugin_registers_both_hooks(plugin):
    ctx = _StubCtx()
    plugin.register(ctx)
    assert set(ctx.hooks) == {"pre_tool_call", "post_tool_call"}


def test_plugin_pre_tool_blocks_incident_command(plugin):
    ctx = _StubCtx()
    plugin.register(ctx)
    result = ctx.hooks["pre_tool_call"](
        tool_name="terminal", args={"command": INCIDENT_COMMAND})
    assert isinstance(result, dict)
    assert result["action"] == "block"
    assert "production-deployment.yml" in result["message"]
    assert "Paperclip" in result["message"]


def test_plugin_pre_tool_allows_readonly(plugin):
    ctx = _StubCtx()
    plugin.register(ctx)
    assert ctx.hooks["pre_tool_call"](
        tool_name="terminal",
        args={"command": "docker compose -f docker-compose.prod.yml config"},
    ) is None


def test_plugin_pre_tool_gates_write_targets(plugin):
    ctx = _StubCtx()
    plugin.register(ctx)
    for tool in ("write_file", "patch"):
        result = ctx.hooks["pre_tool_call"](
            tool_name=tool,
            args={"path": "/Users/lwj04/Projects/nucpot/docker-compose.prod.yml",
                  "content": "services: {}"},
        )
        assert result and result["action"] == "block", tool


def test_plugin_pre_tool_ignores_other_tools(plugin):
    ctx = _StubCtx()
    plugin.register(ctx)
    assert ctx.hooks["pre_tool_call"](
        tool_name="read_file",
        args={"path": "docker-compose.prod.yml"},
    ) is None


def test_plugin_fail_closed_on_internal_error(plugin, monkeypatch):
    """A crashing matcher must BLOCK, not silently allow (ADR-013 fail-closed)."""
    ctx = _StubCtx()
    plugin.register(ctx)
    monkeypatch.setattr(
        plugin.prod_guard, "evaluate_command",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    result = ctx.hooks["pre_tool_call"](
        tool_name="terminal", args={"command": "echo hi"})
    assert result and result["action"] == "block"
    assert "fail-closed" in result["message"].lower()


def test_plugin_fail_closed_on_garbage_args(plugin):
    ctx = _StubCtx()
    plugin.register(ctx)
    # args must never crash the hook even when malformed
    for bad in (None, {}, {"command": None}, {"command": 42},
                {"command": "\x00\xffbroken"}):
        result = ctx.hooks["pre_tool_call"](tool_name="terminal", args=bad)
        assert result is None or (
            isinstance(result, dict) and result.get("action") == "block")


def test_plugin_post_tool_logs_prod_touching_success(plugin, caplog):
    """G3: successful prod-touching terminal calls are logged with full text."""
    ctx = _StubCtx()
    plugin.register(ctx)
    import logging
    with caplog.at_level(logging.INFO, logger=plugin.__name__):
        ctx.hooks["post_tool_call"](
            tool_name="terminal",
            args={"command": "docker compose -f docker-compose.prod.yml config"},
            result='{"output": "..."}',
            status="success",
            duration_ms=123,
        )
    joined = caplog.text
    assert "PROD-TOUCHING" in joined
    assert "docker compose -f docker-compose.prod.yml config" in joined
    assert "success" in joined


def test_plugin_post_tool_skips_non_touching(plugin, caplog):
    ctx = _StubCtx()
    plugin.register(ctx)
    import logging
    with caplog.at_level(logging.INFO, logger=plugin.__name__):
        ctx.hooks["post_tool_call"](
            tool_name="terminal",
            args={"command": "ls -la"},
            result="x", status="success", duration_ms=1,
        )
    assert "PROD-TOUCHING" not in caplog.text


def test_plugin_post_tool_skips_blocked(plugin, caplog):
    """Blocked calls are logged by the pre hook; post must not double-log."""
    ctx = _StubCtx()
    plugin.register(ctx)
    import logging
    with caplog.at_level(logging.INFO, logger=plugin.__name__):
        ctx.hooks["post_tool_call"](
            tool_name="terminal",
            args={"command": INCIDENT_COMMAND},
            result="blocked", status="blocked",
        )
    assert "PROD-TOUCHING terminal command (status=success" not in caplog.text


def test_plugin_redacts_secret_assignments(plugin, caplog):
    """G3 logging keeps security.redact_secrets semantics for env dumps."""
    ctx = _StubCtx()
    plugin.register(ctx)
    import logging
    with caplog.at_level(logging.INFO, logger=plugin.__name__):
        ctx.hooks["post_tool_call"](
            tool_name="terminal",
            args={"command":
                  "PROD_POSTGRES_PASSWORD=supersecretvalue docker compose "
                  "-f docker-compose.prod.yml config"},
            result="x", status="success", duration_ms=5,
        )
    assert "supersecretvalue" not in caplog.text
    assert "docker-compose.prod.yml" in caplog.text


# ---------------------------------------------------------------------------
# Config belt — approvals.deny globs (survives plugin loss/tampering)
# ---------------------------------------------------------------------------

def _belt_patterns():
    import yaml  # nucpot api env has pyyaml; tests run under uv
    data = yaml.safe_load((PLUGIN_DIR / "approvals_deny.yaml").read_text())
    entries = data["approvals"]["deny"]
    assert isinstance(entries, list) and entries
    return entries


def test_belt_blocks_incident_command():
    """The belt alone (fnmatch over the raw lowercased command) must catch
    the exact NFM-4264 incident command."""
    command = INCIDENT_COMMAND.lower()
    assert any(
        fnmatch.fnmatchcase(command, pat.lower())
        for pat in _belt_patterns()
    )


@pytest.mark.parametrize("command", [
    "docker compose -f docker-compose.prod.yml up -d --build",
    "docker-compose --env-file docker/.env.prod up -d --build api web",
    "cd ~/Projects/nucpot && docker compose --env-file docker/.env.prod up -d",
    "sudo docker compose -f docker-compose.prod.yml up -d",
    "docker compose -f docker-compose.prod.yml down",
    "docker compose --env-file docker/.env.prod down -v",
    "docker stop nucpot-prod-api",
    "docker rm -f nucpot-prod-web",
    "docker restart nucpot-prod-api",
    "docker container stop nucpot-prod-api",
    "docker exec -it nucpot-prod-api sh -c true",
    # aecb57d3 marker contract (NFM-4274): belt must also refuse in-session
    # deploy-identity elevation and env-marker prefixes.
    "sudo -u nucpot-deploy docker compose -f docker-compose.prod.yml up -d",
    "NFM_SANCTIONED=1 docker compose --env-file docker/.env.prod up -d",
    "echo 'services: {}' > docker-compose.prod.yml",
    "echo 'X=1' | tee -a docker/.env.prod",
    # NFM-4284 N1 — escape-obfuscated markers must hit the belt floor too
    # (the belt survives plugin loss/tampering, so it needs its own
    # escape-tolerant globs, not just the plugin's shlex unescaping).
    "docker compose -f docker-compose\\.prod\\.yml up -d",
    "docker-compose --env-file docker/.env\\.prod up -d --build api web",
    "docker compose -f docker-compose\\.prod.yml down",
    "docker compose -p nucpot\\-prod up -d",
    "docker compose up -d -f docker-compose\\.prod\\.yml",
    "echo 'services: {}' > docker-compose\\.prod\\.yml",
    "echo 'X=1' >> docker/.env\\.prod",
    # Belt-floor hardening — the same escape matrix the plugin carries,
    # so the belt alone (plugin removed/tampered) still blocks: escaped
    # write-vector destinations, the two-char escaped head separator,
    # and letter-escaped verb/marker spellings.
    "echo 'X=1' | tee docker/.env\\.prod",
    "cp /tmp/f docker-compose\\.prod\\.yml",
    "mv /tmp/backup.yml docker/.env\\.prod",
    "sed -i 's/image: old/image: new/' docker-compose\\.prod\\.yml",
    "install -m 644 /tmp/f docker/.env\\.prod",
    "docker\\-compose -f docker-compose.prod.yml up -d",
    "docker compose -f docker-compose.prod.yml u\\p -d",
    "docker compose -p nucpot-pro\\d up -d",
])
def test_belt_blocks_high_risk_variants(command):
    assert any(
        fnmatch.fnmatchcase(command.lower(), pat.lower())
        for pat in _belt_patterns()
    )


@pytest.mark.parametrize("command", [
    "docker compose -f docker-compose.prod.yml config",
    "docker compose -f docker-compose.prod.yml logs --tail 50 api",
    "cat docker-compose.prod.yml",
    "grep -n uploads docker-compose.prod.yml",
    "docker ps",
    "docker inspect nucpot-prod-api",
    "docker logs nucpot-prod-api",
    "docker container inspect nucpot-prod-api",
    "docker exec nucpot-staging-api ls",
    "bash scripts/deploy_prod.sh",
    "docker compose -f docker-compose.staging.yml up -d --build",
    # NFM-4284: escaped non-prod markers and the NFM-1664 recovery carve-out
    # stay allowed at the belt layer as well.
    "docker compose -f docker-compose\\.staging\\.yml up -d --build",
    "docker start nucpot-prod-api-1",
    "docker build -f docker/prod-api.Dockerfile -t nucpot-prod-api:latest .",
])
def test_belt_allows_readonly_and_sanctioned(command):
    hits = [pat for pat in _belt_patterns()
            if fnmatch.fnmatchcase(command.lower(), pat.lower())]
    assert not hits, f"belt false-positive on read-only/sanctioned: {hits}"


def test_belt_covers_plugin_matrix_reference(pg):
    """Every BLOCK_CASES command that the belt CLAIMS to cover (by design
    doc) isfnmatch-covered; the belt is the floor for the incident class,
    the plugin is the precise layer. This pins the belt's documented scope:
    up/down mutations, bare container verbs, and file writes."""
    belt = [p.lower() for p in _belt_patterns()]
    must_cover = [
        c for label, c in BLOCK_CASES
        if label.startswith(("AC1", "legacy", "-f", "project", "COMPOSE",
                             "chained", "sudo", "mutation verb: down",
                             "bare docker", "redirect", "tee", "sed", "cp ",
                             "mv "))
    ]
    missed = [c for c in must_cover
              if not any(fnmatch.fnmatchcase(c.lower(), p) for p in belt)]
    # The belt intentionally does NOT need to cover wrapper-rich variants
    # (nohup/env/subshell/backtick/piped) — the plugin does. Assert the core
    # incident class is fully covered:
    core = [c for c in missed if "nohup" not in c and "env " not in c
            and "$(" not in c and "`" not in c and "| tee" not in c
            and "PROD_IMAGE_TAG" not in c and "COMPOSE_PROJECT" not in c]
    assert not core, f"belt missed core incident-class commands: {core}"
