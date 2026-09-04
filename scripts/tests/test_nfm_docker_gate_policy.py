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
    Decision,
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
    for sub in ("json", "logs", "stats", "top", "changes", "export", "ports"):
        assert decide("GET", f"/v1.43/containers/abc123/{sub}").allowed, sub


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
    assert not decide("POST", "/v1.43/networks/create", body={"Name": "nucpot-prod_default"}).allowed
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
    body = {"HostConfig": {"Mounts": [
        {"Type": "volume", "Source": "nucpot-prod_pgdata", "Target": "/data"}
    ]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed and result.scope == "prod"


def test_create_mounting_nonprod_volume_allowed():
    body = {"HostConfig": {
        "Binds": ["nucpot-staging_cache:/cache"],
        "Mounts": [{"Type": "volume", "Source": "dev_scratch", "Target": "/s"}],
    }}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert result.allowed


def test_bind_mount_paths_are_not_volume_refs():
    """Absolute bind sources are bind mounts (checked separately by
    _FORBIDDEN_BIND_RE) — they must not crash or pollute volume scoping."""
    body = {"HostConfig": {"Binds": ["/tmp/scratch:/data"],
                           "Mounts": [{"Type": "bind", "Source": "/Users/x", "Target": "/u"}]}}
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
    body = {"NetworkingConfig": {"EndpointsConfig": {"1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef": {}}}}
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
    body = {"HostConfig": {
        "VolumesFrom": ["nucpot-staging-db-1:rw"],
        "NetworkMode": "container:nucpot-staging-api-1",
    }}
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
    for mode in ("default", "bridge", "none", "host", "nucpot-staging_default"):
        body = {"HostConfig": {"NetworkMode": mode}}
        result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
        assert result.allowed, mode


# ---- network connect/disconnect body container ref (NFM-4273 review E2) --------


def test_network_connect_to_prod_container_denied():
    """``docker network connect rogue-net nucpot-prod-api-1`` — the network
    is non-prod but the CONNECT BODY's Container ref is a prod container."""
    result = decide("POST", "/v1.43/networks/rogue-net/connect",
                    body={"Container": "nucpot-prod-api-1"})
    assert not result.allowed and result.scope == "prod"
    assert "nucpot-prod-api-1" in result.reason


def test_network_disconnect_prod_container_denied():
    result = decide("POST", "/v1.43/networks/rogue-net/disconnect",
                    body={"Container": "nucpot-prod-db-1"})
    assert not result.allowed and result.scope == "prod"


def test_network_connect_container_hex_ref_resolved_through_daemon():
    prefix = "c1d2e3f4a5b"

    def resolver(ident):
        assert ident == prefix
        return TargetInfo(name="nucpot-prod-api", project="nucpot-prod")

    result = decide("POST", "/v1.43/networks/rogue-net/connect",
                    body={"Container": prefix}, resolver=resolver)
    assert not result.allowed and result.scope == "prod"


def test_network_connect_container_hex_ref_unresolvable_fails_closed():
    result = decide("POST", "/v1.43/networks/rogue-net/connect",
                    body={"Container": "e" * 64}, resolver=lambda ident: None)
    assert not result.allowed and "fail-closed" in result.reason


def test_network_connect_nonprod_container_allowed():
    result = decide("POST", "/v1.43/networks/bridge/connect",
                    body={"Container": "nucpot-staging-api-1"})
    assert result.allowed


# ---- unresolvable opaque container ids fail closed (NFM-4273 review W1) --------


def test_opaque_id_resolver_failure_fails_closed():
    """A transient daemon failure on the resolver roundtrip must deny, not
    pass the prod container off as non-prod."""
    result = decide("DELETE", f"/v1.43/containers/{'a' * 64}",
                    resolver=lambda ident: None)
    assert not result.allowed and result.audit
    assert "fail-closed" in result.reason


def test_opaque_id_without_resolver_fails_closed():
    result = decide("POST", f"/v1.43/containers/{'b' * 64}/restart", resolver=None)
    assert not result.allowed and "fail-closed" in result.reason


def test_named_container_unaffected_by_resolver_failure():
    result = decide("POST", "/v1.43/containers/nucpot-staging-api/stop",
                    resolver=lambda ident: None)
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
    assert decide("POST", "/v1.43/images/create", "fromImage=nucpot-prod-api&tag=x").allowed
    assert decide("POST", "/v1.43/images/nucpot-prod-api%3Alatest/tag").allowed


def test_image_rm_allowed():
    assert decide("DELETE", "/v1.43/images/nucpot-prod-api:latest").allowed


def test_push_prod_image_denied_exfiltration_guard():
    result = decide("POST", "/v1.43/images/nucpot-prod-api/push")
    assert not result.allowed


def test_push_non_prod_image_allowed():
    assert decide("POST", "/v1.43/images/alpine/push").allowed


# ---- daemon-wide prunes fail closed ------------------------------------------


def test_prunes_denied():
    for path in ("containers/prune", "networks/prune", "volumes/prune", "build/prune"):
        result = decide("POST", f"/v1.43/{path}")
        assert not result.allowed, path


def test_image_prune_allowed():
    assert decide("POST", "/v1.43/images/prune").allowed


# ---- escape-hatch configs denied regardless of scope --------------------------


def test_privileged_container_denied():
    result = decide("POST", "/v1.43/containers/create", "name=harmless",
                    body={"HostConfig": {"Privileged": True}})
    assert not result.allowed and "Privileged" in result.reason


def test_docker_sock_mount_denied():
    body = {"HostConfig": {"Binds": ["/var/run/docker.sock:/x"]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert not result.allowed


def test_root_path_bind_denied():
    for source in ("/", "/Users", "/private/var/db", "/etc"):
        body = {"Mounts": [{"Source": source, "Target": "/x"}]}
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
    body = {"HostConfig": {"Binds": ["/tmp/scratch:/data"]}}
    result = decide("POST", "/v1.43/containers/create", "name=harmless", body=body)
    assert result.allowed


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
