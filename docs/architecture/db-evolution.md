# Database evolution (`apps/api`)

**Ticket:** NFM-3836 [PILOT-W0] A-D1
**Spec:** `docs/architecture/_specs/db-evolution-spec.md` (NFM-3827, Option D — hybrid)
**Parent map:** NFM-3823 [PILOT-W0] Wayfinder 纪律试点启动 — Task A
**Authored:** 2026-08-30 (LE implementation of locked CPO spec)

---

## §1. Overview

This document is the **evolution narrative** of the `apps/api` database layer — Alembic migrations under `apps/api/migrations/versions/` and the corresponding SQLAlchemy ORM models under `apps/api/src/nfm_db/models/`. It indexes the 73 migrations into 7 chronological epochs and cross-references them by domain tag, so a reader can locate the schema layer that supports a feature without grepping the tree.

**Scope.** In scope: `apps/api/migrations/versions/` (73 files) and `apps/api/src/nfm_db/models/` (42 files). Out of scope: the Neo4j→Apache AGE graph migration — that has its own document at `docs/architecture/graph-database-migration-from-neo4j-to-apache-age.md`, which is referenced but not duplicated here.

**How to read it.** Three reading paths are supported: **chronological** (read §3 in order, follow §4 merge sidebars), **by domain** (jump from §2 cross-reference to tagged §3 subsections, finish at §5 debt list), **by question** (use §7 to route). Pick the path that fits the question. The §2 cross-reference table is the reading key for §3 — every epoch subsection and every debt in §5 traces back to it.

---

## §2. Domain cross-reference table (front matter)

Every ORM model file is mapped to (a) the first migration that creates its table, (b) the migrations that subsequently touch it, and (c) the dominant domain tag. This table is the reading key for §3, §4, and §5.

**Domain tags** (locked, ≤10):

| Tag | Description |
|-----|-------------|
| `auth` | user, role, profile, shared `Base` |
| `blog-content` | blog posts, feedback |
| `md-verification` | MD verification jobs, LAMMPS potentials, verification tasks |
| `materials-core` | material, property, unit, classification, corpus |
| `knowledge-graph` | kg_node, kg, hub_node, resource_node, entity_merge, ontology, ontology_version |
| `extraction-pipeline` | extraction_job/step/chunk/figure/result/gap, re_extraction_queue, ref_gap_fill, rerun_idempotency_key |
| `ontology-feedback` | knowledge_gap, data_collection_request, adr009_reconcile_audit |
| `sync-ops` | sync_operation, ingest_log, conflict, conflict_record, data_dna, health_event, hpc_failover_event |
| `data-source` | source, upload_session, dft_calculation |
| `review-traceability` | review |

| # | Model | First migration | Touched by | Domain tag |
|---|-------|-----------------|------------|------------|
| 1 | `__init__` (shared `Base`, `TimestampMixin`, `JSONArray`, `CompatJSONB`) | `001_create_users_table` (first to import `Base`) | every migration that creates or alters a table | `auth` |
| 2 | `adr009_reconcile_audit` | `059_add_adr009_reconcile_audit_log` | 059 | `ontology-feedback` |
| 3 | `blog_post` | `002_create_blog_posts_table` | 002, 008 | `blog-content` |
| 4 | `classification_level` | `009_create_phase1_core_tables` | 009 | `materials-core` |
| 5 | `conflict` (enum re-exports of `ConflictStatus`/`ResolutionStrategy`) | `014_conflict_records` | 014 | `sync-ops` |
| 6 | `conflict_record` | `014_conflict_records` | 014, 022, 039 | `sync-ops` |
| 7 | `corpus` | `030_create_corpus_table` | 030 | `materials-core` |
| 8 | `data_collection_request` | `048_data_collection_request` | 048 | `ontology-feedback` |
| 9 | `data_dna` | `032_create_data_submission_tables` | 032 | `sync-ops` |
| 10 | `dft_calculation` | `023_add_dft_calculations` | 023, `054b39a26310_add_source_to_dft_calculations` | `data-source` |
| 11 | `entity_merge` | `013_add_entity_merge_log` | 013 | `knowledge-graph` |
| 12 | `extraction_chunk` | `042_extraction_step_and_chunk` | 042, 050 | `extraction-pipeline` |
| 13 | `extraction_figure` | `013_extraction_figures` | 013, 026, 014 (Phase-2 sync), 022 | `extraction-pipeline` |
| 14 | `extraction_gap` | `047_extraction_gap` | 047, 053 | `extraction-pipeline` |
| 15 | `extraction_job` | `013_add_multimodal_job_fields` (earliest surviving extension) | 013, 014 (sync), 034, 035, 049, 051, 056, 058, 062 (FK targets) | `extraction-pipeline` |
| 16 | `extraction_result` | `014_sync_phase2_schema_drift` | 014, 022, 039 | `extraction-pipeline` |
| 17 | `extraction_step` | `042_extraction_step_and_chunk` | 042, 061 | `extraction-pipeline` |
| 18 | `feedback` | `d3ddb691ae20_create_feedbacks_table` | d3 | `blog-content` |
| 19 | `health_event` | `037_create_health_events_table` | 037 | `sync-ops` |
| 20 | `hpc_failover_event` | `014_sync_phase2_schema_drift` | 014 | `sync-ops` |
| 21 | `hub_node` | `015_kg_models_complete` | 015 | `knowledge-graph` |
| 22 | `ingest_log` | `032_create_data_submission_tables` | 032 | `sync-ops` |
| 23 | `kg` (`kg_nodes`, `kg_edges`, `kg_review_queue`, `ontology_id_map`) | `011_create_kg_tables` | 011, 015, 020 (merge), 027 (merge) | `knowledge-graph` |
| 24 | `kg_node` | `012_create_kg_nodes_edges` | 012, 015, 014 (sync) | `knowledge-graph` |
| 25 | `knowledge_gap` | `046_add_knowledge_gaps` | 046 | `ontology-feedback` |
| 26 | `material` (`material_categories`, `materials`, `material_aliases`, `material_compositions`) | `009_create_phase1_core_tables` | 009, 030 (corpus ref), 031 (seed), 058 | `materials-core` |
| 27 | `md_verification` (`md_verification_jobs`, `hpc_jobs`, `md_simulation_results`, `defect_analysis_results`, `potential_fitting_results`, `verification_results_md`) | `003_create_md_verification_tables` | 003, 005 (`verification_results_md`), 006 | `md-verification` |
| 28 | `ontology` (`kg_entity_types`, `kg_relation_types`) | `057_create_kg_entity_and_relation_type_tables` | 057, 055 (FK) | `knowledge-graph` |
| 29 | `ontology_version` | `044_add_ontology_version` | 044, 049, 055 | `knowledge-graph` |
| 30 | `potential` | `003_create_potentials_table` | 003, 004 (seed), 005 (verification status) | `md-verification` |
| 31 | `property` (`property_categories`, `property_types`, `datasets`, `property_measurements`, `measurement_conditions`) | `009_create_phase1_core_tables` | 009, 031 (seed), 033 (conditions_hash + composite UNIQUE) | `materials-core` |
| 32 | `re_extraction_queue` | `045_add_re_extraction_queue` | 045 | `extraction-pipeline` |
| 33 | `ref_gap_fill` (`_ref_gap_fill_staging`) | `b5f3a2c1d8e0_add_ref_gap_fill_staging` | b5, 007, 035, 036 (ref_gap_fill_simple), 060 | `extraction-pipeline` |
| 34 | `rerun_idempotency_key` | `062_create_rerun_idempotency_keys` | 062 | `extraction-pipeline` |
| 35 | `resource_node` | `015_kg_models_complete` | 015 | `knowledge-graph` |
| 36 | `review` (`reviews`, `ReviewStatus`, `ReviewMixin`) | `022_phase3_review_traceability` | 022, 028 (backfill) | `review-traceability` |
| 37 | `source` (`data_sources`, `authors`, `data_source_authors`) | `021_add_datasource_storage_columns` (extends `009` Phase-1 `data_sources`) | 021, 025 (merge), 052, 058 | `data-source` |
| 38 | `sync_operation` | `040_create_sync_operations` | 040 | `sync-ops` |
| 39 | `unit` (`units`, `unit_conversions`) | `009_create_phase1_core_tables` | 009 | `materials-core` |
| 40 | `upload_session` | `032_create_data_submission_tables` | 032 | `data-source` |
| 41 | `user` | `001_create_users_table` | 001, 015 (profile fields), 029 (service-account flag), 043 (domain_expert role) | `auth` |
| 42 | `verification_task` | `024_create_verification_tasks_table` | 024 | `md-verification` |

**Reading note.** The 11 merges are tracked in §4 (sidebar) and are NOT part of the "Touched by" list — they have no schema effect but they show up in the topological chain. The 3 hex migrations that appear as model origins (`d3ddb691ae20`, `b5f3a2c1d8e0`, `054b39a26310`) are placed by chain-position per the spec's explicit rule on hex-named migrations.

---

## §3. Epoch narrative (main body)

Migrations are grouped into **7 epochs**, ordered chronologically. Each epoch is a self-contained narrative unit with its own migration table. Cross-references point to ADRs and child PRs/issues where known.

> **A-R1 status:** the migration-classification statistics that A-R1 produces (severity tiers, churn counts, KB-of-SQL) are not yet landed. Where this document cites migration-level classification, it uses the docstring summary as a proxy and marks the slot `A-R1: TBD` if a deeper metric is needed.

### E0 (001–008) — Pre-KG seed: auth + blog + md-verification scaffolding

E0 establishes the application's three foundational domains: authenticated users (`users`), the blog/feedback content layer (`blog_posts`, plus a parallel hex-anchored `feedbacks` lineage), and the MD verification harness (`md_verification_jobs`, `verification_results_md`, `potentials`). The epoch contains one structural merge (`005c_merge_verification_branches`) that resolves a 005a/005b split, and one cross-lineage merge (`9c15710c6321_merge_blog_lineage_002_and_feedback_`) that ties the hex-anchored feedback branch (`d3ddb691ae20` → `b5f3a2c1d8e0`) back into the main chain so that `003_create_md_verification_tables` and `003_create_potentials_table` can descend from a single head.

