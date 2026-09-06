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
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import unquote

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
# GET images/{name}/json — single-image inspect. {name} is multi-segment
# for registry refs (e.g. pgvector/pgvector:pg16), hence ".+".
_IMAGE_INSPECT = re.compile(r"^images/(?P<name>.+)/json$")
# GET exec/{id}/json — single-exec inspect (compose v5's `exec` reads the
# exit code after start). Same shape as _IMAGE_INSPECT: an item read of an
# object the caller already created through an audited endpoint.
_EXEC_INSPECT = re.compile(r"^exec/(?P<id>[^/]+)/json$")
# POST exec/{id}/start — execute an exec instance the caller already
# created via POST containers/{id}/exec (which carries the prod
# scope-check). The start call carries no container reference of its own.
_EXEC_START = re.compile(r"^exec/(?P<id>[^/]+)/start$")
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
# Case-insensitive: the prod host filesystem (APFS) resolves Docker.SOCK
# and /VAR/RUN identically (NFM-4273 CR R2).
_FORBIDDEN_BIND_RE = re.compile(
    r"(^|/)docker\.sock$|(^|/)podman\.sock$"
    r"|^/($|Users($|/)|private($|/)|etc($|/)|var($|/)|usr($|/)|bin($|/)|sbin($|/)|System($|/)|Library($|/)"
    r"|tmp($|/)|opt($|/)|Volumes($|/))",
    re.IGNORECASE,
)

# Opaque daemon ids (container/network hex ids AND their unambiguous
# prefixes, which the daemon accepts at any length). A name matching this
# shape is scope-uncheckable from text alone; without a resolver verdict
# the request fails closed rather than guessing (same philosophy as
# unrecognized paths).
_OPAQUE_ID_RE = re.compile(r"^[0-9a-f]+$")

# Image references shaped like an image ID or digest (bare hex of any
# length, or sha256:<hex>) — the daemon resolves both to the underlying
# image regardless of its repo tags, so text-only prefix scope checks do
# not apply (NFM-4273 review V1).
_OPAQUE_IMAGE_REF_RE = re.compile(r"^sha256:[0-9a-f]+$|^[0-9a-f]+$", re.IGNORECASE)

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
    def from_dict(cls, data: dict[str, Any]) -> ScopeConfig:
        known = {k: tuple(v) for k, v in data.items() if isinstance(v, list)}
        return cls(**known)


@dataclass(frozen=True)
class Decision:
    """Outcome of classifying one request."""

    allowed: bool
    reason: str
    scope: str = "n/a"  # "prod" | "non-prod" | "image" | "n/a"
    audit: bool = False  # write an audit record (mutation seen)
    target: str | None = None  # best-effort human-readable target

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

    name: str | None = None
    project: str | None = None  # com.docker.compose.project label
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

    def prod_violation(self, cfg: ScopeConfig) -> str | None:
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


# A module-level alias is RUNTIME code — `from __future__ import
# annotations` does not defer it — so PEP 604 `|` here crashes the
# py3.9 interpreter the launchd proxies run under (NFM-4320).
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


def _container_config_flags(body: dict[str, Any]) -> str | None:
    """Return a refusal reason if the container config is an escape hatch."""
    host_config = body.get("HostConfig") or {}
    if not isinstance(host_config, dict):
        return None
    if host_config.get("Privileged"):
        return "Privileged=true"
    if host_config.get("PidMode") == "host":
        return "PidMode=host"
    if host_config.get("NetworkMode") == "host":
        # Asymmetric with the per-network scope checks: "host" networking
        # reaches every local service inside the VM (NFM-4273 CR F2).
        return "NetworkMode=host"
    caps = host_config.get("CapAdd") or []
    if isinstance(caps, list) and {"SYS_ADMIN", "SYS_PTRACE"} & set(caps):
        return f"CapAdd={caps}"
    device_requests = host_config.get("DeviceRequests") or []
    if isinstance(device_requests, list) and device_requests:
        # Requests grant host device nodes by capability — not a path the
        # bind regex can interpret, so presence itself fails closed
        # (NFM-4273 CR R2).
        return "DeviceRequests grants host devices (fail-closed)"
    sources: list[str] = []
    binds = host_config.get("Binds") or []
    for bind in binds if isinstance(binds, list) else []:
        sources.append(str(bind).split(":")[0])
    # The daemon honors HostConfig.Mounts (what `docker run --mount`
    # produces), NOT the top-level Mounts key the old dead loop read
    # (NFM-4273 CR R2). Any absolute Source is a bind by some spelling,
    # whatever the declared Type.
    mounts = host_config.get("Mounts") or []
    for mount in mounts if isinstance(mounts, list) else []:
        if isinstance(mount, dict) and mount.get("Source"):
            src = str(mount["Source"])
            if mount.get("Type") == "bind" or src.startswith("/"):
                sources.append(src)
    devices = host_config.get("Devices") or []
    for device in devices if isinstance(devices, list) else []:
        if isinstance(device, dict) and device.get("PathOnHost"):
            sources.append(str(device["PathOnHost"]))
    for src in sources:
        # normpath collapses /./, //, trailing /., and ../ segments — the
        # same string the daemon's mount resolver will end up opening.
        if _FORBIDDEN_BIND_RE.search(src) or _FORBIDDEN_BIND_RE.search(posixpath.normpath(src)):
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


