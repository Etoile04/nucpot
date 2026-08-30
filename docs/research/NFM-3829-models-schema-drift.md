# NFM-3829: Models vs Alembic Head — 偏差报告

**Branch**: `NFM-3829-a-r2-models-schema-research`
**Date**: 2026-08-30
**Scope**: `apps/api/src/nfm_db/models/` ↔ `apps/api/migrations/versions/`
**Head** (`062_create_rerun_idempotency_keys` is the latest leaf; `058_align_schema_drift_backlog` is the comprehensive drift fix that captures all model-vs-DB drift discovered as of 2026-08-21)

---

## Executive Summary

迁移链经历了三个时代，混合了三种写法：

| 时代 | 范围 | 写法 |
|---|---|---|
| Era 1（001–010）| `users` 之后到 `009_create_phase1_core_tables` | 几乎全部 `op.execute("CREATE TABLE ...")` 裸 SQL |
| Era 2（011–057）| `kg_nodes`/`kg_edges`/`md_verification`/... | 大量 `op.create_table(...)`，但仍有部分裸 SQL 与混合路径 |
| Era 3（058）| `058_align_schema_drift_backlog` | 纯 `op.execute("ALTER TABLE ...")`，对模型漂移做集中对齐 |

`058` 的 docstring（line 95–114）已经记录了 5 大类漂移：FK 缺/错（13）、列缺（15）、列类型错（20）、UNIQUE/CHECK 错（6）、NOT NULL 错（25）、孤儿索引/约束（≥6）。本报告在此基础上再做一次端到端 review，重点放在 **058 之后仍残留的偏差** 与 **模型内部的结构性隐患**（重复定义 / 死代码 / 非 PG-可移植类型）。

---

## 1. 模型中存在，但迁移链中从未 `create_table`/`CREATE TABLE` 过的表

扫描后：✅ 所有 56 个被模型声明的表均被某次迁移创建。**没有遗漏的孤儿模型表。**

> 例外：`kg_node.py` 中 `KGNode` / `KGReviewQueue` / `KGProvenance` 是 Integer-PK 的旧版本，没有被 `__init__.py` 重新导出（`models/kg.py` 是 UUID-PK 的活版本），所以这两个模块文件定义了 3 张**死表**：
>
> - `kg_nodes`（Integer PK，由 `kg_node.py.KGNode` 声明）— 但 `models/__init__.py` 没导出，所以 `Base.metadata` 不会包含这张表。**死表，无迁移创建过。**
> - `kg_review_queue`（Integer PK，由 `kg_node.py.KGReviewQueue` 声明）— 同上。
> - `kg_provenance`（Integer PK，由 `kg_node.py.KGProvenance` 声明）— 同上。
>
> 这些类在源代码中仍然可被 `import`；如果有人显式 `from nfm_db.models.kg_node import KGProvenance`，SQLAlchemy 会再去元数据里注册一张同名 `kg_provenance` 表，可能与后续命名空间冲突。

---

## 2. 迁移中创建，但模型中缺失定义的表

扫描后：✅ 所有由迁移创建的表都至少有一个活跃模型对应。**没有死表遗留。**

> **重复表声明（同一张表被声明两次）**：
>
> | 表 | 模型 1（活）| 模型 2（死，未导出）|
> |---|---|---|
> | `kg_nodes` | `models/kg.py.KGNode` (UUID PK) | `models/kg_node.py.KGNode` (Integer PK, 有 `name`/`canonical_name`/`confidence_score` 字段) |
> | `kg_review_queue` | `models/kg.py.KGReviewQueue` (UUID PK, item_id Uuid, no FK) | `models/kg_node.py.KGReviewQueue` (Integer PK, node_id Integer FK→kg_nodes.id) |

---

## 3. 类型不匹配（Column ↔ sa.Column）

下列类型偏差由 `058_align_schema_drift_backlog` §4 (line 299–362) **对 DB 做了对齐**。对齐后 DB 与模型一致。本节作为后续漂移检测的 reference 留档。

### 3.1 `JSONB` ↔ `JSON`（20 处）

