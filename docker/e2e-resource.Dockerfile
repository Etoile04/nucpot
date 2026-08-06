FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src

COPY apps/nfm-node-client/pyproject.toml /app/pyproject.toml
COPY apps/nfm-node-client/src/ /app/src/
COPY apps/nfm-node-client/e2e/ /app/e2e/

RUN pip install --no-cache-dir --default-timeout=120 --retries=5 \
      -i https://pypi.tuna.tsinghua.edu.cn/simple . || \
    pip install --no-cache-dir --default-timeout=180 --retries=10 .

CMD ["python", "/app/e2e/resource_daemon.py"]
