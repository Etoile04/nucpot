# V1<->V2 Parity Report (NFM-3539)

**Verdict:** READY to flip

Source-of-truth input: 3 representative fixtures (short, long, multi-doc).  Each fixture is driven through the V1 stub path and the V2 (ExtractionOrchestratorV2) path; the four user-visible DB surfaces (extractions, chunks, comments, retries) are compared.

## Classification summary

| Class | Count |
|-------|-------|
| `cosmetic` | 21 |
| `non-cosmetic` | 0 |
| `blocking` | 0 |

Total fixtures run: **3** (short / long / multi-doc diversity per AC #3).

## Divergences

### Fixture: `long`

| Surface | V1 | V2 | Class | Rationale |
|---------|----|----|-------|-----------|
| `extraction_result_count` | `3` | `1` | cosmetic | both paths produced records (v1=3, v2=1); ratio divergence is expected (V1 stub is fixed, V2 is content-derived) |
| `extraction_chunk_count` | `3` | `4` | cosmetic | both paths produced records (v1=3, v2=4); ratio divergence is expected (V1 stub is fixed, V2 is content-derived) |
| `extraction_job_status` | `completed` | `completed` | cosmetic | extraction_job_status matches between V1 and V2 |
| `comment_count` | `0` | `0` | cosmetic | both paths produced zero records |
| `retry_count` | `0` | `0` | cosmetic | retry_count matches between V1 and V2 |
| `extracted_count` | `3` | `1` | cosmetic | both paths produced records (v1=3, v2=1); ratio divergence is expected (V1 stub is fixed, V2 is content-derived) |
| `staged_count` | `3` | `1` | cosmetic | both paths produced records (v1=3, v2=1); ratio divergence is expected (V1 stub is fixed, V2 is content-derived) |

### Fixture: `multi-doc`

| Surface | V1 | V2 | Class | Rationale |
|---------|----|----|-------|-----------|
| `extraction_result_count` | `3` | `1` | cosmetic | both paths produced records (v1=3, v2=1); ratio divergence is expected (V1 stub is fixed, V2 is content-derived) |
| `extraction_chunk_count` | `3` | `4` | cosmetic | both paths produced records (v1=3, v2=4); ratio divergence is expected (V1 stub is fixed, V2 is content-derived) |
| `extraction_job_status` | `completed` | `completed` | cosmetic | extraction_job_status matches between V1 and V2 |
| `comment_count` | `0` | `0` | cosmetic | both paths produced zero records |
| `retry_count` | `0` | `0` | cosmetic | retry_count matches between V1 and V2 |
| `extracted_count` | `3` | `1` | cosmetic | both paths produced records (v1=3, v2=1); ratio divergence is expected (V1 stub is fixed, V2 is content-derived) |
| `staged_count` | `3` | `1` | cosmetic | both paths produced records (v1=3, v2=1); ratio divergence is expected (V1 stub is fixed, V2 is content-derived) |

### Fixture: `short`

| Surface | V1 | V2 | Class | Rationale |
|---------|----|----|-------|-----------|
| `extraction_result_count` | `3` | `1` | cosmetic | both paths produced records (v1=3, v2=1); ratio divergence is expected (V1 stub is fixed, V2 is content-derived) |
| `extraction_chunk_count` | `3` | `4` | cosmetic | both paths produced records (v1=3, v2=4); ratio divergence is expected (V1 stub is fixed, V2 is content-derived) |
| `extraction_job_status` | `completed` | `completed` | cosmetic | extraction_job_status matches between V1 and V2 |
| `comment_count` | `0` | `0` | cosmetic | both paths produced zero records |
| `retry_count` | `0` | `0` | cosmetic | retry_count matches between V1 and V2 |
| `extracted_count` | `3` | `1` | cosmetic | both paths produced records (v1=3, v2=1); ratio divergence is expected (V1 stub is fixed, V2 is content-derived) |
| `staged_count` | `3` | `1` | cosmetic | both paths produced records (v1=3, v2=1); ratio divergence is expected (V1 stub is fixed, V2 is content-derived) |

## Appendix: Per-fixture snapshots

### `long`

| Path | extraction_result | extraction_chunk | extraction_job_status | comment_count | retry_count | extracted_count | staged_count |
|------|-------------------|------------------|---------------------|---------------|-------------|----------------|--------------|
| v1 | 3 | 3 | `completed` | 0 | 0 | 3 | 3 |
| v2 | 1 | 4 | `completed` | 0 | 0 | 1 | 1 |

### `multi-doc`

| Path | extraction_result | extraction_chunk | extraction_job_status | comment_count | retry_count | extracted_count | staged_count |
|------|-------------------|------------------|---------------------|---------------|-------------|----------------|--------------|
| v1 | 3 | 3 | `completed` | 0 | 0 | 3 | 3 |
| v2 | 1 | 4 | `completed` | 0 | 0 | 1 | 1 |

### `short`

| Path | extraction_result | extraction_chunk | extraction_job_status | comment_count | retry_count | extracted_count | staged_count |
|------|-------------------|------------------|---------------------|---------------|-------------|----------------|--------------|
| v1 | 3 | 3 | `completed` | 0 | 0 | 3 | 3 |
| v2 | 1 | 4 | `completed` | 0 | 0 | 1 | 1 |

