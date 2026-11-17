# NFM-5951 步 0 — C2 测试装配三层分账基线

> **用途（CTO 修正 N7）**：后续验收线（单模块 PR CI ≤ 全量 1/3 ≈ 7 分钟）不达标时，
> 按「装配面 / per-test create_all 常数 / CI 固定开销」三层归因——**不许未记账就宣布失败或放宽线**。
> 本文件必须在任何 C2 步 4+ 集群 PR 之前落地（步 0 最先合并）。
>
> 规格出处：[NFM-5951](https://github.com/Etoile04/nucpot) issue body（权威规格）；背景：ADR-026 探索报告 C2 节（父票 NFM-5949）。

- **测量时间**：2026-11-11（本票开工时）
- **测量基准 commit**：`dc5d613b6`（main @ 2026-11-10，本分支起点）
- **本地测量环境**：darwin arm64，CPython 3.12.12（uv 管理，与 CI `setup-python@3.12` 同主版本），warm uv cache
- **落地后断言（N5）**：落地前收集数 + 0 = 落地后收集数（本票收集数 delta = 0）；复核证据见 NFM-5951 交付 comment / PR 描述。

---

## (a) 装配面 — 收集数基线

| 指标 | 值 | 复现命令（cwd = `apps/api`） |
|---|---|---|
| 测试文件数 | **432** | `find tests -name "test_*.py" \| wc -l` |
| 全量收集数（8767 口径） | **8767 collected** | `uv run pytest --collect-only -q -o addopts=""` |
| 收集墙钟（pytest 内部计时） | **1.58 s**（本机；纯收集） | 同上 |
| 收集墙钟（含 uv/解释器启动） | ≈4.3 s（本机） | `time` 包裹同上 |
| 默认 addopts 下 | 8584 selected + 183 deselected（`-m 'not integration'`）= 8767 | `uv run pytest --collect-only -q` |
| 对照：coverage 插桩下收集 | 20.36 s（`--cov` 使导入面放大 ~13×） | `uv run pytest --collect-only -q` |

规格参考值：collect-only ≈8 s（CI 级 runner 口径）；本机 warm-cache 数值更低，量级一致。

**归因层 1（装配面）**：`nfm_db.main` 模块级全量装配（44 个 router 模块 import + FastAPI 实例化）+ 432 个测试模块 import。

## (b) per-test create_all 常数层 — 表快照

`sorted(Base.metadata.tables)` @ `dc5d613b6`：**63 张表**。

```
_ref_gap_fill_staging, adr009_reconcile_audit_log, authors, blog_posts,
classification_levels, conflict_records, corpus, data_collection_requests,
data_dna, data_source_authors, data_sources, dataset_versions, datasets,
defect_analysis_results, dft_calculations, entity_merge_log, extraction_chunks,
extraction_figures, extraction_gaps, extraction_jobs, extraction_results,
extraction_steps, feature_flags, feedbacks, health_events, hpc_failover_events,
hpc_jobs, hub_nodes, ingest_logs, kg_edges, kg_entity_types, kg_nodes,
kg_relation_types, kg_review_queue, knowledge_gaps, material_aliases,
material_categories, material_compositions, materials, md_simulation_results,
md_verification_jobs, measurement_conditions, ontology_id_map, ontology_versions,
potential_fitting_results, potentials, property_categories, property_measurements,
property_types, rag_access_log, rag_index_audit_log, re_extraction_queue,
reference_values, rerun_idempotency_keys, resource_nodes, reviews,
sync_operations, unit_conversions, units, upload_sessions, users,
verification_results_md, verification_tasks
```

**归因层 2（per-test create_all 常数，主嫌疑层）**：`db_session` 为 function 级 fixture，
每个用例对 63 张表全量 `create_all` + `drop_all`（SQLite in-memory）。规格口径：186 文件 ×
function 级全库建表；本地字符串级探针（`db_session|async_client|authenticated_client|pg_session`
引用文件数）= 204 文件（含 docstring 提及，口径偏宽）。

## (c) CI 固定开销层 — 近 12 run 墙钟

工作流 `API Tests`（test-api.yml），main 分支 push 触发、success，窗口 2026-11-02 → 2026-11-11。

| # | run id | 日期 | job 墙钟 (s) | workflow 墙钟 (s) |
|---|---|---|---|---|
| 1 | 42957760936 | 11-11 | 926 | 992 |
| 2 | 42550621770 | 11-09 | 937 | 1007 |
| 3 | 42477796857 | 11-08 | 923 | 994 |
| 4 | 42447348200 | 11-08 | 722 | 796 |
| 5 | 42385242077 | 11-07 | 906 | 972 |
| 6 | 42331571172 | 11-07 | 828 | 916 |
| 7 | 42010696403 | 11-05 | 936 | 1007 |
| 8 | 41927575204 | 11-04 | 555 | 625 |
| 9 | 41885572964 | 11-04 | 589 | 663 |
| 10 | 41814087980 | 11-04 | 946 | 1022 |
| 11 | 41641133053 | 11-03 | 870 | 949 |
| 12 | 41464818070 | 11-02 | 950 | 1056 |

- **job「API Tests with Coverage」**：范围 555–950 s，中位 **≈915 s**，均值 ≈841 s
- **workflow 级**（含 `needs: test` 的 deploy-lock PG job 链 + parity job）：范围 625–1056 s，中位 **≈982 s**
- 规格历史口径（签发时）：957–1634 s，中位 ~1172 s——早于本窗口；当前窗口明显更快，但线不变（见下）。

**归因层 3（CI 固定开销）**：uv sync、alembic 单头检查、schema-compat 检查、coverage 插桩
（对照 (a)：coverage 使收集面 1.58 s → 20.36 s）、runner 2-core 规格。

## 验收线与归因规则

**验收线**：单模块 PR CI ≤ 全量 1/3 ≈ 7 分钟（规格口径，基于历史中位 1172 s）。
按当前窗口 job 中位 915 s 同口径折算 ≈ 305 s；**线仍按规格 ≈ 390 s（7 分钟）执行，不因窗口变快而收紧**。

**不达标时归因顺序**（N7，禁止跳过）：

1. **装配面**：对照 (a)——收集/导入墙钟是否上涨（新 import 边、模块级副作用）；
2. **per-test create_all 常数**：对照 (b)——表数是否 >63（新表进入全库建表面）或 function 级建表文件数上升；
3. **CI 固定开销**：对照 (c)——uv/coverage/检查步骤漂移（对照 job 内 step 计时）。

三层之外的现象（如 flaky、外网依赖）另立票，不改本线。
