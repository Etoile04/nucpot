# NFM-360 CPO Implementation Plan
## Achieve 80% Overall API Test Coverage (Non-HPC Modules)

**Issue:** NFM-360
**Parent:** NFM-350 (done)
**Priority:** medium
**Author:** CPO
**Date:** 2026-06-30

---

## Current State

| Metric | Value |
|--------|-------|
| Overall Coverage | 43.47% (2,515 / 5,786 lines) |
| Target Coverage | 80% |
| Gap | ~36.5 percentage points |
| Existing Tests | 503 tests across 74 test files |
| Coverage Config | `--cov-fail-under=80` (pyproject.toml) |

### Package-Level Coverage

| Package | Coverage | Gap to 80% |
|---------|----------|------------|
| `nfm_db.api.v4` | 95.16% | ✅ |
| `nfm_db.models` | 93.58% | ✅ |
| `nfm_db.schemas` | 86.57% | ✅ |
| `nfm_db.api.v1` | 48.49% | 31.5pp |
| `nfm_db.services.domain_expert` | 43.78% | 36.2pp |
| `nfm_db.core` | 36.07% | 43.9pp |
| `nfm_db.services` | 23.53% | 56.5pp |

---

## Child Issue Breakdown

### Child 1: NFM-360.1 — Tier 1: Pure Logic & Schema Tests (Quick Wins)
**Assignee:** Lead Engineer
**Effort:** 13-17 hours
**Description:** Add tests for zero-dependency and low-dependency modules.

**Modules:**
1. `schemas/domain_expert.py` (0% → 80%+) — 8 Pydantic models, pure validation
2. `core/blog_state.py` (45% → 80%+) — State machine, 5 pure functions
3. `core/auth.py` (65% → 80%+) — Auth middleware, FastAPI dependencies
4. `services/feedback.py` (22% → 80%+) — CRUD + priority classification

**Acceptance Criteria:**
- [ ] `schemas/domain_expert.py` ≥ 80% coverage
- [ ] `core/blog_state.py` ≥ 80% coverage — test all valid/invalid state transitions
- [ ] `core/auth.py` ≥ 80% coverage — test 401/403/permission paths
- [ ] `services/feedback.py` ≥ 80% coverage — test priority classification + pagination
- [ ] All new tests pass (`pytest -m 'not integration'`)
- [ ] No regressions in existing 503 tests
- [ ] Follow pytest conventions (fixtures, parametrize, descriptive names)

**UX Constraints:** N/A (backend only)
**References:** CTO architecture — pytest config in pyproject.toml, pythonpath=["src", "."]

---

### Child 2: NFM-360.2 — Tier 2: Medium-Complexity Service Tests
**Assignee:** Lead Engineer
**Effort:** 22-28 hours
**Description:** Add tests for services with database dependencies requiring mock sessions.

**Modules:**
1. `services/celery_app.py` (13% → 80%+) — Redis counters, HPC health checks
2. `services/verification_service.py` (16% → 80%+) — Export, bulk processing
3. `services/promotion_service.py` (40% → 80%+) — State transitions, approval/rejection
4. `services/upload_service.py` (32% → 80%+) — File validation, CRUD, hash computation

**Acceptance Criteria:**
- [ ] `services/celery_app.py` ≥ 80% coverage — mock Redis + Celery, test counters + failover
- [ ] `services/verification_service.py` ≥ 80% coverage — test export filters, bulk updates, F-grade auto-reject
- [ ] `services/promotion_service.py` ≥ 80% coverage — test approve/reject transitions, metadata updates
- [ ] `services/upload_service.py` ≥ 80% coverage — test extension validation, size limits, filename sanitization, hash computation
- [ ] All new tests pass (`pytest -m 'not integration'`)
- [ ] No regressions in existing 503 tests
- [ ] Database mocking uses AsyncSession fixture pattern from existing tests

**UX Constraints:** N/A (backend only)
**References:** CTO architecture — AsyncSession fixtures in existing test files

---

### Child 3: NFM-360.3 — Tier 3: High-Complexity Service Tests
**Assignee:** Lead Engineer
**Effort:** 49-74 hours
**Description:** Add tests for services with complex external dependencies (HTTP, LLM, graph algorithms, multi-entity CRUD).

**Modules:**
1. `services/external_data_sources.py` (0% → 80%+) — HTTP client, caching, rate limiting
2. `services/blog_post.py` (14% → 80%+) — CRUD + state machine + file I/O
3. `services/ontology_service.py` (21% → 80%+) — Graph derivation, cursor pagination
4. `services/extraction_pipeline.py` (37% → 80%+) — LLM pipeline, stub mode
5. `services/md_verification.py` (48% → 80%+) — 25+ CRUD operations across 5 entity types

