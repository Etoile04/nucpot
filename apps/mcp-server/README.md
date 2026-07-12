# NFM MCP Server

Model Context Protocol server for the Nuclear Fuel & Materials Properties Database (NFM).
Exposes nuclear materials, property data, thermodynamic potentials, literature sources,
domain ontology, knowledge graph, and document extraction pipelines as MCP tools.

## Prerequisites

- **Python 3.12+**
- [uv](https://docs.astral.sh/uv/) — package manager
- PostgreSQL (asyncpg driver) or SQLite for tests

## Quick Start

```bash
cd apps/mcp-server
uv sync
uv run nfm-mcp-server          # stdio transport (default for Claude Code)
uv run nfm-mcp-server --transport streamable_http --port 8002
```

## Configuration

All settings use the `NFM_MCP_` prefix and can be placed in a `.env` file
next to the entry-point.

| Variable | Default | Description |
|---|---|---|
| `NFM_MCP_DATABASE_URL` | `postgresql+asyncpg://nfm:nfm@localhost:5432/nfm` | Async database connection string |
| `NFM_MCP_DATABASE_POOL_SIZE` | `5` | SQLAlchemy connection pool size |
| `NFM_MCP_DATABASE_POOL_TIMEOUT` | `30.0` | Pool acquire timeout (seconds) |
| `NFM_MCP_API_BASE_URL` | `http://localhost:8000/v1` | NFM REST API base URL (fallback) |
| `NFM_MCP_API_TIMEOUT` | `30.0` | REST API request timeout (seconds) |
| `NFM_MCP_KG_SERVICE_URL` | `http://localhost:8001` | Knowledge-graph service URL |
| `NFM_MCP_LLM_SERVICE_URL` | `http://localhost:8003` | LLM service for extraction pipeline |
| `NFM_MCP_TRANSPORT` | `stdio` | Transport: `stdio`, `streamable_http`, or `sse` |
| `NFM_MCP_HOST` | `127.0.0.1` | Listen host (HTTP/SSE transports) |
| `NFM_MCP_PORT` | `8002` | Listen port (HTTP/SSE transports) |
| `NFM_MCP_LOG_LEVEL` | `INFO` | Logging verbosity |

## Available Tools

| Tool | Description |
|---|---|
| `search_materials` | Search the NFM database for nuclear fuel and materials |
| `get_material` | Retrieve detailed information about a specific material |
| `query_properties` | Query property data (thermal, mechanical, etc.) for a material |
| `search_sources` | Search the literature source database |
| `query_potentials` | Query thermodynamic potential models for a material |
| `browse_ontology` | Browse the NFM domain ontology tree |
| `query_knowledge_graph` | Query the knowledge graph for material relationships |
| `trigger_extraction` | Submit a document for data extraction |
| `get_extraction_status` | Check the status of an extraction job |

## Claude Code Integration

### Prerequisites

- [uv](https://docs.astral.sh/uv/) installed and on `PATH`
- PostgreSQL running (or adjust `NFM_MCP_DATABASE_URL` to point to your DB)
- NFM API dependencies installed (`uv --project apps/mcp-server sync`)

### Quick Start

1. Copy the example config to your project root:

   ```bash
   cp .mcp.json.example .mcp.json
   ```

2. Edit `.mcp.json` and set your `NFM_MCP_DATABASE_URL`.

3. Restart Claude Code — it auto-discovers `.mcp.json` in the project root.

### Configuration

Add to your project's `.mcp.json` (or Claude Code's global config):

```json
{
  "mcpServers": {
    "nfm": {
      "command": "uv",
      "args": ["--project", "apps/mcp-server", "run", "nfm-mcp-server"],
      "env": {
        "NFM_MCP_DATABASE_URL": "postgresql+asyncpg://nfm:nfm@localhost:5432/nfm"
      }
    }
  }
}
```

### Verification

After restarting Claude Code, confirm the MCP server is connected:

1. All 9 tools should appear: `search_materials`, `get_material`, `query_properties`,
   `search_sources`, `query_potentials`, `browse_ontology`, `query_knowledge_graph`,
   `trigger_extraction`, `get_extraction_status`.
2. Try a quick query: "Search for uranium dioxide materials" — Claude Code should
   use the `search_materials` tool against the live database.

## Development

```bash
uv sync --extra dev          # install dev dependencies
uv run pytest                # run unit tests (80% coverage gate)
uv run ruff check src tests  # lint
uv run mypy src              # typecheck
```

Integration tests (requiring live services) are excluded by default:

```bash
uv run pytest -m integration
```

## Architecture

See [ADR-823](../../docs/design/adr-nfm-823-mcp-server-architecture.md) for the
architectural decision record covering transport design, tool registration patterns,
and dependency injection strategy.