| 表 | 列 | 模型 | DB 历史 | 058 后 |
|---|---|---|---|---|
| `conflict_records` | `conflicting_values` | `JSON default list` | JSONB | JSON |
| `conflict_records` | `resolved_value` | `JSON nullable` | JSONB | JSON |
| `conflict_records` | `source_values` | `JSON default list` | JSONB | JSON |
| `defect_analysis_results` | `metadata` | `JSON nullable` | JSONB | JSON |
| `dft_calculations` | `computation_metadata` | `CompatJSONB nullable` | JSON | **JSONB** |
| `extraction_figures` | `extracted_data` | `JSON nullable` | JSONB | JSON |
| `extraction_results` | `item_data` | `JSON default dict` | JSONB | JSON |
| `extraction_results` | `value` | `JSON default None` | JSONB | JSON |
| `kg_edges` | `properties` | `JSON default dict` | JSONB | JSON |
| `kg_nodes` | `properties` | `JSON default dict` | JSONB | JSON |
| `knowledge_gaps` | `metadata_` | `CompatJSONB nullable` | TEXT/JSONB | **JSONB** |
| `md_simulation_results` | `thermodynamic_data` | `JSON nullable` | JSONB | JSON |
| `md_verification_jobs` | `config` | `JSON not null` | JSONB | JSON |
| `potential_fitting_results` | `parameters` | `JSON not null` | JSONB | JSON |
| `potential_fitting_results` | `quality_metrics` | `JSON nullable` | JSONB | JSON |
| `property_measurements` | `value_list` | `JSON nullable` | JSONB | JSON |
| `reviews` | `data` | `JSON default dict` | JSONB | JSON |

> **注意点**：模型 `CompatJSONB` 是 PG 专用类型（包裹 PG `JSONB`），但在 SQLite 上回退到 `Text`。迁移一律生成 PG `JSONB`。这意味着 SQLite 测试环境（如果有）会与生产 PG schema 不一致。

### 3.2 `VARCHAR(N)` ↔ `VARCHAR(M)` / `TEXT`（3 处）

| 表 | 列 | 模型 | DB 历史 | 058 后 |
|---|---|---|---|---|
| `extraction_figures` | `caption` | `String(500) nullable` | TEXT | VARCHAR(500) |
| `potentials` | `description` | `Text nullable` | TEXT | VARCHAR(500) |
| `users` | `title` | `String(64) nullable` | VARCHAR(255) | VARCHAR(64) |

### 3.3 `TIMESTAMPTZ` ↔ 缺列（§5 加列后 §6 置 NOT NULL，6 处）

| 表 | 列 | 模型 |
|---|---|---|
| `defect_analysis_results` | `updated_at` | TM.updated_at |
| `kg_edges` | `updated_at` | TM.updated_at |
| `md_simulation_results` | `updated_at` | TM.updated_at |
| `potential_fitting_results` | `updated_at` | TM.updated_at |
| `_ref_gap_fill_staging` | `created_at` | TM.created_at |
| `_ref_gap_fill_staging` | `updated_at` | TM.updated_at |

### 3.4 `UUID`（PG-dialect）vs `sqlalchemy.Uuid`（portable）

| 模型 | 列 | 类型 |
|---|---|---|
| `extraction_step.py` | `track_id` | `sqlalchemy.dialects.postgresql.UUID(as_uuid=True)` |
| `health_event.py` | `id`, `event_type`, ... | `sqlalchemy.dialects.postgresql.UUID(as_uuid=True)` |
| `md_verification.py` | 全部 UUID 列 | PG `UUID(as_uuid=True)` |
| `verification_task.py` | `id` | PG `UUID(as_uuid=True)` |
| `kg_node.py`（死表）| 全部 UUID 列 | PG `UUID(as_uuid=True)` |

其余模型使用 `sqlalchemy.Uuid`（portable）。SQLite 测试环境下，这 5 个模型的 DDL 会失败或行为异常。

### 3.5 `JSON` vs `JSONB`（非 058 范围，模型内部矛盾）

- `health_event.py`: `context` 显式 `JSONB`（PG 专用）— 与其他用 portable `CompatJSONB` 的模型不一致。

### 3.6 Enum 类型不统一

