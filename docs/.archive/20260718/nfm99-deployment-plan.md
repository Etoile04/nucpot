# NFM-99 Deployment Plan: Domain Expert Service Integration

**Issue**: NFM-99 - NFM-87.3: Integrate Nuclear Domain Expert Agent with verification API
**CTO**: Claude (agent 3a0e0b92-5e86-4cd4-99fb-e84db376d5a2)
**Date**: 2026-06-13
**Status**: Ready for staging deployment

---

## Overview

This document outlines the deployment strategy for integrating the Domain Expert Service with the NFM verification architecture. The integration bridges the Verification API to the Nuclear Domain Expert Agent (fe09f6ec-1998-46a0-96af-f0b26e79abdf).

### Architecture Flow

```
Verification API Layer
    ↓
Domain Expert Service (NEW)
    ↓
Nuclear Domain Expert Agent (fe09f6ec-1998-46a0-96af-f0b26e79abdf)
    ├─ Skills (NFM-97): nuclear-materials-knowledge, literature-search, lammps-debugger
    └─ Workflows (NFM-98): reference-validation, f-grade-adjudication, quarterly-audit
    ↓
External Data Sources (NIST IPR, OpenKIM, Materials Project)
```

### Components Deployed

1. **Domain Expert Service** (`apps/api/src/nfm_db/services/domain_expert/`)
   - Reference validation workflow
   - F-grade adjudication workflow
   - Quarterly audit workflow

2. **Verification API Endpoints** (`apps/api/src/nfm_db/api/v1/verification.py`)
   - POST `/api/v1/verification/check-gap`
   - POST `/api/v1/verification/adjudicate-grade`
   - POST `/api/v1/verification/quarterly-audit`
   - GET `/api/v1/verification/health`

3. **External Data Source Client** (`apps/api/src/nfm_db/services/external_data_sources.py`)
   - NIST IPR integration
   - OpenKIM integration
   - Materials Project integration
   - Caching and rate limiting

4. **Integration Tests** (`apps/api/tests/test_verification_api.py`)
   - 20+ test cases covering all endpoints
   - High/low confidence validation tests
   - F-grade adjudication scenarios
   - Quarterly audit execution

---

## Phase 1: Staging Deployment

### 1.1 Pre-Deployment Checklist

- [x] Domain Expert Service implemented
- [x] Verification API endpoints created
- [x] External data source client with caching/rate limiting
- [x] Integration tests written (20+ test cases)
- [x] Verification router registered in main.py
- [ ] Database migrations applied (if needed)
- [ ] Environment variables configured
- [ ] External API keys obtained (Materials Project)

### 1.2 Staging Environment Configuration

**Environment Variables**:
```bash
# External API keys (staging)
MATERIALS_PROJECT_API_KEY=${STAGING_MP_API_KEY}
NIST_IPR_API_KEY=${STAGING_NIST_API_KEY}

# Rate limiting (staging - more lenient)
NIST_IPR_RATE_LIMIT=120  # 2x production
OPENKIM_RATE_LIMIT=240
MATERIALS_PROJECT_RATE_LIMIT=120

# Cache TTL (seconds)
CACHE_TTL=3600
```

**Database**:
- Use staging database
- No schema changes required (uses existing RefGapFillStaging table)

### 1.3 Test Cases for Staging

#### Reference Validation Test Cases (20 entries)

**High Confidence Tests** (5 cases):
1. NIST IPR source with DOI, experimental method, uncertainty provided
2. Peer-reviewed journal with uncertainty
3. Materials Project data with DOI
4. OpenKIM verified potential with uncertainty
5. Multiple literature matches with >90% agreement

**Medium Confidence Tests** (5 cases):
6. Conference proceedings with uncertainty
7. Preprint with DOI and uncertainty
8. Single literature match with 70-90% agreement
9. Experimental method without DOI
10. DFT calculation with uncertainty

**Low Confidence/Escalation Tests** (5 cases):
11. Unknown source, no uncertainty, no DOI
12. Preprint without uncertainty
13. Value outside known P0 property range
14. Single source with <70% literature agreement
15. Conference proceedings without uncertainty

**Edge Cases** (5 cases):
16. Very large value (check range handling)
17. Negative value for typically positive property
18. Zero uncertainty (edge case handling)
19. Extremely small uncertainty (0.0001)
20. Missing optional fields (phase, temperature, method)

#### F-Grade Adjudication Test Cases (10 cases)

**Common LAMMPS Errors** (7 cases):
1. NaN instability in compute
2. Divergence error
3. Missing potential file
4. Neighbor list error
5. Potential not recognized error
6. Pair_style parameter error
7. Boundary condition error

**Edge Cases** (3 cases):
8. Empty error log
9. Very long error log (>1000 chars)
10. Non-English error message

### 1.4 Staging Deployment Steps

