# Spec: `docs/architecture/db-evolution.md`

**Decision ticket:** NFM-3827 (A-D1) — CEO ruling: **Option D — 混合：时间线为主轴 + 领域标签交叉索引**
**Parent map issue:** NFM-3823 [PILOT-W0] Wayfinder 纪律试点启动
**Research inputs (deferred):** A-R1 (73 migrations classification, AFK→LE), A-R2 (models/schema drift, AFK→LE)
**Author:** CPO, 2026-08-30
**Implementer:** Lead Engineer
**Source path:** `docs/architecture/db-evolution.md` (does not exist yet)

---

## Context

The codebase has accumulated **73 Alembic migrations** in `apps/api/migrations/versions/` and **42 ORM models** in `apps/api/src/nfm_db/models/`. There is no architecture document that maps these together, records the merge history, or flags drift. NFM-3823 commissioned this document as Task A of the Wayfinder pilot. NFM-3827 settled the organizational axis: **D — chronological main spine, with a domain cross-reference table up front and merge reconciliation as a sidebar element**.

This spec defines the structure, content, and acceptance criteria of `docs/architecture/db-evolution.md`. Implementation is delegated to Lead Engineer.

---

## Document structure (locked)

The document MUST contain the following sections in this order. Section names are normative.

### §1. Overview

Three short paragraphs (≤120 words total):

1. What this document is — an evolution narrative of `apps/api` Alembic migrations and ORM models, indexed by epoch and tagged by domain.
2. Scope — `apps/api/migrations/versions/` (73 files) and `apps/api/src/nfm_db/models/` (42 files). Out of scope: Neo4j→Apache AGE graph (separate doc `graph-database-migration-from-neo4j-to-apache-age.md`).
3. How to read it — pick a path: chronological (read §3 in order), domain (jump to §2 then §3 sections by tag), reconciliation (jump to §4 sidebar).

### §2. Domain cross-reference table (front matter)

A single Markdown table that maps every model in `apps/api/src/nfm_db/models/` to (a) the epoch where it first appears in migrations, (b) the migration IDs that touch it, and (c) the dominant domain tag. Required columns:

| Model | First migration | Touched by | Domain tag |
|-------|----------------|------------|------------|

**Domain tags** (use exactly these, ≤10):

- `auth` — user, role, profile
- `blog-content` — blog posts, feedback
- `md-verification` — md_verification, jobs, staging quality
- `materials-core` — material, property, unit, classification_level, corpus
- `knowledge-graph` — kg_node, kg, hub_node, resource_node, entity_merge, ontology, ontology_version, kg_entity/relation_type
- `extraction-pipeline` — extraction_job, extraction_step, extraction_chunk, extraction_figure, extraction_result, extraction_gap, re_extraction_queue, ref_gap_fill, rerun_idempotency_key
- `ontology-feedback` — knowledge_gap, data_collection_request, adr009_reconcile_audit
- `sync-ops` — sync_operation, ingest_log, conflict, conflict_record, data_dna, health_event, hpc_failover_event
- `data-source` — source, data_submission, upload_session, dft_calculation
- `review-traceability` — review, md_verification, potential

The table is the **reading key** for the rest of the doc. Every model must appear exactly once.

### §3. Epoch narrative (main body)

Group migrations into **7 epochs**, ordered chronologically. For each epoch:

- **Heading** `### E{n} {year-month span} — {one-line name}`
- **3-5 sentence narrative** describing what this epoch added and why
- **Migration table** (cols: ID | Revision | Summary | Domain tags)
- **Cross-references** — bullet list of relevant ADRs (`docs/architecture/ADR-*.md`) and child PRs/issues (where known)

Epoch partition (locked, do not merge or split further):

| Epoch | Range | Span | One-line name |
|-------|-------|------|---------------|
| **E0** | 001–008 | Pre-KG seed | auth + blog + md-verification scaffolding |
| **E1** | 009–014 | 2025 Q3 | Materials core + first KG nodes/edges |
| **E2** | 015–027 | 2025 Q4 | Ref/Gap v3–v4, multimodal figures, schema-drift sync, **3 merges** (020, 025, 027) |
| **E3** | 028–041 | 2026 Q1 | Ref/Gap v4 stabilization + health events + sync ops + dedup + multimodal flags, **4 merges** (036, 037, 038, 041) |
| **E4** | 042–053 | 2026 Q2 | Extraction Pipeline V2 + ontology versioning + Gap/DCR schema + ADR-NFM-2675 alignment |
| **E5** | 054–059 | 2026 Q3 | Ontology FK wiring + KG entity/relation_type tables + ADR009 reconcile audit |
| **E6** | 060–062 | 2026 Q3 | Pipeline orchestration: track-id wiring + rerun idempotency |