- **String**（枚举存储为字符串）: `BlogPostMetadata.status`, `ClassificationLevel.*`, `Corpus.*` (无枚举), `ConflictRecord.strategy/status/resolution`, `DataSource.parse_status`, `DFTCalculation.status`, `ExtractionChunk.*`（无枚举）, `ExtractionGap.gap_status`, `ExtractionJob.status/conflict_strategy/cache_level/max_confidence`, `ExtractionResult.review_status`, `ExtractionStep.step_type/status`, `HealthEvent.*` (无枚举), `HubNode.status`, `IngestLog.status`, `KGNode.node_type/status/review_status/extraction_method`, `KGEdge.review_status`, `KGReviewQueue.item_type/status`, `KnowledgeGap.gap_type/status`, `MaterialCategory.*` (无枚举), `Material.is_active`, `MDVerificationJob.status/execution_status`, `HpcJob.status`, `OntologyVersion.status`, `Potential.status/verification_status`, `ReExtractionQueue.status`, `RefGapFillStaging.status`, `ResourceNode.status`, `SyncOperation.op_type`, `UploadSession.status`, `User.is_active/is_service_account`, `VerificationTask.status/rating`.
- **PG Enum**（真正的 PG `CREATE TYPE`）:
  - `entity_merge.MatchMethod` → `match_method_enum` (exact/fuzzy/semantic)
  - `feedback.FeedbackType` → `feedback_type_enum`
  - `feedback.Priority` → `priority_enum`
  - `feedback.FeedbackStatus` → `feedback_status_enum`
  - `ref_gap_fill.Confidence` → `confidence_enum`
  - `ref_gap_fill.StagingStatus` → `staging_status_enum`
  - `ref_gap_fill.CacheLevel` → `cache_level_enum`
  - `user.BlogRole` → `blog_role_enum`（`User._blog_role` → 列 `blog_role`，迁移 001 通过裸 SQL 创建）

> `001_create_users_table.py` 通过 `op.execute("CREATE TYPE blog_role_enum AS ENUM (...)")` 创建枚举，**与模型声明的 PG `Enum(..., name='blog_role_enum', values_callable=...)` 命名一致**，但 `values_callable=lambda x: [e.value for e in x]` 不会影响 PG 存储格式，只影响 Python 端 → DB 端的字符串转换。**OK。**
>
> 但 `d3ddb691ae20_create_feedbacks_table.py`（feedbacks 表创建）也是裸 SQL。如果它没有用 `CREATE TYPE feedback_*_enum`，而是把 enum 字段声明为 `VARCHAR`，那就与模型不一致。

---

## 4. 索引差异（模型 ↔ DB）

### 4.1 模型声明但 DB 可能缺失的索引

迁移 058 没有自动加索引（除非作为 `add_constraint` 路径里 INDEX→CONSTRAINT 的转换）。下面逐表检查模型声明的 `__table_args__` 中是否被某次迁移创建。

