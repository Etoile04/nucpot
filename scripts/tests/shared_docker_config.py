"""NFM-4807: read-only fingerprint of the production shared DOCKER_CONFIG.

Incident (2026-09-12, run 34705930216): /tmp/nfm848-no-cred-docker-config
was shared mutable state — concurrent deploy/pytest sessions re-pointed its
compose-plugin symlink at garbage-collected tmpdirs. Every test that
executes deploy_prod.sh snapshots this path before and after and asserts
equality: nothing may mutate it. Importers:
test_nfm_4807_docker_config_isolation, test_check_deploy_drift,
test_check_prod_image_tag.
"""

import os
from pathlib import Path

# The pre-NFM-4807 shared literal. Tests only ever READ this path.
SHARED_DOCKER_CONFIG = Path("/tmp/nfm848-no-cred-docker-config")


def snapshot_shared_config() -> dict:
    """Read-only fingerprint of the shared literal, before/after a run."""
    link = SHARED_DOCKER_CONFIG / "cli-plugins" / "docker-compose"
    cfg = SHARED_DOCKER_CONFIG / "config.json"
    return {
        "dir_exists": SHARED_DOCKER_CONFIG.exists(),
        "link_exists": link.is_symlink() or link.exists(),
        "link_target": os.readlink(link) if link.is_symlink() else None,
        "config_json": cfg.read_bytes() if cfg.is_file() else None,
    }
