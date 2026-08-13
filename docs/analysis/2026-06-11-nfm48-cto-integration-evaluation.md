# CTO Integration Evaluation — ref-gp-fill → NFMD Platform

**Issue**: NFM-48
**Date**: 2026-06-11
**Status**: Evaluation complete (API publish blocked by stale checkout; artifact committed)
**Parent**: NFM-40 (ref-gp-fill integration)

> NFM-47 (Lili consultation) is blocked by OpenClaw gateway failure. CTO had sufficient prior knowledge from NFM-40 planning to complete this evaluation independently. NFM-47 results can supplement later.

---

## 1. Module Mapping

| ref-gp-fill Module | NFMD Architecture Layer | Integration Type |
|---|---|---|
| `gap_analyzer.py` | Backend Service Layer (new module `ref_gapfill/`) | Extract → independent Python package |
| `cache_query.py` | Backend Data Access Layer | Extract → query orchestration service |
| `write_ref_value.py` | Backend Service Layer | Extract → write service (with quality gates) |
| `adapters/nfmd.py` | Data Access Layer (NFMD adapter) | **Rewrite** → NFMD PostgreSQL direct |
| `adapters/ontology.py` | Data Access Layer (OntoFuel adapter) | **Keep** → L3 data source |
| `adapters/wiki.py` | Data Access Layer (Wiki adapter) | **Keep** → L3 data source |
| `property_mapping.py` | Shared Utility | Extract → shared mapping module |
| `data/property-mapping.json` | Configuration | Expand → cover NFMD full property catalog |

### Target Package Structure

```
nfm-ref-gapfill/
├── analyzer/              # gap_analyzer extraction
│   ├── __init__.py
│   ├── gap_analyzer.py
│   └── target_matrix.py
├── query/                 # cache_query extraction
│   ├── __init__.py
│   ├── cache.py           # three-level cache orchestration
│   └── property_map.py
├── write/                 # write_ref_value extraction
│   ├── __init__.py
│   ├── quality_gate.py
│   ├── dedup.py
│   └── writer.py
├── adapters/              # strategy pattern adapter interface
│   ├── base.py            # AbstractAdapter
│   ├── nfmd_postgres.py   # new: NFMD PG direct
│   ├── nfmd_supabase.py   # new: legacy Supabase read-only
│   ├── ontology.py        # keep L3
│   └── wiki.py            # keep L3
├── config/
│   └── property_mapping.json
└── tests/                 # 108+ test migration
```

---

## 2. Integration Points

### 2.1 Database Schema Alignment

NFMD integrated data model (Rev 4, 30 tables) ↔ ref-gp-fill mapping:

| ref-gp-fill Concept | NFMD Target Table | Mapping Notes |
|---|---|---|
| `reference_values` write | `property_measurements` | Core mapping. NFMD uses multi-type values (scalar/range/expression/list/text) |
| Data source tracking | `data_sources` + `authors` + `data_source_authors` | Must expand: every filled value links to source |
| Element/phase combos | `materials` + `material_categories` | Match via alias (NFMD has 367 aliases / 89 materials) |
| Property names | `property_types` + `property_categories` | property_mapping.json must expand for full NFMD catalog |
| Confidence/review status | `review_logs` | New field or record in review_logs |
| Gap metadata | New table `reference_gaps` or extend `datasets` | Record gap discovery time, priority, fill status |

**Key Schema Differences:**

- nucpot `reference_values.value_type`: scalar/range → NFMD `property_measurements.value`: JSONB multi-type
- nucpot has no provenance tracking → NFMD requires `data_sources` + `authors` 3-table linkage
- nucpot hardcodes 14 target combos → NFMD needs dynamic read from `materials` table

### 2.2 API Endpoint Design

New endpoint groups for NFMD FastAPI backend:

