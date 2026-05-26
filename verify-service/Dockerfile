FROM python:3.11-slim

# Install system deps for ASE/scientific computing
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ gfortran \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

# Copy source
COPY src/ src/
COPY tests/ tests/

# Activate KIM API if available (mounted volume)
ENV KIM_API_PATH=/usr/local/kim-api
ENV PKG_CONFIG_PATH=${KIM_API_PATH}/lib/pkgconfig:${PKG_CONFIG_PATH}
ENV LD_LIBRARY_PATH=${KIM_API_PATH}/lib:${LD_LIBRARY_PATH}

EXPOSE 8000

CMD ["python", "-m", "verify_service"]