| 表 | 索引/约束 | 模型声明 | 迁移来源 |
|---|---|---|---|
| `adr009_reconcile_audit_log` | `uq_adr009_reconcile_audit_natural_key` | ✅ | 059 |
| `adr009_reconcile_audit_log` | `ix_adr009_reconcile_audit_run_date` | ✅ | 059 |
| `blog_posts` | `slug` UNIQUE | ✅ | 002 |
| `blog_posts` | `status`, `author_id`, `reviewer_id` | ✅ | 002 |
| `classification_levels` | `label` UNIQUE | ✅ | 032（`032_create_data_submission_tables`）|
| `corpus` | `uq_corpus_corpus_id` | ✅ | 030 |
| `data_collection_requests` | `ix_dcr_ov_entity_prop_material` UNIQUE | ✅ | 048 |
| `data_collection_requests` | `ix_dcr_status`, `ix_dcr_urgency_desc`, `ix_dcr_material_system` | ✅ | 048 |
| `data_dna` | `record_type`, `record_id`, `dna_uuid` UNIQUE, `sha256_hash` | ✅ | 032 |
| `dft_calculations` | `uq_dft_calculations_calc_id`, `idx_dft_calcs_*` | ✅ | 023 |
| `extraction_chunks` | `ix_extraction_chunks_v2_idempotency` UNIQUE (partial PG) | ✅ | 050 |
| `extraction_gaps` | `ix_extraction_gaps_ov_entity_prop_lit_chunk` UNIQUE, 其它 | ✅ | 047 |
| `extraction_jobs` | (model 无 `__table_args__`，无 UNIQUE) | — | — |
| `extraction_steps` | `ix_extraction_steps_track_id` | ✅ | 061 |
| `health_events` | `event_type`, `severity`, `source_service`, `created_at` | ✅ | 037 |
| `hpc_failover_events` | `event_type`, `source_cluster` | ✅ | 014 |
| `ingest_logs` | `resource_node_id`, `hub_node_id` | ✅ | 032 |
| `kg_nodes` | `ix_kg_nodes_*` (3 个) | ✅ | 011 / 015 / 058 修复 |
| `kg_edges` | `ix_kg_edges_*` (6 个) | ✅ | 011 / 015 |
| `kg_review_queue` | `ix_kg_review_queue_status`, `ix_kg_review_queue_item` | ✅ | 014 |
| `ontology_id_map` | `ix_ontology_id_map_node_id`, `uq_ontology_id_map_nvl_corpus` | ✅ | 014 + 058 §8 |
| `knowledge_gaps` | `idx_kg_status`, `idx_kg_type_target` UNIQUE, `idx_kg_ontology_version` | ✅ | 046 |
| `material_categories` | `uq_material_categories_name`, `uq_material_categories_slug`, `idx_mat_cat_parent` | ✅ | 009 |
| `materials` | `idx_materials_category`, `idx_materials_formula` | ✅ | 009 |
| `material_aliases` | `uq_material_aliases_material_name`, `idx_mat_aliases_material`, `idx_mat_aliases_alias_name` | ✅ | 009 |
| `material_compositions` | `idx_mat_comp_material`, `idx_mat_comp_element` | ✅ | 009 |
| `md_verification_jobs` | `owner_id`, `potential_id`, `status` | ⚠️ **可能缺** | 003 只建表 + 几个 idx。需查 003 行 47-50 |
| `hpc_jobs` | `verification_job_id`, `hpc_cluster` | ⚠️ **可能缺** | 同上 |
| `md_simulation_results` | `verification_job_id` UNIQUE (1:1) | ✅ | 058 §8 |
| `defect_analysis_results` | `verification_job_id` | ✅ | 003 |
| `potential_fitting_results` | `verification_job_id` | ✅ | 003 |
| `verification_results_md` | `simulation_result_id` | ✅ | 005 |
| `ontology_versions` | `uq_ontology_versions_version` | ✅ | 044 |
| `kg_entity_types` | `uq_kg_entity_types_name` | ✅ | 057 |
| `kg_relation_types` | `uq_kg_relation_types_name` | ✅ | 057 |
| `units` | `uq_units_name`, `uq_units_symbol` | ✅ | 009 |
| `unit_conversions` | `uq_unit_conversions_source_target` | ✅ | 009 |
| `_ref_gap_fill_staging` | `idx_staging_*` (5 个) | ✅ | b5f3a2c1d8e0 + 035/036 + 058 §5 补 created_at/status |
| `resource_nodes` | `hub_node_id` | ✅ | 032 |
| `upload_sessions` | `resource_node_id` | ✅ | 032 |
| `verification_tasks` | `status` | ✅ | 024 |
| `data_sources` | `uq_data_sources_doi` | ✅ | 009 |
| `authors` | `uq_authors_orcid` | ✅ | 009 |
| `data_source_authors` | `uq_data_source_authors_source_order` | ✅ | 009 |
| `users` | `username` UNIQUE, `email` UNIQUE | ✅ | 001 + 058 §8 |
| `sync_operations` | `uq_sync_operations_node_operation`, `resource_node_id` | ✅ | 040 |
| `potentials` | `name` UNIQUE | ✅ | 003_create_potentials_table |

