# nucpot 技术架构文档：本体驱动的材料数据抽取与问答平台

**版本**: v1.0
**日期**: 2026-08-07
**作者**: Hermes Agent（基于代码库审计）· lwj04（方向）
**代码基线**: `main` + PR #687（`feat/NFM-1366-p2-parallel-vlm`）
**相关**: NFM-1366 / NFM-2538 / NFM-768 / NFM-54 / NFM-796 / NFM-2013

---

## 1. 项目目标

### 1.1 两条主线

| # | 目标 | 说明 |
|---|---|---|
| **G1** | **本体驱动的高精度抽取** | 利用材料本体（OntoFuel）提高对文献中**文字、图、表**三类载体的材料数据提取准确性 |
| **G2** | **材料问答** | 利用 RAG + 知识图谱实现面向核燃料材料领域的自然语言问答 |

### 1.2 目标的隐含要求

G1 中的"提高准确性"必须可度量，而准确性有两个正交维度：

- **精确率（Precision）**——抽出来的数据是否正确
- **召回率（Recall）**——该抽的数据是否都抽到了

> ⚠️ **当前项目只度量了精确率。** 2026-07-29 交付的"57 个 kg_nodes，96% 通过人工审核"是**精确率**指标。漏抽的数据从未进入分母——数据库里不存在的行，审核界面上看不见。这是 G1 目标下最大的度量盲区。

---

## 2. 技术要求

用户提出的四项要求，构成本架构的设计约束：

| # | 要求 | 解决的问题 |
|---|---|---|
| **R1** | **模块化 pipeline** | 当前 `trigger_extraction()` 是 300 行单体函数，步骤间无边界 |
| **R2** | **每步可存可审** | 中间产物（尤其 chunk）不落地，无法审查、无法单步重跑 |
| **R3** | **数据缺口评估** | 用本体评估数据库中某类材料的性能数据是否存在缺口 |
| **R4** | **驱动下一轮补全** | 缺口转化为可调度的数据收集需求，形成闭环 |

### 2.1 为什么 R1+R2 是其余一切的前提

前期审计发现的每一个缺陷——chunk 拿不到、事务前派发、三层静默异常、60 秒超时、双写无对账——**根因都是同一个**：pipeline 是一条隐式的函数调用链，中间产物不持久化，因此每一步都无法观测、无法重跑、无法审查。

R3/R4 的缺口检测也需要挂载点：**没有 chunk 持久化，gap 检测无从附着**。

---

## 3. 技术架构

### 3.1 现状架构（审计所见）

```
┌──────────────────────────────────────────────────────┐
│                   PDF / 文献输入                       │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│  mineru_vision_extractor.py                          │
│  MinerU 解析 → 图表 refs → VLM 抽取                    │
│  ✅ PR #687: asyncio.gather(concurrency=6) ~50% 加速   │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│  extraction_pipeline.py                              │
│  _chunk_content(20K chars) → list[str] ⚠️ 用完即弃     │
│  → ontofuel_extract() 串行 for-loop ⚠️ 未并发          │
│  ⚠️ prompt 来自 property_catalog 硬编码，非本体          │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│  extraction_to_db_mapper → Postgres                  │
│  materials / properties / extraction_results         │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│  kg_re.py  GraphBuilder → KGNode / KGEdge            │
│  ⚠️ kg_re.py:463 flush() 后、commit() 前派发异步任务     │
└────────────────────────┬─────────────────────────────┘
                         ▼ fire-and-forget（三层静默）
┌──────────────────────────────────────────────────────┐
│  kg_lightrag_sync.py                                 │
│  serialize_build_result() → "[Material] UO2\n- ..."  │
│  ⚠️ 送进 LightRAG 的是 KG 序列化文本，不是原文           │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│  LightRAG sidecar (:9621)                            │
│  容器内部 chunking → entity extraction → KG           │
└──────────────────────────────────────────────────────┘

查询侧（独立路径）:
  kg.py:172 → RAGProviderSelector.query()
               ├─ try  LightRAGProvider → POST /query  ⚠️ 60s 超时
               └─ except → RuleBasedFallbackProvider (PG ts_rank)

本体侧（第三条独立路径，与抽取零交集）:
  nvl_ontology_data.json（前端静态资产）
       → seed_ontofuel.py → KGNode/KGEdge/OntologyIdMap
       → api/v1/ontology.py（4 个 GET，零写入端点）
```

