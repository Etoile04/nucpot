# NFM-DB — 核燃料与材料物性数据库

> 可持续共享的核燃料与材料物性数据库平台
>
> Nuclear Fuel & Materials Properties Database — a sustainable and sharing platform for nuclear materials data in China.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 15 (App Router) + TypeScript + Ant Design 5 |
| Backend | Python 3.12+ / FastAPI |
| Database | PostgreSQL 16 |
| Package Manager | pnpm (JS) / uv (Python) |
| CI/CD | GitHub Actions |

## Prerequisites

- **Node.js** ≥ 20 (LTS recommended)
- **pnpm** ≥ 9 (`corepack enable && corepack prepare pnpm@latest --activate`)
- **Python** ≥ 3.12
- **uv** (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Docker** (for PostgreSQL, optional if you have a local instance)

## Quick Start

```bash
# 1. Clone and enter the repo
git clone <repo-url> && cd nfm-db

# 2. Start PostgreSQL (one-time)
docker compose -f docker/docker-compose.yml up -d

# 3. Install frontend dependencies
pnpm install

# 4. Install backend dependencies
cd apps/api && uv sync --dev && cd ../..

# 5. Start both dev servers (in separate terminals)
pnpm dev          # Frontend → http://localhost:3000
pnpm dev:api      # Backend  → http://localhost:8000
```

That's it — the frontend runs on port 3000 with hot reload, and the API on port 8000 with auto-reload.

## Project Structure

```
nfm-db/
├── apps/
│   ├── web/              # Next.js 15 frontend
│   │   ├── src/
│   │   │   ├── app/      # App Router pages
│   │   │   ├── components/
│   │   │   ├── lib/
│   │   │   └── styles/
│   │   └── e2e/          # Playwright E2E tests
│   └── api/              # FastAPI backend
│       ├── src/nfm_db/
│       │   ├── api/v1/   # API routes
│       │   ├── models/   # SQLAlchemy models
│       │   ├── schemas/  # Pydantic schemas
│       │   └── services/ # Business logic
│       └── tests/
├── packages/
│   ├── shared/           # Shared TypeScript types
│   └── config/           # Shared config (ESLint, etc.)
├── docker/               # Docker Compose & Dockerfiles
├── docs/                 # Project documentation
├── .github/workflows/    # CI pipeline
├── pnpm-workspace.yaml
├── pyproject.toml        # Python project config (Ruff, mypy, pytest)
└── README.md
```

## Scripts

| Command | Description |
|---------|-------------|
| `pnpm dev` | Start Next.js dev server (port 3000) |
| `pnpm dev:api` | Start FastAPI dev server (port 8000) |
| `pnpm build` | Build the Next.js frontend |
| `pnpm lint` | Lint all packages |
| `pnpm typecheck` | Type-check all packages |
| `pnpm test` | Run unit tests |
| `pnpm test:e2e` | Run Playwright E2E tests |

## Backend (Python)

```bash
cd apps/api

# Install dependencies
uv sync --dev

# Run dev server with auto-reload
uvicorn nfm_db.main:app --reload --port 8000

# Run tests
uv run pytest

# Lint
uv run ruff check src tests

# Type check
uv run mypy src
```

API documentation is auto-generated at `http://localhost:8000/docs` (Swagger UI).

## Frontend (Next.js)

```bash
cd apps/web

# Install dependencies
pnpm install

# Run dev server
pnpm dev

# Run tests
pnpm test

# Type check
pnpm typecheck
```

## CI Pipeline

GitHub Actions runs on every push to `main` and on pull requests:

1. **Frontend**: lint → type-check → unit tests → build
2. **Backend**: lint (ruff) → type-check (mypy) → tests (pytest)

Both jobs must pass before a PR can be merged.

## Environment Variables

Create `.env` files for local development:

**Backend** (`apps/api/.env`):
```
NFM_DATABASE_URL=postgresql+asyncpg://nfm:nfm@localhost:5432/nfm_db
NFM_DEBUG=true
```

## License

Proprietary — all rights reserved.
