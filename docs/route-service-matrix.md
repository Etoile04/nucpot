# Route-to-Service Call Matrix Report (NFM-3868)

**Routes:** 42  |  **Services:** 120  |  **Active (imported):** 38  |  **Dead (0 imports):** 82  |  **Active edges:** 51  |  **Matrix density:** 1.0%

### Import-Count Distribution

| Tier | Count |
|------|-------|
| 1 route (single-use) | 28 |
| 2–4 routes | 10 |
| 5–9 routes | 0 |
| 10+ routes | 0 |

---

## Dead Service Candidates (0 route imports)

**82 services** are never directly imported by any route module.  
These may be: (a) internal pipeline/worker modules called via Celery tasks, 
(b) sub-modules imported only by other service modules, or 
(c) truly dead code.

- `adr009_audit`
- `adr009_flag`
- `adr009_paperclip_wake`
- `adr009_reconcile_routine`
- `backup/config`
- `backup/gfs_tier`
- `backup/guardrails`
- `backup/metrics`
- `backup/models`
- `backup/retention`
- `backup/sre_alert`
- `backup_service`
- `bibliographic_metadata`
- `chunk_upload_service`
- `chunker`
- `classification_guard`
- `conflict_resolution`
- `conflict_resolver`
- `dna_service`
- `dna_write_integration`
- `domain_expert/f_grade_adjudication`
- `domain_expert/quarterly_audit`
- `domain_expert/reference_validation`
- `entity_linker`
- `external_data_sources`
- `extraction/steps/chunk_builder`
- `extraction/steps/entity_extractor`
- `extraction/steps/property_normalizer`
- `extraction/steps/raw_text_loader`
- `extraction/steps/section_segmenter`
- `extraction/types`
- `extraction_gap_scanner`
- `extraction_normalizer`
- `extraction_orchestrator`
- `extraction_orchestrator_v2`
- `extraction_prompt`
- `figure_detector`
- `fusion_pipeline`
- `gap_reopen_service`
- `health_event_emitter`
- `heuristic_extractor`
- `hpc_failover`
- `hpc_file_transfer`
- `hpc_job_monitor`
- `hpc_metrics`
- `hpc_orchestration`
- `hpc_slurm`
- `hpc_ssh`
- `hpc_sync`
- `kg_lightrag_sync`
- `kg_query_service`
- `kg_re`
- `kg_to_staging_bridge`
- `lightrag_lifecycle`
- `lightrag_prompts`
- `literature_service`
- `llm_client`
- `md_tasks`
- `mineru_client`
- `mineru_vision_extractor`
- `multi_source_fusion`
- `multimodal_extraction`
- `ocr_fallback`
- `ontology_import`
- `ontology_loader`
- `ontology_register`
- `openkim_mapper`
- `page_splitter`
- `plot_extractor`
- `priority`
- `providers/base`
- `providers/composite`
- `providers/local`
- `providers/openkim`
- `quality_service`
- `review_queue_service`
- `seed_ontofuel`
- `seed_service`
- `table_extractor`
- `v4_mapper`
- `verification_rating`
- `vision_client`

---

## Shared Infrastructure (tiered)

### ≥3 route imports (3 services)
- `auth_service` — **3** routes  ███
- `rate_limit` — **3** routes  ███
- `gap_scanner` — **3** routes  ███

### ≥5 route imports (0 services)
*(none at this tier)*

### ≥10 route imports (0 services)
*(none at this tier)*

---

## Route Dependency Counts (descending)

- `v1/literature` — 5 services
- `v1/ontology` — 4 services
- `v1/extraction` — 4 services
- `v1/materials` — 3 services
- `v1/reference_values` — 3 services
- `v1/md_verification` — 3 services
- `v1/kg` — 3 services
- `v1/reference_gaps` — 2 services
- `v1/data_collection` — 2 services
- `v1/potentials` — 2 services
- `v1/kg_graph` — 1 services
- `v1/lightrag` — 1 services
- `v1/profile` — 1 services
- `v1/properties` — 1 services
- `v1/re_extraction` — 1 services
- `v1/review` — 1 services
- `v1/sources` — 1 services
- `v1/viz` — 1 services
- `admin/health` — 1 services
- `v4/extraction` — 1 services
- `v1/jobs` — 1 services
- `v1/auth_endpoints` — 1 services
- `v1/health` — 1 services
- `v1/feedback` — 1 services
- `v1/extraction_gaps` — 1 services
- `v1/dedup` — 1 services
- `v1/admin_health` — 1 services
- `v1/auth` — 1 services
- `v1/blog` — 1 services
- `v1/batch` — 1 services
- `routes/ontology` — 0 services
- `v1/verification` — 0 services
- `v1/upload` — 0 services
- `v1/seed` — 0 services
- `v1/composition` — 0 services
- `v1/hub_nodes` — 0 services
- `v1/conflict` — 0 services
- `v1/prediction` — 0 services
- `v1/ontology_version` — 0 services
- `v1/design` — 0 services
- `v1/dft` — 0 services
- `admin/backups` — 0 services