**架构的核心问题**：三条路径（抽取 / 本体 / LightRAG）互不相交。本体站在旁路只做可视化；LightRAG 站在管线末端只做二次索引。**目标 G1 所说的"本体驱动"在代码中不存在。**

### 3.2 目标架构

```
                    ┌─────────────────────────────┐
                    │   本体（可编辑 + 版本化）      │
                    │  KEntityType / KRelationType│
                    │  required_properties        │
                    └──────┬───────────────┬──────┘
                           │ 指导抽取       │ 定义目标
                           ▼               │
┌──────────────────────────────────────────┼──────────┐
│              模块化 Pipeline（5 步）        │          │
│                                          │          │
│  Step 1  parse    → ExtractionFigure     │          │
│                     + parsed_markdown    │          │
│           ↓                              │          │
│  Step 2  chunk    → ExtractionChunk 🆕   │          │
│                     (含 _source_span)    │          │
│           ↓                              │          │
│  Step 3  extract  → ExtractionResult     │          │
│                     + ExtractionGap 🆕   │          │
│           ↓         (漏抽 = 召回率)        │          │
│  Step 4  graph    → KGNode / KGEdge      │          │
│           ↓                              │          │
│  Step 5  index    → LightRAG + track_id  │          │
│                                          │          │
│  每步独立事务 · ExtractionStep 🆕 记录状态  │          │
└──────────────────────┬───────────────────┼──────────┘
                       │                   │
                       ▼                   ▼
              ┌────────────────┐  ┌──────────────────┐
              │  人工审查        │  │  GapScanService   │
              │  精确率 + 召回率  │  │  （换血后）        │
              └────────┬───────┘  └────────┬─────────┘
                       │                   │
                       │                   ▼
                       │        ┌──────────────────────┐
                       │        │ DataCollectionRequest│
                       │        │  🆕 覆盖率缺口         │
                       │        └──────────┬───────────┘
                       │                   │
                       │      ┌────────────┼────────────┐
                       │      ▼            ▼            ▼
                       │  文献检索      DFT 计算    外部库(MP API)
                       │      └────────────┼────────────┘
                       │                   │
                       │                   ▼
                       └───────────► 下一轮 pipeline
                                    （缺口收敛）

问答侧:
  RAGProviderSelector → LightRAG (8s 超时) → PG FTS 降级
```

### 3.3 关键设计决策

#### D1：两张 gap 表，故意不合并

| 表 | 度量 | 语义 | 修复手段 |
|---|---|---|---|
| `extraction_gaps` | **召回率** | 文献里有，我们没抽到 | 改 prompt、重跑抽取 |
| `data_collection_requests` | **覆盖率** | 领域该有，我们库里没有 | 找新文献、算 DFT、查外部库 |

两者都叫 "gap"，但**修复路径完全不同**。合并成一张表会导致 triage 混乱。

#### D2：chunk 存偏移量而非全文

`ExtractionChunk` 存 `source_span_start/end` + 短 preview，全文按需从 `parsed_markdown` 切片还原。

- 成本：一篇 281KB 文献 ≈ 15 chunk × 20KB，1000 篇 ≈ 300MB 纯文本，不可接受
- 收益：溯源从 `_derive_paragraph` 的**关键词反查猜测**变成**偏移量直读**

#### D3：本体版本化是强制项

改本体会改变抽取行为。每个产物必须记 `ontology_version`，否则本体一改，历史数据的可比性立即丧失。

**衍生要求**：coverage 指标必须**按本体版本分层报告**，区分"本体扩充导致的新缺口"与"新材料导致的新缺口"。否则本体一扩，coverage% 断崖下跌，看起来像质量倒退，实际是标准提高了。

#### D4：chunking 策略可插拔

chunk 成为一等公民后，切分策略变成配置项：

| 策略 | 来源 | 适用 |
|---|---|---|
| 当前 `_chunk_content` | 自研，段落+句子边界 | 保底 |
| LightRAG `R` (recursive character) | `lightrag.chunker` | **推荐**，分隔符级联 + token 计长 + `_source_span` |
| LightRAG `V` (semantic vector) | 需 embedding | 不推荐，收益低于成本 |

---

## 4. 目前项目状态与缺口

### 4.1 已具备的能力（地基约 60%）