**Acceptance Criteria:**
- [ ] `services/external_data_sources.py` ≥ 80% coverage — mock httpx, test cache hit/miss, rate limiting, TTL
- [ ] `services/blog_post.py` ≥ 80% coverage — test full workflow (draft→review→approved→published), slug generation, file operations
- [ ] `services/ontology_service.py` ≥ 80% coverage — test graph derivation, cursor encoding/decoding, chunking, 50K node ceiling
- [ ] `services/extraction_pipeline.py` ≥ 80% coverage — test stub mode (CI), pipeline stages, job tracking, error handling
- [ ] `services/md_verification.py` ≥ 80% coverage — test CRUD for all 5 entity types, composite queries, ownership filtering, FK cascades
- [ ] All new tests pass (`pytest -m 'not integration'`)
- [ ] No regressions in existing 503 tests
- [ ] LLM-dependent tests use stub/mock mode (no real API calls in CI)
- [ ] HTTP-dependent tests use respx or httpx mock transport

**UX Constraints:** N/A (backend only)
**References:** CTO architecture — `_is_stub_mode()` pattern in extraction_pipeline.py

---

### Child 4: NFM-360.4 — API v1 Endpoint Integration Tests
**Assignee:** Lead Engineer
**Effort:** 28-35 hours
**Description:** Add endpoint-level tests for uncovered API v1 routes using FastAPI TestClient.

**Modules:**
1. `api/v1/verification.py` — 4 endpoints (check-gap, adjudicate, quarterly-audit, health)
2. `api/v1/ontology.py` — 1 endpoint (graph derivation with ETag)
3. `api/v1/md_verification.py` — 8 endpoints (job CRUD, status, results)
4. `api/v1/extraction.py` — 2 endpoints (trigger, status)

**Acceptance Criteria:**
- [ ] `api/v1/verification.py` endpoints ≥ 80% coverage — test all 4 workflows
- [ ] `api/v1/ontology.py` endpoint ≥ 80% coverage — test graph response, ETag, cursor pagination
- [ ] `api/v1/md_verification.py` endpoints ≥ 80% coverage — test all 8 endpoints, Celery mocks
- [ ] `api/v1/extraction.py` endpoints ≥ 80% coverage — test trigger validation, status polling
- [ ] All new tests pass (`pytest -m 'not integration'`)
- [ ] No regressions in existing 503 tests
- [ ] Use FastAPI TestClient + AsyncSession override pattern

**UX Constraints:** N/A (backend only)
**References:** CTO architecture — existing endpoint test patterns in tests/api/

---

### Child 5: NFM-360.5 — Coverage Verification & Gate Check
**Assignee:** Lead Engineer
**Description:** Run final coverage report, verify ≥80% overall, ensure no regressions.
**Effort:** 2-3 hours
**Dependencies:** NFM-360.1, NFM-360.2, NFM-360.3, NFM-360.4 all done

**Acceptance Criteria:**
- [ ] `pytest -m 'not integration' --cov=src --cov-report=term-missing --cov-fail-under=80` passes
- [ ] Coverage XML report generated at `apps/api/coverage.xml`
- [ ] No test count regressions (existing 503+ tests all pass)
- [ ] Document final per-package coverage in PR description

**UX Constraints:** N/A (backend only)

---

## Execution Order & Dependencies

```
NFM-360.1 (Tier 1: Quick Wins)
    ↓
NFM-360.2 (Tier 2: Medium)
    ↓
NFM-360.3 (Tier 3: High) ← parallel with NFM-360.4
    ↓                           ↓
    └─────── NFM-360.5 ────────┘
              (Final Verification)
```

- NFM-360.1 and NFM-360.2 can be sequential (2-3 weeks)
- NFM-360.3 and NFM-360.4 can run in parallel (3-4 weeks)
- NFM-360.5 is the final gate after all others complete

## Handoff to Code Reviewer

After each child issue is marked done by the Lead Engineer, it MUST be assigned to the Code Reviewer for approval before merging.

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| LLM-dependent tests flaky in CI | Use `_is_stub_mode()` pattern; never call real APIs |
| Database mock complexity grows | Establish shared AsyncSession fixture library in Tier 1 |
| Coverage measurement drift | Pin coverage to exact pyproject.toml config; rerun gate in NFM-360.5 |
| Test runtime bloat | Use `@pytest.mark.parametrize` instead of brute-force loops |