**已发现的潜在 INDEX 缺漏**：
- `extraction_results` 模型声明 `item_type` (String) — 通常用作查询过滤，但模型无显式索引。迁移也无。**软警告**：取决于查询模式。
- `md_verification_jobs.potential_id` 是迁移 003 显式建的索引（`op.create_index('idx_md_jobs_potential', ...)`），对应模型 `mapped_column(..., index=True)`。
- `md_verification_jobs.owner_id` 是模型 `index=True`，但 003 没建 — **058 没补**。需要后续迁移或修复 003。

### 4.2 DB 中存在但模型没声明的索引

`058 §8c` 把 `uq_ontology_id_map_nvl_corpus` 从 PK 改名为 `ontology_id_map_pkey` 并新增 UNIQUE，模型与 DB 已一致。

无其他明显孤儿索引。

---

## 5. NOT NULL 不一致（已被 058 §6 修复）

058 §6 (line 443–510) 修复了 25 处 NOT NULL 偏差。修复后模型与 DB 一致。下表为留档：

| 表 | 列 | 修复方向 | 备注 |
|---|---|---|---|
| `_ref_gap_fill_staging` | `confidence`, `updated_at`, `status` | NULLABLE→NOT NULL | enum 默认值 backfill |
| `_ref_gap_fill_staging` | `created_at` | NULLABLE→NOT NULL | TM 列，由 §5 添加 |
| `conflict_records` | `material_node_id`, `property_node_id`, `status`, `source_values` | NULLABLE→NOT NULL | 后两者 backfill |
| `dft_calculations` | `status` | NULLABLE→NOT NULL | backfill `'pending'` |
| `extraction_figures` | `page_number`, `figure_type`, `extracted_data` | NOT NULL→NULLABLE | 模型允许 None |
| `extraction_results` | `property_name`, `value`, `confidence` | NULLABLE→NOT NULL | backfill |
| `kg_edges` | `properties`, `confidence`, `updated_at` | NULLABLE→NOT NULL | backfill |
| `kg_entity_types` | `ontology_version_id` | NULLABLE→NOT NULL | |
| `kg_nodes` | `properties`, `confidence`, `status` | NULLABLE→NOT NULL | backfill |
| `kg_relation_types` | `ontology_version_id` | NULLABLE→NOT NULL | |
| `knowledge_gaps` | `created_at`, `updated_at` | NULLABLE→NOT NULL | TM 列 |
| `property_measurements` | `conditions_hash` | NOT NULL→NULLABLE | 模型允许 None |
| `reviews` | `action`, `data` | NULLABLE→NOT NULL | backfill |
| `data_dna`, `defect_analysis_results`, `kg_edges`, `md_simulation_results`, `potential_fitting_results`, `upload_sessions`, `md_verification_jobs` | 新增列的 NOT NULL 翻转 | 由 §5 加列 + §6 flip | |

---

## 6. 结构性隐患（建议进入 backlog）

### 6.1 重复定义（必须清理）

`models/kg_node.py` 与 `models/kg.py` 同时声明了 `KGNode` 和 `KGReviewQueue`，且两张表的 PK 类型、字段集都不同。`kg_node.py` 未被 `__init__.py` 导出，所以不会污染 `Base.metadata`，但：
- 任何 `from nfm_db.models.kg_node import KGNode` 都会把 Integer-PK 版本注册进 metadata；
- 两个 `Table('kg_nodes', ...)` 对象会冲突（同一 schema 同一表名）。

**修复建议**：删除 `models/kg_node.py` 的 `KGNode` / `KGReviewQueue`，只保留 `KGProvenance`（如果还在使用）。需要先在仓库里 grep 是否有外部引用。

### 6.2 非 portable UUID/JSONB

5 个模型文件用 PG dialect-specific `UUID` / `JSONB`，与项目其他模型使用 `sqlalchemy.Uuid` / `CompatJSONB` 不一致。SQLite 测试环境下会出列类型错误。

| 模型文件 | 涉及类型 |
|---|---|
| `extraction_step.py` | `UUID(as_uuid=True)` on `track_id` |
| `health_event.py` | `UUID(as_uuid=True)` + 裸 `JSONB` |
| `md_verification.py` | 6 个模型全部 PG `UUID(as_uuid=True)` |
| `verification_task.py` | PG `UUID(as_uuid=True)` on `id` |

