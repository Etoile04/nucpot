# ADR-026 — 仓库形态：单仓模块插件化 + 选择性拆仓，否决全量 polyrepo

| Field | Value |
| --- | --- |
| **Status** | Accepted（裁决依据照录：lwj04 2026-11-06 形态选 C、起步选 B；2026-11-11 九问 grilling 全按推荐（§6）；2026-11-11 CTO 评审通过（§7，评审修正 N1–N7）。评审入库：[NFM-5950](/NFM/issues/NFM-5950)） |
| **Date** | 2026-11-06 |
| **Author** | ZCode（评估与起草）· lwj04（裁决：形态选 C、起步选 B，2026-11-06） |
| **Source issue** | [NFM-5950](/NFM/issues/NFM-5950)（评审入库票；CTO 评审全文：[NFM-5949](/NFM/issues/NFM-5949) `#document-evaluation` 文档） |
| **Evidence** | 2026-11-06 仓内实测：代码与测试规模、贡献者构成、CI 墙钟、模块耦合链（见 §1）；评估对话裁决记录（Question Rounds Q1→C、Q2→B） |
| **Related** | [ADR-NFM-2737](../architecture/ADR-NFM-2737-strangler-fig-extraction-dispatch.md)（strangler-fig 先例）、[ADR-NFM-4076](../architecture/ADR-NFM-4076-session-provider-seam.md)（seam 纪律先例）、[ADR-NFM-2081](../architecture/ADR-NFM-2081-commit-issue-reference-enforcement.md)（commit 门）、`.github/workflows/batch1-ci.yml`（NFM-851/922 路径过滤先例）、`docs/architecture/db-evolution.md`（迁移演进） |
| **Precedent (外部)** | NFMD↔nfmd-check 拆仓经验：vendor 复用 ~1500 行受控重复 + 跨仓文件误判（对方仓 AGENTS.md 明文记载） |

---

## 1. Context

### 1.1 动机

网站（NFM-DB）进入多模块并行维护期，需要：功能模块可由多人（人类 + agent 舰队）并行开发维护，避免每次变更都触发全仓 PR 合并队列与全量测试。

### 1.2 实测现状（2026-11-06，main）

- **规模**：`apps/api` ~96k 行 Python + `apps/web` ~83k 行 TS；后端 **461 个测试文件**（约 4,400 个测试函数，其中 272 个文件平铺在 `tests/` 根）+ 50 个 Playwright E2E spec 文件。
- **协作密度**：2026-06 以来 2,200+ commits；两位人类（928 / 654）+ 约 10 个 agent 身份（Release Engineer 146、Hermes 131、NucPot Dev 115、dependabot 76…）；近两个月 532 commits（~9/天）；PR 编号至 #1523。
- **CI 成本**：主 CI（`ci.yml`）每 PR 全量——前端 lint + typecheck + test + **build**，后端全量 API 测试——**单 PR 20–23 分钟**（实测：改一行边缘缓存 TTL 的 PR 22m03s）；merge queue + 必需检查 + `enforce_admins` + code-owner review 强制串行。
- **已有的自救补丁**（均为「CI 粒度 = 全仓」这一根因的局部缓解）：E2E 移出 PR（cron / post-deploy）；batch1/2/3 按路径过滤的专项 CI；heavy-route 边缘缓存 TTL。
- **模块边界现状**：web 路由目录与 40+ API router 一一对应，边界在目录层面已存在；但测试**平铺**在 `apps/api/tests/` 根下，不按模块聚集，无法按路径选测；CODEOWNERS 仅覆盖哨兵脚本，无模块所有权。
- **部署**：单一 docker-compose 整栈；模块变更即整站重发。

### 1.3 模块间业务耦合（否决全量 polyrepo 的核心事实）

依根 `CONTEXT.md` 的领域链：**验证等级 = 势函数 MD 计算结果 vs 验证数据集的偏差**（势函数↔数据集同库互链）；**知识图谱由抽取管线从文献 PDF 构建**，抽取结果同时供结构化入库与语义问答；**双通道录入（网页人工 + agent 自动）共用 schema、来源标注与校对闸门**。这些模块共享同一 PostgreSQL schema、同一套 ORM 模型与手写在 `apps/web/src/lib` 的前后端类型契约（`packages/shared` 现仅承载 i18n 与信封类型，非类型契约）。**与核心域零 schema 耦合的只有博客等内容类模块。**

### 1.4 前车之鉴

