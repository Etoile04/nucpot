# NFM MCP Server — Tool Reference

Machine-readable reference for all 9 MCP tools exposed by the NFM server.

## Tool Summary

| # | Tool Name | Domain | Read-Only | Description |
|---|-----------|--------|-----------|-------------|
| 1 | `search_materials` | Materials | Yes | Full-text search across material names, compositions, aliases |
| 2 | `get_material` | Materials | Yes | Retrieve full material record by UUID |
| 3 | `query_properties` | Properties | Yes | Query measured/calculated property data for a material |
| 4 | `search_sources` | Sources | Yes | Search literature references (journals, reports, handbooks) |
| 5 | `query_potentials` | Potentials | Yes | Query thermodynamic potential models with coefficients |
| 6 | `browse_ontology` | Ontology | Yes | Browse hierarchical domain ontology tree |
| 7 | `query_knowledge_graph` | Knowledge Graph | Yes | Semantic cross-referencing across entities |
| 8 | `trigger_extraction` | Extraction | No | Submit document for automated data extraction |
| 9 | `get_extraction_status` | Extraction | Yes | Monitor extraction job progress |

---

## 1. search_materials

**Description:** Search the NFM database for nuclear fuel and materials.
Performs full-text search across material names, compositions, and aliases.

**Annotations:** `readOnlyHint=true`, `idempotentHint=true`, `destructiveHint=false`, `openWorldHint=false`

| Parameter | Type | Required | Default | Constraints | Description |
|-----------|------|----------|---------|-------------|-------------|
| `query` | string | Yes | — | 1–500 chars | Free-text search query |
| `material_type` | string\|null | No | null | — | Filter: `fuel`, `cladding`, `coolant` |
| `limit` | integer | No | 20 | 1–100 | Maximum results |
| `offset` | integer | No | 0 | ≥0 | Pagination offset |

**Returns:** JSON array of materials with `id`, `name`, `formula`, `crystal_structure`, `description`.

**Error shape:** `{"error": "Search failed: <message>"}`

---

## 2. get_material

**Description:** Retrieve detailed information about a specific nuclear material.
Returns composition, crystal structure, aliases, and available property data categories.

**Annotations:** `readOnlyHint=true`, `idempotentHint=true`, `destructiveHint=false`, `openWorldHint=false`

| Parameter | Type | Required | Default | Constraints | Description |
|-----------|------|----------|---------|-------------|-------------|
| `material_id` | string | Yes | — | 1–200 chars, valid UUID | Unique material identifier |

**Returns:** JSON object with full material record.

**Error shape:** `{"error": "Material '<id>' not found"}` or `{"error": "Invalid material identifier format"}`

---

## 3. query_properties

**Description:** Query property data for a specific nuclear material.
Retrieves thermal conductivity, density, specific heat, Young's modulus,
thermal expansion, and more.

**Annotations:** `readOnlyHint=true`, `idempotentHint=true`, `destructiveHint=false`, `openWorldHint=false`

| Parameter | Type | Required | Default | Constraints | Description |
|-----------|------|----------|---------|-------------|-------------|
| `material_id` | string | Yes | — | 1–200 chars, valid UUID | Material UUID to query |
| `property_name` | string\|null | No | null | — | Property filter (e.g., `thermal_conductivity`) |
| `temperature_range` | string\|null | No | null | — | Range filter (e.g., `300-1500 K`) |
| `limit` | integer | No | 50 | 1–500 | Maximum data points |

**Returns:** JSON array of property data points with `temperature`, `value`, `unit`, `source_reference`.

**Error shape:** `{"error": "Invalid material_id '<id>'. Must be a valid UUID."}`

---

## 4. search_sources

**Description:** Search the NFM literature source database.
Find journal articles, technical reports, handbooks, and other references
cited as data sources.

**Annotations:** `readOnlyHint=true`, `idempotentHint=true`, `destructiveHint=false`, `openWorldHint=false`

| Parameter | Type | Required | Default | Constraints | Description |
|-----------|------|----------|---------|-------------|-------------|
| `query` | string | Yes | — | 1–500 chars | Search by author, title, journal, DOI |
| `source_type` | string\|null | No | null | — | Filter: `journal`, `report`, `handbook` |
| `limit` | integer | No | 20 | 1–100 | Maximum results |
| `offset` | integer | No | 0 | ≥0 | Pagination offset |

**Returns:** JSON array of source records with `id`, `authors`, `title`, `year`, `citation_count`.

---

## 5. query_potentials

**Description:** Query thermodynamic potential models for a nuclear material.
Retrieves Gibbs energy, enthalpy, entropy, heat capacity, and other models
with parametric coefficients and valid temperature ranges.

**Annotations:** `readOnlyHint=true`, `idempotentHint=true`, `destructiveHint=false`, `openWorldHint=false`

