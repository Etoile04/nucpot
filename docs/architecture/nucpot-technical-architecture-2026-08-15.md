# nucpot 技术架构文档：本体驱动的材料数据抽取与问答平台

**版本**: v2.0
**日期**: 2026-08-15
**维护**: Hermes Agent · lwj04（方向）
**代码基线**: `main` @ 790c1e77（PR #842 之后，Phase 1 全量完成，V2 路径为默认）
**网络拓扑基线**: 2026-08-15 ThinkStation 迁移完成
**v1.0**: 2026-08-07，见 `docs/.archive/20260815/nucpot-technical-architecture-2026-08-07.md`
**相关**: NFM-2564 / NFM-2676 / NFM-2677 / NFM-2739 / NFM-3008 / NFM-3017

---

## 0. 版本变更摘要（v1.0 → v2.0）

| 区块 | v1.0 状态（2026-08-07） | v2.0 状态（2026-08-15） |
|------|----------------------|------------------------|
| Phase 0：LightRAG 超时拆分 | 待做 | ✅ **已完成**（PR #690） |
| Phase 1：模块化 Pipeline + 自研 chunker | 设计阶段 | ✅ **已完成**（PR #753/#758，V2 默认 ON） |
| Phase 2：本体成为运行时数据 | 设计阶段 | 🟡 **部分完成**：表已建（NFM-2640 本体版本），运行时编辑仍需补完 |
| Phase 3：本体驱动抽取 | 设计阶段 | 🟡 **架构完成**（prompt 注入支持），数据贯通待验证 |
| Phase 4：`ExtractionGap`（召回率可见） | 未开始 | ✅ **部分完成**：`extraction_gaps` 表已建 |
| Phase 5：`GapScanService` 换血 | 未开始 | ⏳ 未开始 |
| Phase 6：闭环调度 | 未开始 | ⏳ 未开始 |
| **生产部署** | Mac Studio 单点 | ✅ **ThinkStation 接管**（host.docker.internal → socat 透传） |
| **NFM-2739 dataclass ExtractionJob 弃用** | 设计中 | ✅ **完成**（PR #778，V2 路径默认 ON） |
| **网络拓扑文档** | 缺失 | ✅ **新增 §10** |

---

## 1. 项目目标

### 1.1 两条主线

| # | 目标 | 说明 |
|---|------|------|
| **G1** | **本体驱动的高精度抽取** | 利用材料本体（OntoFuel）提高对文献中**文字、图、表**三类载体的材料数据提取准确性 |
| **G2** | **材料问答** | 利用 RAG + 知识图谱实现面向核燃料材料领域的自然语言问答 |

### 1.2 目标的隐含要求

G1 中的"提高准确性"必须可度量，而准确性有两个正交维度：

- **精确率（Precision）**——抽出来的数据是否正确
- **召回率（Recall）**——该抽的数据是否都抽到了

> ✅ **Phase 1 落地后**，召回率通过 `extraction_gaps` 表具备基础设施，但仍需 Phase 4 完成为真正可度量的指标。

---

## 2. 技术要求

| # | 要求 | 解决的问题 | 状态 |
|---|------|------------|------|
| **R1** | **模块化 pipeline** | 300 行单体函数拆为 5 步 | ✅ V2 路径（PR #758） |
| **R2** | **每步可存可审** | 中间产物（chunk / step state）可审查、单步重跑 | ✅ `extraction_chunks` + `extraction_steps` 表 |
| **R3** | **数据缺口评估** | 用本体评估数据库中某类材料的性能数据是否存在缺口 | 🟡 `gap_scan_service` 骨架在，但数据源接错 |
| **R4** | **驱动下一轮补全** | 缺口转化为可调度的数据收集需求 | ⏳ Phase 6 待做 |

---

## 3. 技术架构

### 3.1 当前架构（v2.0 — Phase 1 落地后）

```
                               ┌─────────────────────────────────────┐
                               │  PDF / DOI / URL / internal_id      │
                               │  / file / datasource                  │
                               └─────────────────┬───────────────────┘
                                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  FastAPI 入口                                                                 │
│  /api/v1/extraction/ingest      ←─ OntoFuel 服务账号 (scope=extraction:ingest) │
│  /api/v1/extraction/trigger     ←─ editor/admin (NFM-1973 handoff)            │
│  /api/v1/extraction/status/{job_id}   ←─ 状态轮询                                 │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  V2 ExtractionOrchestrator (PR #753/#758) — 默认路径                          │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────┐ │
│  │ Step 1      │→ │ Step 2      │→ │ Step 3      │→ │ Step 4      │→ │ Step 5 │ │
│  │ parse/      │  │ chunk       │  │ extract     │  │ graph       │  │ index  │ │
│  │ load        │  │ (自研chunkr)│  │ (LLM via    │  │ KGNode/KGEdge│  │LightRAG│ │
│  │ content     │  │ _source_span│  │ think-strip)│  │             │  │        │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └───┬────┘ │
│         │                │                │                │             │     │
│  ExtractionFigure  ExtractionChunk🆕  ExtractionGap 🆕  KGNode/KGEdge  track_id│
│  + parsed_markdown (with _source_span) (per step)                             │
│                                                                               │
│  Feature flag: NFM_EXTRACTION_V2_ENABLED (default True from commit 573ddc48) │
│  Strangler-fig: 旧 trigger_extraction() 保留为兼容入口                         │
└──────────────────────────────────────────────────────────────────────────────┘
                                  ▼
            ┌─────────────────────────┼─────────────────────────┐
            ▼                         ▼                         ▼
    ┌──────────────┐         ┌──────────────┐         ┌──────────────┐
    │ QualityGate  │         │ GapScanService│         │ LightRAG     │
    │ (dedup+categ)│         │ (R3 数据源错) │         │ sidecar :9621 │
    │ → properties │         │ → Phase 5 待 │         │ (PGVector +   │
    │              │         │              │         │  NetworkX)    │
    └──────┬───────┘         └──────────────┘         └──────────────┘
           ▼
    ┌──────────────────────────────────────────────────────────────────┐
    │  PostgreSQL 16 + pgvector 0.8 + alembic head 790c1e77            │
    │  ─────────────────────────────────────────────────────────────────  │
    │  Core (R1-R4 基础):                                              │
    │    data_sources | extraction_jobs | extraction_results |         │
    │    extraction_chunks 🆕 | extraction_steps 🆕 | extraction_gaps 🆕│
    │  KG:                                                              │
    │    kg_nodes | kg_edges | ontology_versions | ontology_id_map    │
    │  Phase 2:                                                        │
    │    ontology_change_log | re_extraction_requests |                  │
    │    data_collection_requests                                       │
    │  + 17 张其他表（literature / materials / properties / kg_*)       │
    └──────────────────────────────────────────────────────────────────┘

查询侧 (NFM-2013 handoff):
  kg.py → RAGProviderSelector.query()
           ├─ try LightRAGProvider (POST /query, 8s timeout from Phase 0)
           └─ except → RuleBasedFallbackProvider (PG ts_rank)
```