**Migration table**

| ID | Revision | Summary | Domain tags |
|----|----------|---------|-------------|
| `001_create_users_table` | 001 | create `users` with `blog_role` enum (auth foundation) | `auth` |
| `002_create_blog_posts_table` | 002 | create `blog_posts` with workflow metadata | `blog-content` |
| `d3ddb691ae20_create_feedbacks_table` | d3ddb691ae20 | create `feedbacks` table on a parallel branch (chain root, down=None) | `blog-content` |
| `b5f3a2c1d8e0_add_ref_gap_fill_staging` | b5f3a2c1d8e0 | create `_ref_gap_fill_staging` staging table (chain parent: d3) | `extraction-pipeline` |
| `9c15710c6321_merge_blog_lineage_002_and_feedback_` | 9c15710c6321 | merge `002` (blog lineage) with `b5f3a2c1d8e0` (feedback lineage) | `blog-content`, `extraction-pipeline` |
| `003_create_md_verification_tables` | 003 | create MD verification tables for LAMMPS integration | `md-verification` |
| `003_create_potentials_table` | 003 | create `potentials` table | `md-verification` |
| `004_seed_potentials` | 004 | seed demonstration `potentials` rows | `md-verification` |
| `005_add_verification_results_md_and_extend_jobs` | 005 | add `verification_results_md`; extend `md_verification_jobs` | `md-verification` |
| `005_add_verification_status` | 005 | add `verification_status` column to `potentials` | `md-verification` |
| `005c_merge_verification_branches` | 005c | merge 005a + 005b (verification branches) | `md-verification` |
| `006_add_cancelled_status_to_md_jobs` | 006 | add `'cancelled'` to `md_verification_jobs.status` check constraint | `md-verification` |
| `007_add_staging_quality_gate_columns` | 007 | add quality-gate and v4 columns to `_ref_gap_fill_staging` | `extraction-pipeline` |
| `008_add_blog_posts_title` | 008 | add `title` column to `blog_posts` | `blog-content` |

**Cross-references**
- ADR-NFM-2081 (commit-issue reference enforcement) — establishes the `<type>(NFM-####):` commit-subject discipline used throughout this epoch's chain.
- 005c → see §4 sidebar row 1.

**Detailed additions**
- `001_create_users_table` introduces the canonical `users` row with the `blog_role` Postgres enum (later extended in `043`), `email_verified` flag, and `is_active` flag. The shared `TimestampMixin` (`created_at`/`updated_at`) and `Base` from `nfm_db.models` are first imported here — every later migration transitively depends on this import chain.
- `002_create_blog_posts_table` lands `blog_posts` with `title`, `slug`, `content_md`, `author_id`, `status` (draft|in_review|published), `published_at`, plus workflow metadata. The 1-to-many `users → blog_posts` is established here.
- `d3ddb691ae20_create_feedbacks_table` is a **chain root** (`down_revision = None`), creating the `feedbacks` table on a parallel branch that runs independently of the 001/002 line. This is the seed of the blog-content + extraction-pipeline dual lineage.
- `b5f3a2c1d8e0_add_ref_gap_fill_staging` chains off `d3` and creates the `_ref_gap_fill_staging` staging table — initially just two columns (`id`, `source_text`). The full v3/v4 schema lands in E3 migrations (007, 035, 036).
- `9c15710c6321_merge_blog_lineage_002_and_feedback_` reconciles the two hex-lineage heads (`002` from the main branch, `b5f3a2c1d8e0` from the feedback branch) so that the 003 migrations can descend from a single head. No schema changes; this is a structural-only merge.
- `003_create_md_verification_tables` creates `md_verification_jobs` (the orchestrator), `hpc_jobs` (the LAMMPS dispatch row), `md_simulation_results` (output), `defect_analysis_results`, `potential_fitting_results`. The two `003_*` migrations share the `003` numeric prefix but each carries the string `revision: str = "003"` — Alembic allows this because each file has a unique `down_revision` chain (see §5 DEBT-8 on the single-head invariant).
- `003_create_potentials_table` creates the `potentials` table with `formula`, `source_url`, `is_canonical`, `verification_status` (the latter added in `005_add_verification_status`).
- `004_seed_potentials` is the project's first seed migration: 3 demo LAMMPS potentials covering UO2, Zr-2.5Nb, and PuO2 — chosen because they map to the demo materials in `009`.
- `005_add_verification_results_md_and_extend_jobs` adds `verification_results_md` and extends `md_verification_jobs` with `celery_task_id`. The `005_add_verification_status` parallel branch adds the `verification_status` column to `potentials`. The `005c_merge_verification_branches` reconciles them.
- `006_add_cancelled_status_to_md_jobs` adds the `'cancelled'` value to `md_verification_jobs.status` CHECK constraint — the third terminal state after `completed` and `failed`.
- `007_add_staging_quality_gate_columns` is the first v3/v4 work on `_ref_gap_fill_staging`: adds `quality_gate_score`, `quality_gate_passed`, `dedup_hash`, `processed_at`. The `pgcrypto` dependency is introduced here.
- `008_add_blog_posts_title` retroactively adds the `title` column to `blog_posts` — a 1-line patch that fixes a UI bug where the blog index page couldn't render a headline.

### E1 (009–014) — 2025 Q3: Materials core + first KG nodes/edges

E1 plants the materials-domain spine and the first knowledge-graph tables. `009_create_phase1_core_tables` is the largest single migration in the project — it creates `material_categories`, `materials`, `material_aliases`, `material_compositions`, `property_categories`, `property_types`, `datasets`, `property_measurements`, `measurement_conditions`, `units`, `unit_conversions`, `classification_levels`, `data_sources`, and `authors` in one transaction. `010_seed_phase1_reference_data` populates the reference data. `011_create_kg_tables` and `012_create_kg_nodes_edges` introduce the knowledge graph. `013_add_multimodal_job_fields` extends `extraction_jobs` with multimodal columns, and `014_sync_phase2_schema_drift` reconciles the model/schema drift that accumulated when models were added before their tables. `014_conflict_records` lands on a side branch.

**Migration table**

| ID | Revision | Summary | Domain tags |
|----|----------|---------|-------------|
| `009_create_phase1_core_tables` | 009 | create Phase 1 core tables (materials, properties, units, classifications, data_sources, authors) | `materials-core`, `data-source` |
| `010_seed_phase1_reference_data` | 010 | seed Phase 1 reference data | `materials-core` |
| `011_create_kg_tables` | 011 | create KG tables (NFM-838 Batch 2) | `knowledge-graph` |
| `012_create_kg_nodes_edges` | 012 | create `kg_nodes` and `kg_edges` with performance indexes | `knowledge-graph` |
| `013_add_entity_merge_log` | 013 | add `entity_merge_log` table | `knowledge-graph` |
| `013_add_multimodal_job_fields` | 013 | add multimodal extraction fields to `extraction_jobs` | `extraction-pipeline` |
| `014_conflict_records` | 014 | create `conflict_records` table and `default_conflict_strategy` (NFM-861) — side branch | `sync-ops` |
| `014_sync_phase2_schema_drift` | 014 | synchronize Phase 2 schema drift — columns and tables added to models (`extraction_results`, `hpc_failover_events`, stub tables) | `extraction-pipeline`, `sync-ops` |

**Cross-references**
- ADR-NFM-796 (review provenance) — establishes `extraction_method` and review traceability that the E3 multimodal flags will leverage.
- ADR-KR3-deploy-events — Phase-2 schema-drift sync at 014 precedes this ADR's first deploy.

**Detailed additions**
- `009_create_phase1_core_tables` is the **largest single migration in the project's history** (A-R1 metric, estimated by migration file size: ~520 LOC DDL). It lands 13 tables in one transaction:
  - `material_categories` (lookup), `materials` (root), `material_aliases` (1-N), `material_compositions` (1-N with `element`, `weight_percent`)
  - `property_categories` (lookup), `property_types` (lookup, later seeded in `031`), `datasets` (curated bundle), `property_measurements` (the value row), `measurement_conditions` (1-N)
  - `units`, `unit_conversions`
  - `classification_levels`
  - `data_sources` (later extended in `021`), `authors`, `data_source_authors` (M-N)
  All FK relationships, CHECK constraints, and partial UNIQUE indexes (for `materials.is_canonical`) are declared inline.
- `010_seed_phase1_reference_data` populates `material_categories` (e.g. `fuel`, `cladding`, `moderator`), `property_categories` (9 core: `thermal`, `mechanical`, `corrosion`, `physical`, `nuclear`, `thermochem`, `kinetics`, `microstructural`, `radiation`), and the canonical `units` (`K`, `MPa`, `μm`, `GWd/t`, etc.). The `units` table is the canonical source for unit parsing — every measurement row joins through it.
- `011_create_kg_tables` (NFM-838 Batch 2) introduces the first knowledge-graph tables in the project: `kg_nodes` and `kg_edges` are initially stub schemas; the full schema lands in `012`. This migration also adds the `ontology_id_map` lookup that bridges graph nodes to the eventual `ontology_version` table (E4).
- `012_create_kg_nodes_edges` finalizes `kg_nodes` with `node_type`, `label`, `properties` JSONB, `confidence`, `source_id`, plus performance indexes on `(node_type, label)` and `GIN(properties)`. `kg_edges` gets `source_node_id`, `target_node_id`, `relation_type`, `properties`, `confidence`. This is the first migration where `properties` JSONB is used to carry per-node payloads — the pattern repeated through every later model.
- `013_add_entity_merge_log` creates the `entity_merge_log` table for tracking KG node merges (when two nodes are detected as duplicates). Each row records `(source_node_id, target_node_id, strategy, confidence)`. The model class `entity_merge.EntityMerge` is in §2 row 11.
- `013_add_multimodal_job_fields` is the *first* `extraction_jobs` extension, adding `has_figure`, `figure_count`, `has_table`, `table_count`. (The `extraction_jobs` table itself is created in a parallel workstream that lands in `014`.) The `013` numeric prefix is shared across three files because of the merged chain at E2; see §5 DEBT-8.
- `014_conflict_records` (NFM-861) creates `conflict_records` on a **side branch** (chain parent: `016` — which no longer exists on disk because the branch was abandoned in favour of the parallel `022` lineage). The table schema survives in `conflict_record.py`; the enums (`ConflictStatus`, `ResolutionStrategy`) are re-exported from `conflict.py` for backward compatibility.
- `014_sync_phase2_schema_drift` is the **schema-drift reconciliation** migration: it adds the columns and stub tables that were added to ORM models before any migration landed. Notable additions: `extraction_results` (the per-property value row), `hpc_failover_events` (NFM-1760-era), and a handful of placeholder tables for graphs and sync queues. This migration's docstring warns future readers that any new drift must be reconciled before merging.
- `015_kg_models_complete` (chain parent: `017` — also absent on disk) extends `kg_nodes` with `figure_id` FK, `corpus_id` FK, `age_synced_at` (for the Neo4j→Apache AGE migration), and creates `kg_review_queue` and `ontology_id_map` lookups. Lives in E2 by chain position even though its numeric prefix is `015`.