```bash
# 1. Deploy to staging
git checkout main
git pull origin main
cd apps/api

# 2. Install dependencies
uv sync

# 3. Run tests
uv run pytest tests/test_verification_api.py -v

# 4. Start staging server
uv run uvicorn nfm_db.main:app --host 0.0.0.0 --port 8001

# 5. Verify health endpoint
curl http://localhost:8001/api/v1/verification/health
```

### 1.5 Staging Verification

**Health Check**:
```bash
curl http://localhost:8001/api/v1/verification/health
# Expected: {"status": "healthy", "module": "verification", ...}
```

**Test Reference Validation**:
```bash
curl -X POST http://localhost:8001/api/v1/verification/check-gap \
  -H "Content-Type: application/json" \
  -d '{
    "element_system": "UO2",
    "property_name": "lattice_constant",
    "value": 5.47,
    "unit": "Å",
    "source": "Sallee1985",
    "source_type": "nist_ipr",
    "source_doi": "10.1063/1.123456",
    "method": "experimental",
    "uncertainty": 0.01
  }'
# Expected: confidence_score >= 0.8, is_validated: true
```

**Test F-Grade Adjudication**:
```bash
curl -X POST http://localhost:8001/api/v1/verification/adjudicate-grade \
  -H "Content-Type: application/json" \
  -d '{
    "staging_id": "00000000-0000-0000-0000-000000000001",
    "element_system": "UO2",
    "property_name": "thermal_conductivity",
    "error_log": "ERROR: NaN in compute at step 15"
  }'
# Expected: primary_category: "nan_instability", suggested_fixes: [...]
```

### 1.6 Staging Acceptance Criteria

- [ ] All 20 reference validation tests pass
- [ ] All 10 F-grade adjudication tests pass
- [ ] Health endpoint returns 200 OK
- [ ] API response time < 5 seconds for validation
- [ ] API response time < 3 seconds for adjudication
- [ ] Cache hit rate > 30% for repeated queries
- [ ] No rate limiting errors in normal usage
- [ ] Error handling returns 422/500 with clear messages

---

## Phase 2: Production Rollout

### 2.1 Pre-Production Checklist

- [ ] Staging deployment verified
- [ ] All test cases pass with >85% accuracy
- [ ] External API keys obtained and stored securely
- [ ] Production database backups verified
- [ ] Rollback plan documented
- [ ] Monitoring configured
- [ ] Alert thresholds set

### 2.2 Production Environment Configuration

**Environment Variables**:
```bash
# External API keys (production)
MATERIALS_PROJECT_API_KEY=${PROD_MP_API_KEY}
NIST_IPR_API_KEY=${PROD_NIST_API_KEY}

# Rate limiting (production - stricter)
NIST_IPR_RATE_LIMIT=60
OPENKIM_RATE_LIMIT=120
MATERIALS_PROJECT_RATE_LIMIT=60

# Cache TTL (seconds)
CACHE_TTL=3600

# Logging
LOG_LEVEL=INFO
```

**Database**:
- Use production database with read replicas
- Connection pooling configured

### 2.3 Production Deployment Strategy

**Blue-Green Deployment**:

1. **Deploy to Green Environment**
   - Spin up new production instances (green)
   - Deploy new code to green
   - Run smoke tests against green

2. **Verify Green Environment**
   - Run health check
   - Execute 5 reference validation tests
   - Execute 3 F-grade adjudication tests
   - Check response times

3. **Switch Traffic**
   - Update load balancer to route traffic to green
   - Monitor for 10 minutes
   - Check error rates, response times

4. **Retain Blue**
   - Keep blue environment running for 30 minutes
   - If issues detected, switch back to blue immediately

### 2.4 Production Monitoring

**Key Metrics**:
- API response times (p50, p95, p99)
- Error rates (4xx, 5xx)
- Cache hit rate
- Rate limit violations
- External API failures

**Alert Thresholds**:
- Response time p95 > 10 seconds → WARNING
- Error rate > 5% → CRITICAL
- Cache hit rate < 20% → INFO
- External API failure rate > 10% → WARNING

### 2.5 Rollback Plan

**Trigger rollback if**:
- Error rate > 10% for 5 minutes
- Response time p95 > 15 seconds for 5 minutes
- External API completely down affecting validation
- Database connection failures

**Rollback steps**:
1. Switch load balancer back to blue environment
2. Verify health checks pass on blue
3. Investigate logs from green environment
4. Fix issue and retry deployment

---

## Phase 3: External Data Source Integration

### 3.1 Current Status

**Placeholder Implementation**:
- External data source client created with caching/rate limiting
- Queries return placeholder data structures
- Ready for actual API integration

### 3.2 Integration Requirements

**NIST IPR Integration**:
- Obtain API key from NIST
- Implement CIF format query
- Parse thermodynamics data
- Map to reference value structure