| 能力 | 载体 | 状态 |
|---|---|---|
| MinerU 多模态解析 | `mineru_vision_extractor.py` | ✅ 已上线，PR #687 加 6 路并发 |
| 图表产物持久化 | `ExtractionFigure` | ✅ 表结构完整 |
| 抽取结果 + 审查 | `ExtractionResult` | ✅ **设计优秀**，含 `review_status`/`reviewed_by`/`source_paragraph`/`source_page`/`source_doi` |
| Job 级状态 | `ExtractionJob` | ✅ status/error/counts/timestamps |
| KG 存储 | `KGNode`/`KGEdge`/`OntologyIdMap` | ✅ |
| RAG 抽象 | `RAGProvider` 协议 + 双 Provider | ✅ 接口干净，仅 2 处调用点 |
| 批量调度 | Celery + `literature_dispatcher` | ✅ 独立队列 |
| 缺口扫描骨架 | `GapScanService` (274 行) | ⚠️ 抽象正确，数据源接错 |

### 4.2 关键缺口（按严重度）

#### 🔴 C1：本体不参与抽取（G1 目标未实现）

`extraction_prompt.py:17` 注入的是硬编码常量：
```python
from nfm_db.core.property_catalog import STANDARD_PROPERTIES, PropertyCategory
```
`PropertyCategory` 是 11 值枚举，还带 `assert len(all_categories) == 11` 锁死。**全文无任何 `KEntityType` / `KRelationType` / OntoFuel 引用。**

`KEntityType.required_properties` 字段存在于模型中，**被零处代码消费**。

#### 🔴 C2：本体不可编辑

`api/v1/ontology.py`（400 行）端点清单：
```
GET  /ontology/corpora/{corpus_id}/graph
GET  /ontology/node/{node_id}
GET  /ontology/search
GET  /ontology/path
POST /ontology/sync          # 同步到 AGE，非编辑
```
**零个写入端点。** 唯一来源是前端静态文件 `apps/web/public/ontology-viewer/data/nvl_ontology_data.json`——改本体要改代码、重新部署、跑 seed。

> 2026-07-24 那次"本体颜色不更新"事故正是此架构的症状：本体是编译期资产，不是运行时数据。

#### 🔴 C3：漏抽不可见（召回率无法度量）

`ExtractionResult` 只能审"抽出来的对不对"，审不了"该抽的没抽出来"。漏抽在数据库里就是不存在的行。

#### 🔴 C4：chunk 产物不落地

PR #687 的 `_chunk_content()` 返回 `list[str]`，串行消费后丢弃。导致：
- chunk 质量无法审查
- 改切分策略必须从 PDF 重解析
- 溯源只能靠 `_derive_paragraph` 关键词反查（2026-07-27 的 281KB FRAPCON 测试正是在验证这个猜测准不准）

#### 🟠 C5：`GapScanService` 三处接错

| 缺陷 | 位置 | 说明 |
|---|---|---|
| target 是 12 条硬编码 | `gap_scan_service.py:27-40` | 代码自陈 *"For now we define a representative set to demonstrate"* |
| 比对错的表 | `_get_covered_tuples()` | 查 `RefGapFillStaging`（暂存表）而非 `materials`/`properties` 主表——**衡量的是"尝试填过什么"而非"实际有什么"，逻辑是反的** |
| 优先级是玩具 | `_compute_priority()` | `{"U":1,"UO2":2,"Zr":3}` 硬编码字典，ATF 候选策略未体现 |

#### 🟠 C6：LightRAG 集成两处缺陷

1. **事务前派发**（`kg_re.py:444-463`）：`flush()` 后、commit 前创建 `asyncio.Task`。若后续 rollback，Postgres 无数据而 LightRAG 有——**幽灵实体**，无补偿机制。
2. **60 秒共享超时**（`lightrag_client.py:25`）：`_DEFAULT_TIMEOUT = 60.0` 被 ingest 和 query 共用，`RAGProviderSelector` 未覆盖。用户搜索要等满 60 秒才降级到 PG FTS。**这是长期"LightRAG query 超时"报告的真正成因**——功能上 fallback 正确，体验上等于挂了。

#### 🟡 C7：pipeline 无步骤边界

`trigger_extraction()` ~300 行单体函数，无法单步重跑、无法定位失败阶段。

### 4.3 当前 PR 状态

| PR | 标题 | 状态 |
|---|---|---|
| #686 | `fix(NFM-2538): monitoring for daily-reflection recurring issues` | ✅ **已 MERGED** |
| #687 | `perf(NFM-1366): parallel VLM extraction — 50% speedup` | 🟢 OPEN / **mergeState=CLEAN（可合并）** |