> **Hex migration note.** `013_extraction_figures` (extraction-pipeline) has `down_revision = "020_merge_kg_forks"` and is therefore placed in E2 by chain position. `015_kg_models_complete` chains after `017` (parallel to `015_add_user_profile_fields`) and is also placed in E2. Both are excluded from the E1 table above.

### E2 (015–027) — 2025 Q4: Ref/Gap v3–v4, multimodal figures, schema-drift sync, 3 merges

E2 absorbs the late-2025 work: the user-profile fields migration (`015_add_user_profile_fields`), the `extraction_figures` extension (`013_extraction_figures`, `026_add_extraction_figures_columns`), the `dft_calculations` table (`023`), the `verification_tasks` table (`024`), and the Phase-3 review/state-machine migration (`022`). The three named merges (`020_merge_kg_forks`, `025_merge_verification_and_source_branches`, `027_merge_heads_011_and_026`) — plus the hex `f8e2db803b55_merge_dft_and_datasource_branches` and `054b39a26310_add_source_to_dft_calculations` — make E2 the merge-heaviest epoch in the project's first year. By the end of E2 the project has a single head.

**Migration table**

| ID | Revision | Summary | Domain tags |
|----|----------|---------|-------------|
| `015_add_user_profile_fields` | 015 | add user profile fields (affiliation, title, phone) | `auth` |
| `015_kg_models_complete` | 015 | complete KG model schema (`figure_id`, `corpus`, AGE sync, review queue, ontology map — `hub_nodes`, `resource_nodes`) — chain parent: `017`, parallel to `015_user_profile` | `knowledge-graph` |
| `013_extraction_figures` | 013 | create `extraction_figures` table (chain parent: `020`) | `extraction-pipeline` |
| `020_merge_kg_forks` | 020 | merge 011 and 015 forks (KG convergence) | `knowledge-graph` |
| `021_add_datasource_storage_columns` | 021 | add `DataSource` storage + parse-status columns (NFM-1486) | `data-source` |
| `022_phase3_review_traceability` | 022 | Phase 3: review state machine, source provenance, audit trail | `review-traceability`, `sync-ops` |
| `023_add_dft_calculations` | 023 | add `dft_calculations` table | `data-source` |
| `024_create_verification_tasks_table` | 024 | create `verification_tasks` for LAMMPS verification from Pareto recommendations | `md-verification` |
| `025_merge_verification_and_source_branches` | 025 | merge `024` (verification) with `054b39a26310` (source) | `md-verification`, `data-source` |
| `f8e2db803b55_merge_dft_and_datasource_branches` | f8e2db803b55 | merge `021` (datasource) with `023` (DFT) | `data-source` |
| `054b39a26310_add_source_to_dft_calculations` | 054b39a26310 | add `source` column to `dft_calculations` (chain parent: `f8e2db803b55`) | `data-source` |
| `026_add_extraction_figures_columns` | 026 | add `source_id` + `page_number` + `extracted_data` + `confidence` to `extraction_figures` | `extraction-pipeline` |
| `027_merge_heads_011_and_026` | 027 | merge heads `011` and `026` (single-head invariant restored) | `knowledge-graph`, `extraction-pipeline` |

**Cross-references**
- 020, 025, 027 → see §4 sidebar rows 3, 4, 5.
- `f8e2db803b55`, `054b39a26310` → see §4 sidebar row 11 and the "Hex migration chain placement" note below.
- ADR-KR3-prod-emission — emission events for `extraction_figures.confidence` provenance land in E2.

**Detailed additions**
- `015_add_user_profile_fields` extends `users` with `affiliation`, `title`, `phone`. The migration lands cleanly off `014_sync_phase2_schema_drift` (no merge needed at this point because the chain had a single head after `014`).
- `015_kg_models_complete` (see E1 detailed additions above) chains after `017` — both `015_*` files share the `015` numeric prefix because the chain converges at `020_merge_kg_forks`.
- `013_extraction_figures` (NFM-852) creates the `extraction_figures` table with `id`, `source_id` FK, `figure_type` (plot|diagram|image), `confidence`, `extraction_method` (added later in `039`). Chain parent: `020_merge_kg_forks` — placed in E2 by chain position per the spec rule on hex-named migrations.
- `020_merge_kg_forks` reconciles the two `015_*` forks (`015_add_user_profile_fields` and `015_kg_models_complete`) into a single head. This is the third named merge in §4 (row 3).
- `021_add_datasource_storage_columns` (NFM-1486) extends `data_sources` (created in `009`) with `storage_url`, `parse_status`, `parse_error`, `parsed_at`, plus JSONB `metadata` (the latter retroactively extended in `052`). The storage backend is configured separately via env (`DATA_SOURCE_BACKEND=s3|local`).
- `022_phase3_review_traceability` is the **Phase-3 review state-machine migration**: creates `reviews` (the aggregate per-extraction-result review row), adds `ReviewStatus` enum values, extends `extraction_results` with `review_id` FK, `review_status`, `review_confidence`, `review_actor_id`, plus creates `review_audit_trail` for full history. The `ReviewMixin` from `nfm_db.models.review` is the read-side companion.
- `023_add_dft_calculations` creates `dft_calculations` with `material_id` FK, `code` (vasp|qe|castep|... ), `functional`, `cutoff_eV`, `kpoint_density`, `result_json` JSONB, `converged` boolean. The `054b39a26310_add_source_to_dft_calculations` later migration (chain parent: `f8e2db803b55`) adds the missing `source_id` FK.
- `024_create_verification_tasks_table` creates `verification_tasks` for LAMMPS verification driven by Pareto recommendations from the extraction pipeline. Schema: `source_id`, `material_id`, `recommendation_json`, `lammps_input`, `lammps_output_path`, `status` (queued|running|completed|failed), `created_by`.
- `025_merge_verification_and_source_branches` reconciles `024_create_verification_tasks_table` with `054b39a26310_add_source_to_dft_calculations` (a hex-named migration that is itself the chain child of `f8e2db803b55_merge_dft_and_datasource_branches`). This is a 3-way merge in spirit (two real heads plus a sub-line), but Alembic records only the two heads.
- `f8e2db803b55_merge_dft_and_datasource_branches` reconciles `021_add_datasource_storage_columns` and `023_add_dft_calculations` (DFT) into a single head. Sidebar row 11.
- `054b39a26310_add_source_to_dft_calculations` (chain parent: `f8e2db803b55`) adds the `source_id` FK to `dft_calculations`, fixing the E2-born debt that `023` could not add the FK in-line because the source branch was still in flight. Placed in E2 by chain position even though the "054" prefix would suggest E5.
- `026_add_extraction_figures_columns` extends `extraction_figures` (created in `013_extraction_figures`) with `source_id` FK, `page_number`, `extracted_data` JSONB, plus retrofits `confidence` if missing. The `extracted_data` column is the figure payload consumed by the literature-detail UI.
- `027_merge_heads_011_and_026` reconciles `011_create_kg_tables` (which had been re-touched by the 015/020 lineage) with `026_add_extraction_figures_columns` into a single head. This is the single-head invariant restoration in E2.

> **Hex migration chain placement.** Per the spec rule, hex-named migrations are placed by chain position, not filename. `f8e2db803b55_merge_dft_and_datasource_branches` and `054b39a26310_add_source_to_dft_calculations` are therefore in E2 (not E5), because their chain parents — `021_add_datasource_storage_columns` and `023_add_dft_calculations` — are in E2. The "054" prefix is a content indicator (DFT calculations add-source migration), not an epoch marker.

### E3 (028–041) — 2026 Q1: Ref/Gap v4 stabilization + health events + sync ops + dedup + multimodal flags, 4 merges

E3 is the heaviest migration-density epoch. It lands the dedup-index work (`032_add_dedup_unique_indexes`, `033_add_conditions_hash_and_method_to_measurements`), the M2 data-submission tables (`032_create_data_submission_tables`), the `extraction_jobs` persistence and multimodal-flag columns (`034`, `035`), the `_ref_gap_fill_staging` v4 columns (`035_ref_gap_fill_staging_v4_columns`, `036_ref_gap_fill_staging_v4_columns_simple`), the `health_events` table (`037_create_health_events_table`), and the `sync_operations` table (`040_create_sync_operations`). The four named merges (`036_merge_chain_A_and_B`, `037_merge_ref_gap_fill_chain`, `038_merge_health_events_and_ref_gap`, `041_merge_010_and_039`) are the structural fabric of E3.

**Migration table**

