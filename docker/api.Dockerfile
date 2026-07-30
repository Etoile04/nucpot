FROM python:3.12-slim

WORKDIR /app

# Install uv for faster, more resilient dependency resolution.
# uv retries automatically on network errors and falls back
# between indexes — no manual mirror fallback chain needed.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

COPY src/ ./src/

EXPOSE 8000

CMD ["uvicorn", "nfm_db.main:app", "--host", "0.0.0.0", "--port", "8000"]
