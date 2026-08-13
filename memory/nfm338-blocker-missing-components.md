---
name: nfm338-blocker-missing-components
description: NFM-338 Phase 2.6 blocked - missing Phase 2.3-2.5 components
metadata:
  type: project
  status: blocked
  issue: NFM-338
  created: 2026-06-21
---

# NFM-338 Blocker: Missing Phase 2 Components

**Issue**: NFM-338 Phase 2.6: Integration Testing and Documentation  
**Status**: ❌ BLOCKED  
**Blocker Date**: 2026-06-21  
**Assigned To**: CPO (Agent 7095567e)

## Blocker Summary

Cannot proceed with integration testing and documentation for Phase 2 because the required integration components (API endpoints, Celery task queue, MD Runner integration) do not exist yet.

## Current State Investigation

### ✅ What Exists:
1. **Phase 2.1 (NFM-333)** - Database Migration
   - File: `apps/api/migrations/versions/003_create_md_verification_tables.py`
   - 5 tables created with proper FKs and indexes
   - Test: `apps/api/tests/test_md_verification_migration.py`

2. **Phase 2.2 (NFM-334)** - ORM Models
   - File: `apps/api/src/nfm_db/models/md_verification.py`
   - 5 model classes: MDVerificationJob, HpcJob, MDSimulationResult, DefectAnalysisResult, PotentialFittingResult
   - Enums: JobStatus, HpcJobStatus, DefectType, FittingMethod

3. **Service Layer**
   - File: `apps/api/src/nfm_db/services/md_verification.py`
   - MDVerificationService with CRUD operations
   - Pydantic schemas for request/response validation

### ❌ What's Missing:
1. **Phase 2.3 (NFM-335)** - Celery Task Queue Configuration
   - No Celery app configuration found
   - No `@task` or `@shared_task` decorators in codebase
   - No Redis broker configuration

2. **Phase 2.4 (NFM-336)** - FastAPI Endpoints for MD Verification
   - No `/api/v1/md-verification` router found
   - No API endpoints for job submission, status checking, or results retrieval

3. **Phase 2.5 (NFM-337)** - MD Runner Celery Task Implementation
   - No integration with nfm-md-runner package
   - No task definitions for LAMMPS workflow execution
   - No HPC SSH connection handling in task context

## Investigation Commands

```bash
# Searched for Celery code - found nothing
find apps/api -path "*/src/*" -name "*.py" | xargs grep -l "celery\|@task\|@shared_task"

# Searched for MD verification API endpoints - found nothing
grep -r "md.verification\|MDVerification" apps/api/src/nfm_db/api/
```

## Unblock Path

**Dependencies must be completed in order:**
1. NFM-334 (Phase 2.2) → ORM Models (in_progress)
2. NFM-335 (Phase 2.3) → Celery Task Queue (in_progress)
3. NFM-336 (Phase 2.4) → FastAPI Endpoints (in_progress)
4. NFM-337 (Phase 2.5) → MD Runner Integration (in_progress)
5. **THEN** NFM-338 (Phase 2.6) → Integration Testing & Documentation

## What NFM-338 Will Deliver (When Unblocked)

Once Phases 2.2-2.5 are complete, NFM-338 will deliver:

### Integration Test Suite
- Full flow tests: API → Celery → Database
- Mocked HPC SSH connections
- Error scenario testing (HPC down, invalid input, timeout)
- pytest-asyncio async tests
- ≥80% test coverage

### Documentation
- OpenAPI/Swagger API documentation
- Developer setup guide (Redis, Celery worker startup)
- Environment variable documentation
- Integration test documentation

## Next Steps

**For CTO/Lead Engineer:**
1. Prioritize completing NFM-334 through NFM-337
2. Ensure proper integration between components
3. Return to NFM-338 for integration testing once components exist

**For NFM-338 (When Unblocked):**
1. Design comprehensive test fixtures for mocking HPC, Redis, database
2. Implement integration test scenarios per acceptance criteria
3. Generate OpenAPI documentation from FastAPI routes
4. Write developer setup guides with environment variables
5. Verify 80%+ coverage with pytest-cov

## Related Issues
- Parent: [[NFM-329]] Phase 2: NFMD Backend Integration
- Dependency: [[NFM-333]] Phase 2.1: Database Migration ✅
- Dependency: [[NFM-334]] Phase 2.2: ORM Models ⏳
- Dependency: [[NFM-335]] Phase 2.3: Celery Task Queue ⏳
- Dependency: [[NFM-336]] Phase 2.4: FastAPI Endpoints ⏳
- Dependency: [[NFM-337]] Phase 2.5: MD Runner Integration ⏳

---

**Why**: Integration testing requires actual integration components to test.  
**How to apply**: Complete Phases 2.2-2.5 first, then revisit NFM-338 with full-stack integration testing.