| ID | Revision | Summary | Domain tags |
|----|----------|---------|-------------|
| `028_backfill_review_status_confidence` | 028 | backfill `review_status`: auto-approve high-confidence items | `review-traceability` |
| `029_add_user_service_account_flag` | 029 | add `is_service_account` flag to `users` | `auth` |
| `030_create_corpus_table` | 030 | create `corpus` table | `materials-core` |
| `031_seed_property_types` | 031 | seed `property_types` for OntoFuel ingest | `materials-core` |
| `032_add_dedup_unique_indexes` | 032 | add method + conditions_hash + composite UNIQUE indexes for cross-request dedup | `materials-core` |
| `032_create_data_submission_tables` | 032 | create M2 data submission 1+N architecture tables (`upload_sessions`, `data_submissions`, `ingest_logs`, `data_dna`) | `data-source`, `sync-ops` |
| `033_add_conditions_hash_and_method_to_measurements` | 033 | add `conditions_hash` + `method` + composite UNIQUE to `measurements` | `materials-core` |
| `034_add_extraction_job_persistence_columns` | 034 | add `extraction_jobs` persistence columns (NFM-2115 / NFM-2013 AC-2+AC-5) | `extraction-pipeline` |
| `035_add_extraction_job_multimodal_flags` | 035 | add multimodal-flag columns to `extraction_jobs` (NFM-2137) | `extraction-pipeline` |
| `035_ref_gap_fill_staging_v4_columns` | 035 | add quality-gate and v4 columns to `_ref_gap_fill_staging` (NFM-567) | `extraction-pipeline` |
| `036_merge_chain_A_and_B` | 036 | merge chain A (`032_create_data_submission_tables`) with chain B (`035_multimodal`) | `data-source`, `extraction-pipeline` |
| `036_ref_gap_fill_staging_v4_columns_simple` | 036 | add missing `dedup_hash` and quality-gate columns (simplified, no `pgcrypto`) | `extraction-pipeline` |
| `037_create_health_events_table` | 037 | create `health_events` for structured silent-failure tracking | `sync-ops` |
| `037_merge_ref_gap_fill_chain` | 037 | merge the `036_ref_gap_fill_simple` chain into `036_merge_chain_A_and_B` | `extraction-pipeline` |
| `038_merge_health_events_and_ref_gap` | 038 | merge `health_events` chain and `ref_gap_fill` chain into a single head | `sync-ops`, `extraction-pipeline` |
| `039_add_extraction_method_provenance` | 039 | add `extraction_method` provenance columns (NFM-2247) | `extraction-pipeline` |
| `040_create_sync_operations` | 040 | persist Hub sync operations (`sync_operations` table) | `sync-ops` |
| `041_merge_010_and_039` | 041 | merge isolated heads `010` (NFM-2029 chain) and `039` (legacy lineage) | `materials-core`, `extraction-pipeline` |

**Cross-references**
- 036, 037, 038, 041 → see §4 sidebar rows 6, 7, 8, 9.
- ADR-NFM-2139 (deploy-rollback architecture) — the rollback safety net that E3's bulk migration count motivated.
- ADR-NFM-2737 (strangler-fig extraction dispatch) — the architectural decision that motivates the E3 multimodal-flag expansion.

**Detailed additions**
- `028_backfill_review_status_confidence` is a **data migration** (not a schema migration): sets `review_status = 'auto_approved'` for rows where `confidence >= 0.95` AND `review_status = 'pending'`, then commits. Per ADR-NFM-796 (review provenance), every backfill row must carry a provenance marker — this migration logs each auto-approved row to `review_audit_trail` with `actor = 'system:028_backfill'`.
- `029_add_user_service_account_flag` adds `is_service_account BOOLEAN NOT NULL DEFAULT false` to `users`. Used by `nucpot create-service-account` (see `apps/api/src/nfm_db/cli/`) to issue non-interactive accounts. The flag is checked by middleware that bypasses 2FA and password-reset flows for service accounts.
- `030_create_corpus_table` creates `corpus` with `name`, `description`, `kind` (literature|experimental|simulated), `material_count`, `last_ingested_at`. Each `corpus` row is the curated bundle referenced by `kg_nodes` (via `corpus_id` FK, added in `015_kg_models_complete`).
- `031_seed_property_types` seeds the OntoFuel-specific `property_types`: `thermal_conductivity`, `specific_heat`, `melting_point`, `creep_rate`, `swelling_rate`, `Young_modulus`, `Poisson_ratio`, `fracture_toughness`, `oxide_thickness`. Each row carries `unit_id` FK (from `units` table) and `category_id` FK (from `property_categories`).
- `032_add_dedup_unique_indexes` adds composite UNIQUE indexes to `property_measurements`: `(material_id, property_type_id, conditions_hash)` and `(dataset_id, property_type_id, conditions_hash)`. This is the dedup invariant that the cross-request dedup workstream relies on; DEBT-4 (gap-scan reads staging) is the leftover that bypasses this constraint.
- `032_create_data_submission_tables` (the second `032_*` file) creates the M2 data-submission 1+N architecture: `upload_sessions` (parent session), `data_submissions` (N rows per session), `ingest_logs` (audit), `data_dna` (file fingerprint + checksum). The 1+N pattern means a single UI upload produces N validated rows.
- `033_add_conditions_hash_and_method_to_measurements` adds `conditions_hash` (SHA-256 of the conditions JSONB canonical form) and `method` to `property_measurements`, plus the composite UNIQUE constraint `(material_id, property_type_id, method, conditions_hash)`. This is the dedup mechanism that makes re-submissions idempotent.
- `034_add_extraction_job_persistence_columns` (NFM-2115 / NFM-2013 AC-2+AC-5) adds 6 persistence columns to `extraction_jobs`: `started_at`, `finished_at`, `celery_task_id`, `celery_parent_id`, `last_heartbeat_at`, `progress_pct`. Required for the orchestrator to surface job state to the UI.
- `035_add_extraction_job_multimodal_flags` (NFM-2137) adds the multimodal-flag columns to `extraction_jobs`: `has_figure`, `figure_count`, `has_table`, `table_count`, plus `text_token_count`, `estimated_tokens_total`. Used by the dispatch logic to route jobs to figure-aware vs text-only paths.
- `035_ref_gap_fill_staging_v4_columns` (NFM-567) extends `_ref_gap_fill_staging` with the v4 quality-gate columns: `confidence`, `flag_uncertain`, `flag_needs_review`, `v4_quality_score`, plus `processed_at`. This is the v4 line that the parallel `036_ref_gap_fill_staging_v4_columns_simple` revisits.
- `036_merge_chain_A_and_B` reconciles `032_create_data_submission_tables` (chain A) with `035_add_extraction_job_multimodal_flags` (chain B). Sidebar row 6.
- `036_ref_gap_fill_staging_v4_columns_simple` adds the missing `dedup_hash` and quality-gate columns without the `pgcrypto` extension — a simpler fallback that runs in environments where `pgcrypto` is unavailable.
- `037_create_health_events_table` creates `health_events` for structured silent-failure tracking. Schema: `service_name`, `event_type`, `severity` (info|warning|error|critical), `payload` JSONB, `occurred_at`, `resolved_at`. Surfaced by the SRE cron to Paperclip as `[SRE-WARNING]` / `[SRE-CRITICAL]` issues.
- `037_merge_ref_gap_fill_chain` reconciles `036_merge_chain_A_and_B` with `036_ref_gap_fill_staging_v4_columns_simple`. Sidebar row 7.
- `038_merge_health_events_and_ref_gap` reconciles `037_create_health_events_table` with `037_merge_ref_gap_fill_chain` into a single head. Sidebar row 8.
- `039_add_extraction_method_provenance` (NFM-2247) adds the `extraction_method` VARCHAR(100) column to `extraction_results`, `kg_nodes`, and `kg_edges`. The vocabulary is `llm|manual|mineru` (see `apps/api/src/nfm_db/services/provenance.py` and the test in `apps/api/tests/test_extraction_provenance.py`). No server default — provenance must be set explicitly at write time per ADR-NFM-796.
- `040_create_sync_operations` creates `sync_operations` to persist Hub sync operations. Schema: `source_system`, `target_system`, `operation_type`, `payload` JSONB, `status` (queued|in_flight|completed|failed), `last_error`, `started_at`, `finished_at`. Replaces the in-memory queue that lost operations on restart.
- `041_merge_010_and_039` reconciles the long-pending `010_seed_phase1_reference_data` head (which the NFM-2029 chain had orphaned) with `039_add_extraction_method_provenance`. Sidebar row 9.

### E4 (042–053) — 2026 Q2: Extraction Pipeline V2 + ontology versioning + Gap/DCR schema + ADR-NFM-2675 alignment

E4 rebuilds the extraction pipeline on V2 primitives: `042_extraction_step_and_chunk` creates `extraction_steps` and `extraction_chunks` (NFM-2567-T2), `044_add_ontology_version` introduces `ontology_versions` and seeds v0.1.0 (NFM-2579), `045_add_re_extraction_queue` adds the re-extraction queue (NFM-2581 / NFM-2573-T4), `046_add_knowledge_gaps` creates `knowledge_gaps` (NFM-2582 / NFM-2573-T5), `047_extraction_gap` creates `extraction_gaps` (NFM-2575-T1), and `048_data_collection_request` creates `data_collection_requests` (NFM-2619). The end-of-epoch alignment migration `053_align_extraction_gap_with_adr_nfm_2675` is the schema-level commitment to ADR-NFM-2675.

**Migration table**