**OpenKIM Integration**:
- Query OpenKIM REST API
- Parse potential information
- Extract properties if available
- Map to reference value structure

**Materials Project Integration**:
- Obtain API key from Materials Project
- Query materials by formula
- Extract property data
- Handle rate limits (1000 requests/day free tier)

### 3.3 Integration Timeline

**Week 1**: Obtain API keys and test access
**Week 2**: Implement actual API calls in placeholder methods
**Week 3**: Test integration with real data
**Week 4**: Deploy to production

---

## Success Metrics

### Accuracy Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Reference validation accuracy** | >85% | % of agent decisions correct vs human review |
| **F-grade adjudication accuracy** | >80% | % of fix suggestions that resolve the issue |
| **Escalation rate** | <20% | % of cases escalated to human review |
| **Confidence score calibration** | ±10% | Confidence scores match human assessment |

### Performance Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Validation response time** | <5s | Average time for reference validation |
| **Adjudication response time** | <3s | Average time for F-grade adjudication |
| **Cache hit rate** | >30% | % of queries served from cache |
| **External API availability** | >95% | % of successful external API calls |

### Operational Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| **API uptime** | >99% | % of time endpoints are accessible |
| **Error rate** | <1% | % of requests returning 4xx/5xx |
| **Rate limit violations** | <0.1% | % of requests blocked by rate limiter |

---

## Dependencies

### Completed
- ✅ NFM-85: Verification API architecture
- ✅ NFM-87: AI agent hiring decision
- ✅ NFM-97: Domain skills (in progress, pending agent creation)
- ✅ NFM-98: Domain workflows (in progress, pending workflow design)

### Pending
- ⏳ Nuclear Domain Expert Agent creation (fe09f6ec-1998-46a0-96af-f0b26e79abdf)
- ⏳ External API keys (NIST IPR, Materials Project)
- ⏳ Actual external API integration

### Unblocks
- NFM-83: Final implementation (can proceed with AI agent approach)

---

## Maintenance & Operations

### Regular Tasks

**Daily**:
- Monitor error rates and response times
- Check external API status
- Review cache hit rates

**Weekly**:
- Review escalation cases
- Update confidence scoring if drift detected
- Check rate limit usage

**Monthly**:
- Run quarterly audit workflow
- Review external data source coverage
- Update P0 property ranges if needed

**Quarterly**:
- Full audit of P0 systems
- Review and update confidence thresholds
- Evaluate external data source performance

### Escalation Procedures

**High Error Rate** (>5%):
1. Check external API status
2. Review recent code changes
3. Consider enabling cache-only mode
4. Escalate to CTO if persists >1 hour

**External API Down**:
1. Check service status page
2. Enable fallback to cached data
3. Consider using Lili consultation backup for complex cases
4. Update status page

**Confidence Score Drift** (>±15% from expected):
1. Review recent validation results
2. Compare with human review samples
3. Update scoring weights if needed
4. Document rationale for changes

---

## Appendix

### A. Test Case Details

**Reference Validation Template**:
```json
{
  "element_system": "UO2",
  "property_name": "lattice_constant",
  "value": 5.47,
  "unit": "Å",
  "source": "Sallee1985",
  "source_type": "nist_ipr",
  "source_doi": "10.1063/1.123456",
  "method": "experimental",
  "uncertainty": 0.01,
  "temperature": 298,
  "phase": "fluorite"
}
```

**F-Grade Adjudication Template**:
```json
{
  "staging_id": "uuid",
  "element_system": "UO2",
  "property_name": "thermal_conductivity",
  "error_log": "ERROR: NaN in compute at step 15",
  "potential_type": "EAM",
  "lammps_version": "2023.08.25",
  "phase": null,
  "temperature": 300
}
```

### B. API Endpoint Reference

**Check Reference Gap**:
- URL: `POST /api/v1/verification/check-gap`
- Auth: Required (API key)
- Rate Limit: 100 requests/minute
- Timeout: 30 seconds

**Adjudicate F-Grade**:
- URL: `POST /api/v1/verification/adjudicate-grade`
- Auth: Required (API key)
- Rate Limit: 50 requests/minute
- Timeout: 30 seconds

**Quarterly Audit**:
- URL: `POST /api/v1/verification/quarterly-audit`
- Auth: Required (admin)
- Rate Limit: 10 requests/hour
- Timeout: 120 seconds

### C. Contact & Escalation

**Primary Contact**: CTO (agent 3a0e0b92-5e86-4cd4-99fb-e84db376d5a2)
**Backup**: CEO (for critical production issues)
**Lili Consultation**: For complex domain cases requiring expert review

---

*Document Status: READY FOR STAGING DEPLOYMENT*
*Next Action: Deploy to staging, execute test cases, verify acceptance criteria*