> 8/6 两个 PR 的多个 CI 红是 GitHub Actions 基础设施故障（`Failed to resolve action download info: Service Unavailable`，job 在下载 action 阶段就挂了，一行测试没跑），重跑后全绿。

---

## 5. 开发计划

### Phase 0：止血（独立，可立即发布）

| 项 | 内容 | 规模 |
|---|---|---|
| 0.1 | 拆分 LightRAG 超时：`_QUERY_TIMEOUT = 8.0` / `_INGEST_TIMEOUT = 300.0` | ~50 行 |
| 0.2 | `RAGProviderSelector` 显式传 query timeout | |

**收益**：立即把"搜索 60 秒白屏"变成"8 秒降级到 PG"。不动架构，与后续方案完全解耦。

### Phase 1：地基（模块化 + 可存可审）

| 项 | 内容 | 新增表 |
|---|---|---|
| 1.1 | `ExtractionStep` 表 + 步骤状态机 | `extraction_steps` |
| 1.2 | **自研 chunker**（分隔符级联 + overlap + token 计价）+ `ExtractionChunk` 落库（带 `_source_span`） | `extraction_chunks` |
| 1.3 | `trigger_extraction` **绞杀式**拆成 5 步 | — |
| 1.4 | `_fire_lightrag_ingest` 移到 commit 之后 | — |
| 1.5 | `ingest()` 返回并持久化 `track_id`（双写对账） | — |
| 1.6 | 每步 API：`GET /jobs/{id}/steps/{name}` | — |
| 1.7 | 单步重跑：`POST /jobs/{id}/steps/{name}/rerun` | — |

**同时解掉**：C4、C6、C7，以及 PR #687 遗留的 chunk overlap 缺失、char vs token 计价错配、跨 chunk 无去重、chunk 串行未并发。

### Phase 2：本体成为运行时数据（本体管理模块，详见 §8）

| 项 | 内容 |
|---|---|
| 2.1 | 新增 `domain_expert` 角色（Alembic 迁移：改 CHECK 约束 + PG enum）+ `require_domain_expert` |
| 2.2 | `OntologyVersion` / `OntologyChangeLog` / `ReExtractionRequest` 三张表 |
| 2.3 | `KEntityType`/`KRelationType` 增加 `ontology_version_id` 外键 |
| 2.4 | 版本管理 API（列表/详情/激活/归档）+ 自动版本号规则 |
| 2.5 | 上传/下载 API（JSON / OWL / Turtle） |
| 2.6 | 在线编辑 API（增删改实体/关系类型，自动创建新版本 + changelog） |
| 2.7 | 重抽候选推荐 + 批量重抽 API（决策 4） |
| 2.8 | 前端三页：版本列表 / 详情编辑 / 重抽选择 |
| 2.9 | 现有 `nvl_ontology_data.json` 导入为 v1.0；`ontology-viewer` 数据源切到 API |

### Phase 3：本体驱动抽取（G1 核心）

| 项 | 内容 |
|---|---|
| 3.1 | `build_extraction_system_prompt()` 改为 async + session-aware |
| 3.2 | 动态注入 `KEntityType` / `KRelationType` / `required_properties` |
| 3.3 | 抽取后用 `KRelationType.source_types`/`target_types` 校验结果 |

### Phase 4：召回率可见

| 项 | 内容 | 新增表 |
|---|---|---|
| 4.1 | `ExtractionGap` 表 + 本体必需属性的漏抽检测 | `extraction_gaps` |
| 4.2 | 漏抽审查 UI（复用现有 review 组件） | — |

### Phase 5：覆盖率可见（R3）

| 项 | 内容 | 新增表 |
|---|---|---|
| 5.1 | `GapScanService` 换血：target 来自本体 | — |
| 5.2 | `_get_covered_tuples()` 改查 `materials` ⋈ `properties` 主表 | — |
| 5.3 | 优先级重构：本体权重 + ATF 候选标记 + 引用频次 | — |
| 5.4 | `DataCollectionRequest` 持久化 | `data_collection_requests` |
| 5.5 | 本体版本升级时，`wont_fix` 请求按新版 `required_properties` **自动重开**（决策 5） | — |

### Phase 6：闭环（R4）

| 项 | 内容 |
|---|---|
| 6.1 | gap → 文献检索路径调度 |
| 6.2 | gap → DFT 计算路径调度（对接 NFM-1540 HPC 管线） |
| 6.3 | gap → 外部库路径调度（MP API，对接 Layer 1/2 策略） |
| 6.4 | `request.status` 生命周期回写 + 收敛度量 |