| ID | Revision | Summary | Domain tags |
|----|----------|---------|-------------|
| `042_extraction_step_and_chunk` | 042 | create `extraction_steps` and `extraction_chunks` (NFM-2567-T2) | `extraction-pipeline` |
| `043_add_domain_expert_role` | 043 | add `domain_expert` value to `blog_role_enum` (NFM-2573-T1) | `auth`, `blog-content` |
| `044_add_ontology_version` | 044 | create `ontology_versions` and seed v0.1.0 (NFM-2579) | `knowledge-graph` |
| `045_add_re_extraction_queue` | 045 | create `re_extraction_queue` (NFM-2581 / NFM-2573-T4) | `extraction-pipeline` |
| `046_add_knowledge_gaps` | 046 | create `knowledge_gaps` (NFM-2582 / NFM-2573-T5) | `ontology-feedback` |
| `047_extraction_gap` | 047 | create `extraction_gaps` (NFM-2575-T1) | `extraction-pipeline` |
| `048_data_collection_request` | 048 | create `data_collection_requests` (NFM-2619) | `ontology-feedback` |
| `049_add_ontology_version_to_extraction_job` | 049 | add ontology-version columns to `extraction_jobs` (NFM-2638) | `extraction-pipeline`, `knowledge-graph` |
| `050_extraction_chunk_v2_provenance` | 050 | add V2 provenance columns to `extraction_chunks` (NFM-2687) | `extraction-pipeline` |
| `051_extraction_job_orchestration_columns` | 051 | add 10 orchestration columns to `extraction_jobs` (NFM-2745) | `extraction-pipeline` |
| `052_add_datasource_metadata` | 052 | add `DataSource.metadata_` JSONB column (NFM-2649) | `data-source` |
| `053_align_extraction_gap_with_adr_nfm_2675` | 053 | align `extraction_gaps` schema with ADR-NFM-2675 Section 1 | `extraction-pipeline` |

**Cross-references**
- ADR-NFM-2739 (extraction-job dual-class) — establishes the dataclass-vs-ORM split for `ExtractionJob` that DEBT-7 will track.
- ADR-NFM-2675 (referenced via `053_align_...` filename) — schema alignment with this ADR is the E4 commit point.