| Parameter | Type | Required | Default | Constraints | Description |
|-----------|------|----------|---------|-------------|-------------|
| `material_id` | string | Yes | — | 1–200 chars | Material identifier |
| `potential_type` | string\|null | No | null | — | Filter: `Gibbs`, `enthalpy`, `Cp` |
| `model_name` | string\|null | No | null | — | Specific model (e.g., `FINK-LUCUTA2`) |
| `temperature_range` | string\|null | No | null | — | Range filter (e.g., `300-3000 K`) |

**Returns:** JSON array of potential model records with `model_name`, `expression_type`, `coefficients`, `valid_range_k`.

---

## 6. browse_ontology

**Description:** Browse the NFM domain ontology tree.
Defines hierarchical classification of nuclear fuel materials, properties,
measurement types, and relationships.

**Annotations:** `readOnlyHint=true`, `idempotentHint=true`, `destructiveHint=false`, `openWorldHint=false`

| Parameter | Type | Required | Default | Constraints | Description |
|-----------|------|----------|---------|-------------|-------------|
| `query` | string\|null | No | null | — | Search term within ontology |
| `entity_type` | string\|null | No | null | — | Filter: `material`, `property`, `source` |
| `parent_id` | string\|null | No | null | — | Start from this ontology node ID |
| `limit` | integer | No | 50 | 1–200 | Maximum nodes |

**Returns:** JSON array of ontology nodes with `id`, `label`, `entity_type`, `children_count`.

---

## 7. query_knowledge_graph

**Description:** Query the NFM knowledge graph for material relationships.
Connects materials, properties, sources, and measurement conditions
into a semantic network.

**Annotations:** `readOnlyHint=true`, `idempotentHint=true`, `destructiveHint=false`, `openWorldHint=false`

| Parameter | Type | Required | Default | Constraints | Description |
|-----------|------|----------|---------|-------------|-------------|
| `query` | string | Yes | — | 1–1000 chars | Natural-language or Cypher-like query |
| `entity_types` | list[string]\|null | No | null | — | Filter: `material`, `property`, `source` |
| `limit` | integer | No | 20 | 1–100 | Maximum results |

**Returns:** JSON object with `nodes` (matching entities) and `edges` (relationships between them).

---

## 8. trigger_extraction

**Description:** Submit a document for automated data extraction.
Starts an async pipeline that parses the document, extracts material
property data, and inserts it into the NFM database.

**Annotations:** `readOnlyHint=false`, `idempotentHint=false`, `destructiveHint=false`, `openWorldHint=true`

| Parameter | Type | Required | Default | Constraints | Description |
|-----------|------|----------|---------|-------------|-------------|
| `file_url` | string | Yes | — | 1–2000 chars | URL or path of the document |
| `material_id` | string | No | `"auto"` | — | Target material ID or `auto` |

**Returns:** JSON object with `job_id`, `status`, `estimated_duration_seconds`, `message`.

---

## 9. get_extraction_status

**Description:** Check the status of a document extraction job.
Returns current stage, progress percentage, and any errors.

**Annotations:** `readOnlyHint=true`, `idempotentHint=true`, `destructiveHint=false`, `openWorldHint=false`

| Parameter | Type | Required | Default | Constraints | Description |
|-----------|------|----------|---------|-------------|-------------|
| `job_id` | string | Yes | — | 1–200 chars | Job ID from `trigger_extraction` |

**Returns:** JSON object with `job_id`, `status`, `progress`, `stage`, `started_at`, `completed_at`, `entities_extracted`, `properties_extracted`, `error`.

**Error shape:** `{"error": "Job '<id>' not found"}`

---

## Annotation Legend

| Annotation | Meaning |
|------------|---------|
| `readOnlyHint` | Tool does not modify server state |
| `destructiveHint` | Tool may destroy or overwrite data |
| `idempotentHint` | Calling multiple times produces the same result |
| `openWorldHint` | Tool accesses resources outside the MCP server |

## Data Flow

```
search_materials → get_material → query_properties → search_sources
                      ↓
               query_potentials
                      ↓
               browse_ontology
                      ↓
          query_knowledge_graph ← (cross-references all above)

trigger_extraction → get_extraction_status → query_properties (verify)
```

## Source Code

| Tool | Implementation |
|------|----------------|
| `search_materials`, `get_material` | `src/nfm_mcp/tools/materials.py` |
| `query_properties` | `src/nfm_mcp/tools/properties.py` |
| `search_sources` | `src/nfm_mcp/tools/sources.py` |
| `query_potentials` | `src/nfm_mcp/tools/potentials.py` |
| `browse_ontology` | `src/nfm_mcp/tools/ontology.py` |
| `query_knowledge_graph` | `src/nfm_mcp/tools/knowledge_graph.py` |
| `trigger_extraction`, `get_extraction_status` | `src/nfm_mcp/tools/extraction.py` |