def _opaque_ref(refs: tuple[str, ...]) -> str | None:
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
    namespace, and ``--pid``/``--ipc container:<ref>`` share its process
    and IPC namespaces; ``--link nucpot-prod-db-1:db`` embeds a prod
    container reference. None of these touch Binds/Mounts/EndpointsConfig,
    so they are scope-checked here instead (NFM-4273 reviews R1, E1, V3).
    """
    host_config = body.get("HostConfig") or {}
    if not isinstance(host_config, dict):
        return tuple()
    refs: list[str] = []
    for entry in host_config.get("VolumesFrom") or []:
        if isinstance(entry, str):
            refs.append(entry.split(":", 1)[0])
    for entry in host_config.get("Links") or []:
        if isinstance(entry, str):
            target = entry.split(":", 1)[0]
            if target.startswith("/"):
                target = target.lstrip("/").split("/", 1)[0]
            if target:
                refs.append(target)
    for flag in ("NetworkMode", "PidMode", "IpcMode"):
        value = host_config.get(flag)
        if isinstance(value, str) and value.startswith("container:"):
            refs.append(value[len("container:") :])
    return tuple(refs)


def _target_from_create(name: str, body: dict[str, Any]) -> TargetInfo:
    labels = body.get("Labels")
    project = labels.get("com.docker.compose.project") if isinstance(labels, dict) else None
    networks: tuple[str, ...] = tuple()
    endpoints = (body.get("NetworkingConfig") or {}).get("EndpointsConfig") or {}
    if isinstance(endpoints, dict):
        networks = networks + tuple(str(k) for k in endpoints)
    host_config = body.get("HostConfig") or {}
    network_mode = host_config.get("NetworkMode") if isinstance(host_config, dict) else None
    if isinstance(network_mode, str) and network_mode and not network_mode.startswith("container:"):
        networks = (*networks, network_mode)
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
    body_json: dict[str, Any] | None,
    resolver: Resolver | None,
    cfg: ScopeConfig,
    full_mode: bool = False,
) -> Decision:
    """Classify one daemon request. Pure except for the injected resolver."""
    path = _strip_version(raw_path)
    method = method.upper()

    # R3 (fail-closed): the gate scope-checks the raw request text; the
    # daemon percent-decodes path segments and query values server-side.
    # A %-escape anywhere in the PATH means the gate and the daemon may
    # be looking at different targets — never forward what cannot be
    # interpreted identically to the protected service. (Query params are
    # checked per-key below: read-only diagnostics like filters= use
    # %-encoding legitimately.)
    if "%" in path:
        return Decision(
            False,
            f"percent-encoding in path {path!r} cannot be scope-checked (fail-closed)",
            audit=True,
        )

    # ---- reads -----------------------------------------------------------
    if method in ("GET", "HEAD"):
        if path.endswith("/attach/ws"):
            return Decision(False, "websocket attach is interactive, not read-only", audit=True)
        if path == "images/get":
            # Bulk image tar — an exfiltration channel for prod image
            # layers; scope-check the requested names (NFM-4273 CR F1).
            names = parse_query(query).get("names") or []
            if not names:
                # `docker save` always sends explicit names; a bare
                # images/get would tar every image on the daemon.
                return Decision(
                    False, "images/get without explicit names denied (fail-closed)", audit=True
                )
            for name in names:
                if "%" in name:
                    return Decision(
                        False,
                        "percent-encoded image name cannot be scope-checked (fail-closed)",
                        audit=True,
                    )
                if _OPAQUE_IMAGE_REF_RE.match(name.lower()):
                    return Decision(
                        False,
                        f"opaque image ref {name!r} cannot be scope-checked (fail-closed)",
                        audit=True,
                        target=name,
                    )
                if name.startswith(cfg.prod_image_prefixes):
                    return Decision(
                        False,
                        f"export of prod image {name!r} denied (exfiltration guard)",
                        scope="prod",
                        audit=True,
                        target=name,
                    )
            return Decision(True, "read-only endpoint")
        if _IMAGE_INSPECT.match(path):
            # Single-image inspect — a sanctioned read (NFM-4333: this was
            # fail-closed in BOTH modes and `docker compose up` — the
            # sanctioned deploy path — could not resolve a single service
            # image). The full list (images/json) is already allowed and
            # returns the same metadata for every image, so the item
            # endpoint exposes strictly less data. Layer/export channels
            # (images/get) stay scope-guarded above.
            return Decision(True, "read-only endpoint")
        if _EXEC_INSPECT.match(path):
            # Single-exec inspect (NFM-4333: compose v5's `exec` reads the
            # instance's exit code via GET exec/{id}/json after start; this
            # was fail-closed in BOTH modes, so every sanctioned
            # `docker compose exec` — including prod_migrate.sh's
            # pg_isready probe — timed out with the exec never "finishing").
            # The read exposes only the status of an exec the caller already
            # created through the audited POST containers/{id}/exec.
            return Decision(True, "read-only endpoint")
        if path in _GET_ALLOW_EXACT:
            return Decision(True, "read-only endpoint")
        if any(path.startswith(prefix) for prefix in _GET_ALLOW_PREFIX):
            return Decision(True, "read-only endpoint")
        match = _CONTAINER_SUB.match(path)
        if match:
            if match.group("action") == "export":
                # A prod container's whole filesystem as a tar — read-only
                # in verb but exfiltration in effect; opaque ids fail
                # closed alongside (NFM-4273 CR F1).
                ident = match.group("id")
                violation = TargetInfo(name=ident).prod_violation(cfg)
                if violation or _OPAQUE_ID_RE.match(ident.lower()):
                    return Decision(
                        False,
                        f"export denied ({violation or 'opaque id fail-closed'})"
                        " (exfiltration guard)",
                        scope="prod" if violation else "n/a",
                        audit=True,
                        target=ident,
                    )
            return Decision(True, "read-only endpoint")
        return Decision(False, f"unrecognized GET endpoint {path!r} (fail-closed)", audit=True)

    # ---- full (sanctioned) mode: allow everything, audit every mutation --
    if full_mode:
        if method in ("POST", "PUT", "DELETE", "PATCH"):
            return Decision(True, "full mode (sanctioned identity)", audit=True)
        return Decision(True, "full mode (sanctioned identity)")

    # ---- ro mode mutations -------------------------------------------------
    params = parse_query(query)

    # R3 (fail-closed): scope checks must see EXACTLY what the daemon
    # resolves. The daemon percent-decodes query values server-side, so
    # an encoded name could scope-check as innocent here and resolve as
    # prod there. Decode each scope-relevant value once — what the daemon
    # sees after its own single decode — and keep failing closed when a
    # value is still encoded afterwards: a double-encoded ref is never
    # sent by the docker CLI and can only be an evasion attempt.
    # (NFM-4333 RC4: `docker build`'s base-image pull sends
    # repo=docker.io%2Flibrary%2Fpython — a sanctioned image-layer op the
    # old blanket refusal on '%' blocked.)
    decoded: dict[str, list[str]] = {}
    for key in ("name", "fromImage", "fromSrc", "ref", "repo", "tag", "names"):
        values = params.get(key, [])
        if not values:
            continue
        checked = []
        for value in values:
            if "%" in value:
                value = unquote(value)
                if "%" in value:
                    return Decision(
                        False,
                        f"double-encoded {key!r} value cannot be scope-checked (fail-closed)",
                        audit=True,
                    )
            checked.append(value)
        decoded = {**decoded, key: checked}
    params = {**params, **decoded}

    if method == "POST" and path == "build":
        tags = params.get("t") or params.get("tag") or []
        return Decision(
            True, "image-layer op", scope="image", audit=True, target=",".join(tags) or None
        )

    if method == "POST" and path in ("images/create", "images/load"):
        ref = (params.get("fromImage") or params.get("ref") or [""])[0]
        return Decision(True, "image-layer op", scope="image", audit=True, target=ref or path)

    if path in (
        "containers/prune",
        "networks/prune",
        "volumes/prune",
        "build/prune",
        "images/prune",
    ):
        return Decision(
            False,
            f"{path} is daemon-wide and can remove prod state (fail-closed)",
            scope="prod",
            audit=True,
        )

    image_action = _IMAGE_NAME_ACTION.match(path)
    if image_action:
        action, name = image_action.group("action"), image_action.group("name")
        if _OPAQUE_IMAGE_REF_RE.match(name.lower()):
            # IDs/digests resolve to the underlying image whatever its repo
            # tags say — an id-based tag or push launders prod past the
            # name guards, so opaque refs fail closed (NFM-4273 review V1).
            return Decision(
                False,
                f"opaque image ref {name!r} cannot be scope-checked (fail-closed)",
                scope="n/a",
                audit=True,
                target=name,
            )
        if action == "tag" and method == "POST":
            if name.startswith(cfg.prod_image_prefixes):
                # Re-tagging prod to an innocent name launders it past the
                # push guard — same exfiltration guard as push (CR F1).
                return Decision(
                    False,
                    f"re-tag of prod image {name!r} denied (exfiltration guard)",
                    scope="prod",
                    audit=True,
                    target=name,
                )
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
        name = path[len("images/") :]
        if _OPAQUE_IMAGE_REF_RE.match(name.lower()):
            return Decision(
                False,
                f"opaque image ref {name!r} cannot be scope-checked (fail-closed)",
                scope="n/a",
                audit=True,
                target=name,
            )
        if name.startswith(cfg.prod_image_prefixes):
            # Prod images are rollback generations — deleting them from a
            # bare terminal is a prod mutation (NFM-4273 CR F3).
            return Decision(
                False,
                f"delete of prod image {name!r} denied",
                scope="prod",
                audit=True,
                target=name,
            )
        return Decision(True, "image-layer op", scope="image", audit=True, target=name)

    # ---- container create --------------------------------------------------
    if method == "POST" and path == "containers/create":
        name = (params.get("name") or [""])[0]
        body = body_json if isinstance(body_json, dict) else {}
        danger = _container_config_flags(body)
        if danger:
            return Decision(
                False,
                f"container config rejected: {danger}",
                scope="n/a",
                audit=True,
                target=name or None,
            )
        target = _target_from_create(name, body)
        violation = target.prod_violation(cfg)
        if violation:
            return Decision(
                False, violation, scope="prod", audit=True, target=name or target.project
            )
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
        return Decision(
            True, "container create (non-prod)", scope="non-prod", audit=True, target=name or None
        )

    # ---- container mutations by id or name ---------------------------------
    def _resolve(ident: str) -> TargetInfo | None:
        local = TargetInfo(name=ident)
        if local.prod_violation(cfg) is not None:
            return local  # decisive from the CLI text; no daemon roundtrip
        if not _OPAQUE_ID_RE.match(ident.lower()):
            return local  # a plain name is fully decided by the text above
        if resolver is None:
            return None  # opaque id with nothing to consult — fail closed
        return resolver(ident)  # None back means unresolvable — fail closed

    def _deny_if_prod(action_desc: str, ident: str) -> Decision | None:
        info = _resolve(ident)
        if info is None:
            return Decision(
                False,
                f"opaque id {ident!r} cannot be resolved to scope-check (fail-closed)",
                scope="n/a",
                audit=True,
                target=ident,
            )
        violation = info.prod_violation(cfg)
        if violation:
            return Decision(
                False, f"{action_desc}: {violation}", scope="prod", audit=True, target=ident
            )
        return None

    match = _CONTAINER_MUTATION.match(path)
    if match:
        ident, action = match.group("id"), match.group("action")
        denied = _deny_if_prod(f"{action} on prod container", ident)
        if denied:
            return denied
        return Decision(
            True, f"{action} (non-prod container)", scope="non-prod", audit=True, target=ident
        )

    if method == "DELETE" and path.startswith("containers/"):
        ident = path[len("containers/") :]
        denied = _deny_if_prod("rm on prod container", ident)
        if denied:
            return denied
        return Decision(True, "rm (non-prod container)", scope="non-prod", audit=True, target=ident)

    if method == "PUT" and path.startswith("containers/") and path.endswith("/archive"):
        # PUT /containers/{id}/archive — docker cp INTO a container
        ident = path[len("containers/") : -len("/archive")]
        denied = _deny_if_prod("cp into prod container", ident)
        if denied:
            return denied
        return Decision(True, "cp (non-prod container)", scope="non-prod", audit=True, target=ident)

    # ---- networks -----------------------------------------------------------
    if method == "POST" and path == "networks/create":
        name = str((body_json or {}).get("Name", ""))
        if _matches(name, cfg.prod_network_prefixes):
            return Decision(
                False,
                f"network create {name!r} matches prod scope",
                scope="prod",
                audit=True,
                target=name,
            )
        return Decision(
            True, "network create (non-prod)", scope="non-prod", audit=True, target=name
        )

    network_action = _NETWORK_ACTION.match(path)
    if method == "POST" and network_action:
        net_id = network_action.group("id")
        action = network_action.group("action")
        if _matches(net_id, cfg.prod_network_prefixes):
            return Decision(
                False,
                f"network {action}: {net_id!r} matches prod scope",
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
        container = str(body_json.get("Container") or "") if isinstance(body_json, dict) else ""
        if container:
            denied = _deny_if_prod(f"network {action} on prod container", container)
            if denied:
                return denied
        return Decision(
            True, f"network {action} (non-prod)", scope="non-prod", audit=True, target=net_id
        )

    if method == "DELETE" and path.startswith("networks/"):
        net_id = path[len("networks/") :]
        if _matches(net_id, cfg.prod_network_prefixes):
            return Decision(
                False,
                f"network rm {net_id!r} matches prod scope",
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
        return Decision(True, "network rm (non-prod)", scope="non-prod", audit=True, target=net_id)

    # ---- volumes --------------------------------------------------------------
    if method == "POST" and path == "volumes/create":
        name = str((body_json or {}).get("Name", ""))
        if _matches(name, cfg.prod_volume_prefixes):
            return Decision(
                False,
                f"volume create {name!r} matches prod scope",
                scope="prod",
                audit=True,
                target=name,
            )
        return Decision(True, "volume create (non-prod)", scope="non-prod", audit=True, target=name)

    if method == "DELETE" and path.startswith("volumes/"):
        name = path[len("volumes/") :]
        if _matches(name, cfg.prod_volume_prefixes):
            return Decision(
                False,
                f"volume rm {name!r} matches prod scope",
                scope="prod",
                audit=True,
                target=name,
            )
        # Anonymous volume names are 64-hex — opaque, exactly like network
        # ids; a prod volume's anonymous name cannot be text-distinguished
        # (NFM-4273 CR F4).
        if _OPAQUE_ID_RE.match(name.lower()):
            return Decision(
                False,
                f"opaque volume id {name!r} cannot be scope-checked (fail-closed)",
                scope="n/a",
                audit=True,
                target=name,
            )
        return Decision(True, "volume rm (non-prod)", scope="non-prod", audit=True, target=name)

    # ---- everything else that mutates: fail closed ------------------------------
    if method == "POST":
        exec_match = _EXEC_START.match(path)
        if exec_match:
            # POST exec/{id}/start — execute an exec instance the caller
            # created via POST containers/{id}/exec, which already
            # prod-scope-checked the target container. The start call
            # carries no container reference of its own (the binding lives
            # on the exec instance), so there is nothing further to
            # scope-check here (NFM-4333: `docker exec` from the ro
            # context — e.g. the pre-deploy assert smoke — died here with
            # the exec never starting).
            return Decision(
                True,
                "exec start (instance created via audited endpoint)",
                audit=True,
                target=exec_match.group("id"),
            )

    if method in ("POST", "PUT", "DELETE", "PATCH"):
        return Decision(False, f"unrecognized mutation {method} /{path} (fail-closed)", audit=True)

    return Decision(False, f"unrecognized request {method} /{path} (fail-closed)", audit=True)


def parse_json_object(raw: bytes | None) -> dict[str, Any] | None:
    """Best-effort body parse; None for empty/invalid/non-object bodies."""
    if not raw:
        return None
    try:
        value = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None