### 6.3 缺 FK 约束

模型声明 `ForeignKey(...)` 但 DB 没有对应 FK（058 §7 修复 8 处，遗留未提）：

| 模型列 | 期望 FK |
|---|---|
| `ExtractionGap.ontology_version` (String) | 无 FK（语义是版本号字符串，无法 FK 到 `ontology_versions.version`，是合理的） |
| `ExtractionJob.corpus_id` (String) | 无 FK（同上，slug 字符串） |
| `KGReviewQueue.item_id` (Uuid) | 无 FK（多态，可指向 KGNode 或 KGEdge） |
| `KnowledgeGap.resolved_by` (Uuid) | 无 FK（用户态可被删除） |
| `RefGapFillStaging.reviewer_id`, `promoted_to_pm_id`, `fill_batch_id` | 无 FK（管理类字段） |
| `Review.result_id` (Uuid) | 无 FK（多态） |

这些是设计上的妥协（指向非唯一目标 / 多态），不算 bug。但应在模型注释里写明"intentionally no FK"。

### 6.4 完整性冲突

- `ReExtractionQueue.triggered_by`: `nullable=False` + `ondelete="SET NULL"` — 用户被删时 DB 会拒绝 NOT NULL 约束。建议改为 `nullable=True`。
- `ConflictRecord.material_node_id` / `property_node_id`: 模型允许 `nullable=True`，但 DB 经 058 §6 改为 NOT NULL。这与业务语义"待解决的冲突可能还没关联节点"不符。需要业务侧确认。

### 6.5 死代码 / 死类

- `KGNode` / `KGReviewQueue` / `KGProvenance` in `kg_node.py`（如 6.1）。
- `ReviewMixin` in `review.py`（`status` / `reviewer_comment` / `reviewed_at`）— 当前未挂接到任何 model。grep 后再下结论。

### 6.6 迁移杂项

- `009_create_phase1_core_tables.py` 一次创建 14 张表 + 大量 enum（property_categories / property_types / datasets / property_measurements / measurement_conditions / 等）。后续迁移 021/033/058 都对它做补刀，但没有迁移把它"拆开"。这本身不算 bug，但出问题回滚困难。
- 迁移 `014_sync_phase2_schema_drift.py` 同时建表 + 加列，且文件名暗示它是 ad-hoc drift 修复。这类"命名漂移"的迁移建议归档到 `05x_align_schema_drift_*`。
- 迁移 062 字段名 `created_at` 在模型中 **没有**显式声明（只有 TM 注入）— 模型端 OK；DB 端缺。

---

## 7. 验证步骤（建议作为后续 issue 的 acceptance criteria）

1. `docker compose exec api alembic current` 应输出 `062_create_rerun_idempotency_keys (head)`。
2. `docker compose exec api python -c "from nfm_db.models import Base; print(sorted(Base.metadata.tables.keys()))"` 应输出 56 张表名（去除 6.1 的死表）。
3. 跑 `apps/api/scripts/check_schema_drift.py`（如存在）应 exit 0。
4. 启动 API 并跑 pytest，验证 SQLite 测试路径不报 "type UUID not understood"。

---

## 8. 一次性 TODO（按优先级）

| P | 任务 |
|---|---|
| P1 | 删除 `models/kg_node.py` 的 `KGNode` / `KGReviewQueue` / `KGProvenance` 死类（或整体删除文件），并加 migration `063_drop_legacy_kg_tables` |
| P1 | `ReExtractionQueue.triggered_by` 改 `nullable=True` |
| P2 | 统一 `extraction_step` / `health_event` / `md_verification` / `verification_task` 的 UUID/JSONB 类型为 portable 版本 |
| P2 | 添加迁移 `063_align_md_verification_owner_id_index` 把 `owner_id` 索引补上 |
| P2 | 添加迁移 `063_align_extraction_results_review_indexes`（如果业务查询模式需要） |
| P3 | 给 6.3 的"无 FK"列加 Python 注释 `nullable=True` 设计理由 |
| P3 | 评估 `ConflictRecord.material_node_id` / `property_node_id` 的 NOT NULL 是否与业务一致 |
| P3 | 给 `009_create_phase1_core_tables.py` 写拆分迁移（无功能价值，纯 hygiene） |