### 依赖关系

```
Phase 0 ──────────────────────────────► （独立，随时可发）

Phase 1 ──► Phase 2 ──► Phase 3 ──┬──► Phase 4 ──┐
                                   │              ├──► Phase 6
                                   └──► Phase 5 ──┘
```

---

## 6. 优先事项

### P0（本周）

| # | 事项 | 理由 |
|---|---|---|
| 1 | **Merge PR #687** | mergeState 已 CLEAN，P2/P3 主体成果 |
| 2 | **Phase 0：拆分超时** | ~50 行，立即消除"搜索 60 秒"，零架构风险 |
| 3 | **在 NFMD 公司创建项目 `NFMDI2` + Epic** | 已决策。后续所有 PR 受 commit-ref-gate 约束，必须带该项目下的真实 issue 号 |

### P1（接下来 2 周）

| # | 事项 | 理由 |
|---|---|---|
| 4 | **Phase 1.1 + 1.2**：`ExtractionStep` + `ExtractionChunk` | 一切的挂载点。**没有它，Phase 4/5 无从下手** |
| 5 | **Phase 1.4**：修事务前派发 | 数据一致性缺陷，越晚修积累的幽灵实体越多 |
| 6 | **Phase 1.3**：绞杀式拆分 `trigger_extraction` | 需在新表落地后进行 |

### P2（1 个月内）

| # | 事项 |
|---|---|
| 7 | Phase 2：本体版本化 + 写入 API |
| 8 | Phase 3：本体 → prompt 注入 |

### P3（视 P0-P2 结果排期）

| # | 事项 |
|---|---|
| 9 | Phase 4：`ExtractionGap` |
| 10 | Phase 5：`GapScanService` 换血 |
| 11 | Phase 6：闭环调度 |

### 优先级判断依据

- **Phase 0 排在最前**，不是因为最重要，而是因为**成本最低、收益最直接、与其他一切解耦**
- **Phase 1 是真正的关键路径**。R2/R3/R4 三项要求全部依赖它
- **Phase 3 才是 G1 的核心**，但它必须等 Phase 2（本体可编辑）——否则"本体驱动"驱动的是一份改不动的静态 JSON

---

## 7. 其它建议

### 7.1 工程风险与缓解

| 风险 | 依据 | 缓解 |
|---|---|---|
| 重写 `trigger_extraction` 打断管线 | 2026-07-27 "整条链路被 5 层 bug 逐个阻断，每修一层才暴露下一层" 就发生在这块代码 | **绞杀者模式**：新建 `pipeline/steps/` 逐步搬迁，老函数保留为兼容入口，feature flag 切换 |
| Alembic 迁移头冲突 | 有 `027_merge_heads_011_and_026` 前科；当前 53 个 migration 文件 | 新增迁移前先确认 head 唯一；4 张新表**逐个**加，不批量 |
| 存储成本失控 | 1000 篇 × 15 chunk × 20KB ≈ 300MB 纯文本 | D2：只存 span + preview |
| 本体扩充导致指标断崖 | coverage% 会因标准提高而下跌 | D3：按本体版本分层报告 |
| 双写不一致 | Postgres 与 LightRAG 独立失败 | Phase 1.5 记 `track_id`；每步独立事务使对账成为可能 |

### 7.2 已否决的方案（记录以免重复讨论）

| 方案 | 否决理由 |
|---|---|
| **用 LightRAG sidecar 做 chunking** | **技术上不可行**。核对上游 `document_routes.py` 全部路由（`/text` `/texts` `/upload` `/scan` `/paginated` `/pipeline_status` `/track_status/{id}` 等）——**没有任何端点返回 chunk**。chunking 在容器内部完成并直接喂给 entity extraction，对外契约是"文档进、答案出" |
| **Fork LightRAG 加 `/chunks` 端点** | 项目 pin `lightrag-hku[api]>=1.0.0`；上游近期刚重构 chunking（`operate.py` → `chunker/` 包，含 F/R/V/P 四策略）。fork 维护成本超过收益 |
| **上 V（语义向量）chunking** | 需额外 embedding 调用 + `langchain-experimental`（LightRAG 自己都要 pin `<1` 并写 drift guard 防上游私有 API 变动）。对 MinerU 输出的结构化 Markdown，收益低于 R 策略 |
| **只修单点 bug，不做模块化** | C3/C4/C5 是结构性问题——它们**存在的原因**就是中间产物不落地。点修复不会让漏抽可见，也不会让步骤可重跑 |

