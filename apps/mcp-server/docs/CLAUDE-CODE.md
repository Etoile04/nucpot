# NFM MCP Server — Claude Code Integration Guide

> Nuclear Fuel & Materials Properties Database (NFM) MCP Server.
> Provides tools to search nuclear materials, query property data,
> browse the domain ontology, query the knowledge graph, and
> trigger document extraction pipelines.

## Prerequisites

- **Python 3.12+** with [uv](https://docs.astral.sh/uv/) package manager
- **PostgreSQL** running (asyncpg driver)
- Claude Code CLI (`claude`)

## Setup

### 1. Install Dependencies

```bash
cd apps/mcp-server
cp .env.example .env          # edit DATABASE_URL to match your environment
uv sync
```

### 2. Configure Claude Code

Add the MCP server to your project's `.mcp.json` (auto-discovered by Claude Code):

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

Or configure globally in Claude Code settings (`~/.claude/settings.json`).

### 3. Verify

Restart Claude Code and confirm all 9 tools appear in the tool list.

Run a quick query to verify connectivity:

> "Search for uranium dioxide materials"

Claude Code should call `search_materials` against the live database.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NFM_MCP_DATABASE_URL` | `postgresql+asyncpg://nfm:nfm@localhost:5432/nfm` | Async database connection string |
| `NFM_MCP_DATABASE_POOL_SIZE` | `5` | SQLAlchemy connection pool size |
| `NFM_MCP_DATABASE_POOL_TIMEOUT` | `30.0` | Pool acquire timeout (seconds) |
| `NFM_MCP_API_BASE_URL` | `http://localhost:8000/v1` | NFM REST API base URL (fallback) |
| `NFM_MCP_KG_SERVICE_URL` | `http://localhost:8001` | Knowledge-graph service URL |
| `NFM_MCP_LLM_SERVICE_URL` | `http://localhost:8003` | LLM service for extraction pipeline |
| `NFM_MCP_TRANSPORT` | `stdio` | Transport: `stdio`, `streamable_http`, or `sse` |
| `NFM_MCP_LOG_LEVEL` | `INFO` | Logging verbosity |

## Tool Reference

### search_materials

Search the NFM database for nuclear fuel and materials by name,
composition, or alias. Results are ranked by relevance.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | Free-text search (e.g., "UO2 thermal conductivity") |
| `material_type` | string | No | Filter by type: `fuel`, `cladding`, `coolant` |
| `limit` | integer | No | Max results (1–100, default 20) |
| `offset` | integer | No | Pagination offset (default 0) |

**Example queries:**
- "Find all UO2 fuel materials"
- "Search for zirconium alloy cladding"
- "List materials with thermal conductivity data"

### get_material

Retrieve detailed information about a specific nuclear material by UUID,
including composition, crystal structure, aliases, and available property
categories.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `material_id` | string | Yes | UUID identifier of the material |

**Example queries:**
- "Get details for material 550e8400-e29b-41d4-a716-446655440000"
- "Show the full record for this uranium dioxide entry"

### query_properties

Query measured and calculated property data for a material: thermal
conductivity, density, specific heat, Young's modulus, thermal expansion,
and more.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `material_id` | string | Yes | UUID of the material |
| `property_name` | string | No | Filter by property (e.g., `thermal_conductivity`) |
| `temperature_range` | string | No | Range filter (e.g., "300-1500 K") |
| `limit` | integer | No | Max data points (1–500, default 50) |

**Example queries:**
- "Get thermal conductivity data for UO2 between 300 and 1500 K"
- "Show all density measurements for this zirconium alloy"
- "What properties are available for this material?"

### search_sources

Search the literature source database — journal articles, technical
reports, handbooks, and other references cited as data sources.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | Search by author, title, journal, or DOI |
| `source_type` | string | No | Filter: `journal`, `report`, `handbook` |
| `limit` | integer | No | Max results (1–100, default 20) |
| `offset` | integer | No | Pagination offset (default 0) |

**Example queries:**
- "Find papers by Fink and Lucuta on UO2 properties"
- "Search for NUREG technical reports"
- "What are the main handbooks referenced for thermal conductivity?"

### query_potentials

Query thermodynamic potential models — Gibbs energy, enthalpy, entropy,
heat capacity — with their parametric coefficients and valid temperature
ranges.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `material_id` | string | Yes | Material identifier |
| `potential_type` | string | No | Filter: `Gibbs`, `enthalpy`, `Cp` |
| `model_name` | string | No | Specific model (e.g., `FINK-LUCUTA2`) |
| `temperature_range` | string | No | Range filter (e.g., "300-3000 K") |

**Example queries:**
- "Get all Gibbs energy models for UO2"
- "Show thermodynamic potentials valid above 2000 K"
- "What is the Fink-Lucuta2 model for uranium dioxide?"

### browse_ontology

Browse the NFM domain ontology — the hierarchical classification of
nuclear fuel materials, properties, measurement types, and relationships.
Useful for discovering valid search terms and understanding the data model.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | No | Search term (e.g., `fuel`, `thermal`) |
| `entity_type` | string | No | Filter: `material`, `property`, `source` |
| `parent_id` | string | No | Start from this ontology node |
| `limit` | integer | No | Max nodes (1–200, default 50) |

**Example queries:**
- "Show the ontology structure for fuel materials"
- "What property types are available in the ontology?"
- "Browse under the thermal conductivity node"

### query_knowledge_graph

Query the semantic knowledge graph connecting materials, properties,
sources, and measurement conditions. Use for complex cross-referencing
queries.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | Natural-language or Cypher-like query |
| `entity_types` | list[string] | No | Filter by types: `material`, `property`, `source` |
| `limit` | integer | No | Max results (1–100, default 20) |

**Example queries:**
- "Find materials with thermal conductivity above 10 W/mK"
- "What sources report UO2 density data?"
- "Show relationships between fuel and cladding materials"

### trigger_extraction

Submit a document for automated data extraction. Starts an async pipeline
that parses the document, extracts material property data, and inserts
it into the NFM database. Returns a job ID for tracking.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_url` | string | Yes | URL or path of the document |
| `material_id` | string | No | Target material ID or `auto` for detection |

**Example queries:**
- "Extract data from this PDF on UO2 thermal properties"
- "Process this document and link it to material 550e8400..."

### get_extraction_status

Check the status of a document extraction job — current stage, progress
percentage, and any errors encountered.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `job_id` | string | Yes | Job ID from `trigger_extraction` |

**Example queries:**
- "What is the status of extraction job abc123?"
- "Check if my document extraction has finished"

## Usage Patterns

### Research Workflow

1. **Discover materials**: `search_materials` with a general query
2. **Narrow down**: `get_material` for the specific UUID
3. **Pull property data**: `query_properties` with temperature filters
4. **Trace sources**: `search_sources` to find the original references
5. **Cross-reference**: `query_knowledge_graph` for semantic relationships

### Data Ingestion

1. **Submit document**: `trigger_extraction` with the file URL
2. **Monitor progress**: `get_extraction_status` with the returned job ID
3. **Verify results**: `query_properties` to confirm extracted data

### Ontology Exploration

1. **Browse top-level**: `browse_ontology` with no filters
2. **Drill down**: `browse_ontology` with `parent_id`
3. **Find terms**: `browse_ontology` with `query` to discover valid search strings

## Architecture

The MCP server imports `nfm_db.services.*` directly (no HTTP hop),
uses FastMCP from the official MCP Python SDK, and communicates over
stdio transport to Claude Code.

See [ADR-823](../../docs/design/adr-nfm-823-mcp-server-architecture.md) for
the full architectural decision record.