**Detailed additions**
- `042_extraction_step_and_chunk` (NFM-2567-T2) creates `extraction_steps` (the orchestrator's per-step state row) and `extraction_chunks` (the chunked input row). `extraction_steps` schema: `job_id` FK, `step_name`, `status`, `started_at`, `finished_at`, `error`, plus `input`/`output` JSONB. `extraction_chunks` schema: `step_id` FK, `chunk_index`, `text`, `char_start`, `char_end`, plus `token_count` (added in `050`).
- `043_add_domain_expert_role` (NFM-2573-T1) adds the `domain_expert` value to `blog_role_enum`. This is the third role value after `admin` and `user`, and the only role that grants access to the `/api/v1/knowledge-gap/*` endpoints (per the ontology-feedback domain).
- `044_add_ontology_version` (NFM-2579) creates `ontology_versions` with `version` VARCHAR, `semver` VARCHAR, `ontology_data` JSONB, `is_active`, `released_at`, `notes`. Seeds v0.1.0 as the inaugural row. The `ontology_data` JSONB is the payload consumed by `extraction_prompt.build_ontology_extraction_prompt` (see DEBT-1 on the hardcoded budget).
- `045_add_re_extraction_queue` (NFM-2581 / NFM-2573-T4) creates `re_extraction_queue` for queueing re-extraction jobs when an ontology version changes. Schema: `job_id` FK, `reason`, `priority`, `scheduled_for`, `status` (pending|in_flight|completed|failed).
- `046_add_knowledge_gaps` (NFM-2582 / NFM-2573-T5) creates `knowledge_gaps` for surfacing ontology gaps to the user. Schema: `ontology_version_id` FK, `category`, `entity_name`, `gap_type` (missing_property|missing_relation|missing_entity), `priority`, `status` (open|triaged|closed).
- `047_extraction_gap` (NFM-2575-T1) creates `extraction_gaps` for tracking gaps discovered during extraction (as opposed to gaps known a priori). Schema: `job_id` FK, `material_id` FK, `property_type_id` FK, `gap_type`, `confidence`, `status`. Later aligned to ADR-NFM-2675 in `053`.
- `048_data_collection_request` (NFM-2619) creates `data_collection_requests` for the user's "I need this data" workflow. Schema: `requester_id` FK, `knowledge_gap_id` FK (optional), `extraction_gap_id` FK (optional), `priority`, `justification`, `status` (draft|approved|in_progress|completed|cancelled).
- `049_add_ontology_version_to_extraction_job` (NFM-2638) adds `ontology_version_id` FK to `extraction_jobs`, recording which ontology version was active when the job ran. This is the audit trail for "what did the LLM see?" — required for reproducing extraction results after ontology updates.
- `050_extraction_chunk_v2_provenance` (NFM-2687) adds V2 provenance columns to `extraction_chunks`: `token_count`, `embedding_model_version`, `embedding_model_id`, plus the `provenance_token` (the same vocabulary as `extraction_method` in `039`). Note: this migration does **not** add the `content` column — that is DEBT-3 (chunk text not persisted).
- `051_extraction_job_orchestration_columns` (NFM-2745) adds 10 orchestration columns to `extraction_jobs`: `orchestrator_state`, `current_step_id` FK, `retry_count`, `max_retries`, `next_retry_at`, `worker_id`, `worker_lock_expires_at`, `dispatch_strategy`, `chunk_strategy`, `rerun_strategy`. The `worker_lock_expires_at` enables safe restarts without losing in-flight jobs.
- `052_add_datasource_metadata` (NFM-2649) adds the `metadata_` JSONB column to `data_sources`. The trailing underscore is required because `metadata` is reserved by SQLAlchemy's declarative API.
- `053_align_extraction_gap_with_adr_nfm_2675` is a schema-alignment migration: drops the `confidence` column from `extraction_gaps` (ADR-NFM-2675 §1 says gap detection is not a confidence-bearing signal), renames `gap_type` to `gap_category`, and adds `severity` (low|medium|high|critical) plus `detected_at`. This is the schema-level commitment to ADR-NFM-2675.

### E5 (054–059) — 2026 Q3: Ontology FK wiring + KG entity/relation_type tables + ADR009 reconcile audit

E5 closes the ontology-version loop by adding FK columns from the KG type tables back to `ontology_versions` (`055`), creating the missing `kg_entity_types` and `kg_relation_types` tables (`057`), wiring `track_id` into `extraction_jobs` (`056`), aligning the schema-drift backlog (`058`), and creating the ADR-009 §4.3 reconcile audit log (`059`). The chain order in E5 is `053 → 057 → 055 → 056 → 058 → 059` — the `055` and `057` numeric inversion is intentional: `057` creates the type tables first, then `055` adds the FK column to them.

**Migration table**

| ID | Revision | Summary | Domain tags |
|----|----------|---------|-------------|
| `055_add_ontology_version_fk_to_type_tables` | 055 | add `ontology_version_id` FK to `kg_entity_types` and `kg_relation_types` (NFM-2873-T1) | `knowledge-graph` |
| `056_add_track_id_to_extraction_job` | 056 | add `track_id` column to `extraction_jobs` | `extraction-pipeline` |
| `057_create_kg_entity_and_relation_type_tables` | 057 | create the missing `kg_entity_types` and `kg_relation_types` tables | `knowledge-graph` |
| `058_align_schema_drift_backlog` | 058 | align the schema-drift backlog (NFM-3446-P2) | `materials-core`, `data-source`, `extraction-pipeline` |
| `059_add_adr009_reconcile_audit_log` | 059 | ADR-009 §4.3 reconcile audit log table (NFM-3586) | `ontology-feedback` |

**Cross-references**
- ADR-009 is not in the `docs/architecture/ADR-*.md` set yet — it is referenced by file content (per `059`) and via §6's drift placeholder.

**Detailed additions**
- `055_add_ontology_version_fk_to_type_tables` (NFM-2873-T1) adds `ontology_version_id` FK to `kg_entity_types` and `kg_relation_types`. **Important**: this migration's `055` numeric prefix is greater than `057` (the migration that creates the type tables) — the chain order is `057 → 055`, not `055 → 057`. This inversion is intentional: `057` creates the tables (so they can be referenced), then `055` adds the FK column.
- `056_add_track_id_to_extraction_job` adds the `track_id` column to `extraction_jobs`. The `track_id` is the dispatch primitive that ADR-NFM-2737 (strangler-fig extraction dispatch) uses to route jobs through the new orchestrator vs the legacy pipeline. Also referenced by E6's `061_add_track_id_to_extraction_step`.
- `057_create_kg_entity_and_relation_type_tables` creates the missing `kg_entity_types` (with `id`, `name`, `description`, `ontology_version_id` FK, `required_properties` JSONB) and `kg_relation_types` (with `id`, `name`, `description`, `source_types`, `target_types`, `ontology_version_id` FK). These tables are the typed catalog that ADR-NFM-2675 §2 says should drive the extraction prompt — the `extraction_prompt.build_ontology_extraction_prompt` function reads them via `ontology_data` JSONB in `044`.
- `058_align_schema_drift_backlog` (NFM-3446-P2) reconciles the schema-drift backlog: adds the `is_service_account`-equivalent flag to `material_categories`, adds `data_dna.checksum` column, and pins down `ref_gap_fills.dedup_hash` UNIQUE constraint. Per ADR-NFM-2139 (deploy-rollback architecture), every drift alignment must be reversible; this migration's `downgrade()` drops the same set of columns.
- `059_add_adr009_reconcile_audit_log` (NFM-3586) creates `adr009_reconcile_audit` with `run_id`, `started_at`, `finished_at`, `matched_count`, `mismatched_count`, `mismatches` JSONB, `actor`, `notes`. This is the audit log that ADR-009 §4.3 mandates for every reconcile run.

### E6 (060–062) — 2026 Q3: Pipeline orchestration: track-id wiring + rerun idempotency

E6 finishes the project-to-date surface area. `060_backfill_ref_gap_fill_staging_source` backfills empty `source` values in `_ref_gap_fill_staging`, `061_add_track_id_to_extraction_step` propagates `track_id` from `extraction_jobs` to `extraction_steps` (NFM-3595 / NFM-3543-A), and `062_create_rerun_idempotency_keys` introduces the `rerun_idempotency_keys` table for `POST /jobs/{id}/steps/{name}/rerun`. After `062` the project has a single alembic head.

**Migration table**

| ID | Revision | Summary | Domain tags |
|----|----------|---------|-------------|
| `060_backfill_ref_gap_fill_staging_source` | 060 | backfill empty `source` values in `_ref_gap_fill_staging` | `extraction-pipeline` |
| `061_add_track_id_to_extraction_step` | 061 | add `track_id` column to `extraction_steps` (NFM-3595 / NFM-3543-A) | `extraction-pipeline` |
| `062_create_rerun_idempotency_keys` | 062 | create `rerun_idempotency_keys` for `POST /jobs/{id}/steps/{name}/rerun` | `extraction-pipeline` |

**Cross-references**
- ADR-NFM-3404 (RAG query timeout alignment) — invoked by the same SRE-driven orchestration work that produced E6.
- ADR-NFM-2737 (strangler-fig extraction dispatch) — `track_id` is the dispatch primitive the strangler-fig refactor needs.

**Detailed additions**
- `060_backfill_ref_gap_fill_staging_source` is a **data migration**: backfills empty `source` values in `_ref_gap_fill_staging` by joining against the corresponding `ref_gap_fills` row (the promotion target) and inheriting its `source_id`. Rows that cannot be matched are logged to a separate `unmatched_backfill` table for SRE review.
- `061_add_track_id_to_extraction_step` (NFM-3595 / NFM-3543-A) propagates `track_id` from `extraction_jobs` (added in `056`) to `extraction_steps`. The propagation is enforced by a Postgres trigger function that fires on `extraction_jobs` UPDATE — `track_id` is inherited on step creation. The `track_id` carries through to `extraction_chunks` via the `step_id` FK.
- `062_create_rerun_idempotency_keys` creates `rerun_idempotency_keys` for the `POST /jobs/{id}/steps/{name}/rerun` endpoint. Schema: `idempotency_key` (UUID, PK), `job_id` FK, `step_name`, `request_hash` (SHA-256 of the request body), `response_status`, `response_body` JSONB, `created_at`, `expires_at`. The endpoint checks for an existing key before re-running; if found and not expired, returns the cached response. This is the mechanism that makes retries safe under transient failures.

---

## §4. Merge reconciliation sidebar

> [!NOTE] **Merge reconciliation (11 entries)**
>
> This sidebar lists every merge migration in the project. A merge migration has no schema effect; it exists only to reconcile divergent chains. Closure PR/issue is best-effort — `TBD` marks any merge where the closure PR cannot be located in `gh pr list --state merged` for the merge window. Doc publication does not block on resolving `TBD`s.
>
> | # | Merge ID | Revisions merged | Closure PR/issue |
> |---|----------|------------------|------------------|
> | 1 | `005c_merge_verification_branches` | `005a`, `005b` (verification branches) | TBD |
> | 2 | `013_add_entity_merge_log` (merge log table itself, not a chain merge) | n/a — table create, not a merge migration | n/a |
> | 3 | `020_merge_kg_forks` | `011`, `015` (KG branches) | TBD |
> | 4 | `025_merge_verification_and_source_branches` | `024`, `054b39a26310` (verification + source) | TBD |
> | 5 | `027_merge_heads_011_and_026` | `011`, `026` (single-head restoration) | TBD |
> | 6 | `036_merge_chain_A_and_B` | `032_create_data_submission_tables`, `035_add_extraction_job_multimodal_flags` | TBD |
> | 7 | `037_merge_ref_gap_fill_chain` | `036_merge_chain_A_and_B`, `036_ref_gap_fill_staging_v4_columns_simple` | TBD |
> | 8 | `038_merge_health_events_and_ref_gap` | `037_create_health_events_table`, `037_merge_ref_gap_fill_chain` | TBD |
> | 9 | `041_merge_010_and_039` | `010`, `039_add_extraction_method_provenance` | TBD |
> | 10 | `9c15710c6321_merge_blog_lineage_002_and_feedback_` | `002`, `b5f3a2c1d8e0` (blog + feedback lineages) | TBD |
> | 11 | `f8e2db803b55_merge_dft_and_datasource_branches` | `021`, `023` (DFT + datasource) | TBD |

> **Note on row 2.** The spec lists `013_add_entity_merge_log` as merge entry #2. That file creates the `entity_merge_log` *table* (it is not a chain merge). It is included in the sidebar as a structural reference, but the "Revisions merged" and "Closure PR/issue" columns are marked `n/a` — there are no parent heads to reconcile.

---

## §5. Debt list

A standalone record of known technical debt in the database layer and adjacent code paths. Each entry has an ID, a one-sentence title, a `Where` pointer, the observable symptom, a short proposed remediation, and a severity tag. Severities: **S0** = blocks merge; **S1** = must fix before next milestone; **S2** = housekeeping.

| ID | Title | Where | Symptom | Proposed remediation | Severity |
|----|-------|-------|---------|----------------------|----------|
| DEBT-1 | Hard-coded ontology-context budget constant in extraction prompt | `apps/api/src/nfm_db/services/extraction_prompt.py:31` (`ONTOLOGY_CONTEXT_BUDGET_CHARS = 8000`) | Prompt-builder cannot be tuned per `ontology_version`; small ontologies waste tokens, large ones truncate. | Make the budget a per-`OntologyVersion` column with the constant as fallback. | S2 — housekeeping; no immediate breakage, but blocks per-version prompt tuning. |
| DEBT-2 | Ontology API surface is read-only | `apps/api/src/nfm_db/api/v1/ontology.py` (1 POST endpoint + 5 GET endpoints — no PUT/DELETE) | There is no API path to update or deprecate `ontology_versions` outside the seed scripts; bumps are SQL-only. | Add `PUT/PATCH/DELETE` endpoints behind an admin role, mirroring `ontology_version` CRUD. | S1 — must fix before next ontology-version rollout, otherwise versioning remains admin-only via SQL. |
| DEBT-3 | `_chunk_content()` output is not persisted | `apps/api/src/nfm_db/services/extraction_pipeline.py:93` (def) and `:535` (call site); superseded path tracked in PR #687 | Re-chunking the same source content re-runs `_chunk_content()` and yields the same slices, but downstream has no way to recover the slice text from `extraction_chunk.id`. | Persist the chunked text into `extraction_chunks.content` (the V2 provenance migration `050` adds adjacent columns but not `content`); update `_chunk_content` to write through. | S1 — must fix before the rerun API in `062` is exercised at scale; otherwise reruns cannot verify byte-equality. |
| DEBT-4 | `GapScanService` reads staging tables instead of main tables | `apps/api/src/nfm_db/services/gap_scan_service.py:91` (`_parse_staging_counts`), `:155` (`_get_staging_counts`); third site is `gap_scanner.py` call chain | Gap-scan results are computed against `_ref_gap_fill_staging` even after `035_ref_gap_fill_staging_v4_columns` promoted rows to main; counts drift from production truth. | Switch the 3 query sites to read from the main tables (`ref_gap_fills`, `materials`) and keep staging read-only as an ingestion buffer. | S0 — blocks the next milestone's gap-scan SLO; reporting wrong numbers is treated as blocking. |
| DEBT-5 | LightRAG query and ingest share a single timeout budget | `apps/api/src/nfm_db/config.py` and `apps/api/src/nfm_db/services/lightrag_client.py:38` (timeout comment); both paths pull from the same `RAG_QUERY_BUDGET` env | Long-running ingest can starve the user-facing query budget, surfacing as `RAG_TIMEOUT` on interactive `/kg/search` calls. | Split into `RAG_QUERY_BUDGET` and `RAG_INGEST_BUDGET` (per ADR-NFM-3404 follow-up); default ingest to 3× query budget. | S1 — must fix before next milestone; observability gap already caused a P1 incident. |
| DEBT-6 | `trigger_extraction()` is a 167-line monolith | `apps/api/src/nfm_db/services/extraction_pipeline.py:723–889` | Function has 5+ concerns (validation, dispatch, status emit, ontology load, chunk warmup); tests must mock the whole surface. | Continue the strangler-fig refactor in flight per ADR-NFM-2737; split into `validate → dispatch → emit_status → warmup_chunks` with each owning its own surface. | S1 — must fix before the next milestone; the function is already a test bottleneck. |
| DEBT-7 | Dual `ExtractionJob` definitions (ORM vs dataclass) | `apps/api/src/nfm_db/models/extraction_job.py:31` (`class ExtractionJob(Base)`); dataclass variant per ADR-NFM-2739 | Callers must pick between two `ExtractionJob` types depending on whether they touch the DB; type-checker cannot prevent crossing the streams. | Promote the dataclass to a typed DTO with `model_validator` from the ORM class; remove the parallel class once all callers migrated. | S2 — housekeeping; ADR-NFM-2739 already documents the dual-class rationale and the planned consolidation. |
| DEBT-8 | Historical hard-coded alembic-head allow-list (now resolved) | `apps/api/tests/test_extraction_provenance.py:205–225` (`test_alembic_has_a_single_head`) — the test docstring at L207–214 references the pre-`ffaab68f` allow-list | The current test uses dynamic `ScriptDirectory.get_heads()` and asserts a single head (NFM-167 gate). The legacy hard-coded allow-list is documented in the docstring as a cautionary note. | No code change needed — debt is preserved in the docstring as a record of why the dynamic test was adopted. New migrations land without test edits. | S2 — housekeeping; already resolved. Keep the docstring intact as institutional memory. |

> **Severity rationale (one sentence per debt).**
>
> - DEBT-1 (S2): the hardcoded budget is observable but every prompt uses the same default, so per-version tuning is a feature request, not a defect.
> - DEBT-2 (S1): no write endpoint means ontology-version bumps are SQL-only, which is acceptable for one-off bumps but blocks the planned v0.2.0 rollout automation.
> - DEBT-3 (S1): rerun byte-equality depends on persisted chunk text, and `062_create_rerun_idempotency_keys` is the first feature that surfaces the gap.
> - DEBT-4 (S0): gap-scan output is in the next milestone's SLO; reporting counts from staging after promotion is a correctness defect.
> - DEBT-5 (S1): the shared budget caused a P1 incident on 2026-08-15; the fix is documented in ADR-NFM-3404 and only awaits code change.
> - DEBT-6 (S1): the monolith is the primary reason integration tests for extraction take >30s; strangler per ADR-NFM-2737 is mid-flight.
> - DEBT-7 (S2): dual classes are intentional per ADR-NFM-2739 and tracked; no merge-blocker.
> - DEBT-8 (S2): the test was rewritten in commit `ffaab68f`; the debt is preserved as documentation, not as live code.

**Detailed justifications (extended reading).**

**DEBT-1 — `ONTOLOGY_CONTEXT_BUDGET_CHARS`.** The constant is referenced from `apps/api/src/nfm_db/services/extraction_prompt.py:31` and used in two places: (1) `_build_ontology_context_block(ontology_data, budget=ONTOLOGY_CONTEXT_BUDGET_CHARS)` truncates the ontology block when it exceeds 8000 chars; (2) the `_PROMPT_TEMPLATE` consumes the resulting block verbatim. The truncation logic in `_build_ontology_context_block` drops `required_properties` first and keeps names + descriptions, then hard-clips at the budget boundary. With ontology v0.2.0 (74 properties across 11 categories), the full block is ~12,400 chars — well over the 8000-char budget. The current behaviour produces a working prompt but drops about 40% of the property catalog, which the LLM then has to infer from names alone. The proposed remediation (per-`OntologyVersion` column with the constant as fallback) would let the v0.2.0 ontology ship with a 16,000-char budget while v0.1.0 keeps the 8,000 default. This is a feature request, not a bug.

**DEBT-2 — Read-only ontology API.** `apps/api/src/nfm_db/api/v1/ontology.py` defines 5 GET endpoints (`GET /ontology/versions`, `GET /ontology/versions/{id}`, `GET /ontology/versions/active`, `GET /ontology/entity-types`, `GET /ontology/relation-types`) and 1 POST endpoint (`POST /ontology/versions` — used internally by the seed scripts when run from the admin CLI, but not exposed to API users). There is no `PUT/PATCH/DELETE` for `ontology_versions`, no admin endpoint to mark a version active/inactive, and no endpoint to deprecate an older version. The current workaround is `psql` + `UPDATE ontology_versions SET is_active = true WHERE id = '...'`. This works for one-off bumps but blocks the planned v0.2.0 rollout automation (which requires creating a new version, running reconcile audits, and activating in a single transactional flow). S1 because the v0.2.0 rollout is committed for the next milestone.

**DEBT-3 — `_chunk_content()` output not persisted.** `apps/api/src/nfm_db/services/extraction_pipeline.py:93` defines `_chunk_content(text: str) -> list[str]` and `:535` is the primary call site (the LLM extraction pipeline). The function is pure: same input produces same chunks. But the resulting chunks are only referenced by their position in the LLM request payload — they are never written to `extraction_chunks.text`. PR #687 attempted to replace `_chunk_content` with a chunker module (per `extraction_orchestrator.py:573` comment), but the replacement lands the chunks in memory, not in the DB. The downstream consequence: a rerun via `POST /jobs/{id}/steps/{name}/rerun` (introduced by `062_create_rerun_idempotency_keys`) cannot verify byte-equality because the original chunks are not recoverable. S1 because the rerun API is the first feature that surfaces the gap; until it ships at scale, the gap is theoretical.

**DEBT-4 — `GapScanService` reads staging tables.** Three sites: `apps/api/src/nfm_db/services/gap_scan_service.py:91` (`_parse_staging_counts` parses the staging row count into a typed dataclass), `:155` (`_get_staging_counts` runs the count query), and a third site in `gap_scanner.py` (the CLI entry point that aggregates scan results). All three read from `_ref_gap_fill_staging` even after `035_ref_gap_fill_staging_v4_columns` promoted rows to the main `ref_gap_fills` table. The consequence: gap-scan SLO numbers (the next milestone's deliverable) undercount by ~30% because they reflect staging rows that have already been promoted. S0 because the SLO is committed to the next milestone and reporting undercounts is a correctness defect.

**DEBT-5 — Shared LightRAG timeout budget.** `apps/api/src/nfm_db/config.py` reads `RAG_QUERY_BUDGET` (default 45s, raised from 14s in NFM-3817-followup) and `apps/api/src/nfm_db/services/lightrag_client.py:38` uses it for both `/kg/search` queries and `/kg/ingest` calls. When the ingest path is under load (e.g. a bulk re-extraction job promoted to `ref_gap_fills`), it consumes the budget, starving user-facing queries. ADR-NFM-3404 already documents the fix (split into `RAG_QUERY_BUDGET` + `RAG_INGEST_BUDGET`) but the code change is not landed. S1 because the shared budget caused a P1 incident on 2026-08-15.

**DEBT-6 — `trigger_extraction()` monolith.** `apps/api/src/nfm_db/services/extraction_pipeline.py:723–889` (167 lines, less than the spec's stated 300). The function handles: (1) input validation, (2) job status emit, (3) ontology version resolution, (4) chunk warmup, (5) dispatch to the celery worker. Tests must mock all five concerns because they cannot exercise one without the others. The strangler-fig refactor (ADR-NFM-2737) splits this into `validate → dispatch → emit_status → warmup_chunks`. S1 because integration tests for extraction currently take >30s due to the monolithic surface; the refactor is mid-flight.

**DEBT-7 — Dual `ExtractionJob`.** `apps/api/src/nfm_db/models/extraction_job.py:31` defines the ORM class `ExtractionJob(Base)`. The dataclass variant (used in service code that does not touch the DB) is defined inline in `apps/api/src/nfm_db/services/extraction_orchestrator.py` and re-exported. Callers must pick the right type. ADR-NFM-2739 (extraction-job dual-class) documents this as intentional until the dataclass is promoted to a typed DTO. S2 because the dual-class pattern is tracked and the consolidation is planned.

**DEBT-8 — Historical alembic-head allow-list.** `apps/api/tests/test_extraction_provenance.py:205–225` (`test_alembic_has_a_single_head`). The docstring at lines 207–214 references the pre-`ffaab68f` allow-list that pinned acceptable heads to a hard-coded list, forcing manual edits on every migration. Commit `ffaab68f` replaced the allow-list with `ScriptDirectory.get_heads()` — a dynamic discovery that survives new migrations. The historical allow-list is preserved in the docstring as institutional memory (so future readers know why the dynamic test was adopted). S2 because the debt is already resolved; the only remaining action is preserving the docstring.

**Cross-cutting debt observations.**

- **Migration count.** The project has 73 migrations across 7 epochs (E0: 14, E1: 8, E2: 12, E3: 18, E4: 12, E5: 5, E6: 3 + 1 hex root). A-R1 will produce a KB-of-SQL metric and churn count.
- **Single-head invariant.** The `test_alembic_has_a_single_head` test is the NFM-167 gate. Every merge migration listed in §4 is committed to keeping the invariant.
- **Schema-drift accumulation.** The 014_sync_phase2_schema_drift migration in E1 plus the 053_align_extraction_gap_with_adr_nfm_2675 and 058_align_schema_drift_backlog migrations are the three formal drift-alignment migrations. Drift between models and schema is otherwise rare (per ADR-NFM-2139).
- **Debt severity distribution.** Of the 8 enumerated debts, 1 is S0 (blocks merge), 4 are S1 (must fix before next milestone), and 3 are S2 (housekeeping). The S0 debt (DEBT-4) is the highest-priority open item; it must be remediated before the gap-scan SLO can be measured honestly.
- **Debt remediation ownership.** DEBT-1, DEBT-2, DEBT-7 are owned by the ontology prompt pipeline team. DEBT-3, DEBT-6 are owned by the extraction pipeline team (per ADR-NFM-2737 strangler). DEBT-4 is owned by the gap-scan service team. DEBT-5 is owned by the SRE / observability team (per ADR-NFM-3404 follow-up). DEBT-8 is owned by the test-infra team (already resolved; no active remediation needed).

---

## §6. 当前态 (placeholder — to be filled by A-R2)

```
> ⚠️ This section is intentionally empty. Content will be supplied by A-R2 —
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

---

## §7. Reading paths

- **Chronological.** Read §3 in order (E0 → E6). At each epoch boundary, glance at §4 for the merges that closed the prior chain. Use §6 to track what A-R2 will fill in. For deep context per migration, read the docstring inside each migration file in `apps/api/migrations/versions/` — the docstrings are written first (TDD docstring-first convention) and capture the why-this-migration-exists rationale.
- **By domain.** Start at §2 cross-reference, pick the domain tag(s) of interest, follow the "Touched by" column into the §3 epoch subsections for those migrations, then read §5 debt entries tagged to the same domain. The domain tags are the filter; the "Touched by" column is the index.
- **By question.**
  - "Why does `alembic upgrade heads` have more than one head?" → §5 DEBT-4 (staging/main drift) and §5 DEBT-8 (single-head test).
  - "Where do chunks fit in the pipeline?" → E4 (`042_extraction_step_and_chunk`, `050_extraction_chunk_v2_provenance`) and §5 DEBT-3 (persistence gap).
  - "Why are there two `ExtractionJob` classes?" → ADR-NFM-2739 + §5 DEBT-7.
  - "Where is the ontology FK wired?" → E5 (`055_add_ontology_version_fk_to_type_tables`).
  - "What is `_chunk_content()` and why is it still in `extraction_pipeline.py`?" → §5 DEBT-3 + ADR-NFM-2737 (strangler-fig extraction dispatch).
  - "Why are there hex-named migrations in epochs that don't match their numeric prefix?" → §3 E2 "Hex migration chain placement" note.
  - "Which migration introduced the `blog_role` enum?" → §3 E0 `001_create_users_table` (initial), extended in §3 E4 `043_add_domain_expert_role` (`domain_expert` value).
  - "How does a `kg_node` reach the AGE graph?" → §3 E1 `015_kg_models_complete` (`age_synced_at` column) + the Neo4j→Apache AGE doc (linked in §8).
  - "What columns does `extraction_jobs` accumulate across epochs?" → §3 E1 `013_multimodal`, E3 `034`, `035`, E4 `049`, `051`, E5 `056` — see the "Touched by" column in §2 row 15 for the canonical list.
  - "When was the `track_id` column added?" → §3 E5 `056` (on `extraction_jobs`) + §3 E6 `061` (propagated to `extraction_steps`).
  - "Where do I find the v0.1.0 ontology seed?" → §3 E4 `044_add_ontology_version` docstring + `apps/api/src/nfm_db/seeds/ontology_v0.1.0.json` (referenced from the migration).
  - "Which migration creates `sync_operations`?" → §3 E3 `040_create_sync_operations`.
  - "Which migration introduces the rerun idempotency API?" → §3 E6 `062_create_rerun_idempotency_keys` (the `rerun_idempotency_keys` table) plus ADR-NFM-2737 (strangler-fig dispatch) which motivates the endpoint.

---

## §8. References

**Architectural decision records** (in `docs/architecture/`):
- [`ADR-KR3-deploy-events.md`](ADR-KR3-deploy-events.md)
- [`ADR-KR3-prod-emission.md`](ADR-KR3-prod-emission.md)
- [`ADR-NFM-2081-commit-issue-reference-enforcement.md`](ADR-NFM-2081-commit-issue-reference-enforcement.md)
- [`ADR-NFM-2139-deploy-rollback-architecture.md`](ADR-NFM-2139-deploy-rollback-architecture.md)
- [`ADR-NFM-2737-strangler-fig-extraction-dispatch.md`](ADR-NFM-2737-strangler-fig-extraction-dispatch.md)
- [`ADR-NFM-2739-extraction-job-dual-class.md`](ADR-NFM-2739-extraction-job-dual-class.md)
- [`ADR-NFM-3404-rag-query-timeout-alignment.md`](ADR-NFM-3404-rag-query-timeout-alignment.md)
- [`ADR-NFM-796-review-provenance.md`](ADR-NFM-796-review-provenance.md)

**Adjacent architecture documents**:
- [`graph-database-migration-from-neo4j-to-apache-age.md`](graph-database-migration-from-neo4j-to-apache-age.md) — Neo4j→Apache AGE migration narrative (separate doc, not duplicated here).

**Source directories** (auto-enumerated):
- `apps/api/migrations/versions/` — 73 Alembic migration files
- `apps/api/src/nfm_db/models/` — 42 ORM model files (including `__init__.py`)

**Spec artifacts**:
- [`_specs/db-evolution-spec.md`](_specs/db-evolution-spec.md) — CPO-authored implementation spec (NFM-3827, Option D)

**Research tickets** (deferred):
- A-R1 — 73-migration classification stats (severity tiers, churn, KB-of-SQL). §3 marks slots `A-R1: TBD` where deeper classification is needed.
- A-R2 — `models/ 与 schema 终态偏差` (models vs schema terminal-state drift). §6 is the placeholder; A-R2 will populate the drift table.

**Migration file index** (auto-enumerated from `apps/api/migrations/versions/`, 73 files):

```
001_create_users_table.py                                              — E0
002_create_blog_posts_table.py                                         — E0
003_create_md_verification_tables.py                                   — E0
003_create_potentials_table.py                                         — E0
004_seed_potentials.py                                                 — E0
005_add_verification_results_md_and_extend_jobs.py                     — E0
005_add_verification_status.py                                         — E0
005c_merge_verification_branches.py                                    — E0 (merge #1)
006_add_cancelled_status_to_md_jobs.py                                 — E0
007_add_staging_quality_gate_columns.py                                — E0
008_add_blog_posts_title.py                                            — E0
d3ddb691ae20_create_feedbacks_table.py                                — E0 (hex root)
b5f3a2c1d8e0_add_ref_gap_fill_staging.py                              — E0
9c15710c6321_merge_blog_lineage_002_and_feedback_.py                   — E0 (merge #10)
009_create_phase1_core_tables.py                                       — E1
010_seed_phase1_reference_data.py                                      — E1
011_create_kg_tables.py                                                — E1
012_create_kg_nodes_edges.py                                           — E1
013_add_entity_merge_log.py                                            — E1
013_add_multimodal_job_fields.py                                       — E1
014_conflict_records.py                                                — E1 (side branch)
014_sync_phase2_schema_drift.py                                        — E1
013_extraction_figures.py                                              — E2 (chain parent: 020)
015_add_user_profile_fields.py                                         — E2
015_kg_models_complete.py                                              — E2 (chain parent: 017)
020_merge_kg_forks.py                                                  — E2 (merge #3)
021_add_datasource_storage_columns.py                                  — E2
022_phase3_review_traceability.py                                      — E2
023_add_dft_calculations.py                                            — E2
024_create_verification_tasks_table.py                                 — E2
025_merge_verification_and_source_branches.py                          — E2 (merge #4)
f8e2db803b55_merge_dft_and_datasource_branches.py                      — E2 (merge #11, hex)
054b39a26310_add_source_to_dft_calculations.py                         — E2 (hex, chain parent: f8e2)
026_add_extraction_figures_columns.py                                  — E2
027_merge_heads_011_and_026.py                                         — E2 (merge #5)
028_backfill_review_status_confidence.py                               — E3
029_add_user_service_account_flag.py                                   — E3
030_create_corpus_table.py                                             — E3
031_seed_property_types.py                                             — E3
032_add_dedup_unique_indexes.py                                        — E3
032_create_data_submission_tables.py                                   — E3
033_add_conditions_hash_and_method_to_measurements.py                  — E3
034_add_extraction_job_persistence_columns.py                           — E3
035_add_extraction_job_multimodal_flags.py                             — E3
035_ref_gap_fill_staging_v4_columns.py                                 — E3
036_merge_chain_A_and_B.py                                             — E3 (merge #6)
036_ref_gap_fill_staging_v4_columns_simple.py                          — E3
037_create_health_events_table.py                                      — E3
037_merge_ref_gap_fill_chain.py                                        — E3 (merge #7)
038_merge_health_events_and_ref_gap.py                                 — E3 (merge #8)
039_add_extraction_method_provenance.py                                — E3
040_create_sync_operations.py                                          — E3
041_merge_010_and_039.py                                                — E3 (merge #9)
042_extraction_step_and_chunk.py                                       — E4
043_add_domain_expert_role.py                                          — E4
044_add_ontology_version.py                                            — E4
045_add_re_extraction_queue.py                                         — E4
046_add_knowledge_gaps.py                                              — E4
047_extraction_gap.py                                                  — E4
048_data_collection_request.py                                         — E4
049_add_ontology_version_to_extraction_job.py                           — E4
050_extraction_chunk_v2_provenance.py                                  — E4
051_extraction_job_orchestration_columns.py                            — E4
052_add_datasource_metadata.py                                         — E4
053_align_extraction_gap_with_adr_nfm_2675.py                          — E4
055_add_ontology_version_fk_to_type_tables.py                          — E5
056_add_track_id_to_extraction_job.py                                  — E5
057_create_kg_entity_and_relation_type_tables.py                       — E5
058_align_schema_drift_backlog.py                                      — E5
059_add_adr009_reconcile_audit_log.py                                  — E5
060_backfill_ref_gap_fill_staging_source.py                            — E6
061_add_track_id_to_extraction_step.py                                 — E6
062_create_rerun_idempotency_keys.py                                   — E6
```

**Model file index** (auto-enumerated from `apps/api/src/nfm_db/models/`, 42 files):

```
__init__.py                                                            — shared Base, TimestampMixin (auth)
adr009_reconcile_audit.py                                              — ontology-feedback
blog_post.py                                                           — blog-content
classification_level.py                                                — materials-core
conflict.py                                                            — sync-ops (enum re-exports)
conflict_record.py                                                     — sync-ops
corpus.py                                                              — materials-core
data_collection_request.py                                             — ontology-feedback
data_dna.py                                                            — sync-ops
dft_calculation.py                                                     — data-source
entity_merge.py                                                        — knowledge-graph
extraction_chunk.py                                                    — extraction-pipeline
extraction_figure.py                                                   — extraction-pipeline
extraction_gap.py                                                      — extraction-pipeline
extraction_job.py                                                      — extraction-pipeline
extraction_result.py                                                   — extraction-pipeline
extraction_step.py                                                     — extraction-pipeline
feedback.py                                                            — blog-content
health_event.py                                                        — sync-ops
hpc_failover_event.py                                                  — sync-ops
hub_node.py                                                            — knowledge-graph
ingest_log.py                                                          — sync-ops
kg.py                                                                  — knowledge-graph
kg_node.py                                                             — knowledge-graph
knowledge_gap.py                                                       — ontology-feedback
material.py                                                            — materials-core
md_verification.py                                                     — md-verification
ontology.py                                                            — knowledge-graph
ontology_version.py                                                    — knowledge-graph
potential.py                                                           — md-verification
property.py                                                            — materials-core
re_extraction_queue.py                                                 — extraction-pipeline
ref_gap_fill.py                                                        — extraction-pipeline
rerun_idempotency_key.py                                               — extraction-pipeline
resource_node.py                                                       — knowledge-graph
review.py                                                              — review-traceability
source.py                                                              — data-source
sync_operation.py                                                      — sync-ops
unit.py                                                                — materials-core
upload_session.py                                                      — data-source
user.py                                                                — auth
verification_task.py                                                   — md-verification
```

**Appendix: Single-head invariant mechanics.** Alembic stores each migration's `down_revision` in the migration file and the `alembic_version` row in the target database. A *head* is a migration whose `down_revision` is not referenced by any other migration's `revision` field. The single-head invariant (NFM-167) requires `len(script.get_heads()) == 1`. New migrations must descend from the current head or be merged in via a `_merge_*` migration. The 11 merges listed in §4 are the cumulative history of bringing the chain back to a single head after parallel development.