### 7.3 chunking 实现参考（自研，不引依赖）

按决策 2，**不引入 `lightrag-hku` / `langchain-text-splitters`**，但 LightRAG 的 `chunker/` 包（F/R/V/P 四策略）是成熟参考实现，值得照搬其设计而非代码：

| 借鉴点 | LightRAG 中的体现 | 我方自研落点 |
|---|---|---|
| 统一契约 | `(tokenizer, content, chunk_token_size, *, ...)` → `list[dict]` | `(content, max_tokens, *, opts)` → `list[Chunk]` |
| 源偏移 | `_source_span: {"start","end"}` | 同名字段，直接落 `ExtractionChunk` |
| fail-closed | `_window_step()` 在 `overlap >= size` 时 `raise ValueError` | 同样处理，避免静默丢段 |
| 分隔符级联 | `["\n\n", "\n", " ", ""]` 递归下探 | 增加 `"\n## "` 优先级（MinerU Markdown 带标题） |
| 硬上界兜底 | V 策略超限片段回落到 R 再切 | 任何策略的超限产物强制二次切分 |
| 降级链 | V→R、P→R，每层明确 fallback | 结构感知失败 → 段落 → 句子 → 硬切 |

> 参考源：`https://github.com/HKUDS/LightRAG/tree/main/lightrag/chunker`
> （`token_size.py` / `recursive_character.py` / `semantic_vector.py` / `paragraph_semantic.py`）

### 7.4 已决策事项（2026-08-07 lwj04 确认）

| # | 问题 | **决策** | 影响 |
|---|---|---|---|
| 1 | Epic 归属 | **NFMD 公司新开项目 `NFMDI2`**，新建 Epic | 所有 commit subject 使用该项目下的新 issue 号 |
| 2 | chunking 默认策略 | **自研**，参考 LightRAG 的 chunking 方法 | 不引入 `lightrag-hku` / `langchain-text-splitters` 依赖 |
| 3 | 本体编辑权限 | **限定 `domain_expert` 角色**（NFMD 本体专家）；网站端支持**下载本体文件 / UI 查看编辑 / 上传新本体文件** | 需新增角色 + 三类端点，见 §8 |
| 4 | 版本升级的历史处理 | **允许用户选择对哪些历史文献重抽**，需提供文献列表选项 | 需建 re-extraction 队列 + 文献选择 UI，见 §8 |
| 5 | `wont_fix` 的收集需求 | **新本体版本重新要求该属性时，自动重开** | `DataCollectionRequest` 需按 `ontology_version` 重新评估 |

#### 决策 2 的实施说明（自研 chunking）

不引依赖，但照搬 LightRAG 已验证的设计要点：

| 借鉴点 | 说明 |
|---|---|
| 分隔符级联 | `["\n## ", "\n\n", "\n", ". ", " ", ""]` 递归下探。MinerU 输出的 Markdown 天然带 `#` 标题，按标题切比按段落切更贴合 |
| 统一契约 | `(content, max_tokens, *, strategy_opts) -> list[Chunk]`，策略可插拔 |
| `_source_span` | 每个 chunk 携带原文字符偏移 `{start, end}` |
| fail-closed | `overlap >= size` 时直接 `raise ValueError`，不让 `range()` 静默返回空序列丢整段 |
| 硬上界兜底 | 任何策略产出的超限片段，强制再切一次 |

同时修复 PR #687 `_chunk_content` 的既有缺陷：
- 硬切分支的内容丢失风险（需补 property test 断言 `"".join(chunks)` 覆盖原文）
- 零 overlap（跨边界属性被腰斩），默认 200 字符
- char 计价 vs token 预算错配（中文≈1:1，英文≈4:1，同样 20K 字符中文会爆 context）
- 跨 chunk 结果无去重
- chunk 串行 await（复用 `_process_figures_parallel` 的 `asyncio.gather` + `Semaphore` 模式）

#### 决策 3 的现状阻碍

`models/user.py` 当前角色枚举只有三个值，且被 **DB CHECK 约束**锁死：

```python
# models/user.py:69
"blog_role IN ('admin', 'editor', 'reviewer')", name="check_blog_role"
```

