FROM python:3.12-slim

WORKDIR /app

# NFM-2239: install from apps/api/ — the repo-root pyproject.toml + repo-root
# src/ layout this Dockerfile previously used is dead (root src/nfm_db has no
# __init__.py or main.py, so `pip install .` ships only the metadata shell and
# `import nfm_db` fails with ModuleNotFoundError). The staging and prod
# Dockerfiles already point at apps/api/; this Dockerfile is the broken
# sibling the issue's verification command exercises. Copy both the project
# definition and the source tree BEFORE `pip install .` so setuptools'
# [tool.setuptools.packages.find] where=["src"] can locate the package and
# [tool.setuptools.package-data] can ship nfm_db/config/*.json alongside it.
COPY apps/api/pyproject.toml ./pyproject.toml
COPY apps/api/src/ ./src/
RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "nfm_db.main:app", "--host", "0.0.0.0", "--port", "8000"]
