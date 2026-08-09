# ADR-NFM-2675: ExtractionGap Model Architecture for Recall Visibility

**Status**: Proposed — 2026-08-09
**Author**: CTO
**Parent Issue**: NFM-2675 ([NFM-2564-P4] ExtractionGap model + recall visibility)
**Epic**: NFM-2564 ([EPIC] 本体驱动的材料数据抽取与问答平台)
**Depends on**: NFM-2673 ([NFM-2564-P1] Pipeline decomposition via strangler fig, in_progress)
**Blocks**: NFM-2674 ([NFM-2564-P5] Unblock dispatch API + migration, in_progress)

## Context

NFM-2564 Phase 4 makes extraction recall rate visible and auditable. Today only precision
is measurable (review interface shows what was extracted), but missed extractions
(false negatives) are invisible because they don't exist in the DB. The core
requirement (R3): "用本体评估数据库缺口" (use the ontology to assess database gaps).

**Prior implementation history**:
- NFM-2575 (Phase 4 original, **done**) delivered an `ExtractionGap` model with
  ontology_version, entity_type, property, source_reference, chunk_id, gap_status
  columns. Schema was sound; per-document scan was monolithic.
- NFM-2576 (Phase 5, **done**) introduced `DataCollectionRequest` for coverage gaps
  distinct from recall gaps (CTO ruling: "repair actions differ fundamentally —
  fix prompt vs find new data").
- NFM-2673 (new P1, **in_progress**) decomposes `trigger_extraction()` into 5
  independent `ExtractionStep` units via strangler fig + feature flag. Each step
  persists an `ExtractionChunk` row with `_source_span` for offset-based provenance.

This ADR re-derives the Phase 4 schema for the new pipeline. The original NFM-2575
schema is the starting point; the P1 strangler fig refactor means gap detection
can now happen at chunk granularity rather than document granularity — finer
diagnosis and easier audit trail.

## Decision

### 1. ExtractionGap Data Model

```sql
CREATE TABLE extraction_gap (
    id                UUID PRIMARY KEY,
    ontology_version  TEXT    NOT NULL,                       -- e.g. "v2.1.0"
    entity_type       TEXT    NOT NULL,                       -- KEntityType identifier
    property          TEXT    NOT NULL,                       -- property name expected
    literature_id     UUID    NOT NULL REFERENCES literature(id),
    chunk_id          UUID    REFERENCES extraction_chunk(id), -- nullable for non-chunk gaps
    source_reference  TEXT,                                   -- offset pointer, e.g. "p3:offset=1240-1290"
    gap_status        TEXT    NOT NULL DEFAULT 'open',        -- open | filling | filled | wont_fix
    detected_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ontology_version, entity_type, property, literature_id, chunk_id)
);

CREATE INDEX idx_extraction_gap_lit_ontology ON extraction_gap(literature_id, ontology_version);
CREATE INDEX idx_extraction_gap_status       ON extraction_gap(gap_status);
CREATE INDEX idx_extraction_gap_chunk        ON extraction_gap(chunk_id) WHERE chunk_id IS NOT NULL;
```

**Why chunk_id nullable**: gaps that occur at document level (extraction produced no
chunks at all, e.g. text-only literature with no ontology hits) must still be tracked.
The bulk of gaps will have `chunk_id IS NOT NULL` after P1 lands.

**Why source_reference**: per the epic's review-provenance requirement
(ADR-NFM-796), gaps must be auditable to a specific text offset for the
reviewer UI to render the gap inline.

**Why UNIQUE constraint**: idempotent scan — running `scan_literature()` twice
should not create duplicate gap rows. Re-running with `only_open=True` returns
existing rows instead.

### 2. GapScanService Interface

```python
class GapScanService:
    def scan_literature(
        self,
        literature_id: UUID,
        ontology_version: str,
        *,
        only_open: bool = True,
        persist: bool = True,
    ) -> list[ExtractionGap]:
        """Compare ontology expectations vs extracted chunks for one literature.

        Returns gaps (entity_type+property combinations expected by ontology
        under ontology_version, but absent in extraction_chunk rows for this
        literature under the same version).

        When only_open=True, existing 'open' or 'filling' gaps are reused (no
        duplicates). When persist=False, the scan is a dry run that returns
        what *would* be persisted.
        """
        ...

    def compute_recall(
        self,
        literature_id: UUID,
        ontology_version: str,
    ) -> RecallMetrics:
        """Per-literature recall rate.

        recall = extracted_slots / ontology_expected_slots
        where extracted_slots = distinct (entity_type, property) pairs found in
        extraction_chunk rows for this literature under this version, and
        ontology_expected_slots = the set the ontology version declares.

        Returns: {recall_rate, extracted_slots, expected_slots, gap_count}
        """
        ...

    def compute_coverage(
        self,
        ontology_version: str,
    ) -> CoverageMetrics:
        """Per-ontology-version coverage %.

        coverage% = literature_fully_covered / literature_total
        where 'fully covered' means all ontology properties for at least one
        entity_type are present in DB records for that literature under this
        ontology_version.

        Returns: {coverage_rate, literature_total, literature_fully_covered,
                  gap_distribution: dict[(entity_type, property)] -> int}

        Coverage % is reported per ontology version, NOT aggregated across
        versions. Rationale: ontology upgrades cause coverage cliffs — only
        per-version reporting reveals the cause.
        """
        ...
```

**Why three methods, not one**:
- `scan_literature` writes gap records (one-shot, idempotent).
- `compute_recall` is a read-only metric for the per-document review UI.
- `compute_coverage` is a read-only metric for the per-ontology-version report.

These have different callers (gap-detection worker vs review UI vs ops dashboard)
and different cadences (one-shot vs on-demand). Keeping them separate avoids
mixing persistence with read APIs (R2: 每步可存可审).

### 3. API Contracts

```
GET /api/v1/literature/{id}/recall?ontology_version=vN
    -> 200 { recall_rate: float, extracted_slots: int, expected_slots: int, gaps: [...] }
    -> 404 if literature not found

GET /api/v1/ontology/{version}/coverage
    -> 200 { coverage_rate: float, literature_total: int, literature_fully_covered: int }
    -> 404 if ontology_version not found
```

**Authz**: review UI endpoints require `domain_expert` role (per NFM-2564 epic
governance: "本体编辑限定 domain_expert 角色"). Coverage report requires `admin`
or `domain_expert` (read-only ontology is permitted for domain_expert).

### 4. Alembic Migration

- Filename: `028_add_extraction_gap_chunk_id_and_source_reference.py`
- Pre-conditions: confirm Alembic head is unique (epic governance note: "Alembic
  head 冲突（有 027_merge_heads 前科）→ 迁移逐个加").
- Migration MUST be additive: existing rows (from NFM-2575) must have
  `chunk_id=NULL`, `source_reference=NULL` — these are valid.

### 5. Acceptance Criteria Mapping

| AC | Mapped to |
|---|---|
| ExtractionGap model with Alembic migration | §1 SQL DDL + §4 migration |
| GapScanService can scan a document and produce gap records | §2 `scan_literature()` |
| Recall rate visible per document in review interface | §2 `compute_recall()` + §3 GET endpoint |
| Coverage % reported per ontology version | §2 `compute_coverage()` + §3 GET endpoint |

## Dependency Graph

```
NFM-2673 (P1: strangler fig) ─── produces ExtractionChunk (FK target)
                                        ↓
NFM-2675 (P4: this issue) ──────────── uses ExtractionChunk.chunk_id in ExtractionGap
                                        ↓
NFM-2674 (P5: dispatch) ────────────── depends on ExtractionGap.gap_status for fill requests
```

NFM-2675 is **blocked by** NFM-2673 (the new ExtractionChunk table must exist).
NFM-2675 **blocks** NFM-2674 (the P5 dispatch needs gap rows to drive
DataCollectionRequest fan-out).

## Out of Scope

- Filling the gaps (covered by NFM-2674 P5).
- Coverage gap → DataCollectionRequest fan-out (covered by NFM-2576, may extend).
- Frontend review UI (separate task, not part of P4 backend).
- Re-running gap scans after ontology version bumps (separate task; ontology
  upgrade already auto-reopens `wont_fix` per NFM-2564 ruling).

## Risks

| Risk | Mitigation |
|---|---|
| Alembic head collision (epic governance has prior 027_merge_heads) | Run `alembic heads` before merge; add migration one at a time |
| Existing NFM-2575 rows lose chunk_id after P1 ships | Migration is additive (NULL OK); backfill job in NFM-2674 P5 |
| Scan performance on large literature | Add (literature_id, ontology_version) index; scan is idempotent so re-runs are cheap |
| Coverage % cliffs after ontology upgrade | Per-version reporting (§2 `compute_coverage` arg) prevents aggregation confusion |

## Verification Plan

- [ ] Unit tests for `scan_literature`: open vs filling vs filled states, idempotency, dry-run mode.
- [ ] Unit tests for `compute_recall`: empty literature, fully extracted, partial extraction.
- [ ] Unit tests for `compute_coverage`: empty ontology, fully covered, partial coverage.
- [ ] Alembic upgrade + downgrade round-trip test.
- [ ] API endpoint contract test (OpenAPI schema matches §3).
- [ ] Integration test: scan → recall → coverage end-to-end against dev Postgres with
      a fixture literature + ontology.