```
# Gap Discovery & Management
GET    /api/v1/reference-gaps              # list current gaps, filterable (system, property, priority)
POST   /api/v1/reference-gaps/scan         # trigger gap scan (sync/async)
GET    /api/v1/reference-gaps/{id}         # single gap detail

# Gap Filling
POST   /api/v1/reference-gaps/{id}/fill    # trigger fill for specific gap
POST   /api/v1/reference-gaps/batch-fill   # batch fill (gap ID list)
GET    /api/v1/reference-gaps/fill-status/{job_id}  # async fill job status

# Reference Data Management
GET    /api/v1/reference-values             # filled reference values, paginated
GET    /api/v1/reference-values/bulk        # bulk export (for verification pipeline)
GET    /api/v1/reference-values/pending-review  # pending review list

# Review (Admin)
PUT    /api/v1/reference-values/{id}/approve   # approve
PUT    /api/v1/reference-values/{id}/reject    # reject
POST   /api/v1/reference-values/batch-review   # batch review

# Statistics & Monitoring
GET    /api/v1/reference-gaps/stats         # coverage statistics
GET    /api/v1/reference-gaps/coverage      # coverage heatmap data by system/property
```

### 2.3 Frontend UI Requirements

| Page | Function | Priority |
|---|---|---|
| Gap Dashboard | Gap list by system/property, priority sorted, coverage overview | P0 |
| Fill Queue | Fill task queue with status tracking (pending/running/success/fail) | P1 |
| Review Queue | Pending review reference values, single or batch approve | P0 |
| Coverage Heatmap | System × property matrix, visual fill coverage | P2 |
| Fill History | Fill history with data source traceability | P1 |

---

## 3. Migration Path Analysis

### Option A: Extract as Independent Python Package `nfm-ref-gapfill` ✅ Recommended

**Pros:**
- Clean module boundary, independently versionable and testable
- Both NFMD and nucpot-autovc can consume as clients
- Strategy pattern adapters allow per-project data source customization
- Independent CI/CD, no impact on existing nucpot release cycle
- 108 existing tests as regression safety net

**Cons:**
- Initial extraction effort (~1-2 days)
- Must update nucpot-autovc import paths
- Must handle shared dependencies (property_mapping, etc.)

**Effort estimate: 2-3 days**

### Option B: Import as nucpot-autovc Submodule

**Pros:**
- Less initial work
- No code changes needed

**Cons:**
- Tight coupling: NFMD can't control dependencies or release rhythm
- nucpot-autovc DB config differs from NFMD (Supabase vs direct PG)
- Mixed tests, hard to isolate NFMD-specific failures
- Violates separation of concerns

**Verdict: Not recommended.** Short-term savings, long-term maintenance burden.

### Migration Steps (Option A)

1. Copy ref-gp-fill files from nucpot-autovc to new package
2. Abstract adapter interface (`AbstractAdapter` strategy pattern)
3. Rewrite `adapters/nfmd.py` → `nfmd_postgres.py` (NFMD PG direct)
4. Expand property_mapping.json for full NFMD property catalog
5. Migrate 108 tests, ensure all pass
6. Update nucpot-autovc imports to `from nfm_ref_gapfill import ...`
7. Publish v0.1.0 internal package

---

## 4. Database Strategy

### Three-Level Cache Redefined for NFMD

| Level | Original (nucpot) | NFMD Definition | Notes |
|---|---|---|---|
| **L1** | Postgres `ref_values` | **NFMD PostgreSQL direct** (`property_measurements`) | Primary DB, FastAPI queries via SQLAlchemy/asyncpg |
| **L2** | NFMD Supabase | **Legacy NFMD Supabase read-only** | Historical data migration source, initial data fill only |
| **L3** | Wiki + OntoFuel | **Unchanged** | OntoFuel adapter + Wiki adapter |

### Why Direct PostgreSQL Over Supabase for NFMD

| Dimension | Direct PostgreSQL (Recommended) | Supabase (Not Recommended) |
|---|---|---|
| **Latency** | ~1-5ms local query | ~10-50ms PostgREST HTTP |
| **Transactions** | Native ACID, efficient batch writes | REST single-record ops, no transactions |
| **Connection Pool** | asyncpg/pgbouncer, configurable | Supabase limits connection count |
| **Migrations** | Alembic-managed, version-controlled | Supabase Dashboard manual |
| **Existing** | NucPot already runs PG 17 direct | Supabase only for legacy NFMD |
| **Writes** | Batch INSERT/UPSERT | Per-record REST calls |

**Conclusion: L1 uses direct PostgreSQL (asyncpg). L2 retains Supabase as read-only historical source.**

NFMD tech stack confirms PostgreSQL 17 (NFM-2). FastAPI backend uses SQLAlchemy 2.0 + asyncpg.

---

## 5. Quality Gate Rules

