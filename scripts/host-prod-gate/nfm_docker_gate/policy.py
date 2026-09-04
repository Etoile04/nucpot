"""Request classification for the NFM-4270 (ADR-013 G2) docker gate.

The gate is an HTTP-over-unix-socket filtering proxy in front of the real
Docker daemon socket. Every client request's (method, path, query, body)
is classified here BEFORE any byte is forwarded upstream:

  * read-only endpoints          -> allow, no audit (AC-G2.2 frictionless)
  * image-layer mutations        -> allow + audit (candidate builds tag
                                    ``nucpot-prod-api:latest`` as the
                                    desktop user today; image ops cannot
                                    stop/rm a running prod container)
  * non-prod container mutations -> allow + audit (staging / autovc / e2e
                                    stacks share this single daemon)
  * prod-scoped mutations        -> DENY + audit (AC-G2 wall)
  * daemon-wide prunes, swarm/plugin control, exec/attach into prod,
    escape-hatch container configs -> DENY regardless of scope

"prod-scoped" is a name/label/network/volume match, not a docker-compose
file match: the proxy never sees which -f/--env-file the CLI was invoked
with, so scope is resolved from what the daemon itself knows (container
name, ``com.docker.compose.project`` label, network/volume names, attached
volume names). Opaque ids are resolved through an injected resolver
callback that asks the daemon (read-only GET) who the target actually is;
network ids have no resolver path, so opaque ones fail closed.

This module is pure logic: no sockets, no I/O. Everything here is
exercised by scripts/tests/test_nfm_docker_gate_policy.py.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Docker API paths arrive as /v1.43/containers/json or /containers/json.
_VERSION_PREFIX = re.compile(r"^/v[0-9]+\.[0-9]+/")
# /containers/{id}/json — {id} may contain [a-zA-Z0-9][a-zA-Z0-9_.-]*
_CONTAINER_SUB = re.compile(
    r"^containers/(?P<id>[^/]+)/(?P<action>json|logs|stats|top|changes|export|ports)$"
)
_CONTAINER_MUTATION = re.compile(
    r"^containers/(?P<id>[^/]+)/(?P<action>start|stop|kill|restart|pause|unpause|wait|update|rename|exec|attach|archive)$"
)
_IMAGE_NAME_ACTION = re.compile(r"^images/(?P<name>[^/]+)/(?P<action>tag|push)$")
_NETWORK_ACTION = re.compile(r"^networks/(?P<id>[^/]+)/(?P<action>connect|disconnect)$")

# GET endpoints the read-only context may hit. Anything else: denied
# (fail-closed — future daemon endpoints do not silently widen access).
_GET_ALLOW_EXACT = {
    "_ping",
    "version",
    "info",
    "events",
    "networks",
    "volumes",
    "images/json",
    "images/search",
    "images/get",
    "system/df",
    "containers/json",
}
_GET_ALLOW_PREFIX = (
    "networks/",
    "volumes/",
    "distribution/",
)

# Bind-mount sources that turn an arbitrary container into a host takeover.
_FORBIDDEN_BIND_RE = re.compile(
    r"(^|/)docker\.sock$|(^|/)podman\.sock$"
    r"|^/($|Users($|/)|private($|/)|etc($|/)|var($|/)|usr($|/)|bin($|/)|sbin($|/)|System($|/)|Library($|/))"
)

# Opaque daemon ids (container/network hex ids AND their unambiguous
# prefixes, which the daemon accepts at any length). A name matching this
# shape is scope-uncheckable from text alone; without a resolver verdict
# the request fails closed rather than guessing (same philosophy as
# unrecognized paths).
_OPAQUE_ID_RE = re.compile(r"^[0-9a-f]+$")

REFUSAL_HINT = (
    "nfm-g2 (NFM-4270 / ADR-013 G5): prod mutations route via GH Actions "
    "production-deployment.yml or scripts/deploy_prod.sh (run through "
    "/usr/local/lib/nfm-g2/run-deploy.sh); for deterministic recovery use "
    "run-recovery.sh. File a Paperclip issue first if neither fits."
)


def _matches(name: str, prefixes: tuple[str, ...]) -> bool:
    """A name is in scope if it equals a prefix or extends it with -/_."""
    base = name.lstrip("/")
    return any(
        base == prefix or base.startswith(prefix + "-") or base.startswith(prefix + "_")
        for prefix in prefixes
    )


@dataclass(frozen=True)
class ScopeConfig:
    """What counts as "prod" on this daemon (names, not files)."""

    prod_projects: tuple[str, ...] = ("nucpot-prod",)
    prod_name_prefixes: tuple[str, ...] = ("nucpot-prod",)
    prod_network_prefixes: tuple[str, ...] = ("nucpot-prod",)
    prod_volume_prefixes: tuple[str, ...] = ("nucpot-prod",)
    prod_image_prefixes: tuple[str, ...] = ("nucpot-prod-",)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScopeConfig":
        known = {k: tuple(v) for k, v in data.items() if isinstance(v, list)}
        return cls(**known)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Decision:
    """Outcome of classifying one request."""

    allowed: bool
    reason: str
    scope: str = "n/a"  # "prod" | "non-prod" | "image" | "n/a"
    audit: bool = False  # write an audit record (mutation seen)
    target: Optional[str] = None  # best-effort human-readable target

    def to_log_fields(self) -> dict[str, Any]:
        # NB: no "target" here — the proxy adds the request's own target;
        # the classified target is only a fallback detail.
        return {
            "outcome": "allow" if self.allowed else "deny",
            "reason": self.reason,
            "scope": self.scope,
            "classified_target": self.target,
        }


@dataclass
class TargetInfo:
    """Resolved identity of a mutation target (from CLI text or daemon)."""

    name: Optional[str] = None
    project: Optional[str] = None  # com.docker.compose.project label
    networks: tuple[str, ...] = field(default_factory=tuple)
    # Named volumes attached to the target (create body refs, or daemon
    # inspect Mounts[].Name). A rogue container with a prod-looking name
    # alone is already caught; one MOUNTING prod state (nucpot-prod_pgdata)
    # is equally a prod mutation (NFM-4273 review F1).
    volumes: tuple[str, ...] = field(default_factory=tuple)
    # Container references embedded in a create body (HostConfig.VolumesFrom
    # entries, NetworkMode "container:<ref>"). Copying mounts from a prod
    # container or sharing its network namespace is as much a prod mutation
    # as naming the container prod (NFM-4273 review R1).
    containers: tuple[str, ...] = field(default_factory=tuple)

    def prod_violation(self, cfg: ScopeConfig) -> Optional[str]:
        if self.project and self.project in cfg.prod_projects:
            return f"compose project {self.project!r} is prod"
        if self.name and _matches(self.name, cfg.prod_name_prefixes):
            return f"name {self.name!r} matches prod scope"
        for net in self.networks:
            if _matches(net, cfg.prod_network_prefixes):
                return f"network {net!r} matches prod scope"
        for vol in self.volumes:
            if _matches(vol, cfg.prod_volume_prefixes):
                return f"volume {vol!r} matches prod scope"
        for ref in self.containers:
            if _matches(ref, cfg.prod_name_prefixes):
                return f"container ref {ref!r} matches prod scope"
        return None


Resolver = Callable[[str], Optional[TargetInfo]]


def _strip_version(path: str) -> str:
    return _VERSION_PREFIX.sub("", path, count=1).lstrip("/")


def parse_query(query: str) -> dict[str, list[str]]:
    """Minimal query parser for the handful of keys we scope on."""
    out: dict[str, list[str]] = {}
    if not query:
        return out
    for part in query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        out.setdefault(key, []).append(value)
    return out


def _container_config_flags(body: dict[str, Any]) -> Optional[str]:
    """Return a refusal reason if the container config is an escape hatch."""
    host_config = body.get("HostConfig") or {}
    if not isinstance(host_config, dict):
        return None
    if host_config.get("Privileged"):
        return "Privileged=true"
    if host_config.get("PidMode") == "host":
        return "PidMode=host"
    caps = host_config.get("CapAdd") or []
    if isinstance(caps, list) and {"SYS_ADMIN", "SYS_PTRACE"} & set(caps):
        return f"CapAdd={caps}"
    sources: list[str] = []
    binds = host_config.get("Binds") or []
    for bind in binds if isinstance(binds, list) else []:
        sources.append(str(bind).split(":")[0])
    for mount in body.get("Mounts") or []:
        if isinstance(mount, dict) and mount.get("Source"):
            sources.append(str(mount["Source"]))
    for src in sources:
        if _FORBIDDEN_BIND_RE.search(src):
            return f"forbidden bind source {src!r}"
    return None


def _volume_refs(body: dict[str, Any]) -> tuple[str, ...]:
    """Named-volume references from a container-create body.

    Binds entries are ``[name:]container-path[:opts]`` — a source with no
    leading ``/`` is a named volume; a leading ``/`` is a bind mount (already
    covered by _FORBIDDEN_BIND_RE). HostConfig.Mounts entries carry the
    volume name in ``Source`` when ``Type == "volume"``.
    """
    refs: list[str] = []
    host_config = body.get("HostConfig") or {}
    if not isinstance(host_config, dict):
        return tuple(refs)
    binds = host_config.get("Binds") or []
    for bind in binds if isinstance(binds, list) else []:
        source = str(bind).split(":")[0]
        if source and not source.startswith("/"):
            refs.append(source)
    mounts = host_config.get("Mounts") or []
    for mount in mounts if isinstance(mounts, list) else []:
        if isinstance(mount, dict) and mount.get("Type") == "volume":
            name = str(mount.get("Source") or "")
            if name and not name.startswith("/"):
                refs.append(name)
    return tuple(refs)


def _opaque_ref(refs: tuple[str, ...]) -> Optional[str]:
    """First opaque (hex-id) entry in refs, if any — fails closed upstream."""
    for ref in refs:
        if _OPAQUE_ID_RE.match(ref.lower()):
            return ref
    return None


def _container_refs(body: dict[str, Any]) -> tuple[str, ...]:
    """Container references embedded in a create body.

    ``--volumes-from nucpot-prod-db-1`` copies a prod container's mounts
    (incl. prod volumes) into the new container; ``--network
    container:nucpot-prod-api-1`` shares a prod container's network
    namespace. Neither touches Binds/Mounts/EndpointsConfig, so they are
    scope-checked here instead (NFM-4273 review R1).
    """
    host_config = body.get("HostConfig") or {}
    if not isinstance(host_config, dict):
        return tuple()
    refs: list[str] = []
    for entry in host_config.get("VolumesFrom") or []:
        if isinstance(entry, str):
            refs.append(entry.split(":", 1)[0])
    network_mode = host_config.get("NetworkMode")
    if isinstance(network_mode, str) and network_mode.startswith("container:"):
        refs.append(network_mode[len("container:"):])
    return tuple(refs)


def _target_from_create(name: str, body: dict[str, Any]) -> TargetInfo:
    labels = body.get("Labels")
    project = labels.get("com.docker.compose.project") if isinstance(labels, dict) else None
    networks: tuple[str, ...] = tuple()
    endpoints = (body.get("NetworkingConfig") or {}).get("EndpointsConfig") or {}
    if isinstance(endpoints, dict):
        networks = tuple(str(k) for k in endpoints.keys())
    return TargetInfo(
        name=name or None,
        project=project,
        networks=networks,
        volumes=_volume_refs(body),
        containers=_container_refs(body),
    )


def classify(
    method: str,
    raw_path: str,
    query: str,
    body_json: Optional[dict[str, Any]],
    resolver: Optional[Resolver],
    cfg: ScopeConfig,
    full_mode: bool = False,
) -> Decision:
    """Classify one daemon request. Pure except for the injected resolver."""
    path = _strip_version(raw_path)
    method = method.upper()

    # ---- reads -----------------------------------------------------------
    if method in ("GET", "HEAD"):
        if path.endswith("/attach/ws"):
            return Decision(False, "websocket attach is interactive, not read-only", audit=True)
        if path in _GET_ALLOW_EXACT:
            return Decision(True, "read-only endpoint")
        if any(path.startswith(prefix) for prefix in _GET_ALLOW_PREFIX):
            return Decision(True, "read-only endpoint")
        match = _CONTAINER_SUB.match(path)
        if match:
            return Decision(True, "read-only endpoint")
        return Decision(False, f"unrecognized GET endpoint {path!r} (fail-closed)", audit=True)

    # ---- full (sanctioned) mode: allow everything, audit every mutation --
    if full_mode:
        if method in ("POST", "PUT", "DELETE", "PATCH"):
            return Decision(True, "full mode (sanctioned identity)", audit=True)
        return Decision(True, "full mode (sanctioned identity)")

    # ---- ro mode mutations -------------------------------------------------
    params = parse_query(query)

    if method == "POST" and path == "build":
        tags = params.get("t") or params.get("tag") or []
        return Decision(True, "image-layer op", scope="image", audit=True, target=",".join(tags) or None)

    if method == "POST" and path in ("images/create", "images/load", "images/prune"):
        ref = (params.get("fromImage") or params.get("ref") or [""])[0]
        return Decision(True, "image-layer op", scope="image", audit=True, target=ref or path)

    if path in ("containers/prune", "networks/prune", "volumes/prune", "build/prune"):
        return Decision(
            False, f"{path} is daemon-wide and can remove prod state (fail-closed)", scope="prod", audit=True
        )

    image_action = _IMAGE_NAME_ACTION.match(path)
    if image_action:
        action, name = image_action.group("action"), image_action.group("name")
        if action == "tag" and method == "POST":
            return Decision(True, "image-layer op", scope="image", audit=True, target=name)
        if action == "push" and method == "POST":
            if name.startswith(cfg.prod_image_prefixes):
                return Decision(
                    False,
                    f"push of prod image {name!r} denied (exfiltration guard)",
                    scope="prod",
                    audit=True,
                    target=name,
                )
            return Decision(True, "image push (non-prod)", scope="image", audit=True, target=name)

    if method == "DELETE" and path.startswith("images/"):
        name = path[len("images/"):]
        return Decision(True, "image-layer op", scope="image", audit=True, target=name)

    # ---- container create --------------------------------------------------
    if method == "POST" and path == "containers/create":
        name = (params.get("name") or [""])[0]
        body = body_json if isinstance(body_json, dict) else {}
        danger = _container_config_flags(body)
        if danger:
            return Decision(
                False, f"container config rejected: {danger}", scope="n/a", audit=True, target=name or None
            )
        target = _target_from_create(name, body)
        violation = target.prod_violation(cfg)
        if violation:
            return Decision(False, violation, scope="prod", audit=True, target=name or target.project)
        # Opaque refs in EndpointsConfig / VolumesFrom / NetworkMode cannot
        # be scope-checked from text (networks have no resolver path; hex
        # container refs could be resolved but compose always uses names) —
        # deny rather than allow an unverifiable prod attachment.
        opaque = _opaque_ref(target.networks + target.containers)
        if opaque:
            return Decision(
                False,
                f"opaque reference {opaque!r} cannot be scope-checked (fail-closed)",
                scope="n/a",
                audit=True,
                target=name or None,
            )
        return Decision(True, "container create (non-prod)", scope="non-prod", audit=True, target=name or None)

    # ---- container mutations by id or name ---------------------------------
    def _resolve(ident: str) -> TargetInfo:
        local = TargetInfo(name=ident)
        if local.prod_violation(cfg) is not None:
            return local  # decisive from the CLI text; no daemon roundtrip
        info = resolver(ident) if resolver is not None else None
        return info if info is not None else local

    match = _CONTAINER_MUTATION.match(path)
    if match:
        ident, action = match.group("id"), match.group("action")
        violation = _resolve(ident).prod_violation(cfg)
        if violation:
            return Decision(
                False, f"{action} on prod container: {violation}", scope="prod", audit=True, target=ident
            )
        return Decision(True, f"{action} (non-prod container)", scope="non-prod", audit=True, target=ident)

    if method == "DELETE" and path.startswith("containers/"):
        ident = path[len("containers/"):]
        violation = _resolve(ident).prod_violation(cfg)
        if violation:
            return Decision(False, f"rm on prod container: {violation}", scope="prod", audit=True, target=ident)
        return Decision(True, "rm (non-prod container)", scope="non-prod", audit=True, target=ident)

    if method == "PUT" and path.startswith("containers/") and path.endswith("/archive"):
        # PUT /containers/{id}/archive — docker cp INTO a container
        ident = path[len("containers/") : -len("/archive")]
        violation = _resolve(ident).prod_violation(cfg)
        if violation:
            return Decision(False, f"cp into prod container: {violation}", scope="prod", audit=True, target=ident)
        return Decision(True, "cp (non-prod container)", scope="non-prod", audit=True, target=ident)

    # ---- networks -----------------------------------------------------------
    if method == "POST" and path == "networks/create":
        name = str((body_json or {}).get("Name", ""))
        if _matches(name, cfg.prod_network_prefixes):
            return Decision(False, f"network create {name!r} matches prod scope", scope="prod", audit=True, target=name)
        return Decision(True, "network create (non-prod)", scope="non-prod", audit=True, target=name)

    network_action = _NETWORK_ACTION.match(path)
    if method == "POST" and network_action:
        net_id = network_action.group("id")
        if _matches(net_id, cfg.prod_network_prefixes):
            return Decision(
                False,
                f"network {network_action.group('action')}: {net_id!r} matches prod scope",
                scope="prod",
                audit=True,
                target=net_id,
            )
        if _OPAQUE_ID_RE.match(net_id.lower()):
            return Decision(
                False,
                f"opaque network id {net_id!r} cannot be scope-checked (fail-closed)",
                scope="n/a",
                audit=True,
                target=net_id,
            )
        return Decision(True, "network connect (non-prod)", scope="non-prod", audit=True, target=net_id)

    if method == "DELETE" and path.startswith("networks/"):
        net_id = path[len("networks/"):]
        if _matches(net_id, cfg.prod_network_prefixes):
            return Decision(False, f"network rm {net_id!r} matches prod scope", scope="prod", audit=True, target=net_id)
        if _OPAQUE_ID_RE.match(net_id.lower()):
            return Decision(
                False,
                f"opaque network id {net_id!r} cannot be scope-checked (fail-closed)",
                scope="n/a",
                audit=True,
                target=net_id,
            )
        return Decision(True, "network rm (non-prod)", scope="non-prod", audit=True, target=net_id)

    # ---- volumes --------------------------------------------------------------
    if method == "POST" and path == "volumes/create":
        name = str((body_json or {}).get("Name", ""))
        if _matches(name, cfg.prod_volume_prefixes):
            return Decision(False, f"volume create {name!r} matches prod scope", scope="prod", audit=True, target=name)
        return Decision(True, "volume create (non-prod)", scope="non-prod", audit=True, target=name)

    if method == "DELETE" and path.startswith("volumes/"):
        name = path[len("volumes/"):]
        if _matches(name, cfg.prod_volume_prefixes):
            return Decision(False, f"volume rm {name!r} matches prod scope", scope="prod", audit=True, target=name)
        return Decision(True, "volume rm (non-prod)", scope="non-prod", audit=True, target=name)

    # ---- everything else that mutates: fail closed ------------------------------
    if method in ("POST", "PUT", "DELETE", "PATCH"):
        return Decision(False, f"unrecognized mutation {method} /{path} (fail-closed)", audit=True)

    return Decision(False, f"unrecognized request {method} /{path} (fail-closed)", audit=True)


def parse_json_object(raw: Optional[bytes]) -> Optional[dict[str, Any]]:
    """Best-effort body parse; None for empty/invalid/non-object bodies."""
    if not raw:
        return None
    try:
        value = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None