Hex-named migrations (`b5f3a2c1d8e0`, `d3ddb691ae20`, `f8e2db803b55`, `9c15710c6321`, `54b39a26310`) MUST be placed in the epoch that matches their **revision timestamp** in the file's `Revision:` / `down_revision` chain, not by filename.

### §4. Merge reconciliation sidebar (structural, preserved)

A **persistent right-margin callout block** (use Markdown `> [!NOTE]` or a styled aside) summarizing every merge migration. This is a structural sidebar, NOT inline epoch content. Format:

```markdown
> [!NOTE] **Merge reconciliation (11 entries)**
>
> | Merge ID | Revisions merged | Closure PR/issue |
> |----------|-----------------|------------------|
> | `005c_merge_verification_branches` | (005, 005-add-verification-status) | NFM-… |
> | … |
```

The table MUST list all 11 merges:

1. `005c_merge_verification_branches`
2. `013_add_entity_merge_log` (merge log itself)
3. `020_merge_kg_forks`
4. `025_merge_verification_and_source_branches`
5. `027_merge_heads_011_and_026`
6. `036_merge_chain_A_and_B`
7. `037_merge_ref_gap_fill_chain`
8. `038_merge_health_events_and_ref_gap`
9. `041_merge_010_and_039`
10. `9c15710c6321_merge_blog_lineage_002_and_feedback_`
11. `f8e2db803b55_merge_dft_and_datasource_branches`

Closure PR/issue is **best-effort** — write `TBD` if not findable in `gh pr list --state merged` for the merge window. Do not block doc publication on resolving all TBDs.

### §5. Debt list (independent section, do not bury in narrative)

A **standalone section** (NOT scattered across epochs). Each debt entry MUST include:

- **ID** — `DEBT-{n}` (n = 1..N)
- **Title** — one sentence
- **Where** — file path + line range OR migration ID
- **Symptom** — observable failure mode
- **Proposed remediation** — short, no implementation detail
- **Severity** — `S0 (blocks merge)` / `S1 (must fix before next milestone)` / `S2 (housekeeping)`

Seed debts to enumerate (verify each against current code, add more as discovered):