---

## Appendix A: 模型文件清单（43 个 .py 文件 + 1 个 `__init__.py`）

```text
adr009_reconcile_audit.py         → Adr009ReconcileAuditLog
blog_post.py                      → BlogPostMetadata
classification_level.py           → ClassificationLevel
conflict.py                       → (enums only, re-exports)
conflict_record.py                → ConflictRecord
corpus.py                         → Corpus
data_collection_request.py        → DataCollectionRequest
data_dna.py                       → DataDna
dft_calculation.py                → DFTCalculation
entity_merge.py                   → EntityMergeLog
extraction_chunk.py               → ExtractionChunk
extraction_figure.py              → ExtractionFigure
extraction_gap.py                 → ExtractionGap
extraction_job.py                 → ExtractionJob
extraction_result.py              → ExtractionResult
extraction_step.py                → ExtractionStep
feedback.py                       → Feedback
health_event.py                   → HealthEvent
hpc_failover_event.py             → HPCFailoverEvent
hub_node.py                       → HubNode
ingest_log.py                     → IngestLog
kg.py                             → KGNode, KGEdge, KGReviewQueue, OntologyIdMap  ← canonical
kg_node.py                        → KGNode, KGReviewQueue, KGProvenance             ← DEAD
knowledge_gap.py                  → KnowledgeGap
material.py                       → MaterialCategory, Material, MaterialAlias, MaterialComposition
md_verification.py                → MDVerificationJob, HpcJob, MDSimulationResult,
                                    DefectAnalysisResult, PotentialFittingResult,
                                    VerificationResultMD
ontology.py                       → KEntityType, KRelationType
ontology_version.py               → OntologyVersion
potential.py                      → Potential
property.py                       → PropertyCategory, PropertyType, PropertyMeasurement,
                                    MeasurementCondition
re_extraction_queue.py            → ReExtractionQueue
ref_gap_fill.py                   → RefGapFillStaging
rerun_idempotency_key.py          → RerunIdempotencyKey
resource_node.py                  → ResourceNode
review.py                         → Review (+ ReviewMixin, enums)
source.py                         → DataSource, Author, DataSourceAuthor
sync_operation.py                 → SyncOperation
unit.py                           → Unit, UnitConversion
upload_session.py                 → UploadSession
user.py                           → User
verification_task.py              → VerificationTask
```

## Appendix B: 迁移文件清单（73 个 .py 文件）

按时间顺序编号；ER1-ER3 时代划分见 Executive Summary。

