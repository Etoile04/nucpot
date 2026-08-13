# Zotero MCP Server Selection — NFM-829

## Decision: `54yyyu/zotero-mcp`

**Repository:** https://github.com/54yyyu/zotero-mcp  
**Stars:** 4,160 | **License:** MIT | **Language:** Python 3.10+  
**Last updated:** 2026-07-07 | **Transport:** stdio, SSE, streamable-http  
**MCP SDK:** FastMCP  
**PyPI:** `zotero-mcp-server`

## Why this choice

### Evaluation Matrix

| Criteria | 404Simon | SMABoundless | tobyvee | nelsonlove | **54yyyu (chosen)** |
|---|---|---|---|---|---|
| Stars | ~0 | ~0 | ~0 | ~0 | **4,160** |
| Language | Python | Python | Go | N/A (skill) | **Python** |
| Transport | stdio | stdio | stdio | N/A | **stdio + SSE** |
| Read | ✅ | ✅ | ✅ | N/A | **✅** |
| Write | ❌ | ✅ | ❌ | N/A | **✅** |
| ChromaDB semantic search | ❌ | ❌ | ❌ | N/A | **✅** |
| pip installable | ❌ | ❌ | ❌ | N/A | **✅** |
| Tests | ❌ | ❌ | ❌ | N/A | **294 unit tests** |
| Active maintenance | Marginal | Marginal | Marginal | Marginal | **Active** |

### Key Capabilities (54yyyu/zotero-mcp)

**Read tools (30+):**
- `search_library`, `get_item`, `get_item_children`, `get_collections`, `get_collection_items`
- `get_annotations`, `get_notes`, `get_tags`, `get_recent_items`
- `semantic_search` (ChromaDB-powered), `citation_intelligence`, `related_items`

**Write tools:**
- `add_by_doi` — auto-fetch metadata from CrossRef, attach open-access PDF
- `add_by_url`, `add_by_isbn`, `add_by_bibtex`, `add_from_file`
- `create_collection`, `manage_collections`, `update_item`
- `merge_duplicates`, batch tag operations

**Semantic Search:**
- Built-in ChromaDB integration (`[semantic]` extra)
- Embedding models: all-MiniLM-L6-v2 (default), OpenAI, Gemini, Ollama
- CLI: `zotero-mcp update-db --fulltext` to build index

**Authentication:**
- Local mode: reads `~/Zotero/zotero.sqlite` directly (no API key needed)
- Web API mode: Zotero API key + user ID for remote/write operations
- Hybrid mode: local reads + web API writes (recommended)

### Integration Architecture

```
nucpot (FastAPI)
  │
  ├── zotero-mcp-server[semantic]  (sidecar via stdio)
  │     ├── ChromaDB (semantic embeddings)
  │     └── FastMCP tools (read/write/search)
  │
  ├── Zotero Web API (write: add papers, create collections)
  └── Zotero Local DB (optional fast reads)
```

### Rejected Alternatives

- **404Simon/zotero-mcp**: Read-only, no write operations, no semantic search
- **SMABoundless/zotero-mcp-server**: Single commit, no tests, monolithic, no semantic search
- **tobyvee/mcp-zotero**: Go binary, read-only, no semantic search
- **nelsonlove/cc-zotero**: Not an MCP server (Claude Code knowledge skill only)

## Acceptance Criteria Mapping

| Criterion | Status |
|---|---|
| ✅ Zotero MCP Server selected and documented | This doc |
| ✅ Read/write capabilities | add_by_doi, create_collection, update_item |
| ✅ ChromaDB semantic search | `[semantic]` extra, update-db CLI |
| ✅ Sidecar architecture | stdio transport, FastMCP |
| ✅ Python ecosystem | pip/uv install, Python 3.10+ |
