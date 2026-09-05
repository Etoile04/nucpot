"""Unit tests for the NFM-4270 docker gate policy classifier.

NFM-4264 (2026-09-04): a desktop-agent session ran host-side
``docker compose --env-file docker/.env.prod up -d --build api web``
against prod with zero audit trail. ADR-013 G2 puts a wall at the
docker socket; policy.py decides what hits it. These tests pin the
classification matrix: reads frictionless, prod mutations denied,
non-prod mutations allowed, escape-hatch configs denied regardless.
"""

from __future__ import annotations

import sys
from pathlib import Path

GATE_DIR = Path(__file__).resolve().parents[1] / "host-prod-gate"
sys.path.insert(0, str(GATE_DIR))

from nfm_docker_gate.policy import (  # noqa: E402
    ScopeConfig,
    TargetInfo,
    classify,
)

CFG = ScopeConfig()


def decide(method, path, query="", body=None, resolver=None, full=False):
    return classify(method, path, query, body, resolver, CFG, full_mode=full)


# ---- reads are frictionless (AC-G2.2) --------------------------------------


def test_get_containers_json_allowed():
    assert decide("GET", "/v1.43/containers/json", "all=1").allowed


def test_get_container_inspect_logs_stats_allowed():
    for sub in ("json", "logs", "stats", "top", "changes", "ports"):
        assert decide("GET", f"/v1.43/containers/abc123/{sub}").allowed, sub


def test_get_container_export_by_plain_name_allowed():
    # export is read-only in verb; a NON-prod, NON-opaque name stays allowed
    # (the deny applies to prod names and opaque hex ids — CR F1).
    assert decide("GET", "/v1.43/containers/staging-toolbox/export").allowed


def test_get_container_export_opaque_hex_id_denied_fail_closed():
    """CR F1: `abc123`-style all-hex ids are daemon ids (or unambiguous
    prefixes) — an export of an unknown container is an exfiltration
    channel, so opaque ids fail closed like every other opaque ref."""
    result = decide("GET", "/v1.43/containers/abc123/export")
    assert not result.allowed and result.audit
    assert "fail-closed" in result.reason


def test_get_container_export_prod_name_denied():
    result = decide("GET", "/v1.43/containers/nucpot-prod-api/export")
    assert not result.allowed and result.scope == "prod"
    assert "exfiltration" in result.reason


def test_get_version_ping_info_events_df_allowed():
    for path in ("_ping", "version", "info", "events", "system/df"):
        assert decide("GET", f"/v1.43/{path}").allowed, path


def test_get_networks_volumes_images_allowed():
    for path in ("networks", "volumes", "images/json", "networks/nucpot-prod_default"):
        assert decide("GET", f"/v1.43/{path}").allowed, path


def test_get_unknown_endpoint_fail_closed():
    result = decide("GET", "/v1.43/future/endpoint")
    assert not result.allowed


def test_websocket_attach_denied_even_on_get():
    assert not decide("GET", "/v1.43/containers/abc/attach/ws").allowed


# ---- prod mutations denied (AC-G2) ------------------------------------------


def test_delete_prod_container_denied():
    result = decide("DELETE", "/v1.43/containers/nucpot-prod-api")
    assert not result.allowed and result.scope == "prod" and result.audit


def test_post_stop_restart_kill_prod_denied():
    for action in ("stop", "restart", "kill", "pause", "update", "rename", "wait"):
        result = decide("POST", f"/v1.43/containers/nucpot-prod-api/{action}")
        assert not result.allowed, action


def test_exec_into_prod_denied():
    assert not decide("POST", "/v1.43/containers/nucpot-prod-db/exec").allowed


def test_docker_cp_into_prod_denied():
    assert not decide("PUT", "/v1.43/containers/nucpot-prod-api/archive").allowed


def test_create_prod_container_by_name_denied():
    result = decide("POST", "/v1.43/containers/create", "name=nucpot-prod-api-1", body={})
    assert not result.allowed and result.scope == "prod"