NFMD↔nfmd-check 拆仓后：核心校验/比对/单位词表模块 vendor 复用 ~1500 行受控重复（nfmd-check ADR-0001），且已出现「在 NFMD 里找不到那个文件」的跨仓误判（NFMD AGENTS.md 明文记载）。两仓即已产生协调成本。

## 2. Decision

### D1. 否决全量 polyrepo（每功能模块独立成仓）

理由（逐条对本仓实测）：

1. **共享 DB schema 被切断**：势函数↔数据集↔图谱↔抽取同库互链，跨模块 schema 变更加变成 N 仓 N 个 PR + 版本矩阵协调——多仓经典死穴；
2. `packages/shared` 类型契约必须独立版本化，前后端联调成本上升；
3. **agent 舰队成本 ×N**：每仓需 AGENTS.md、commit 门、部署配置、Paperclip 工单路由各一套；两仓时代已有误判先例；
4. CI 分钟为**账号级配额**，拆仓不省；单 PR 时间不变（只是把一条长队变 N 条短队），而高频跨模块链（抽取→KG→数据集）从 1 个 PR 变 N 段串行；团队实际为 2 人 + agent 舰队，协调开销摊不薄。

### D2. 采纳「单仓模块插件化」为主干（modular monolith + plugin modules）

- **模块定义**：module = APIRouter(s) + services + schemas + tests + web routes + CODEOWNERS 所有权，模块间只经 core 接口与显式契约交互。
- **core（共享内核）**：DB 会话（ADR-NFM-4076 seam）、认证/RBAC、来源标注、Pydantic 公共 schema、**全部 DB 迁移**（唯一 schema 所有者）。core 接口变更须过契约测试。
- **后端注册**：Phase 2 起模块以包为单位、经 entry points（`importlib.metadata`）注册 APIRouter；此前维持 `api/v1/__init__.py` 显式聚合。
- **前端**：App Router 路由目录即模块边界（现状已如此）；design system 收敛进 `packages/`。

### D3. 部署单元沿既有缝拆分

web / api / extraction worker（沿 ADR-NFM-2737 已拆的派发缝）/ KG-LightRAG（本已是 sidecar）各自独立构建发布——模块变更不再整站重发。

### D4. 选择性拆仓（本 ADR「C」形态）

仅与核心域**零 schema 耦合**的内容类模块可独立成仓：**博客为首个候选**（Phase 4 按证据触发）；抽取 skill 资产（`nuclear-materials-skills-v4` 一类）保持独立 skill 包。potentials / datasets / kg / auth / extraction 一律留在核心仓。

### D5. 迁移次序（strangler-fig，每步行为可验证、可回退）

| Phase | 内容 | 性质 |
| --- | --- | --- |
| 0 | 模块清单 + 所有权映射（module = router + services + schemas + tests + web routes + 迁移归属） | 文档 |
| **1（先行）** | **测试按模块归位 + CI 路径矩阵（推广 batch1/2/3 模式为全模块矩阵）+ 按目录 CODEOWNERS** | 纯文件移动 + workflow 改造，**行为零变更** |
| 2 | 后端模块包化 + entry points 注册，逐模块迁移 | 渐进重构 |
| 3 | 部署单元拆分（worker、KG sidecar 独立发布） | 部署改造 |
| 4（可选） | 博客独立仓 | 按证据触发 |

### D6. 否决微前端 / 微服务全家桶

2 人 + agent 舰队的团队规模不支持 service sprawl；部署单元数量受控是既定方向（Neo4j→AGE 迁移教训，见 `graph-database-migration-from-neo4j-to-apache-age.md`）。

### D7. 观测指标与 Phase 1 验收线

- 指标：PR 排队时长、单 PR CI 墙钟、跨模块 PR 比例、部署频率、模块级回归率。
- Phase 1 验收线：单模块 PR 的 CI 墙钟 ≤ 全量的 1/3（≈7 分钟）；全量绿基线不退化——载体裁决（2026-11-11 Q8，见 §6）：merge_group 前置全量为主 + 夜跑 cron 为辅。

## 3. Considered & rejected

- **全量 polyrepo**：见 D1，技术可行、工程不划算。
- **微服务 / 微前端**：见 D6。
- **维持现状 + 仅扩 runner 并行度**：不解决根因（CI 粒度 = 全仓、测试平铺、单一部署单元），只稀释症状。

## 4. Consequences

**正向**：直击四个实测根因（CI 粒度、所有权缺位、测试不聚集、单一部署单元）；保留跨模块原子 PR（一条数据链的变更仍是 1 个 PR）；agent 工作流零迁移；迁移渐进可回退。

