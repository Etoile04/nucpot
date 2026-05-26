# NucPot Verify Service

Automated verification service for nuclear material interatomic potentials.

## Architecture

```
verify-service/
├── src/verify_service/
│   ├── api/          # FastAPI REST endpoints
│   ├── core/         # Calculation engine (ASE + grading)
│   ├── workers/      # Celery async tasks
│   └── supabase.py   # Supabase client
```

## Quick Start

```bash
# Install
cd verify-service
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env with your Supabase credentials

# Run API
python -m verify_service

# Run worker (separate terminal)
celery -A verify_service.workers.celery_app worker --loglevel=info
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/health | Health check |
| POST | /api/verify/{potential_id} | Submit verification job |
| GET | /api/verify/{potential_id}/status | Get verification status |
| GET | /api/reference-values | List reference values |
| POST | /api/reference-values | Add reference value |

## Calculated Properties

- **Lattice constant**: BFGS relaxation + volume→a conversion
- **Bulk modulus**: Birch-Murnaghan EOS fit from E-V curve
- **Cohesive energy**: E_isolated - E_bulk/N
- **Vacancy formation energy**: E_vac - (N-1)/N * E_perfect

## Grading

| Grade | Relative Error |
|-------|---------------|
| A | ≤ 1% |
| B | ≤ 3% |
| C | ≤ 5% |
| D | ≤ 10% |
| F | > 10% |