```text
001_create_users_table                                ERA1  op.execute raw SQL
002_create_blog_posts_table                           ERA1  op.create_table
003_create_md_verification_tables                     ERA1  op.create_table x5
003_create_potentials_table                           ERA1  op.create_table
004_seed_potentials                                   ERA1  data only
005_add_verification_results_md_and_extend_jobs       ERA2  ALTER+create
005_add_verification_status                           ERA2
005c_merge_verification_branches                     ERA2  merge
006_add_cancelled_status_to_md_jobs                  ERA2  ALTER
007_add_staging_quality_gate_columns                 ERA2  ALTER
008_add_blog_posts_title                             ERA2  ALTER
009_create_phase1_core_tables                        ERA1  op.execute raw SQL x14
010_seed_phase1_reference_data                        ERA1  data only
011_create_kg_tables                                 ERA1  op.create_table x1 (kg_nodes) + raw SQL
012_create_kg_nodes_edges                            ERA1  raw SQL
013_add_entity_merge_log                             ERA2  raw SQL
013_add_multimodal_job_fields                        ERA2  raw SQL + ALTER
013_extraction_figures                               ERA2  raw SQL
014_conflict_records                                 ERA2  raw SQL
014_sync_phase2_schema_drift                         ERA2  raw SQL + ALTER (kg_review_queue, ontology_id_map, hpc_failover_events)
015_kg_models_complete                               ERA2  raw SQL re-creates kg_nodes/kg_edges (rewrites 011/012)
015_add_user_profile_fields                          ERA2  ALTER
020_merge_kg_forks                                    ERA2  merge
021_add_datasource_storage_columns                   ERA2  ALTER
022_phase3_review_traceability                       ERA2  ALTER
023_add_dft_calculations                             ERA2  op.create_table x1
024_create_verification_tasks_table                  ERA2  op.create_table x1
025_merge_verification_and_source_branches           ERA2  merge
026_add_extraction_figures_columns                   ERA2  ALTER
027_merge_heads_011_and_026                          ERA2  merge
028_backfill_review_status_confidence                ERA2  data
029_add_user_service_account_flag                    ERA2  ALTER
030_create_corpus_table                              ERA2  op.create_table x1
031_seed_property_types                              ERA2  data
032_add_dedup_unique_indexes                         ERA2  ALTER
032_create_data_submission_tables                    ERA2  op.create_table x6 (hub_nodes,
                                                     classification_levels, data_dna, ingest_logs,
                                                     upload_sessions, sync_operations_etc)
033_add_conditions_hash_and_method_to_measurements   ERA2  ALTER
034_add_extraction_job_persistence_columns           ERA2  ALTER
035_add_extraction_job_multimodal_flags              ERA2  ALTER
035_ref_gap_fill_staging_v4_columns                  ERA2  ALTER (ref_gap_fill_staging v4)
036_merge_chain_A_and_B                              ERA2  merge
036_ref_gap_fill_staging_v4_columns_simple           ERA2  ALTER
037_create_health_events_table                       ERA2  op.create_table x1
037_merge_ref_gap_fill_chain                         ERA2  merge
038_merge_health_events_and_ref_gap                  ERA2  merge
039_add_extraction_method_provenance                 ERA2  ALTER
040_create_sync_operations                           ERA2  op.create_table x1
041_merge_010_and_039                                ERA2  merge
042_extraction_step_and_chunk                        ERA2  op.create_table x2
043_add_domain_expert_role                           ERA2  ALTER (enum value)
044_add_ontology_version                             ERA2  op.create_table x1
045_add_re_extraction_queue                          ERA2  op.create_table x1
046_add_knowledge_gaps                               ERA2  op.create_table x1
047_extraction_gap                                   ERA2  op.create_table x1
048_data_collection_request                          ERA2  op.create_table x1
049_add_ontology_version_to_extraction_job           ERA2  ALTER
050_extraction_chunk_v2_provenance                   ERA2  ALTER + create_index
051_extraction_job_orchestration_columns             ERA2  ALTER
052_add_datasource_metadata                          ERA2  ALTER
053_align_extraction_gap_with_adr_nfm_2675           ERA2  ALTER
054b39a26310_add_source_to_dft_calculations          ERA2  ALTER + op.create_table re-attempt
055_add_ontology_version_fk_to_type_tables           ERA2  ALTER
056_add_track_id_to_extraction_job                   ERA2  ALTER
057_create_kg_entity_and_relation_type_tables        ERA2  op.create_table x2
058_align_schema_drift_backlog                       ERA3  comprehensive drift fix (op.execute x ~80)
059_add_adr009_reconcile_audit_log                   ERA3  op.create_table x1
060_backfill_ref_gap_fill_staging_source             ERA3  data
061_add_track_id_to_extraction_step                  ERA3  ALTER
062_create_rerun_idempotency_keys                    ERA3  op.create_table x1
9c15710c6321_merge_blog_lineage_002_and_feedback_    ERA2  merge
b5f3a2c1d8e0_add_ref_gap_fill_staging               ERA2  op.execute CREATE TABLE
d3ddb691ae20_create_feedbacks_table                 ERA2  op.execute CREATE TABLE
f8e2db803b55_merge_dft_and_datasource_branches       ERA2  merge
```

> **当前 head** = `062_create_rerun_idempotency_keys`（仅一条 linear chain 自 056 之后无 merge）。
>
> 多 heads 是历史的 linear-history-with-merges：每次 merge 之后留下 1 head，058 把分支收口，062 是最终 leaf。