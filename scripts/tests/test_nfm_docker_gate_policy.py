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