**成本**：Phase 1 测试归位为大量机械移动（4,436 个测试文件的重新归位需分批）；core 插件契约需长期维护纪律；merge queue 仍在（单 PR 时间下降后吞吐自然上升）。

**风险与缓解**：
- core 接口漂移 → 契约测试守 core 出口（Phase 2 引入）；
- 文件移动斩断 git blame → 约定 `git log --follow` 查史，移动 PR 一律 `[no-issue]` 之外的独立 NFM 工单并在 body 附移动映射表；
- agent 舰队目录习惯变化 → AGENTS.md 同步更新模块地图。

## 5. 与既有决策的关系

- 与 ADR-NFM-2737 / ADR-NFM-4076 **同向**：本 ADR 是既有 seam 纪律从单点（抽取派发、会话管理）到仓库形态的推广，不是新模式；
- 与 `docs/architecture/db-evolution.md`：迁移唯一归属 core，不变；
- 与 ADR-NFM-2081 commit 门：本 ADR 落地各 PR 照常挂 NFM-###；纯移动类 PR 按其口径仍计结构性浪费，须在对应工单内说明理由。

## 6. 裁决记录（2026-11-11，grilling 九问全按推荐）

七个深化候选（C1 模型内核拆分 / C2 conftest 拆解 / C3 路由器深化含 C8 / C4 前端入巢 / C5 导航注册 seam / C6 契约安家 / C7 录入链 contract）经「探索 + 独立复核」工作流产出决策包（4 份复核通过、3 份带保留通过），九条前沿决策全部按推荐裁决：

| # | 裁决 |
|---|---|
| Q1 | 本 ADR 立即启动评审、与工程批次并行；评审期内只放行 C2 步 0–3（不动文件布局），步 4 起 Accepted 为硬前置 |
| Q2 | 每个删除面挂独立 NFM 工单、body 列完整删除清单供评审过目；先删后移（C4 死孪生与误置件约 1000 行、C3 providers 整包与 kg_query_service.py、C6 packages/config） |
| Q3 | 势函数扩展名白名单取并集 31 项，单一事实源放 upload_service；`.dp` 放宽属行为变更，须带 API 测试护栏 |
| Q4 | 湖南大学三支 references 裸串：修数据、类型保持严格（`(Reference \| string)[]` union 移除），走生产数据治理流程 |
| Q5 | shim/兼容层回收闸：CI 计数器 + 「计数归零或满两个 minor 版本（先到者）」双闸 + 删除 PR 前夜跑全量基线；C1 计数范围含第六消费根 apps/mcp-server，直达名字面在测试内动态扫描生成 |
| Q6 | 约定性变更全部入 AGENTS.md/工单 body 显式过目：C4 废止 src/features/ 目录层、C5 导航挪组过 IA 守门（PR 模板先行，CODEOWNERS 落地后转强制）、C6 删 bun.lock 前确认无 bun 工作流协作者 |
| Q7 | C1 core_models 两档方案认可（第二档 unit/classification_level/ontology 并入属设计判断而非 FK 强制，工单明标） |
| Q8 | 全量绿基线载体：merge_group 前置全量为主 + 夜跑 cron 为辅（D7 已同步） |
| Q9 | agent 通道 ExtractionIngestRequest 信封面冻结为红线（权威契约在上游 OntoFuel、permissive 转发为既定语义）；收紧只发生在 mapper 入口经 to_extracted_property 白名单 adapter 归一，先只读盘点差异集 |

**实施前置补丁**（三份「带保留」决策包的复核保留项，开对应工单前须并入）：

- **C1**：消费根计数器范围改为全仓扫描（补 apps/mcp-server/tests/test_tools_integration.py:357 第六根，它不在 apps/api 的 PR CI 范围内）；直达兼容面 ≈96 名字的清单在测试内动态扫描生成，不得硬编码快照。
- **C2**：batch3-ci.yml 的 paths 触发段（:6-27）与测试迁移同批原子修改；步 5b 后收集数闸 432→431 显式注记并更新基线；迁移后的测试文件自带 sys.path 注入（scripts/tests 目录惯例，现 conftest 仅 9 行 collect_ignore）。
- **C7**：Step3a/3c 段归属统一按迁移步骤 5 的段界（归段 2 extract），shape 段 3 内化清单去掉 3a/3c，anchor 句柄经 ctx 跨段——两处相反指示须在拆段 PR 开工前定死。