def test_create_container_with_prod_compose_project_label_denied():
    body = {"Labels": {"com.docker.compose.project": "nucpot-prod"}}
    result = decide("POST", "/v1.43/containers/create", "name=some-random-name", body=body)
    assert not result.allowed and result.scope == "prod"


def test_create_container_joining_prod_network_denied():
    body = {"NetworkingConfig": {"EndpointsConfig": {"nucpot-prod_default": {}}}}
    result = decide("POST", "/v1.43/containers/create", "name=rogue", body=body)
    assert not result.allowed


def test_prod_network_create_connect_rm_denied():
    assert not decide(
        "POST", "/v1.43/networks/create", body={"Name": "nucpot-prod_default"}
    ).allowed
    assert not decide("POST", "/v1.43/networks/nucpot-prod_default/connect", body={}).allowed
    assert not decide("DELETE", "/v1.43/networks/nucpot-prod_default").allowed


def test_prod_volume_create_rm_denied():
    assert not decide("POST", "/v1.43/volumes/create", body={"Name": "nucpot-prod_pgdata"}).allowed
    assert not decide("DELETE", "/v1.43/volumes/nucpot-prod_pgdata").allowed


def test_opaque_hex_id_resolved_through_daemon():
    opaque = "a" * 64

    def resolver(ident):
        assert ident == opaque
        return TargetInfo(name="nucpot-prod-worker", project="nucpot-prod")

    result = decide("DELETE", f"/v1.43/containers/{opaque}", resolver=resolver)
    assert not result.allowed and result.scope == "prod"


def test_opaque_hex_id_with_prod_volume_resolved_through_daemon():
    """NFM-4273 review F1: a rogue container with an innocent name but prod
    state MOUNTED is still a prod mutation — the resolver surfaces attached
    volumes from daemon inspect and the violation fires on the volume."""
    opaque = "c" * 64

    def resolver(ident):
        return TargetInfo(name="innocent-toolbox", volumes=("nucpot-prod_pgdata",))

    result = decide("DELETE", f"/v1.43/containers/{opaque}", resolver=resolver)
    assert not result.allowed and result.scope == "prod"
    assert "nucpot-prod_pgdata" in result.reason


# ---- volumes in scope (NFM-4273 review F1) -------------------------------------