### 5.1 Write Gates

| Rule | Description | Failure Action |
|---|---|---|
| **Schema compliance** | Write must conform to `property_measurements` table structure | REJECT |
| **Type safety** | Multi-type values (scalar/range/expression/list/text) correctly encoded as JSONB | REJECT |
| **Material exists** | `material_id` must reference valid `materials` record | REJECT |
| **Property exists** | `property_type_id` must reference valid `property_types` record | REJECT |
| **Source provenance** | Must link to `data_sources` record with `source_url` or `source_doi` | REJECT |
| **Dedup** | Same (material_id, property_type_id, conditions_hash) must not duplicate | DEDUP (skip) |
| **Confidence routing** | high → auto-write, medium → `pending_review`, low → REJECT | Route |
| **Value range** | Value within physically reasonable range (e.g., density 0-25 g/cm³) | PENDING_REVIEW |
| **Unit consistency** | Value unit matches `property_types.unit_id` | REJECT |

### 5.2 Data Source Quality Weights

| Data Source | Default Confidence | Notes |
|---|---|---|
| NFMD PG (L1) | high | Curated data |
| Legacy Supabase (L2) | medium | Historical data, needs verification |
| OntoFuel (L3) | medium | Auto-extracted, needs human review |
| Wiki (L3) | low | Unstructured source, needs verification |

### 5.3 Review Workflow

```
New value → Quality gate check →
  ├─ REJECT → Log reason, no database entry
  ├─ high confidence → Auto-write + record in review_logs
  ├─ medium confidence → pending_review + notify reviewer
  └─ low confidence → REJECT + suggest manual verification

Reviewer actions:
  ├─ Approve → Write to property_measurements + update review_logs
  └─ Reject → Mark rejected + record reason
```

---

## 6. Risks and Dependencies

### Risk Register

| # | Risk | Impact | Probability | Mitigation |
|---|---|---|---|---|
| R1 | **Property naming divergence**: NFMD property names differ from nucpot | High | Medium | Expand property_mapping.json; establish naming convention |
| R2 | **L3 data quality**: Wiki/OntoFuel extracted values may be inaccurate | Medium | Medium | Enforce medium/low confidence review; no auto-ingest |
| R3 | **Scope creep**: 14 targets → 50+ systems | Medium | High | Phased expansion; Phase 2 covers 14 core systems only |
| R4 | **Migration coupling**: nucpot-autovc import path changes | Low | Medium | Extract first, update second; 108 tests as regression net |
| R5 | **OntoFuel runtime dependency**: Phase 3 literature pipeline needs OntoFuel service | Medium | Low | Phase 2 independent of OntoFuel; ensure service for Phase 3 |
| R6 | **NFMD 30-table model unconfirmed**: Data model still evolving | High | Medium | Adapter abstraction isolates schema changes; sync with NFM-4 |
| R7 | **NFM-47 blocked**: Lili consultation incomplete due to infrastructure | Low | Occurred | Completed evaluation with existing knowledge; supplement when NFM-47 unblocks |

### External Dependencies

| Dependency | Type | Status | Required Action |
|---|---|---|---|
| nucpot-autovc codebase | Git repo | Active | Extract ref-gp-fill module |
| NFMD PostgreSQL 17 | Database | Running | Schema includes 30 tables |
| OntoFuel service | Microservice | Running | Phase 3 only |
| NFMD frontend (Next.js) | Frontend | Running | New Gap/Review pages needed |
| NFM-47 (Lili consultation) | Issue | Blocked | Supplement when infrastructure fixed |

---

## Summary & Recommendations

1. **Recommended Option A**: Extract `nfm-ref-gapfill` as independent Python package with strategy-pattern adapters
2. **Database**: L1 PostgreSQL direct (asyncpg), L2 legacy Supabase read-only, L3 unchanged
3. **API**: 15 REST endpoints under `/api/v1/reference-gaps/*` and `/api/v1/reference-values/*`
4. **Quality gates**: 9 write gate rules, 3-tier confidence routing, mandatory provenance tracking
5. **Effort estimate**: Phase 2 (Architecture + Migration) = 6-10 days (revised up from 2-3 days)
6. **Blocker**: NFM-47 infrastructure issue; evaluation completed independently

Phase 2 detailed implementation plan should be created as sub-issues after evaluation confirmation.