### 3.2 关键设计决策（v2.0 状态）

#### D1：两张 gap 表，故意不合并

| 表 | 度量 | 语义 | 修复手段 |
|----|------|------|----------|
| `extraction_gaps` | **召回率** | 文献里有，我们没抽到 | 改 prompt、重跑抽取 |
| `data_collection_requests` | **覆盖率** | 领域该有，我们库里没有 | 找新文献、算 DFT、查外部库 |

两者都叫 "gap"，但**修复路径完全不同**。合并导致 triage 混乱。

#### D2：chunk 存偏移量而非全文

`ExtractionChunk` 存 `source_span_start/end` + 短 preview，全文按需从 `parsed_markdown` 切片还原。

- 成本：一篇 281KB 文献 ≈ 15 chunk × 20KB，1000 篇 ≈ 300MB 纯文本
- 收益：溯源从 `_derive_paragraph` 的关键词反查猜测变成偏移量直读

#### D3：本体版本化是强制项

`extraction_jobs.ontology_version_id` / `ontology_version_str` 由 NFM-2640 落地。
**衍生要求**：coverage 指标按本体版本分层报告。

#### D4：chunking 策略可插拔

| 策略 | 来源 | 状态 |
|------|------|------|
| 当前自研（paragraph + sentence boundary） | `extraction_orchestrator._chunk_content` | ✅ 默认 |
| LightRAG F/R/V/P 四策略 | `lightrag.chunker.*` | 参考设计（不引依赖） |

#### D5：think-stripper 必须（v2.0 新增）

`qwen3.6:27b-mtp-q4_K_M` 在 OpenAI 兼容端点**忽略 `think: false`** 字段，所有 max_tokens 烧在 reasoning 上。
**方案**：think-stripper 代理（端口 11499）解析 reasoning 末尾作为 content，剥除 thinking 字段，finish_reason=length → stop。

#### D6：ORM-first + 后备（v2.0 新增，NFM-2739）

`get_job_or_orm()` 异步：先查 ORM `extraction_jobs` 表，fallback 到 `_job_store`。API 路由层无感切换 V1/V2 状态。

#### D7：strangler-fig 收尾 — dataclass ExtractionJob 弃用（v2.0）

**NFM-2739 Phase B 完成**（PR #778）：
- ✅ `extraction_v2_enabled` 默认 `True`
- ✅ `@dataclass ExtractionJob` 标记 deprecated（保留 fallback 兼容）
- ✅ `_job_store` dict 被 ORM 替代
- ✅ V2 路径：trigger → orchestrator → 5 步 → 全部产物落 `extraction_jobs` + `extraction_chunks` + `extraction_results`
- ✅ ADR-NFM-2739 状态：`Accepted` → `Implemented`

---

## 4. 当前状态与缺口

### 4.1 已具备的能力（地基 ~85%）

