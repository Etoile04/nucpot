---
name: nfm685-cpo-breakdown
description: CPO breakdown of NFM-685 Phase 1.3 Extraction-DB bridge + seed data into 5 child issues
metadata:
  type: project
---

# NFM-685 CPO Breakdown (2026-07-06)

**Issue:** NFM-685 — Phase 1.3: Extraction-DB bridge + seed data
**Status:** in_progress (children delegated, awaiting Lead Engineer)

## Blocker Verification

Both dependencies confirmed DONE:
- **NFM-680** (Schema) → done — Models (NFM-688), Schemas (NFM-689), Migration (NFM-690) all complete
- **NFM-683** (CRUD API) → done — All 4 children done (NFM-696/697/698/699), 93% test coverage

## 5 Child Issues Created

All assigned to Lead Engineer (`98fc3168-be45-4673-808e-22238b366352`):

| # | Issue | Title | UUID | Status |
|---|-------|-------|------|--------|
| 1 | **NFM-700** | Extraction-to-DB Mapper service | c3a6ed24-e9e4-415f-bb16-dc950ab8130c | todo |
| 2 | **NFM-701** | Batch Seed Pipeline (async + retry) | b5bf6134-152b-4512-b028-3576a8fa7384 | blocked |
| 3 | **NFM-702** | Seed API endpoints | 2c0ebccd-1c6d-406d-b867-120cc6b5a049 | blocked |
| 4 | **NFM-703** | Seed DOI list + e2e integration test | 8d171827-ef76-476b-9337-a1bd60bba1cf | blocked |
| 5 | **NFM-704** | Quality metrics report + verification | 61ca9c82-54de-4457-b3f5-c0cb925e25ba | blocked |

## Dependency Chain

```
NFM-700 (mapper) → NFM-701 (batch pipeline) → NFM-702 (API endpoints)
                                                  ↘ NFM-703 (seed data + e2e test)
NFM-702 (API) + NFM-703 (seed data) → NFM-704 (quality report)
```

## Files to Create

- `apps/api/src/nfm_db/services/extraction_to_db_mapper.py`
- `apps/api/src/nfm_db/services/seed_service.py`
- `apps/api/src/nfm_db/services/quality_service.py`
- `apps/api/src/nfm_db/api/v1/seed.py`
- `apps/api/src/nfm_db/data/seed_dois.json`
- `apps/api/tests/integration/test_seed_e2e.py`

## Key Decisions

- asyncio.TaskGroup (not Celery) for MVP
- In-memory batch tracking for MVP
- All data through core schema, no bypass
- NFM-700 is unblocked and ready for Lead Engineer to start

## Completion Gate

NFM-685 stays in_progress until all 5 children are done and acceptance criteria verified.
