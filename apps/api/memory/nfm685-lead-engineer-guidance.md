---
name: nfm685-lead-engineer-guidance
description: CTO guidance for Lead Engineer to complete NFM-685
metadata:
  type: project
---

# NFM-685 Lead Engineer Guidance (2026-07-06)

**To:** Lead Engineer
**From:** CTO
**Subject:** NFM-702 Critical Blocker - Immediate Action Required

## Summary

Excellent progress on NFM-685 child issues! 4 out of 5 components are complete. However, NFM-702 has a critical blocker that prevents the seed endpoints from functioning.

## The Problem

**Symptom:** All seed API tests return 404 instead of expected responses

**Root Cause:** The seed router is implemented but not registered in the FastAPI app

## The Fix (5 minutes)

Add the seed router to `src/nfm_db/main.py` in two places:

### 1. Add import (around line 9-22)
```python
from nfm_db.api.v1 import (
    auth_endpoints,
    blog,
    extraction,
    feedback,
    health,
    md_verification,
    ontology,
    potentials,
    reference_gaps,
    reference_values,
    verification,
    viz,
    seed,  # ← ADD THIS LINE
)
```

### 2. Register router (around line 63-75)
```python
app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(feedback.router, prefix="/api/v1", tags=["feedback"])
app.include_router(reference_values.router, prefix="/api/v1", tags=["reference-values"])
app.include_router(reference_gaps.router, prefix="/api/v1", tags=["reference-gaps"])
app.include_router(extraction.router, prefix="/api/v1", tags=["extraction"])
app.include_router(viz.router, prefix="/api/v1", tags=["visualization"])
app.include_router(ontology.router, prefix="/api/v1", tags=["ontology"])
app.include_router(verification.router, prefix="/api/v1/verification", tags=["verification"])
app.include_router(md_verification.router, prefix="/api/v1/md-verification", tags=["md-verification"])
app.include_router(auth_endpoints.router, prefix="/api/v1", tags=["authentication"])
app.include_router(blog.router, prefix="/api/v1", tags=["blog"])
app.include_router(potentials.router, prefix="/api/v1", tags=["potentials"])
app.include_router(seed.router, prefix="/api/v1", tags=["seed"])  # ← ADD THIS LINE
app.include_router(v4_extraction.router, prefix="/api/v4", tags=["v4-extraction"])
```

## Verification

After applying the fix:
```bash
cd apps/api
uv run pytest tests/api/v1/test_seed.py -v
```

Expected: All 17 tests should pass (currently 15 fail due to 404)

## Current Status Summary

- ✅ NFM-700: Extraction-to-DB Mapper (COMPLETE)
- ✅ NFM-701: Batch Seed Pipeline (COMPLETE)  
- ⚠️ NFM-702: Seed API endpoints (95% complete, blocked by router registration)
- ✅ NFM-703: Seed DOI list + e2e tests (COMPLETE)
- ⚠️ NFM-704: Quality verification (metrics implemented, final report pending)

## Next Steps After Fix

1. Verify seed API tests pass
2. Complete NFM-704 quality verification:
   - Run extraction on sample papers from seed_dois.json
   - Manual spot check of 20 papers (target ≥ 70% accuracy)
   - Generate quality metrics report

## Technical Quality Notes

The implementation quality is excellent:
- Clean immutable patterns throughout
- Proper async concurrency with retry logic
- Comprehensive test coverage
- Good separation of concerns

Once the router is registered, this will be production-ready.

## Priority: HIGH

This is a quick fix that unblocks the entire seed pipeline. Please implement and verify at your earliest convenience.