- DEBT-1 — `extraction_prompt.py:17` hardcoded ontology constants (ontology → prompt wiring)
- DEBT-2 — `api/v1/ontology.py` zero write endpoints (ontology is read-only)
- DEBT-3 — chunk output not persisted (`_chunk_content` in PR #687)
- DEBT-4 — `GapScanService` queries staging tables instead of main (3 sites)
- DEBT-5 — LightRAG timeout shared between query/ingest
- DEBT-6 — `trigger_extraction()` 300-line monolith (strangler in flight per ADR-NFM-2737)
- DEBT-7 — Dual `ExtractionJob` (dataclass vs ORM) per ADR-NFM-2739
- DEBT-8 — `alembic heads` single-head test (`tests/test_alembic_has_a_single_head.py:222-234`) hardcoded exclusion list

Each debt has a **Severity** tag. Severity assignments must be defensible in one sentence per debt.

### §6. 当前态（placeholder — to be filled by A-R2）

This section is a **stub**. Body MUST contain a single Markdown block:

```markdown
> ⚠️ This section is intentionally empty. Content will be supplied by **A-R2** —
> the research ticket `models/ 与 schema 终态偏差` — which compares each
> `apps/api/src/nfm_db/models/*.py` against its corresponding migration's
> resulting schema and reports drift. Each drift item becomes a row in this
> section in the form:
>
> | Model | Migration | Drift | Severity |
>
> A-R2 is dispatched to Lead Engineer as a sibling of NFM-3827 under NFM-3823.
> When A-R2 lands, this section is populated. Until then, do not write narrative
> in this section.
```

Do NOT invent drift items. The doc ships with this section as a placeholder.

### §7. Reading paths (short)

Three short lists:

- **Chronological** — read §3 in order, follow merge sidebar notes (§4).
- **By domain** — start at §2 table, jump to tagged §3 sections, finish at §5 debt list.
- **By question** — `alembic upgrade heads` issues? → §5. `How does chunk fit in?` → E4 in §3. `Why two ExtractionJob classes?` → ADR-NFM-2739 + DEBT-7.

### §8. References

- All `docs/architecture/ADR-*.md` files referenced inline
- All `docs/architecture/graph-database-migration-from-neo4j-to-apache-age.md`
- `apps/api/migrations/versions/` directory (auto-enumerate)
- `apps/api/src/nfm_db/models/` directory (auto-enumerate)

---

## Acceptance criteria (testable)

Numbered, pass/fail:

1. Document exists at `docs/architecture/db-evolution.md`.
2. §2 cross-reference table has exactly 42 rows (one per ORM model file in `apps/api/src/nfm_db/models/`). Verified by row-count check vs `ls apps/api/src/nfm_db/models/*.py | wc -l`.
3. §3 has exactly 7 epoch subsections (E0–E6) in this order.
4. §4 sidebar lists all 11 merge migrations.
5. §5 debt list is its own `##` heading, not nested in §3.
6. §5 has ≥8 debt entries, each with all 6 fields (ID, Title, Where, Symptom, Remediation, Severity).
7. §6 contains ONLY the placeholder block (no drift rows added by implementation). Verified by absence of `drift item` body content.
8. No code is invented — every migration ID and revision referenced in the doc exists on `main`. Verified by a grep of migration IDs in the doc against `ls apps/api/migrations/versions/`.
9. Total document length 600–1200 lines.
10. Document builds in the docs pipeline (no broken Markdown — pass `markdownlint` if configured; otherwise spot-check headings, tables, and links).
11. Cross-references to `docs/architecture/ADR-*.md` resolve to existing files.
12. The doc mentions A-R2 by identifier and explains what will fill §6.

---

## File reference table

| File | Change |
|------|--------|
| `docs/architecture/db-evolution.md` | **NEW** — written by Lead Engineer per this spec |
| `docs/architecture/_specs/db-evolution-spec.md` | This spec artifact (CPO-authored, for traceability) |
| `apps/api/migrations/versions/` | READ-ONLY — source of truth for §3 + §4 |
| `apps/api/src/nfm_db/models/` | READ-ONLY — source of truth for §2 |

No source code touched. No migration touched. No model touched.

---

## Out of scope (do not produce in this ticket)

- Neo4j→Apache AGE migration narrative — already exists at `docs/architecture/graph-database-migration-from-neo4j-to-apache-age.md`. Link to it, do not duplicate.
- A-R1 migration classification stats — A-R1 produces its own deliverable; this doc consumes its numbers in §3 epoch summaries if available, otherwise leaves TBD.
- A-R2 drift report — A-R2 produces its own deliverable; §6 is a placeholder for it.
- New ADRs — if a debt item needs a formal decision, route to a separate ADR ticket under CTO. Do not author ADRs in this doc.
- Code refactors for any DEBT — §5 names them; fixing them is separate work.

---

## Dependencies

- **A-R2 must complete before §6 can be filled.** A-R2 is dispatched by NFM-3823 to Lead Engineer as a sibling of NFM-3827. Until A-R2 lands, §6 ships as placeholder.
- A-R1 is OPTIONAL — §3 epoch summaries can ship without it (write `A-R1: TBD` if not yet available).

---

## Sequencing (when implementation runs)

1. Implementer reads this spec end-to-end.
2. Run `ls apps/api/migrations/versions/ | wc -l` to confirm 73 files (sanity).
3. Run `ls apps/api/src/nfm_db/models/*.py | wc -l` to confirm 42 files.
4. Generate §2 table first (it's the index for everything else).
5. Write §3 epoch narratives by reading migration files in order. Cross-check each cited revision ID.
6. Compile §4 sidebar from merge filenames.
7. Enumerate §5 debts by reading flagged code sites; severity each.
8. Leave §6 as the literal placeholder block.
9. Write §7 + §8 (mechanical).
10. Run acceptance criteria 1–12 against the produced file.

---

## Effort estimate

Per-component:

- §2 cross-ref table: 30 min (mostly mechanical, 42 rows)
- §3 epoch narratives: 90 min (7 epochs × ~13 min each, with migration reading)
- §4 merge sidebar: 20 min (compile + TBD lookup)
- §5 debt list: 60 min (verify each debt against current code, severity reasoning)
- §6 placeholder: 5 min
- §7 + §8: 15 min
- Acceptance pass: 15 min

Total: **~4 hours** human-team work, or **~25 min** CC-led.

---

## Rollback plan

This deliverable is documentation-only. Rollback = revert the PR / delete the file. No data or schema impact. No rollback procedure needed beyond standard `git revert`.
