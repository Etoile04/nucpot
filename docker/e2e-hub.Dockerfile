FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/api/src:/app/node/src

COPY apps/api/pyproject.toml /app/api/pyproject.toml
COPY apps/api/src/ /app/api/src/
COPY apps/api/migrations/ /app/api/migrations/
COPY apps/api/alembic.ini /app/api/alembic.ini
COPY apps/api/e2e/ /app/api/e2e/
COPY apps/nfm-node-client/pyproject.toml /app/node/pyproject.toml
COPY apps/nfm-node-client/src/ /app/node/src/

RUN pip install --no-cache-dir --default-timeout=120 --retries=5 \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      /app/api /app/node || \
    pip install --no-cache-dir --default-timeout=180 --retries=10 /app/api /app/node

EXPOSE 8000
CMD ["sh", "-c", "cd /app/api && alembic upgrade heads && python /app/api/e2e/seed_hub.py && exec uvicorn nfm_db.main:app --host 0.0.0.0 --port 8000 --http h11"]