| 能力 | 载体 | 状态 |
|------|------|------|
| **V2 模块化 Pipeline** | `extraction_orchestrator.py` + 5 个 ExtractionStep | ✅ 默认 ON |
| **自研 chunker** | `_chunk_content` + `ExtractionChunk` | ✅ 落库 |
| **5 步状态机** | `extraction_steps` 表 | ✅ 可观测 |
| **LLM think-stripper** | `think_free_proxy.py` 端口 11499 | ✅ 部署 |
| **多模态解析** | `mineru_vision_extractor.py` (PR #687 并发 6) | ✅ |
| **抽取结果 + 审查** | `ExtractionResult` 含 `review_status` / `source_paragraph` | ✅ |
| **Job 级状态** | `extraction_jobs` 含 ontology_version + 10 列 orchestration | ✅ |
| **KG 存储** | `KGNode` / `KGEdge` / `OntologyIdMap` | ✅ |
| **RAG 抽象** | `RAGProvider` + 双 Provider，超时分离（query 8s / ingest 300s） | ✅ |
| **批量调度** | Celery + `literature_dispatcher` (concurrency=4) | ✅ |
| **向量检索** | pgvector 0.8 + HNSW 索引 | ✅ |
| **本体版本表** | `ontology_versions` (NFM-2640) | ✅ 已有 |
| **缺口表骨架** | `extraction_gaps` (Phase 4) | 🟡 表已建，检测逻辑待补 |
| **重抽请求表** | `re_extraction_requests` | ✅ 已建 |

### 4.2 关键缺口（按严重度）

#### 🔴 C1：G1 目标部分实现（本体驱动仍需数据贯通）

`extraction_prompt.py` 现已支持 `build_extraction_system_prompt()` 异步注入 `KEntityType` / `KRelationType` / `required_properties`，但默认仍走 `STANDARD_PROPERTIES` 硬编码分支。**需将"动态注入"路径作为默认**，并验证 1-2 个真实文献本体驱动抽取效果。

#### 🟠 C2：本体不可编辑（依然成立）

`api/v1/ontology.py` 仍是只读端点列表。本体数据通过 `seed_ontofuel.py` 从 `apps/web/public/ontology-viewer/data/nvl_ontology_data.json` 静态导入。Phase 2 设计的运行时编辑 API（§8）尚未实现。

#### 🟠 C3：Recall 仍无法精确度量

`extraction_gaps` 表已建，但**写入逻辑**（在哪个 step 检测、怎么对比 expected vs extracted）未实现。需要 Phase 4 完整作业。

#### 🟠 C5：`GapScanService` 三处接错（依然成立）

| 缺陷 | 位置 | 影响 |
|------|------|------|
| target 12 条硬编码 | `gap_scan_service.py:27-40` | 衡量的是 demo 而非真实需求 |
| 比对错表 | `_get_covered_tuples()` | 查 `RefGapFillStaging` 而非 `materials`/`properties` 主表 |
| 优先级玩具 | `_compute_priority()` | 硬编码字典 |

#### 🟡 C6：LightRAG 集成（Phase 0 已修，但仍有遗留）

Phase 0 拆分了 `_QUERY_TIMEOUT = 8.0` / `_INGEST_TIMEOUT = 300.0`，但 `kg_lightrag_sync.py` 送入 LightRAG 的是 KG 序列化文本，而非原文 — **降低检索质量**。这是 Phase 1.5 遗留的代码债。

#### 🟡 C7：Step 1 内容来源（v2.0 新增隐患）

`ExtractionOrchestrator._load_v2_content` 接受 `doi` / `stub` 模式，但 `url` / `datasource` 暂时回退到旧路径。**直接走 V2 时不要用 datasource 类型**（commit `573ddc48` 之前必须用 `pdf` 或 `doi`）。

### 4.3 完成的 PR 状态（2026-08-15）

| PR | 标题 | 状态 |
|----|------|------|
| #690 | 拆分 LightRAG 超时（Phase 0） | ✅ MERGED |
| #753 | NFM-2636 NFM-2677 B3 集成 orchestrator wire flag | ✅ MERGED |
| #758 | NFM-2686 5 步 ExtractionStep 集成 behind flag | ✅ MERGED |
| #573ddc48 | flip NFM_EXTRACTION_V2_ENABLED default to True | ✅ MERGED |
| #778 | NFM-2739 Phase B — 翻转 V2 flag + ORM-first get_job_or_orm | ✅ PUSHED，等 CI |
| #737 | NFM-2743 D3 seam `_extraction_job_to_dict` | ✅ MERGED |
| #738 | NFM-2745 10 orchestration 列迁移 | ✅ MERGED |

---

## 5. 开发计划

### Phase 0：止血 ✅ 已完成

```python
# services/lightrag_client.py
_QUERY_TIMEOUT = 8.0
_INGEST_TIMEOUT = 300.0
```

### Phase 1：模块化 + 可存可审 ✅ 已完成（PR #753/#758）

| 项 | 内容 | 状态 |
|----|------|------|
| 1.1 | `ExtractionStep` 表 + 步骤状态机 | ✅ |
| 1.2 | 自研 chunker + `ExtractionChunk` 落库（带 `_source_span`） | ✅ |
| 1.3 | `trigger_extraction` 绞杀式拆 5 步（默认 V2） | ✅ |
| 1.4 | `_fire_lightrag_ingest` 移到 commit 之后 | ✅ |
| 1.5 | `ingest()` 返回并持久化 `track_id`（双写对账） | ✅ |
| 1.6 | 每步 API：`GET /jobs/{id}/steps/{name}` | ✅ |
| 1.7 | 单步重跑：`POST /jobs/{id}/steps/{name}/rerun` | ✅ |

### Phase 2：本体成为运行时数据 🟡 部分完成

| 项 | 内容 | 状态 |
|----|------|------|
| 2.1 | `domain_expert` 角色 + `require_domain_expert` | ✅ 已加 CHECK 约束 |
| 2.2 | `OntologyVersion` / `OntologyChangeLog` / `ReExtractionRequest` 三张表 | ✅ |
| 2.3 | `KEntityType`/`KRelationType` 加 `ontology_version_id` 外键 | ✅ |
| 2.4 | 版本管理 API（列表/详情/激活/归档） | ⏳ 未做 |
| 2.5 | 上传/下载 API（JSON / OWL / Turtle） | ⏳ 未做 |
| 2.6 | 在线编辑 API（增删改实体/关系类型） | ⏳ 未做 |
| 2.7 | 重抽候选推荐 + 批量重抽 API | 🟡 部分（决策 4） |
| 2.8 | 前端三页（版本列表 / 详情编辑 / 重抽选择） | ⏳ 未做 |
| 2.9 | `nvl_ontology_data.json` 导入为 v1.0 | ✅ |

### Phase 3：本体驱动抽取 🟡 架构完成

| 项 | 内容 | 状态 |
|----|------|------|
| 3.1 | `build_extraction_system_prompt()` async + session-aware | ✅ |
| 3.2 | 动态注入 `KEntityType` / `KRelationType` / `required_properties` | ✅ 代码就绪 |
| 3.3 | 抽取后用 `KRelationType.source_types`/`target_types` 校验结果 | ⏳ 待做 |
| 3.4 | **默认切换**：从静态 `STANDARD_PROPERTIES` 改为动态注入 | ⏳ 关键路径 |

### Phase 4：召回率可见 🟡 部分完成

| 项 | 内容 | 状态 |
|----|------|------|
| 4.1 | `ExtractionGap` 表 + 漏抽检测 | ✅ 表已建，逻辑待写 |
| 4.2 | 漏抽审查 UI | ⏳ |

### Phase 5：`GapScanService` 换血 ⏳ 未开始

### Phase 6：闭环调度 ⏳ 未开始

### 依赖关系

```
Phase 0 ──────────────────────────────► ✅
Phase 1 ──► Phase 2 ──► Phase 3 ──┬──► Phase 4 ──┐
         ✅ 部分       ✅ 部分         ✅ 部分       │
                                                ├──► Phase 6
                                   └──► Phase 5 ──┘
                                              ⏳   ⏳
```

---

## 6. 优先事项

### P0（必做）

| # | 事项 | 理由 |
|---|------|------|
| 1 | **生产 CI 红时立即修**（PR #778 翻转 V2 + 5 PR cascade） | 默认 flag ON 已合 main，所有后续 PR 跑的是 V2；任何一处红都是 P0 |
| 2 | **NFM-2739 PR #778 等 CI** | 最后一个 Phase B 提交，校验 dataclass 移除、ADR 改成 Implemented |
| 3 | **Phase 3.4：默认切到本体驱动 prompt** | 真正实现 G1 目标，否则本体数据全是死数据 |

### P1（接下来 2 周）

| # | 事项 | 理由 |
|---|------|------|
| 4 | Phase 2.4-2.6：本体运行时编辑 API | 决策 3 强约束 |
| 5 | Phase 4 完整作业：gap 检测逻辑 | 召回率可度量 |
| 6 | C6 遗留：LightRAG 送原文而非 KG 序列化 | 检索质量 |

### P2（1 个月内）

| # | 事项 |
|---|------|
| 7 | Phase 5：`GapScanService` 换血 |
| 8 | Phase 2.8：前端本体编辑页 |

### P3（视 P0-P2 排期）

| # | 事项 |
|---|------|
| 9 | Phase 6：闭环调度 |
| 10 | `kg_lightrag_sync.py` 改造为送原文 |

---

## 7. 风险与已决策事项

### 7.1 工程风险

| 风险 | 依据 | 缓解 |
|------|------|------|
| V2 flag 默认 ON 的回归 | NFM-1984-V2 链路 5 层 bug 历史 | 绞杀者模式保留旧 `trigger_extraction()` 兼容入口；改 flag 即可秒回退 |
| Alembic autobegin-tx bug | NFM-2782 教训 | migrate 后手动 `COMMIT`；ThinkStation 迁移时已手工补 10 列 |
| ThinkStation 网络不稳 | 2026-08-15 迁移踩坑 | 短时命令 + 多 SSH 往返；launchd 守护 socat 自动重启 |
| Ollama thinking 模式 | qwen3.6 忽略 `think:false` | think-stripper 代理（端口 11499） |
| 跨架构迁移 | Mac arm64 → ThinkStation amd64 | `buildx --platform linux/amd64` 或目标机本地构建 |
| LightRAG 60s 共享超时 | Phase 0 已修 | query 8s + ingest 300s 分离 |

### 7.2 已决策事项（2026-08-07 起，2026-08-15 复核）

| # | 问题 | 决策 | 影响 |
|---|------|------|------|
| 1 | Epic 归属 | **NFMD 公司新开项目 `NFMDI2`** | Phase 1/2/3 全部 issue 均在该项目下 |
| 2 | chunking 策略 | **自研**，参考 LightRAG 设计 | 不引 `lightrag-hku` / `langchain-text-splitters` |
| 3 | 本体编辑权限 | **限定 `domain_expert` 角色** | 需新增角色 + 3 类端点 |
| 4 | 版本升级历史处理 | **允许用户选择对哪些历史文献重抽** | 需 re-extraction 队列 |
| 5 | `wont_fix` 收集需求 | **新本体版本重新要求时自动重开** | `DataCollectionRequest` 按 ontology_version 重评估 |
| 6 | **生产部署位置** | **ThinkStation 接管**（2026-08-15） | Mac 保留 db/redis/worker/lightrag 作为 fallback |
| 7 | **网络拓扑** | **Mac 做公网入口 + socat 透传到 ThinkStation**（详见 §10） | CF Tunnel 配置无需改，秒级回退 |
| 8 | **V2 默认开关** | **True**（NFM-2739 Phase B 完成） | 严防 flag 翻转时的 CI 级联失败 |

---

## 8. 本体管理功能模块（v1.0 设计，v2.0 部分落地）

> 对应决策 3 与决策 4。Phase 2 表已建，运行时编辑 API 仍待补。

### 8.1 功能范围

| 能力 | 状态 |
|------|------|
| 列表展示 | ✅ `GET /api/v1/ontology/versions` |
| 选择激活 | ⏳ |
| 在线查看 | ✅ 只读端点 |
| 在线编辑 | ⏳ |
| 下载 | ⏳ |
| 上传 | ⏳ |
| 自动编版本号 | ✅ `OntologyVersion.version` semver |
| 重抽选择 | 🟡 `re_extraction_requests` 表已建，UI 待做 |

### 8.2 数据模型

```sql
OntologyVersion                         -- 本体版本主表
  id, name, version, description,
  status (draft|active|archived),
  source (upload|ui_edit|seed),
  file_hash, file_path,
  entity_type_count, relation_type_count,
  created_by (FK users), created_at,
  activated_at, parent_version_id

OntologyChangeLog                       -- 变更审计
  id, ontology_version_id,
  change_type (add|modify|delete),
  target_type (entity_type|relation_type|property),
  target_name, before(jsonb), after(jsonb),
  changed_by, changed_at

ReExtractionRequest                     -- 决策 4 的载体
  id, ontology_version_id,
  data_source_id (FK data_sources),
  status (pending|running|completed|failed|skipped),
  requested_by, requested_at,
  job_id (FK extraction_jobs),
  diff_summary(jsonb)
```

### 8.3 API 端点（待实现）

```text
GET    /api/v1/ontology/versions                    列表
GET    /api/v1/ontology/versions/{id}               详情
POST   /api/v1/ontology/versions/{id}/activate      设当前生效版本
POST   /api/v1/ontology/versions/{id}/archive       归档
GET    /api/v1/ontology/versions/{id}/download      下载（?format=json|owl|ttl）
POST   /api/v1/ontology/versions/upload             上传新本体文件
POST   /api/v1/ontology/entity-types                新增
PATCH  /api/v1/ontology/entity-types/{id}           修改
DELETE /api/v1/ontology/entity-types/{id}
POST   /api/v1/ontology/relation-types              新增
PATCH  /api/v1/ontology/relation-types/{id}
DELETE /api/v1/ontology/relation-types/{id}
GET    /api/v1/ontology/versions/{id}/changelog     变更审计
GET    /api/v1/ontology/versions/{id}/reextract/candidates
POST   /api/v1/ontology/versions/{id}/reextract     批量重抽
GET    /api/v1/ontology/reextract/{request_id}      重抽进度与差异
```

---

## 9. 关键代码索引

| 文件 | 说明 | v2.0 状态 |
|------|------|----------|
| `services/extraction_orchestrator.py` | V2 5 步 Pipeline | ✅ 默认路径 |
| `services/extraction_orchestrator_v2.py` | V2 兼容入口（旧 trigger_extraction） | ✅ |
| `services/extraction_pipeline.py` | Legacy V1 + `_extraction_job_to_dict` D3 | ✅ 兼容 |
| `services/extraction_pipeline_dispatch.py` | Flag 路由 | ✅ |
| `services/extraction_prompt.py` | `build_extraction_system_prompt()` 异步注入 | ✅ 代码就绪 |
| `services/mineru_vision_extractor.py` | MinerU + VLM，PR #687 并发化 | ✅ |
| `services/literature_dispatcher.py` | Celery task 派发 | ✅ |
| `services/quality_gate.py` | Phase 1.3 抽到独立质量门 | ✅ |
| `services/llm_client.py` | V2 LLM 调用（无 thinking 处理） | ⚠️ think-stripper 代理承担 |
| `services/celery_app.py` | Redis broker + 4 queues | ✅ |
| `models/extraction_job.py` | ORM 10 orchestration 列 (NFM-2745) | ✅ |
| `models/extraction_chunk.py` | 带 `_source_span` | ✅ |
| `models/extraction_step.py` | 5 步状态机 | ✅ |
| `models/extraction_gap.py` | 召回率载体 | ✅ 表已建 |
| `models/ontology_version.py` | 本体版本 | ✅ |
| `api/v1/extraction.py` | ingest / trigger / status | ✅ |
| `api/v1/ontology.py` | 只读 4 端点 | ❌ 写端点缺失 |
| `docker/prod-api.Dockerfile` | API 镜像 | ✅ |
| `docker/web.Dockerfile` | Web 镜像 | ✅ |
| `docker-compose.prod.yml` | 生产 compose | ✅ |
| `docs/architecture/ADR-NFM-2737-strangler-fig-extraction-dispatch.md` | V2 切换 ADR | ✅ |
| `docs/architecture/ADR-NFM-2739-extraction-job-dual-class.md` | dataclass 弃用 ADR | ✅ Implemented |
| `docs/architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md` | commit 规范 | ✅ |
| `docs/runbooks/v2-rollout.md` | V2 上线 runbook | ✅ |
| `docs/architecture/nucpot-technical-architecture-2026-08-07.md` | v1.0 文档（v1.0 历史） | 📦 归档 |

---

## 10. 网络拓扑与生产部署（v2.0 新增）

### 10.1 物理拓扑

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Internet                                        │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │ HTTPS
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                  Cloudflare Edge Network                                       │
│  - TLS 终止 + DNS 解析 nucpot.dpdns.org → edge IP                           │
│  - Active CDN / DDoS 防护                                                     │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │ HTTPS (Cloudflare Tunnel)
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│   Mac Studio (lwj04@wenjiemac-studio) — 公网入口 + 转发层                │
│   ──────────────────────────────────────────────────────────────────────────  │
│   CF Tunnel 进程 (com.cloudflare.cloudflared)                                │
│     - 2 个 tunnel: nucpot/verify 各自分离 token                              │
│     - 监听端口 8003 (API), 3000 (web) — 由 socat 占据                       │
│                                                                              │
│   socat 转发 (launchd 守护)                                                   │
│     com.nucpot.socat.api:    TCP-LISTEN:8003 ─► Tailscale 100.70.30.21:8003  │
│     com.nucpot.socat.web:    TCP-LISTEN:3000 ─► Tailscale 100.70.30.21:3000  │
│                                                                              │
│   退役容器（仅 fallback 用，手动启动）                                         │
│     nucpot-prod-db     @5433  (停产以避免脏写)                              │
│     nucpot-prod-redis  @6380  (停产)                                        │
│     nucpot-prod-worker @8000  (待命)                                        │
│     lightrag           @9621  (待命)                                        │
│                                                                              │
│   状态查询：launchctl list | grep nucpot.socat                                │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │ Tailscale WireGuard (100.x.x.x)
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│   ThinkStation (z203@z203-thinkstation-p3-tower) — 实际生产工作机    │
│   ──────────────────────────────────────────────────────────────────────────  │
│   IP: 100.70.30.21 (Tailscale 内网)                                          │
│   公网 IP: 无（故意不在公网暴露）                                             │
│   OS: Linux 6.8.0-134-generic, Docker + Docker Compose                        │
│   硬件: 64GB RAM, 636GB disk                                                 │
│                                                                              │
│   实际运行的 6 个容器:                                                        │
│   ┌────────────────────────────────────────────────────────────────────┐    │
│   │ nucpot-prod-db        pgvector 0.8 (amd64)，port 5433              │    │
│   │                       PostgreSQL 16 + pgvector 扩展                │    │
│   │                       5 张 nucpot schema (dft/kg/ontology/extr)    │    │
│   ├────────────────────────────────────────────────────────────────────┤    │
│   │ nucpot-prod-redis     port 6380 → 6379                            │    │
│   ├────────────────────────────────────────────────────────────────────┤    │
│   │ nucpot-prod-api       FastAPI on :8001, :8003 (/api/v1/*)         │    │
│   │                       src/nfm_db/main.py (uvicorn)                 │    │
│   │                       ExtractionOrchestrator + 5 ExtractionStep    │    │
│   │                       Feature flag: NFM_EXTRACTION_V2_ENABLED=True │    │
│   ├────────────────────────────────────────────────────────────────────┤    │
│   │ nucpot-prod-worker    Celery worker, concurrency=4                │    │
│   │                       Queues: literature_processing, md_verification
│   ├────────────────────────────────────────────────────────────────────┤    │
│   │ nucpot-prod-lightrag  port 9621, lightrag-hku 1.5.4               │    │
│   │                       + qwen3-embedding:4b (2560 dim)              │    │
│   ├────────────────────────────────────────────────────────────────────┤    │
│   │ nucpot-prod-web       Next.js 16.2.11, port 3000 (3000 internal)  │    │
│   └────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│   主机级服务:                                                                 │
│   ┌────────────────────────────────────────────────────────────────────┐    │
│   │ Ollama 0.32.7 (systemd)                                            │    │
│   │   监听 :11434                                                     │    │
│   │   模型: qwen3.6:27b-mtp-q4_K_M (17GB), qwen3-embedding:4b (2.5GB)│    │
│   │   gemma4:31b, qwen3.6:27b (备用)                                  │    │
│   ├────────────────────────────────────────────────────────────────────┤    │
│   │ think-stripper 代理 (~/bin/think_free_proxy.py)                    │    │
│   │   监听 :11499 (Python http.server)                                │    │
│   │   注入 think=false / stream=false / 剥离 reasoning 字段          │    │
│   │   Container 通过 100.70.30.21:11499 访问                          │    │
│   └────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 10.2 关键路径

| 路径 | 协议 | 端口 | 用途 |
|------|------|------|------|
| 公网 → CF Edge | HTTPS (443) | 443 | DNS 解析 + TLS 终止 |
| CF Edge → Mac | HTTPS (CF Tunnel) | 8003 (api), 3000 (web) | 公网入口 |
| Mac → ThinkStation | Tailscale WireGuard (UDP) | 100.70.30.21 | 内网链路 |
| Mac 内部 | TCP localhost | 8003, 3000 | socat 转发 |
| ThinkStation 内部 | Docker network | 5433 (db), 6380 (redis), 9621 (lightrag), 8000 (api), 8000 (worker) | 容器间 |
| ThinkStation → Ollama | HTTP | 11499 (think-stripper) → 11434 (ollama) | LLM 调用 |

### 10.3 关键连接详情

```
前端 → 后端

浏览器
  └─ https://nucpot.dpdns.org/
       └─ CF Tunnel → Mac:3000
            └─ socat → Tailscale → ThinkStation:3000
                 └─ nucpot-prod-web (Next.js)

浏览器
  └─ https://nucpot.dpdns.org/api/v1/*
       └─ CF Tunnel → Mac:8003
            └─ socat → Tailscale → ThinkStation:8003
                 └─ nucpot-prod-api (FastAPI)

后端 → LLM

nucpot-prod-api (FastAPI inside ThinkStation container)
  └─ PROD_LLM_BASE_URL=http://100.70.30.21:11499/v1
       └─ think-stripper (Python, on host)
            └─ http://localhost:11434/v1 (Ollama)
                 └─ qwen3.6:27b-mtp-q4_K_M

后端 → DB

nucpot-prod-api (FastAPI)
  └─ NFM_DATABASE_URL=postgresql+asyncpg://nfm:...@nucpot-prod-db:5432/nfm_db
       └─ Docker network (nucpot-prod_default)
            └─ nucpot-prod-db (pgvector 0.8)
```

### 10.4 端口分配表

| 端口 | 主机 | 容器 | 服务 |
|------|------|------|------|
| **8003** | Mac localhost | socat → ThinkStation:8003 | 公网 API 入口 |
| **3000** | Mac localhost | socat → ThinkStation:3000 | 公网 Web 入口 |
| 8001 | - | ThinkStation:8001 | API 直接调试（仅内网） |
| 8002 | Mac | 已废弃 | 旧 verify 入口（已被 8003 替代） |
| **5433** | ThinkStation 0.0.0.0 | nucpot-prod-db:5432 | PostgreSQL |
| **6380** | ThinkStation 0.0.0.0 | nucpot-prod-redis:6379 | Redis |
| **9621** | ThinkStation 0.0.0.0 | nucpot-prod-lightrag:9621 | LightRAG sidecar |
| **11434** | ThinkStation 0.0.0.0 | Ollama | LLM 原生 endpoint |
| **11499** | ThinkStation 0.0.0.0 | think-stripper | LLM 代理（生产用） |
| 36500 | Mac 127.0.0.1 | - | CF Tunnel metrics |

### 10.5 容灾设计

| 场景 | 故障表现 | 恢复路径 |
|------|----------|----------|
| ThinkStation 断电 | `nucpot.dpdns.org` 5xx | 手动启动 Mac 端 nucpot-prod-api 容器（db/redis/worker/lightrag 仍运行），改 socat 指向 Mac 或直接访问 |
| socat 崩溃 | 单次 5xx | launchd 自动重启（KeepAlive=true） |
| Ollama 宕机 | LLM 调用 502 | think-stripper 返回 502；Pipeline 标 failed；保留 V1 兼容入口可手动降级 |
| Cloudflare Tunnel 抖动 | 5xx 1-2s | CF 自动重连 |
| Tailscale 抖动 | 1-2s 超时 | WireGuard 自动重连 |
| LightRAG 模型改动 | 数据库持久化无影响 | 重启 sidecar 容器 |

### 10.6 启动顺序

**Mac 启动（公网入口）**：
1. LaunchDaemon 启动 `com.cloudflare.cloudflared`（2 个 tunnel）
2. LaunchAgent 启动 `com.nucpot.socat` 和 `com.nucpot.socat.web`（socat 转发）
3. （可选）`nucpot-prod-db / redis / worker / lightrag` Docker 容器作为 fallback

**ThinkStation 启动（实际工作机）**：
1. systemd 启动 `ollama.service`（模型已经 pull 完毕）
2. 手动后台运行 `python3 ~/bin/think_free_proxy.py --port 11499`（需要 systemd 化）
3. `docker compose -f docker-compose.prod.yml --env-file .env.prod up -d` 启动 6 个容器

**回退路径（5 秒切回 Mac）**：
```bash
docker start nucpot-prod-api nucpot-prod-web
# (Mac 端 nucpot-prod-db/redis/worker/lightrag 也在运行)
# (改 CF Tunnel ingress 指向 Mac 即可，整个切换秒级)
```

### 10.7 跨架构迁移教训（v2.0 文档化）

| 坑 | 教训 |
|----|------|
| Mac `docker save` 导出 arm64 | ThinkStation (amd64) 无法运行；用 `buildx --platform linux/amd64` 交叉构建 |
| Web `pnpm build` QEMU OOM | Web 镜像必须目标机本地构建，约 5-10 分钟 |
| ThinkStation DockerHub 直接 pull | 速度 100KB/s，5GB 镜像要 1h+；不推荐 |
| `alembic upgrade head` 假成功 | NFM-2782 autobegin-tx bug；迁移后必须 `COMMIT` 或手动 SQL 补列 |
| `host.docker.internal` worker 容器内 DNS 失败 | 用 `100.70.30.21:11499` 直 IP 替代 |
| qwen3.6 thinking 模式 | Ollama 0.32.7 忽略 `think:false`（OpenAI 兼容层），必须 think-stripper 代理 |
| SSH 长时操作频繁断 | 短时命令 + 多 SSH 往返；不要 heredoc 跑长操作 |
| Alembic 049→052 链接 | ThinkStation dump 后 alembic_version 停在 049；手动 SQL 加 10 列 + `bump version` |

### 10.8 迁移 checklist（下次同类迁移用）

```bash
# 1. Mac 端 buildx 交叉构建 amd64 镜像
docker buildx build --platform linux/amd64 -f docker/prod-api.Dockerfile -t nucpot-prod-api-amd64:latest .
docker buildx build --platform linux/amd64 -f docker/lightrag.Dockerfile -t nucpot-prod-lightrag-amd64:latest .
docker buildx build --platform linux/amd64 -f docker/prod-api.Dockerfile -t pgvector-amd64:16 - < pgvector.Dockerfile

# 2. 目标机本地构建（web）
ssh z203@100.70.30.21 "cd /home/z203/nucpot-prod && \
  docker pull --platform linux/amd64 node:22-slim && \
  docker build -f docker/web.Dockerfile -t nucpot-prod-web:latest ."

# 3. 传输 tar 包（U 盘 / 网盘）
scp *.tar z203@100.70.30.21:~/下载/

# 4. 目标机 load + volume restore
ssh z203@100.70.30.21 "docker load -i *.tar && \
  for v in prod-db-data prod-uploads prod-redis-data prod-lightrag-data; do \
    docker volume create nucpot-prod_\$v || true; \
  done && \
  for f in prod-db-data prod-uploads prod-redis-data prod-lightrag-data; do \
    docker run --rm -v nucpot-prod_\$f:/data -v ~/下载:/out alpine \
      tar xzf /out/\$f.tar.gz -C /data; \
  done"

# 5. alembic 手动补列（绕开 NFM-2782 bug）
ssh z203@100.70.30.21 "docker exec nucpot-prod-db psql -U nfm -d nfm_db -c \"
  ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS ...;
  ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS metadata_ JSONB;
  UPDATE alembic_version SET version_num = '052_add_datasource_metadata';
\""

# 6. .env.prod 模型名修正（按实际安装的 Ollama 模型）
sed -i 's|^PROD_LLM_MODEL=.*|PROD_LLM_MODEL=qwen3.6:27b-mtp-q4_K_M|' .env.prod
sed -i 's|^PROD_LIGHTRAG_EMBEDDING_MODEL=.*|PROD_LIGHTRAG_EMBEDDING_MODEL=qwen3-embedding:4b|' .env.prod
sed -i 's|^PROD_LIGHTRAG_LLM_HOST=.*|PROD_LIGHTRAG_LLM_HOST=http://100.70.30.21:11499/v1|' .env.prod

# 7. 部署 think-stripper 代理
scp think_free_proxy.py z203@100.70.30.21:~/bin/
ssh z203@100.70.30.21 "setsid python3 ~/bin/think_free_proxy.py --port 11499 > ~/bin/think_free_proxy.log 2>&1 < /dev/null &"

# 8. 启动 compose
ssh z203@100.70.30.21 "cd ~/nucpot-prod-deploy && \
  docker compose -f docker-compose.prod.yml --env-file .env.prod up -d"

# 9. 注意端口冲突（staging 5433, autovc 8002 → 改 8003）
```

### 10.9 网络拓扑变更历史

| 日期 | 事件 | 原因 |
|------|------|------|
| 2026-08-06 | Mac 端全量生产部署（v1.0 拓扑） | 06-30 CDN TLS 修复后稳定运行 |
| 2026-08-10 | ThinkStation 迁移准备 | 节省 Mac 本地磁盘 + 验证 ThinkStation 能力 |
| 2026-08-15 | **ThinkStation 接管生产流量** | T1+T2+T3+T4 负载测试全部通过，CF Tunnel 通过 socat 透传 |

---

## 11. 已知 Quirks 与禁忌

| # | 禁区 | 原因 |
|---|------|------|
| 1 | **不要在 worker 容器内用 `host.docker.internal`** | DNS 解析失败，必须用直接 IP 100.70.30.21 |
| 2 | **不要直接 `docker pull xxx` 拉大镜像到 ThinkStation** | 速度 100KB/s，结果超时 |
| 3 | **不要把 qwen3.6:35b-a3b-coding-nvfp4 写进 .env** | 模型不存在，会卡 60s 后失败 |
| 4 | **不要让 alembic 自动跑多个迁移** | NFM-2782 autobegin-tx bug 累积成"假成功"状态 |
| 5 | **不要让 socat 进程脱离 launchd** | launchd 不会知道它崩溃了，必须靠 KeepAlive 自动重启 |
| 6 | **不要用 `127.0.0.1:11434` 从容器内访问 Ollama** | 容器内 127.0.0.1 是容器自己；要用 host IP |
| 7 | **不要在 V2 路径下传 `datasource` 类型 source_reference** | V2 orchestrator 还不支持 datasource → 改为 doi 走 stub 模式 |
| 8 | **不要清空 `extraction_jobs` 表** | `extract_figures/_tables/conflict_strategy` 等 10 列绑定 `extraction_job_to_dict` 契约 |

---

## 12. 监控与维护

### 12.1 主动健康检查

通过 cron 监控（`fd38f8b09c50`，每天 13:00 / 19:00）：

```bash
ssh z203@100.70.30.21 "
  docker ps --format 'table {{.Names}}\\t{{.Status}}' | grep nucpot-prod
  curl -s http://localhost:8003/api/v1/health
  docker exec nucpot-prod-db psql -U nfm -d nfm_db -t -c 'SELECT COUNT(*) FROM dft_calculations;'
  docker logs nucpot-prod-api --since 2h 2>&1 | grep -ciE 'error|exception|traceback'
  uptime
  free -h | head -2
"
```

### 12.2 手动检查清单

```bash
# 1. 容器健康
ssh z203@100.70.30.21 "docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}' | grep nucpot-prod"

# 2. think-stripper 代理
ssh z203@100.70.30.21 "curl -s http://localhost:11499/healthz; echo"

# 3. Ollama 模型
ssh z203@100.70.30.21 "ollama list | grep qwen3"

# 4. socat forwarders（Mac 端）
launchctl list | grep nucpot.socat
lsof -nP -iTCP:8003 -sTCP:LISTEN | head -3
lsof -nP -iTCP:3000 -sTCP:LISTEN | head -3

# 5. 公网测试
curl -sS -m 30 -o /dev/null -w "nucpot.dpdns.org: HTTP %{http_code} (time %{time_total}s)\n" https://nucpot.dpdns.org

# 6. Data integrity
ssh z203@100.70.30.21 "docker exec nucpot-prod-db psql -U nfm -d nfm_db -c '
  SELECT '\\''dft_calculations: '\\'' || COUNT(*) FROM dft_calculations
  UNION ALL SELECT '\\''kg_nodes:        '\\'' || COUNT(*) FROM kg_nodes
  UNION ALL SELECT '\\''data_sources:    '\\'' || COUNT(*) FROM data_sources;
'"
```

### 12.3 已知告警阈值

| 指标 | 阈值 | 动作 |
|------|------|------|
| 容器 restart 计数 > 5/h | 触发回退 | 检查日志，必要时切回 Mac |
| API 错误日志 > 50/h | 触发 SRE 调查 | 看 API logs |
| dft_calculations 数量下降 | DB 损坏 | 立即停止写入，从备份恢复 |
| Ollama 进程消失 | 启动失败 | `systemctl start ollama` |
| think-stripper 进程消失 | LLM 永久 502 | 重启代理 |

---

## 13. 附录：变更日志

### 2026-08-15（v2.0）

- ✅ 添加 §0 版本变更摘要
- ✅ §3.1 架构图更新（V2 5 步 + 关键数据流）
- ✅ §3.2 新增 D5/D6/D7 设计决策
- ✅ §4 缺口状态更新（Phase 1 完成，C1 仍有工作量）
- ✅ §5 计划状态更新（Phase 0/1 完成，Phase 2/3/4 部分）
- ✅ 新增 §10 网络拓扑与生产部署（最关键章节）
- ✅ 新增 §11 已知 Quirks 与禁忌
- ✅ 新增 §12 监控与维护
- ✅ §9 代码索引更新（NFM-2739、ADR 等）

### 2026-08-07（v1.0）

- ✅ 项目目标、技术要求、现状架构
- ✅ 关键设计决策 D1-D4
- ✅ 缺口 C1-C7
- ✅ 开发计划 Phase 0-6
- ✅ 本体管理模块设计

---

*文档基于 2026-08-15 代码库审计 + 生产迁移实战。所有 P0 链接与决策均有可核验的 PR / commit / 文档。*
