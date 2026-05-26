# Multi-stage build for NucPot Verify Service
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim

WORKDIR /app

# Copy installed dependencies from builder stage
COPY --from=builder /install /usr/local

# Copy application source
COPY pyproject.toml .
COPY src/ src/

# Install the package
RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "verify.api.routes:app", "--host", "0.0.0.0", "--port", "8000"]
