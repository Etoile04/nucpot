# ADR-NFM-2577: Gap Filling Path Dispatch Router

**Status:** Proposed
**Date:** 2026-08-08
**Issue:** NFM-2644 (originally planned as NFM-2577)

## Context

The NFMD platform detects coverage gaps between the ontology schema and actual database records via `CoverageScanService`. Each gap produces a `DataCollectionRequest` (DCR) with a `source_preference` field (`literature | dft | external_db | any`). Currently, these requests accumulate in `open` status with no automated routing to filling paths.

We need a dispatch layer that:
1. Reads open DCRs
2. Routes each to the correct filling path based on `source_preference`
3. Tracks dispatch state (when, which path, result)

## Existing Infrastructure

| Component | File | Role |
|-----------|------|------|
| `DataCollectionRequest` model | `models/data_collection_request.py` | Tracks (entity_type, property, material_system) gaps with `source_preference` |
| `CoverageScanService` | `services/coverage_scan_service.py` | Creates DCRs for uncovered properties |
| `GapFillService` | `services/gap_fill_service.py` | L1/L2 cache fills (simulated data, not integrated with dispatch) |
| `LiteratureDispatcher` | `services/literature_dispatcher.py` | Celery-based PDF/DOI processing (existing, but not gap-aware) |
| `ExternalDataSourceClient` | `services/external_data_sources.py` | NIST IPR, OpenKIM, Materials Project query clients |
| `ExtractionOrchestrator` | `services/extraction_orchestrator.py` | Pipeline: chunk, extract, map, quality_gate, gap_scan |

## Decision

### Architecture: Strategy Pattern Dispatch Router

Implement a `GapDispatchService` using the Strategy pattern:

```
DataCollectionRequest (open)
        |
        v
+---------------------+
|  GapDispatchService  |  reads open DCRs, resolves source_preference
|  dispatch(request)    |
+---------+-----------+
          |
    +-----+--------------+
    v     v              v
+--------+ +--------+ +------------+
|Litera- | |  DFT   | | ExternalDB |
|tureFill| |  Fill  | |   Fill     |
|Path    | | Path   | |   Path     |
+--------+ +--------+ +------------+
```

### Dispatch Resolution Rules

| source_preference | Resolved Path(s) | Priority |
|---------------------|------------------|----------|
| `literature` | LiteratureFillPath | Single |
| `dft` | DFTFillPath | Single |
| `external_db` | ExternalDBFillPath | Single |
| `any` | All three, in parallel; first success wins | Cascade |

### Data Model Changes (Alembic migration)

Add four columns to `data_collection_requests`:

- `dispatched_at`: DateTime(timezone=True), nullable=True
- `dispatched_path`: String(50), nullable=True (literature / dft / external_db / cascade)
- `dispatch_status`: String(20), nullable=True, default=None (pending / running / success / failed)
- `result_reference`: String(500), nullable=True (celery_task_id / external_ref / etc.)

**Why columns (not metadata_)?** These fields are queryable: we need to filter/sort by `dispatch_status` and `dispatched_path`. JSONB metadata is opaque to indexes.

### Path Handler Protocol

```python
class GapFillPath(Protocol):
    async def can_handle(self, request: DataCollectionRequest) -> bool: ...
    async def execute(self, request: DataCollectionRequest) -> DispatchResult: ...
```

Each path handler returns an immutable `DispatchResult`:

```python
@dataclass(frozen=True)
class DispatchResult:
    success: bool
    path: str
    reference: str | None  # celery task ID, external job ID, etc.
    error: str | None
    data_found: bool  # whether the path found actual data
```

### Path Handler Implementations

#### 1. LiteratureFillPath
- **Action:** Create a search query targeting the missing property + material_system, then queue for literature extraction.
- **Integration:** Create a DataSource placeholder with search metadata; don't implement full literature search yet.
- **Result reference:** Celery task ID (from literature_dispatcher).
- **MVP simplification:** The key is the dispatch plumbing, not the search algorithm.

#### 2. DFTFillPath
- **Action:** Create a DFT calculation request stub.
- **Integration:** No existing DFT workflow exists. Create a `DFTCalculationRequest` model (simple status tracker) and a stub handler.
- **Result reference:** DFT request UUID.
- **Future:** Wire to real DFT workflow (VASP, Quantum ESPRESSO, etc.)

#### 3. ExternalDBFillPath
- **Action:** Query `ExternalDataSourceClient` (Materials Project, NIST IPR, OpenKIM) for the missing property + material_system.
- **Integration:** Use existing `ExternalDataSourceClient` to query by material system and property name.
- **Result reference:** Source + query ID.

### API Endpoints

- `POST /api/v1/data-collection/dispatch` - Trigger dispatch for open requests (domain_expert required)
- `GET /api/v1/data-collection/dispatch/status` - Paginated list of dispatched requests with filter support
- `POST /api/v1/data-collection/dispatch/{request_id}/retry` - Retry a failed dispatch (domain_expert required)

### Status Lifecycle Extension

Current DCR status: `open -> in_progress -> completed | declined`

After dispatch:
- `open` becomes `in_progress` when dispatch starts executing
- If path succeeds and data found: `completed`
- If path fails: stays `in_progress` (retryable), or `declined` if all paths exhausted

### Cascade Strategy (source_preference="any")

When source_preference = "any":
1. Try `external_db` first (fastest, structured data)
2. If no data found: try `literature` (moderate speed)
3. If no data found: try `dft` (slowest, compute-intensive)
4. Stop at first success

## Consequences

### Positive
- Clean separation via Strategy pattern; adding new filling paths requires only implementing `GapFillPath`
- Queryable dispatch state via proper columns (not JSONB)
- Reuses existing `ExternalDataSourceClient` and Celery infrastructure
- `any` mode provides intelligent cascade for maximum coverage

### Risks
- DFT path is a stub; may confuse users if they see "DFT" status with no real computation
- Literature search is not yet implemented; the path creates placeholders only
- External DB queries may hit rate limits

### Mitigations
- DFT path must clearly mark results as "stub/placeholder" in metadata_
- Literature path MVP: create DataSource with search keywords, don't attempt actual search
- Batch dispatch with configurable rate limits (default: 10 requests per batch)
- Dispatch endpoint requires `domain_expert` role

## File Layout

```
apps/api/src/nfm_db/
  models/
    data_collection_request.py  (ADD columns)
  schemas/
    data_collection_request.py  (ADD dispatch fields to response)
  services/
    gap_dispatch_service.py     (NEW: router + cascade logic)
    paths/
      __init__.py
      base.py                   (GapFillPath protocol + DispatchResult)
      literature_fill.py        (LiteratureFillPath)
      dft_fill.py               (DFTFillPath: stub)
      external_db_fill.py       (ExternalDBFillPath)
    coverage_scan_service.py    (unchanged)
  api/v1/
    data_collection.py          (ADD dispatch endpoints)
  migrations/versions/
    xxxx_add_dispatch_tracking_to_dcr.py  (Alembic migration)
```

## Test Plan

1. **Unit tests:** Each path handler tested in isolation with mocked dependencies
2. **Dispatch router tests:** Verify source_preference routing, cascade logic, batch limiting
3. **Integration test:** Full dispatch cycle for a single DCR through external_db path
4. **API tests:** POST /dispatch, GET /dispatch/status, POST retry
5. **Migration test:** Verify new columns nullable, backward-compatible