def test_create_mounting_prod_volume_via_binds_denied():
    body = {"HostConfig": {"Binds": ["nucpot-prod_pgdata:/var/lib/postgresql/data"]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and result.scope == "prod"
    assert "nucpot-prod_pgdata" in result.reason


def test_create_mounting_prod_volume_via_mounts_denied():
    body = {
        "HostConfig": {
            "Mounts": [{"Type": "volume", "Source": "nucpot-prod_pgdata", "Target": "/data"}]
        }
    }
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and result.scope == "prod"


def test_create_mounting_nonprod_volume_allowed():
    body = {
        "HostConfig": {
            "Binds": ["nucpot-staging_cache:/cache"],
            "Mounts": [{"Type": "volume", "Source": "dev_scratch", "Target": "/s"}],
        }
    }
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert result.allowed


def test_bind_mount_paths_are_not_volume_refs():
    """Absolute bind sources are bind mounts (checked separately by
    _FORBIDDEN_BIND_RE) — they must not crash or pollute volume scoping.
    Benign sources only; the forbidden-source pins live in the CR R2 tests."""
    body = {
        "HostConfig": {
            "Binds": ["/srv/scratch:/data"],
            "Mounts": [{"Type": "bind", "Source": "/mnt/toolbox", "Target": "/u"}],
        }
    }
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert result.allowed


def test_target_info_prod_violation_on_volume_alone():
    assert TargetInfo(volumes=("nucpot-prod_pgdata",)).prod_violation(CFG)
    assert TargetInfo(volumes=("unrelated",)).prod_violation(CFG) is None


# ---- opaque network ids fail closed (NFM-4273 review F2) ------------------------


def test_network_connect_opaque_id_denied_fail_closed():
    """Network ids have no resolver path — a hex id cannot be scope-checked
    from text, so it must fail closed rather than pass as 'non-prod'."""
    opaque = "d" * 64
    result = decide("POST", f"/v1.43/networks/{opaque}/connect", body={})
    assert not result.allowed and result.audit
    assert "fail-closed" in result.reason


def test_network_disconnect_opaque_id_denied_fail_closed():
    result = decide("POST", f"/v1.43/networks/{'e' * 64}/disconnect", body={})
    assert not result.allowed


def test_network_rm_opaque_id_denied_fail_closed():
    result = decide("DELETE", f"/v1.43/networks/{'f' * 64}")
    assert not result.allowed and "fail-closed" in result.reason


def test_network_actions_by_name_still_classified():
    """Named networks keep the original behavior both ways."""
    assert not decide("POST", "/v1.43/networks/nucpot-prod_default/connect").allowed
    assert decide("POST", "/v1.43/networks/bridge/connect").allowed


def test_create_with_opaque_network_ref_denied_fail_closed():
    """Create joining a network by opaque id is the same hole as connect —
    deny (compose always references networks by name)."""
    body = {
        "NetworkingConfig": {
            "EndpointsConfig": {
                "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef": {}
            }
        }
    }
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and "fail-closed" in result.reason


# ---- container refs embedded in create bodies (NFM-4273 review R1) ---------------


def test_create_volumes_from_prod_container_denied():
    """--volumes-from nucpot-prod-db-1 copies prod mounts (incl. the prod
    data volume) into a rogue container — a prod mutation by reference."""
    body = {"HostConfig": {"VolumesFrom": ["nucpot-prod-db-1"]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and result.scope == "prod"
    assert "nucpot-prod-db-1" in result.reason


def test_create_volumes_from_prod_container_with_mode_suffix_denied():
    body = {"HostConfig": {"VolumesFrom": ["nucpot-prod-db-1:ro"]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and result.scope == "prod"


def test_create_network_mode_container_prod_denied():
    """--network container:nucpot-prod-api-1 shares a prod container's
    network namespace without touching Binds/EndpointsConfig."""
    body = {"HostConfig": {"NetworkMode": "container:nucpot-prod-api-1"}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and result.scope == "prod"
    assert "nucpot-prod-api-1" in result.reason


def test_create_with_opaque_volumes_from_ref_denied_fail_closed():
    body = {"HostConfig": {"VolumesFrom": ["a1b2c3d4e5f67890"]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and "fail-closed" in result.reason


def test_create_with_opaque_network_mode_container_ref_denied_fail_closed():
    body = {"HostConfig": {"NetworkMode": "container:a1b2c3d4e5f67890"}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and "fail-closed" in result.reason


def test_create_with_nonprod_container_refs_allowed():
    body = {
        "HostConfig": {
            "VolumesFrom": ["nucpot-staging-db-1:rw"],
            "NetworkMode": "container:nucpot-staging-api-1",
        }
    }
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert result.allowed


def test_create_link_prod_container_denied():
    """--link nucpot-prod-db-1:db embeds a prod container reference in the
    create body (NFM-4273 review V3)."""
    body = {"HostConfig": {"Links": ["nucpot-prod-db-1:db"]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and result.scope == "prod"
    assert "nucpot-prod-db-1" in result.reason


def test_create_link_prod_container_inspect_spelling_denied():
    # The daemon's inspect spelling of a link is /name/alias — same ref.
    body = {"HostConfig": {"Links": ["/nucpot-prod-db-1/db"]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and result.scope == "prod"


def test_create_link_opaque_ref_denied_fail_closed():
    body = {"HostConfig": {"Links": ["a1b2c3d4e5f67890:db"]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and "fail-closed" in result.reason


def test_create_link_nonprod_allowed():
    body = {"HostConfig": {"Links": ["nucpot-staging-db-1:db"]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert result.allowed


# ---- create-body NetworkMode names + Pid/Ipc namespaces (NFM-4273 review E1) ---


def test_create_network_mode_prod_network_name_denied():
    """``docker run --network nucpot-prod_default`` attaches to the prod
    network through HostConfig alone — no NetworkingConfig involved."""
    body = {"HostConfig": {"NetworkMode": "nucpot-prod_default"}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and result.scope == "prod"
    assert "nucpot-prod_default" in result.reason


def test_create_network_mode_opaque_network_id_denied_fail_closed():
    body = {"HostConfig": {"NetworkMode": "d" * 64}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and "fail-closed" in result.reason


def test_create_pid_mode_container_prod_denied():
    """``--pid container:nucpot-prod-api-1`` shares a prod container's
    process namespace (ptrace-equivalent access) without touching mounts."""
    body = {"HostConfig": {"PidMode": "container:nucpot-prod-api-1"}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and result.scope == "prod"
    assert "nucpot-prod-api-1" in result.reason


def test_create_ipc_mode_container_prod_denied():
    body = {"HostConfig": {"IpcMode": "container:nucpot-prod-db-1"}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and result.scope == "prod"


def test_create_built_in_network_modes_still_allowed():
    # "host" deliberately absent: it reaches every local service inside
    # the VM — denied below (NFM-4273 CR F2).
    for mode in ("default", "bridge", "none", "nucpot-staging_default"):
        body = {"HostConfig": {"NetworkMode": mode}}
        result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
        assert result.allowed, mode


def test_create_network_mode_host_denied():
    """CR F2: --network host reaches every local service inside the Docker
    VM — an escape hatch asymmetric with the per-network scope checks."""
    body = {"HostConfig": {"NetworkMode": "host"}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed
    assert "NetworkMode=host" in result.reason


# ---- network connect/disconnect body container ref (NFM-4273 review E2) --------


def test_network_connect_to_prod_container_denied():
    """``docker network connect rogue-net nucpot-prod-api-1`` — the network
    is non-prod but the CONNECT BODY's Container ref is a prod container."""
    result = decide(
        "POST", "/v1.43/networks/rogue-net/connect", body={"Container": "nucpot-prod-api-1"}
    )
    assert not result.allowed and result.scope == "prod"
    assert "nucpot-prod-api-1" in result.reason


def test_network_disconnect_prod_container_denied():
    result = decide(
        "POST", "/v1.43/networks/rogue-net/disconnect", body={"Container": "nucpot-prod-db-1"}
    )
    assert not result.allowed and result.scope == "prod"


def test_network_connect_container_hex_ref_resolved_through_daemon():
    prefix = "c1d2e3f4a5b"

    def resolver(ident):
        assert ident == prefix
        return TargetInfo(name="nucpot-prod-api", project="nucpot-prod")

    result = decide(
        "POST", "/v1.43/networks/rogue-net/connect", body={"Container": prefix}, resolver=resolver
    )
    assert not result.allowed and result.scope == "prod"


def test_network_connect_container_hex_ref_unresolvable_fails_closed():
    result = decide(
        "POST",
        "/v1.43/networks/rogue-net/connect",
        body={"Container": "e" * 64},
        resolver=lambda ident: None,
    )
    assert not result.allowed and "fail-closed" in result.reason


def test_network_connect_nonprod_container_allowed():
    result = decide(
        "POST", "/v1.43/networks/bridge/connect", body={"Container": "nucpot-staging-api-1"}
    )
    assert result.allowed


# ---- unresolvable opaque container ids fail closed (NFM-4273 review W1) --------


def test_opaque_id_resolver_failure_fails_closed():
    """A transient daemon failure on the resolver roundtrip must deny, not
    pass the prod container off as non-prod."""
    result = decide("DELETE", f"/v1.43/containers/{'a' * 64}", resolver=lambda ident: None)
    assert not result.allowed and result.audit
    assert "fail-closed" in result.reason


def test_opaque_id_without_resolver_fails_closed():
    result = decide("POST", f"/v1.43/containers/{'b' * 64}/restart", resolver=None)
    assert not result.allowed and "fail-closed" in result.reason


def test_named_container_unaffected_by_resolver_failure():
    result = decide(
        "POST", "/v1.43/containers/nucpot-staging-api/stop", resolver=lambda ident: None
    )
    assert result.allowed


# ---- hex id prefixes of ANY length are opaque (NFM-4273 review R2) ---------------


def test_short_hex_network_id_prefix_fails_closed():
    """The daemon accepts id prefixes of any unambiguous length — an 11-hex
    prefix is as opaque as the full 64-hex id (networks have no resolver)."""
    prefix = "a1b2c3d4e5f"
    assert not decide("DELETE", f"/v1.43/networks/{prefix}").allowed
    assert not decide("POST", f"/v1.43/networks/{prefix}/connect", body={}).allowed
    assert not decide("POST", f"/v1.43/networks/{prefix}/disconnect", body={}).allowed


def test_short_hex_container_id_prefix_resolved_through_daemon():
    """Container ids DO resolve: a short-hex prefix must be treated as an
    id and roundtripped through the resolver, not passed as a 'name'."""
    prefix = "b1c2d3e4f5a"

    def resolver(ident):
        assert ident == prefix
        return TargetInfo(name="nucpot-prod-api", project="nucpot-prod")

    result = decide("POST", f"/v1.43/containers/{prefix}/restart", resolver=resolver)
    assert not result.allowed and result.scope == "prod"


def test_named_ref_skips_resolver():
    def resolver(ident):  # pragma: no cover - must not be called
        raise AssertionError("resolver must not be called for named refs")

    assert not decide("DELETE", "/v1.43/containers/nucpot-prod-api", resolver=resolver).allowed


# ---- non-prod mutations allowed + audited (staging/autovc/e2e keep working) --


def test_staging_mutation_allowed_and_audited():
    result = decide("POST", "/v1.43/containers/nucpot-staging-api/stop")
    assert result.allowed and result.audit and result.scope == "non-prod"


def test_staging_create_allowed():
    body = {"Labels": {"com.docker.compose.project": "nucpot-staging"}}
    result = decide("POST", "/v1.43/containers/create", "name=nucpot-staging-api-1", body=body)
    assert result.allowed


def test_unrelated_mutation_allowed():
    assert decide("DELETE", "/v1.43/containers/nucpot-autovc-repo-api-1").allowed
    assert decide("POST", "/v1.43/containers/supabase_db_nucpot/stop").allowed


# ---- image layer: allowed + audited (candidate builds tag prod-api images) ---


def test_build_allowed_with_tags_in_audit_target():
    result = decide("POST", "/v1.43/build", "t=nucpot-prod-api:candidate-abc")
    assert result.allowed and result.audit and result.target == "nucpot-prod-api:candidate-abc"


def test_pull_and_tag_allowed():
    assert decide("POST", "/v1.43/images/create", "fromImage=alpine&tag=x").allowed
    assert decide("POST", "/v1.43/images/candidate-build:latest/tag").allowed


def test_retag_of_prod_image_denied():
    """CR F1: re-tagging a prod image to an innocent name launders it past
    the push guard — same exfiltration class as push itself."""
    result = decide("POST", "/v1.43/images/nucpot-prod-api:latest/tag")
    assert not result.allowed and result.scope == "prod"
    assert "exfiltration" in result.reason


def test_image_rm_allowed():
    assert decide("DELETE", "/v1.43/images/alpine:latest").allowed


def test_prod_image_rm_denied():
    """CR F3: prod images are rollback generations — removing one from a
    bare terminal is a prod mutation."""
    result = decide("DELETE", "/v1.43/images/nucpot-prod-api:latest")
    assert not result.allowed and result.scope == "prod"


def test_image_rm_by_id_or_digest_denied_fail_closed():
    """V1: `docker rmi sha256:<id>` (or a bare hex prefix) reaches the prod
    image whatever its repo tags say — opaque refs fail closed."""
    digest = "sha256:" + "a1" * 32
    for name in (digest, "a1b2c3d4e5f67890"):
        result = decide("DELETE", f"/v1.43/images/{name}")
        assert not result.allowed and "fail-closed" in result.reason, name


def test_retag_by_opaque_image_id_denied_fail_closed():
    """V1: `docker tag <prod-image-id> innocent:latest` launders the image
    past the push guard when the id bypasses the name check."""
    result = decide("POST", "/v1.43/images/a1b2c3d4e5f67890/tag")
    assert not result.allowed and "fail-closed" in result.reason


def test_push_by_digest_denied_fail_closed():
    result = decide("POST", f"/v1.43/images/sha256:{'a1' * 32}/push")
    assert not result.allowed and "fail-closed" in result.reason


def test_push_prod_image_denied_exfiltration_guard():
    result = decide("POST", "/v1.43/images/nucpot-prod-api/push")
    assert not result.allowed


def test_push_non_prod_image_allowed():
    assert decide("POST", "/v1.43/images/alpine/push").allowed


# ---- daemon-wide prunes fail closed ------------------------------------------


def test_prunes_denied():
    # images/prune included since CR F3: an unfiltered image prune can
    # delete prod rollback generations.
    for path in (
        "containers/prune",
        "networks/prune",
        "volumes/prune",
        "build/prune",
        "images/prune",
    ):
        result = decide("POST", f"/v1.43/{path}")
        assert not result.allowed, path


# ---- escape-hatch configs denied regardless of scope --------------------------


def test_privileged_container_denied():
    result = decide(
        "POST",
        "/v1.43/containers/create",
        "name=harmless",
        body={"HostConfig": {"Privileged": True}},
    )
    assert not result.allowed and "Privileged" in result.reason


def test_docker_sock_mount_denied():
    body = {"HostConfig": {"Binds": ["/var/run/docker.sock:/x"]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed


def test_root_path_bind_denied():
    # HostConfig.Mounts is the live key (CR R2): the daemon ignores the
    # top-level Mounts this test used to pin — that path was dead logic.
    for source in ("/", "/Users", "/private/var/db", "/etc"):
        body = {"HostConfig": {"Mounts": [{"Type": "bind", "Source": source, "Target": "/x"}]}}
        result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
        assert not result.allowed, source


def test_pid_mode_host_denied():
    body = {"HostConfig": {"PidMode": "host"}}
    assert not decide("POST", "/v1.43/containers/create", "name=harmless", body=body).allowed


def test_users_home_bind_denied():
    body = {"HostConfig": {"Binds": ["/Users/lwj04/Projects/nucpot/data:/data"]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed  # /Users mounts would leak credentials to containers


def test_workspace_scoped_bind_allowed():
    body = {"HostConfig": {"Binds": ["/workspace/scratch:/data"]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert result.allowed


def test_tmp_volumes_opt_binds_denied():
    """V2: /tmp is the symlink spelling of the already-denied /private/tmp
    and hosts the deploy tooling's own host state (the health-gate marker,
    cutover snapshot dirs, the deploy DOCKER_CONFIG); /Volumes (external
    disks) and /opt (homebrew toolchain) are same-class takeover roots."""
    for source in (
        "/tmp",
        "/tmp/scratch",
        "/private/tmp/x",
        "/Volumes/Backup",
        "/opt/toolbox",
        "/opt/homebrew/bin",
    ):
        body = {"HostConfig": {"Binds": [f"{source}:/data"]}}
        result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
        assert not result.allowed, source


# ---- CR R2: HostConfig.Mounts bind-collection bypass shapes ----------------------


def test_hostconfig_mounts_type_bind_docker_sock_denied():
    """CR R2 executable bypass: `docker run --mount
    type=bind,source=/var/run/docker.sock,target=/x` lands in
    HostConfig.Mounts (the daemon IGNORES the top-level Mounts key the old
    dead loop read) — must deny exactly like the Binds spelling."""
    body = {
        "HostConfig": {
            "Mounts": [{"Type": "bind", "Source": "/var/run/docker.sock", "Target": "/x"}]
        }
    }
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed
    assert "forbidden bind source" in result.reason


def test_hostconfig_mounts_absolute_source_despite_volume_type_denied():
    """Declared Type "volume" with an ABSOLUTE Source is a bind by whatever
    spelling produced it — the type label is client-controlled text."""
    body = {
        "HostConfig": {
            "Mounts": [{"Type": "volume", "Source": "/var/run/docker.sock", "Target": "/x"}]
        }
    }
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed


def test_hostconfig_mounts_dot_segment_normalization_denied():
    """/var/run/./docker.sock collapses to the forbidden path under
    posixpath.normpath — the same string the daemon's mount resolver opens."""
    for source in (
        "/var/run/./docker.sock",
        "/var/run//docker.sock",
        "/var/run/docker.sock/.",
        "/var/run/../var/run/docker.sock",
    ):
        body = {"HostConfig": {"Mounts": [{"Type": "bind", "Source": source, "Target": "/x"}]}}
        result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
        assert not result.allowed, source


def test_hostconfig_mounts_case_variant_sock_denied():
    """APFS is case-insensitive: Docker.SOCK resolves to docker.sock."""
    body = {
        "HostConfig": {
            "Mounts": [{"Type": "bind", "Source": "/VAR/RUN/Docker.SOCK", "Target": "/x"}]
        }
    }
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed


def test_hostconfig_devices_path_on_host_docker_sock_denied():
    """--device /var/run/docker.sock:/dev/dsock is a character-device bind
    of the daemon socket — PathOnHost joins the same forbidden regex."""
    body = {
        "HostConfig": {
            "Devices": [{"PathOnHost": "/var/run/docker.sock", "PathInContainer": "/dev/dsock"}]
        }
    }
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed
    assert "forbidden bind source" in result.reason


def test_hostconfig_device_requests_fail_closed():
    """--gpus / DeviceRequests grant host device nodes by capability — not a
    path the bind regex can interpret, so presence itself denies."""
    body = {
        "HostConfig": {
            "Devices": [],
            "DeviceRequests": [{"Driver": "", "Count": -1, "Capabilities": [["gpu"]]}],
        }
    }
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed
    assert "DeviceRequests" in result.reason


# ---- CR R3: percent-encoding scope mismatch ---------------------------------------


def test_percent_encoded_path_denied():
    """%2F, %2E, %3A … in the path decode server-side into different segment
    boundaries than the gate scope-checked — never forward."""
    for path in (
        "/v1.43/containers/nucpot%2Dprod-api/stop",
        "/v1.43/images/nucpot-prod-api%3Alatest/tag",
        "/v1.43/containers/nucpot-prod-api%2Fstop",
    ):
        result = decide("POST", path)
        assert not result.allowed, path
        assert "percent-encoding in path" in result.reason


def test_percent_encoded_scope_query_value_denied():
    """NFM-4333 RC4 semantics: scope keys are percent-DECODED once and
    checked on what the daemon will resolve — an encoded prod name
    (`%2D` = '-') is caught BY SCOPE, not by encoding shape."""
    result = decide("POST", "/v1.43/containers/create", "name=nucpot%2Dprod-api-1", body={})
    assert not result.allowed and result.scope == "prod"


def test_percent_encoded_pull_of_registry_ref_allowed():
    """The RC4 fix's whole point: `docker build`/`docker pull` send
    multi-segment image refs percent-encoded in images/create — a
    sanctioned image-layer op that the old blanket '%' refusal blocked
    (candidate build died: "percent-encoded 'repo' value cannot be
    scope-checked")."""
    for query in (
        "fromImage=docker.io%2Flibrary%2Fpython&tag=3.12-slim",
        "fromImage=pgvector%2Fpgvector&tag=pg16",
        "repo=docker.io%2Flibrary%2Fpython&tag=latest",
    ):
        result = decide("POST", "/v1.43/images/create", query)
        assert result.allowed, query


def test_double_encoded_scope_value_denied_fail_closed():
    """One decode, never two: %25 (a literal '%') can only be an evasion
    attempt — the docker CLI never double-encodes."""
    result = decide("POST", "/v1.43/images/create", "fromImage=a%252Fb")
    assert not result.allowed and result.audit
    assert "fail-closed" in result.reason


def test_percent_encoded_read_filter_still_allowed():
    """Legitimate %-encoding in NON-scope read params (filters) is not
    collateral damage — reads stay frictionless."""
    result = decide(
        "GET", "/v1.43/containers/json", "filters=%7B%22status%22%3A%5B%22running%22%5D%7D"
    )
    assert result.allowed


def test_percent_encoded_name_in_read_query_denied():
    """ "name" is a scope key even on reads (docker inspect --filter by name
    reaches /containers/json?filters=… — but images/get?names= is the
    pinned exfiltration channel; the key list is shared)."""
    result = decide("GET", "/v1.43/images/get", "names=nucpot%2Dprod-api")
    assert not result.allowed


# ---- CR F1: image exfiltration guards ----------------------------------------------


def test_images_get_prod_name_denied():
    result = decide("GET", "/v1.43/images/get", "names=nucpot-prod-api:latest")
    assert not result.allowed and result.scope == "prod"
    assert "exfiltration" in result.reason


def test_images_get_non_prod_name_allowed():
    assert decide("GET", "/v1.43/images/get", "names=alpine:latest").allowed


def test_images_get_without_names_denied():
    """A bare images/get tars EVERY image on the daemon, prod included."""
    result = decide("GET", "/v1.43/images/get")
    assert not result.allowed and result.audit


def test_images_get_opaque_id_or_digest_denied_fail_closed():
    """V1: `docker save sha256:<prod-image-id>` exports prod layers while
    the name check sees only the digest — opaque refs fail closed."""
    digest = "sha256:" + "a1" * 32
    for names in (f"names={digest}", f"names={digest}&names=alpine:latest"):
        result = decide("GET", "/v1.43/images/get", names)
        assert not result.allowed and "fail-closed" in result.reason, names


# ---- NFM-4333: single-image inspect is a sanctioned read ---------------------
# `docker compose up` resolves every service image via GET
# images/{ref}/json; it was fail-closed in BOTH modes and blocked all
# sanctioned deploys. The full list (images/json) is already an allowed
# read returning the same metadata per image — the item endpoint exposes
# strictly less data.


def test_get_image_inspect_allowed_ro_mode():
    for ref in ("nucpot-prod-api:latest", "alpine:3.20", "pgvector/pgvector:pg16"):
        result = decide("GET", f"/v1.43/images/{ref}/json")
        assert result.allowed, ref


def test_get_image_inspect_allowed_full_mode():
    result = decide("GET", "/v1.43/images/nucpot-prod-api:c96b8c4d1/json", full=True)
    assert result.allowed


def test_get_image_inspect_does_not_widen_layer_export():
    # The read fix must not touch the images/get exfiltration guard.
    assert not decide("GET", "/v1.43/images/get", "names=nucpot-prod-api:latest").allowed


def test_get_image_other_subresources_still_fail_closed():
    # Only inspect (json) is allowed; e.g. history stays unrecognized.
    result = decide("GET", "/v1.43/images/alpine:3.20/history")
    assert not result.allowed and result.audit


# ---- CR F4: opaque volume ids -------------------------------------------------------


def test_opaque_hex_volume_rm_denied_fail_closed():
    """Anonymous volume names are 64-hex — text-indistinguishable from a prod
    anonymous volume; deny rather than guess."""
    result = decide("DELETE", f"/v1.43/volumes/{'c' * 64}")
    assert not result.allowed
    assert "fail-closed" in result.reason


def test_plain_volume_rm_still_allowed():
    assert decide("DELETE", "/v1.43/volumes/dev_scratch").allowed


# ---- full (sanctioned) mode ----------------------------------------------------


def test_full_mode_allows_prod_stop_with_audit():
    result = decide("POST", "/v1.43/containers/nucpot-prod-api/stop", full=True)
    assert result.allowed and result.audit


def test_full_mode_prune_allowed():
    assert decide("POST", "/v1.43/containers/prune", full=True).allowed


# ---- unknown mutations fail closed ------------------------------------------------


def test_unknown_post_fail_closed():
    assert not decide("POST", "/v1.43/swarm/init").allowed
    assert not decide("POST", "/v1.43/plugins/pull").allowed
    assert not decide("PATCH", "/v1.43/whatever").allowed
