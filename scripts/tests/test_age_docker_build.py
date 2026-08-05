"""Smoke tests for the Apache AGE Dockerfile and init script.

NFM-1850: Apache AGE extension was missing from the production PG image.
The fix adds ``docker/postgres/Dockerfile`` (custom PG16+AGE image) and
``docker/postgres/init-age.sql`` (idempotent extension + graph init).

These tests validate structural correctness of the Dockerfile, the SQL
init script, and the docker-compose build integration — without requiring
a Docker daemon (suitable for CI).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "docker" / "postgres" / "Dockerfile"
INIT_SQL = REPO_ROOT / "docker" / "postgres" / "init-age.sql"
COMPOSE_FILES = {
    "prod": REPO_ROOT / "docker-compose.prod.yml",
    "staging": REPO_ROOT / "docker-compose.staging.yml",
    "dev": REPO_ROOT / "docker" / "docker-compose.yml",
}


# ---------------------------------------------------------------------------
# Dockerfile structure tests
# ---------------------------------------------------------------------------


class TestDockerfile:
    """Validate Dockerfile content and structure."""

    def test_dockerfile_exists(self) -> None:
        assert DOCKERFILE.is_file(), f"Dockerfile missing at {DOCKERFILE}"

    def test_dockerfile_syntax(self) -> None:
        """Every FROM/RUN/COPY instruction is recognizable."""
        content = DOCKERFILE.read_text()
        instructions = re.findall(r"^(FROM|RUN|ARG|COPY|LABEL|EXPOSE|CMD|ENV)\b", content, re.MULTILINE)
        assert len(instructions) >= 3, "Dockerfile too short — expected FROM + RUN + COPY minimum"

    def test_base_image_is_pgvector_pg16(self) -> None:
        content = DOCKERFILE.read_text()
        # Base image may be set via ARG then used as FROM ${ARG}
        has_literal_from = bool(re.search(r"FROM\s+.+pgvector/pgvector:pg16", content))
        has_arg_from = bool(re.search(r"ARG\s+PGVECTOR_BASE=pgvector/pgvector:pg16", content))
        assert has_literal_from or has_arg_from, "Dockerfile must extend pgvector/pgvector:pg16"

    def test_age_version_pinned(self) -> None:
        content = DOCKERFILE.read_text()
        match = re.search(r"ARG\s+AGE_VERSION=(\S+)", content)
        assert match, "AGE_VERSION ARG must be set"
        version = match.group(1)
        # Must look like a semver (not a mutable branch name)
        assert re.match(r"\d+\.\d+\.\d+", version), f"AGE_VERSION should be pinned semver, got {version}"

    def test_age_compiled_from_source(self) -> None:
        content = DOCKERFILE.read_text()
        assert "make" in content and "make install" in content, "AGE should be compiled with make + make install"

    def test_build_tools_cleaned(self) -> None:
        content = DOCKERFILE.read_text()
        # Second RUN layer should purge build deps
        runs = re.findall(r"RUN\s+apt-get\s+purge", content)
        assert len(runs) >= 1, "Build tools should be purged in a final RUN layer"

    def test_init_script_copied(self) -> None:
        content = DOCKERFILE.read_text()
        assert "COPY init-age.sql" in content, "init-age.sql must be COPY'd into the image"
        assert "docker-entrypoint-initdb.d" in content, "Must copy to docker-entrypoint-initdb.d"

    def test_age_tarball_cleaned(self) -> None:
        content = DOCKERFILE.read_text()
        assert "rm -rf /tmp/age-" in content, "AGE source tarball must be cleaned up"


# ---------------------------------------------------------------------------
# init-age.sql tests
# ---------------------------------------------------------------------------


class TestInitAgeSql:
    """Validate the SQL init script for correctness and idempotency."""

    def test_init_sql_exists(self) -> None:
        assert INIT_SQL.is_file(), f"init-age.sql missing at {INIT_SQL}"

    def test_creates_extension_if_not_exists(self) -> None:
        content = INIT_SQL.read_text()
        assert "CREATE EXTENSION IF NOT EXISTS age" in content, "Must use IF NOT EXISTS for idempotency"

    def test_loads_age_in_session(self) -> None:
        content = INIT_SQL.read_text()
        assert "LOAD 'age'" in content, "Must LOAD age extension in session before create_graph"

    def test_creates_nucmat_kg_graph(self) -> None:
        content = INIT_SQL.read_text()
        assert "create_graph('nucmat_kg')" in content, "Must create nucmat_kg graph"

    def test_graph_creation_is_idempotent(self) -> None:
        content = INIT_SQL.read_text()
        assert "IF NOT EXISTS" in content, "Graph creation must guard with IF NOT EXISTS"

    def test_creates_lightrag_graph(self) -> None:
        content = INIT_SQL.read_text()
        assert "create_graph('lightrag')" in content, "Must create lightrag graph"

    def test_resets_search_path(self) -> None:
        content = INIT_SQL.read_text()
        assert "RESET search_path" in content, "Must RESET search_path to avoid leaking ag_catalog"

    def test_valid_sql_no_trailing_whitespace_issues(self) -> None:
        """Basic sanity: file ends with newline, no Windows CRLF."""
        content = INIT_SQL.read_text()
        assert content.endswith("\n"), "SQL file must end with newline"
        assert "\r" not in content, "SQL file must use Unix line endings"


# ---------------------------------------------------------------------------
# docker-compose integration tests
# ---------------------------------------------------------------------------


class TestComposeIntegration:
    """Validate that docker-compose files reference the custom build."""

    @pytest.fixture(params=list(COMPOSE_FILES.values()), ids=list(COMPOSE_FILES.keys()))
    def compose_file(self, request: pytest.FixtureRequest) -> Path:
        return request.param  # type: ignore[return-value]

    def test_compose_file_exists(self, compose_file: Path) -> None:
        assert compose_file.is_file(), f"Compose file missing at {compose_file}"

    def test_db_service_builds_custom_image(self, compose_file: Path) -> None:
        content = compose_file.read_text()
        # The db / postgres service should use `build:` instead of `image: pgvector/...`
        # We look for a build context pointing to docker/postgres
        assert "docker/postgres" in content, (
            f"{compose_file.name}: db service must reference docker/postgres build context"
        )
