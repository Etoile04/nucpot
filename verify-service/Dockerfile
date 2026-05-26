FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for ASE/scipy
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ gfortran \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default environment
ENV VERIFY_HOST=0.0.0.0
ENV VERIFY_PORT=8000
ENV DATABASE_URL=postgresql://nucpot:nucpot_dev@nucpot-db:5432/nucpot

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