决策包全文（七候选 findings/形状/迁移步骤/测试故事）见随附探索报告 `ADR-026-exploration-report.md`（同目录，工作流 dwfrun-814cc98d 产出，证据 path:line 均经独立复核）。

## 7. CTO 评审记录（2026-11-11）

**评审人**：CTO · **评审对象**：本 ADR（D1–D7 + §6 九问裁决）、七候选探索报告、第一批工单草案 T1–T6 · **结论**：**采纳，无实质异议**——D1–D7 与 §6 九问逐条通过；关键实测声明（apps/web 83,472 行 TS/TSX、pytest 收集 432 个测试文件、tests/ 根平铺 277 个 .py、api/v1 40 个 router、ci.yml 单 job 全量串行 + merge_group、CODEOWNERS 仅 4 条哨兵规则、T3/T4/T5 删除清单逐一在库）经独立抽查全部吻合。评审全文见父票 [NFM-5949](/NFM/issues/NFM-5949) `#document-evaluation` 文档；本节依 [NFM-5950](/NFM/issues/NFM-5950) 交付要求逐条回填评审修正 **N1–N7**，与 D1–D7、§6 九问同等生效。

**第一批实票映射（草案标签 → 工单号）**：T1 = [NFM-5950](/NFM/issues/NFM-5950)（本 ADR 入库票）、T2 = [NFM-5951](/NFM/issues/NFM-5951)、T3 = [NFM-5952](/NFM/issues/NFM-5952)、T4 = [NFM-5953](/NFM/issues/NFM-5953)、T5 = [NFM-5954](/NFM/issues/NFM-5954)、T6 = [NFM-5955](/NFM/issues/NFM-5955)。下文 N2–N7 的 T 编号均按此映射解读。

- **N1（无异议）**：D1–D7 与 §6 九问逐条通过；关键实测声明经独立抽查全部属实。
- **N2（T4 清单修正·实质）**：`tests/providers/` 实有 **4** 个测试文件（草案误写 3）。`test_external_real_apis.py` import 的是 `nfm_db.services.external_data_sources`（**活模块**，BUG-24/[NFM-3875](/NFM/issues/NFM-3875)，gap_dispatch_service 消费中）——**禁止删除**，随票迁往 `tests/services/`。仅删 3 个 import `services.providers` 的文件（test_base / test_composite / test_openkim_provider，已逐一 grep 确认）。另 `services/providers/` 目录含 `OPENKIM_API.md`，补入删除清单。收集数 delta = **−4**。
- **N3（T5 范围修正·实质）**：`bun.lock` **从未入 git**（`git log --all` 零历史），仅以 untracked 形式存在于操作者本地 checkout（228KB，9-20）。仓内 pnpm-lock.yaml 是唯一 tracked lockfile，「contracts 新依赖只进 pnpm-lock、bun 使用者拿到缺包依赖图」的风险在仓内不成立。T5 缩为：删本地 untracked 文件 + `.gitignore` 增加 `bun.lock` 防再生成入库；「确认无 bun 协作者」前置（Q6）降级为知会项。`packages/config` 确认死包 ✓。
- **N4（T2/T4 交互）**：T2 步 3 的试点簇 providers 恰是 T4 的删除对象。处理：T2 步 3 执行时若该簇已被 T4 删除，试点簇改用 `tests/future/`（4 文件、无 `__init__.py`；开工前同口径亲验零 import main），闸不变。两票保持并行，不设 blocker。
- **N5（收集数纪律）**：432 收集数是六票共享的闸，落地顺序不定 → 每票写明自身 delta（T4 = −4、T6 = +2、其余 = 0），验收断言「落地前计数 + 本票 delta = 落地后计数」，与落地顺序解耦。
- **N6（批次 B 次序约束，防遗漏）**：C3 的**任务名常量表必须先于任何 Phase 2 包移动**（celery task_routes 硬编码路径，先移包会引爆 unregistered task，C3/E3）。此约束不在本批工单内，记入本 §7 评审记录，**批次 B 建票时作为硬前置**。
- **N7（D7 归因纪律）**：T2 步 0 三层基线账本（装配面 ~8s / per-test create_all 常数 / CI 固定开销）必须先于任何 CI 改造合并；7 分钟线不达标时按此归因，**不许未记账就宣布 Phase 1 失败或放宽验收线**。

**生效说明**：本节随 [NFM-5950](/NFM/issues/NFM-5950) 入库；自此 §6/Q1 的大布局移动闸（C2 步 4+、C1 步 2+、C4 前端入巢）解除，N6/N7 作为批次 B 建票的成文前置约束。