`blog_role_enum`（migration 001 创建的 PG enum）也需同步扩展。新增 `domain_expert` 必须走 Alembic 迁移，同时改 CHECK 约束和 enum 类型——**不能只改 Python 枚举**。

`core/auth.py` 已有 `RequireRole` 依赖注入模式（`require_admin` / `require_reviewer` / `require_admin_or_reviewer`），新增 `require_domain_expert` 可直接复用。

---


### 7.5 度量建议

建议在 Phase 4/5 落地后建立以下指标，并**按本体版本分层**：

| 指标 | 定义 | 数据源 |
|---|---|---|
| 精确率 | approved / (approved + rejected) | `extraction_results.review_status` |
| 召回率 | 1 − open_gaps / expected_properties | `extraction_gaps` |
| 覆盖率 | covered_tuples / target_tuples | `data_collection_requests` |
| 缺口收敛速度 | Δ(filled) / 周 | `data_collection_requests.resolved_at` |
| 单步失败率 | failed / total per step | `extraction_steps` |

---

## 8. 本体管理功能模块（新增）

> 对应决策 3 与决策 4。这是 Phase 2 的具体化，独立成节因为它是一个完整的用户可见功能。

### 8.1 功能范围

| 能力 | 说明 |
|---|---|
| **列表展示** | 表格展示本体文件：名称、版本、描述、创建者、创建时间、状态（active/archived） |
| **选择激活** | 用户可选择采用哪个本体文件作为当前生效版本 |
| **在线查看** | UI 展示本体结构（实体类型、关系类型、required_properties） |
| **在线编辑** | `domain_expert` 可在 UI 中增删改实体/关系类型 |
| **下载** | 导出本体文件（JSON / OWL / Turtle） |
| **上传** | 上传新本体文件，校验后入库 |
| **自动编版本号** | 任何更新自动递增版本号，不覆盖历史 |
| **重抽选择** | 版本升级后，弹出文献列表供用户勾选需重抽的文献 |

### 8.2 数据模型

```
OntologyVersion                        # 本体版本主表
  id, name, version, description,
  status (draft|active|archived),
  source (upload|ui_edit|seed),
  file_hash, file_path,
  entity_type_count, relation_type_count,
  created_by (FK users), created_at,
  activated_at, parent_version_id      # 版本谱系

OntologyChangeLog                      # 变更审计
  id, ontology_version_id,
  change_type (add|modify|delete),
  target_type (entity_type|relation_type|property),
  target_name, before(jsonb), after(jsonb),
  changed_by, changed_at

ReExtractionRequest                    # 决策 4 的载体
  id, ontology_version_id,
  data_source_id (FK data_sources),
  status (pending|running|completed|failed|skipped),
  requested_by, requested_at,
  job_id (FK extraction_jobs),         # 重抽产生的新 job
  diff_summary(jsonb)                  # 新旧结果差异
```

`KEntityType` / `KRelationType` 需增加 `ontology_version_id` 外键，使本体内容与版本绑定。

### 8.3 API 端点

```
# 版本管理
GET    /api/v1/ontology/versions                    列表（名称/版本/描述/状态）
GET    /api/v1/ontology/versions/{id}               详情
POST   /api/v1/ontology/versions/{id}/activate      设为当前生效版本
POST   /api/v1/ontology/versions/{id}/archive       归档

# 文件上传下载
GET    /api/v1/ontology/versions/{id}/download      下载（?format=json|owl|ttl）
POST   /api/v1/ontology/versions/upload             上传新本体文件

# 在线编辑（自动创建新版本）
POST   /api/v1/ontology/entity-types                新增实体类型
PATCH  /api/v1/ontology/entity-types/{id}           修改（含 required_properties）
DELETE /api/v1/ontology/entity-types/{id}
POST   /api/v1/ontology/relation-types
PATCH  /api/v1/ontology/relation-types/{id}
DELETE /api/v1/ontology/relation-types/{id}
GET    /api/v1/ontology/versions/{id}/changelog     变更审计

# 重抽（决策 4）
GET    /api/v1/ontology/versions/{id}/reextract/candidates
                                                     受影响文献列表（含影响原因）
POST   /api/v1/ontology/versions/{id}/reextract     提交勾选的文献批量重抽
GET    /api/v1/ontology/reextract/{request_id}      重抽进度与差异
```

**权限**：所有写操作要求 `domain_expert`（或 `admin`）；读操作沿用现有认证。

### 8.4 版本号规则

自动编号，语义化：

| 变更类型 | 版本递增 | 示例 | 是否触发重抽提示 |
|---|---|---|---|
| 新增实体/关系类型 | minor | 1.2 → 1.3 | ✅ 是 |
| 修改 `required_properties` | minor | 1.3 → 1.4 | ✅ 是 |
| 删除实体/关系类型 | **major** | 1.4 → 2.0 | ✅ 是（且需二次确认） |
| 仅改描述/标签/颜色 | patch | 1.4 → 1.4.1 | ❌ 否 |

> patch 级变更不影响抽取行为，不弹重抽提示——避免噪音。

### 8.5 重抽候选文献的推荐逻辑

`GET /reextract/candidates` 返回时应标注**影响原因**，而非平铺全部文献：

| 原因 | 判定 |
|---|---|
| `new_required_property` | 新版本给某实体类型加了必需属性，该文献抽取过此类型实体 |
| `new_entity_type` | 新增实体类型，该文献的 chunk 中出现过相关关键词 |
| `relation_constraint_changed` | 关系类型的 `source_types`/`target_types` 变化，影响已建边 |
| `has_open_gaps` | 该文献已有未解决的 `extraction_gaps` |

默认按影响程度排序，让用户先勾选高价值的，而不是无差别全量重抽（一篇 281KB 文献重抽成本可观）。

### 8.6 前端页面

```
/admin/ontology                    本体版本列表页
  ├─ 表格：名称 | 版本 | 描述 | 状态 | 创建者 | 创建时间 | 操作
  ├─ 操作：查看 / 编辑 / 下载 / 激活 / 归档
  └─ 顶部：上传新本体文件

/admin/ontology/{id}               本体详情/编辑页
  ├─ Tab 1：实体类型（可编辑表格，含 required_properties）
  ├─ Tab 2：关系类型（含 source_types / target_types 约束）
  ├─ Tab 3：图谱可视化（复用现有 ontology-viewer 组件）
  └─ Tab 4：变更历史（changelog）

/admin/ontology/{id}/reextract     重抽选择页
  ├─ 受影响文献列表（含影响原因标签 + 排序）
  ├─ 批量勾选 + 全选/反选
  └─ 提交后跳转进度页
```

现有 `apps/web/public/ontology-viewer/` 的可视化组件可复用，但**数据源要从静态 JSON 改为 API**。

### 8.7 迁移路径：从静态 JSON 到运行时数据

当前本体唯一来源是 `apps/web/public/ontology-viewer/data/nvl_ontology_data.json`。迁移分三步：

1. **导入**：把现有 JSON 作为 `OntologyVersion v1.0`（`source='seed'`）导入数据库
2. **切换**：`ontology-viewer` 组件数据源改为 `GET /api/v1/ontology/versions/{active}/graph`
3. **废弃**：静态 JSON 保留为只读备份，不再作为运行时来源

> 2026-07-24 "本体颜色不更新" 事故的根治点就在第 2 步——数据从编译期资产变成运行时数据后，改本体不再需要重新部署。

---

## 9. 附录：关键代码索引

| 文件 | 行 | 说明 |
|---|---|---|
| `services/extraction_pipeline.py` | — | `trigger_extraction()` 单体函数；`_chunk_content()`（PR #687） |
| `services/extraction_prompt.py` | 17 | 硬编码 prompt 来源（缺口 C1） |
| `services/mineru_vision_extractor.py` | — | MinerU + VLM，PR #687 并发化 |
| `api/v1/ontology.py` | — | 只读端点（缺口 C2） |
| `services/seed_ontofuel.py` | 42-49 | 本体来自前端静态 JSON |
| `services/gap_scan_service.py` | 25-40, 144-153 | 硬编码 target + 错误的 covered 查询（缺口 C5） |
| `services/kg_re.py` | 444-463 | 事务前派发（缺口 C6.1） |
| `services/lightrag_client.py` | 25 | 共享 60s 超时（缺口 C6.2） |
| `services/rag_provider.py` | 321-335 | `RAGProviderSelector` 降级逻辑 |
| `services/kg_lightrag_sync.py` | 208 | 送入 LightRAG 的是 KG 序列化文本 |
| `models/extraction_result.py` | — | 审查字段设计范本，可复刻给 gap |
| `docs/architecture/ADR-NFM-796-review-provenance.md` | — | 审查数据模型的既有 ADR |

---

*文档基于 2026-08-07 代码库审计。所有缺口均附可核验的文件与行号，便于独立复核。*
