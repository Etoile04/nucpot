# ADR-026 第一批工单草案（2026-11-11）

> 素材来源：`docs/adr/ADR-026-exploration-report.md`（七候选决策包，证据 path:line 经独立复核）+ ADR-026 §6 裁决记录（九问全按推荐）。
> NFM-### 于建票时分配；建票顺序建议 = 下文编号顺序。六票全部满足 Q1「评审期内可动」口径（不动文件大布局）或 Q2 删除口径（先删后移、清单过目）。

---

## T1 · 评审票：ADR-026 仓库形态决策

**标题草案**：评审：ADR-026 仓库形态——单仓模块插件化 + 选择性拆仓（附 2026-11-11 九问裁决）

**类型**：决策评审票（产出 = Accepted/Rejected/修改后接受，非代码变更票）

**评审材料**：
- `docs/adr/ADR-026-NFM-5950-modular-monolith-pluginization.md`（D1–D7 + §6 裁决记录 + §7 CTO 评审记录；原文件名 `ADR-026-modular-monolith-pluginization.md`，Accepted 入库时按 T1 AC 改名）
- `docs/adr/ADR-026-exploration-report.md`（七候选决策包：C1 模型内核 / C2 conftest / C3 路由器含 C8 / C4 前端入巢 / C5 导航注册 / C6 契约安家 / C7 录入链 contract；4 份复核通过、3 份带保留，保留项已并入 §6「实施前置补丁」）

**评审要点**：D1 否决全量 polyrepo 的四条论据（共享 schema 切断 / shared 契约版本化 / agent 舰队成本 ×N / CI 分钟账号级）；D5 Phase 次序（strangler-fig）；D7 验收线 ≈7 分钟与 Q8 已定载体（merge_group 前置为主 + 夜跑为辅）；§6 九问逐条。

**AC**：
1. D1–D7 与 §6 九问逐条过目，异议以评论记录在本票（带编号，便于回填 ADR）；
2. Accepted 后：文件改名 `ADR-026-NFM-<本票号>-modular-monolith-pluginization.md`、Status → Accepted、commit（subject 挂本票号）；
3. 修改后接受：修改清单回写 ADR 草案后重新过目；
4. Rejected：记录理由，ADR 归档并释放全部下游票。

**阻塞关系**：C2 步 4+ 以本票 Accepted 为**硬前置**（Q1）；建议 C1 步 2+、C4 入巢移动等一切大布局移动同以 Accepted 为前置（同因：布局不可逆，闸门口径随评审变动作废）。

---

## T2 · 实施票：C2 步 0–3（评审期内放行批）

**标题草案**：C2 步 0–3：测试装配三层化先行——基线分账 + support 核心层 + create_app 工厂 + providers 簇试点

**类型**：实施（步 0/1/3 机械、步 2 行为；**不动文件布局**，Q1 放行口径）

**依据**：探索报告 C2 节（shape 三层 interface / I1–I5 不变式 / 迁移步骤 0–3）

**步骤**：
1. **步 0 基线三层分账**（机械）：记录 (a) 全量收集数 432 + 8767 collected（collect-only ≈8s = 装配面贡献）；(b) `sorted(Base.metadata.tables)` 快照；(c) 近 12 run CI 墙钟（957–1634s，中位 ~1172s）。后续 7 分钟线不达标按「装配面 / per-test create_all 常数（186 文件 × function 级全库建表，主嫌疑层）/ CI 固定开销」三层归因。
2. **步 1 support 核心层**（机械）：环境钉子（conftest:18-25）、`_replace_jsonb`/`_strip_dangling_fks`/`_safe_create_all`/076 视图 DDL（:59-180）、db_session/db/pg_session 原语（:342-450）、角色 fixtures（:471-556）移入 `tests/support/`；根 conftest 收缩为 re-export + `pytest_configure`（marker 单点注册）+ 三 autouse 原样保留（:183-339，owner 显式：前两个后续随全量 app 簇、`_clear_v2_flag_cache` 留核心层）+ scripts/ sys.path 注入**暂保留**（消费方 test_phase_gate.py 15 行 import，batch3-ci.yml:105 正在 PR 级跑；其迁移属步 5b，不在本票）。
3. **步 2 create_app 工厂**（行为）：main.py 加 `create_app(routers=ALL, middleware=..., handlers=...)`，模块级 `app = create_app()`（:146-357 装配序变数据）；52 个 import 方与 uvicorn 入口零改动。
4. **步 3 providers 簇试点**（机械）：先补 `tests/providers/__init__.py`（providers 是 5 个无 `__init__.py` 簇之一，亲验），跑收集数闸确认导入模式不跳变；随后加簇 conftest（re-export 核心 fixtures + `create_app(router 子集)` 簇级 client）。providers/ 现有 4 文件零 import main（亲验），「簇内不 import 全局 main」闸现状天然成立。

**AC**：
- 收集数闸全程 432（8767 collected 口径）；
- 步 1 后全量绿且 432 文件零改动；
- 步 2 后 openapi 路径/中间件序/异常处理器集与现单例快照等价（I5）；
- 步 3 后 providers 簇绿 + `rg 'nfm_db\.main' tests/providers/` 零命中；
- no_auto_auth 28 文件实跑绿（autouse 语义 E1 探针）。

**依赖**：无（步 4+ 以 T1 Accepted 为硬前置）。

---

## T3 · 删除票 A：C4 前端死代码（完整清单）

**标题草案**：删除：前端死孪生 ×2 + 孤立死文件 ×4 + 连带 lib 对（≈1000 行，全清单过目）

**类型**：实施·机械删除（Q2 口径：先删后移，清单显式过目）

**完整删除清单**：

| # | 文件 | 行数 | 死因（亲验） |
|---|---|---|---|
| 1 | `apps/web/src/app/materials/[id]/graph/MaterialGraphView.tsx` | 288 | 死孪生#1：全仓唯一 import 是自己的测试；路由页实际渲染 `components/materials/MaterialSubgraphView`；生于 eea5901c1（NFM-1283/PR#161），出生即孤儿 |
| 2 | `apps/web/src/app/materials/[id]/graph/__tests__/MaterialGraphView.test.tsx` | — | 随 #1 删 |
| 3 | `apps/web/src/lib/kg-api.ts` | — | 唯一消费者是 #1（:20），与 #1 同 commit 生 |
| 4 | `apps/web/src/app/kg/nodes/[type]/[id]/KgNodeDetailContent.tsx` | 435 | 死孪生#2：零 import（唯一非测试提及是 kg-node-theme.ts:5 注释）；路由页渲染同目录 `NodeDetailContent`；生于 705a172fa（NFM-1337/PR#164），与 #1 同属 KG endpoint 前端半成品工单族 |
| 5 | `apps/web/src/app/kg/nodes/[type]/[id]/KgNodeDetailContent.test.tsx` | — | 随 #4 删 |
| 6 | `apps/web/src/lib/kg-explore-api.ts` | — | 全仓零引用（生于 ac62bbff3/NFM-1376/PR#172，出生当天即被 kg-graph-api 取代） |
| 7 | `apps/web/src/lib/kg-explore-api.test.ts` | — | 随 #6 删 |
| 8 | `apps/web/src/app/admin/blog/components/ReviewBadge.tsx` | — | 零消费方（rg 排除自身零命中） |

**注释同步 6 处**（防 stale 引用）：`hooks/useGraphView.ts:3,21`、`hooks/__tests__/useGraphView.test.tsx:8`、`components/review/LiteratureGraphLayout.tsx:32`、其测试 `:32`、`components/materials/MaterialSubgraphView.tsx:116`、`lib/kg-node-theme.ts:5`。

**AC**：
1. 上表 8 文件 + 6 处注释同步删除/更新后：`pnpm lint && pnpm typecheck && pnpm test && pnpm build` 全绿；
2. `rg -l 'MaterialGraphView|KgNodeDetailContent|kg-explore-api|@/lib/kg-api|ReviewBadge'` 除历史注释外零命中；
3. 路由 URL 零变化（e2e-cron 冒烟：/materials/[id]/graph、/kg/nodes）。

---

## T4 · 删除票 B：C3 孤儿 service 与 providers 假 seam

**标题草案**：删除：kg_query_service.py（502 行孤儿）+ services/providers/ 整包（零生产消费者）

**类型**：实施·机械删除（Q2 + C3 迁移步 11 处决=删除 + 不变式 I5「providers 不得三态长存」）

**完整删除清单**：

| # | 目标 | 死因（亲验） |
|---|---|---|
| 1 | `apps/api/src/nfm_db/services/kg_query_service.py`（502 行） | 孤儿：src 无任何 router import 它（NFM-858 建缓存+超时+指标查询 service；端点 NFM-1142 重构时摘除，service 遗留） |
| 2 | `apps/api/tests/test_kg_query_api.py` | 整文件 skip，头注释自认「/api/v1/kg/query/* endpoints were removed (NFM-1142)」 |
| 3 | `apps/api/src/nfm_db/services/providers/` 整包（`__init__/base/local/openkim/composite`） | 零生产消费者：src 内仅包内 5 文件互引，唯一外部消费者是 tests/providers/ 3 文件；`base.py:13-17` 自认「Mirrors the existing list_potentials parameter set」，`local.py` 是 `select(Potential)` 平行实现（不调 potential_service）——白名单之外第二处查询逻辑漂移源 |
| 4 | `apps/api/tests/providers/` 3 个测试文件 | providers 包唯一消费者，随包删 |

**AC**：
1. 删除后 `ruff check` + 全量 pytest 绿；
2. `rg -n 'kg_query_service|services\.providers|from nfm_db\.services\.providers' apps/ scripts/` 零命中；
3. 「router 内 select 计数器」基线不升（若 T6 已落地，引用其基线值）。

**附注**：C3 后续 kg 查询 service 为**新建**（以「查询能力」为 interface，勿照搬被摘除的 kg_query_service 形状——NFM-1142 重演风险见探索报告 C3 节 E2）。

---

## T5 · 删除票 C：C6 死包与冗余 lockfile

**标题草案**：删除：packages/config 死包 + bun.lock（前置：确认无 bun 工作流协作者）

**类型**：实施·机械删除 + 约定成文（Q2 + Q6）

**删除清单与前置**：

| # | 目标 | 依据 | 前置 |
|---|---|---|---|
| 1 | `packages/config/`（仅一个 package.json：@nfm-db/config 0.1.0 private，零源码） | 全仓零引用（rg 仅命中其自身 package.json） | 无 |
| 2 | `bun.lock`（228KB） | 双 lockfile 并存，`package.json:19` 声明 packageManager pnpm@9.15.4，CI 全 pnpm；contracts 新增 devDependency 只进 pnpm-lock，bun 使用者拿到缺包依赖图 | **建票后先评论确认无在用 bun 工作流的协作者**（Q6），确认后删；AGENTS.md 记「包管理器仅 pnpm」 |

**明确排除（防误删）**：`packages/shared`（活契约：i18n 231 行 + 信封类型，4 个消费点）、`packages/skills-catalog`（ADR-016 §2.7 技能 git SHA pin 锁，`check_skill_pin.py` CI fails closed 消费——活契约非死包）。

**AC**：
1. `rg -l '@nfm-db/config'` 全仓零命中；
2. `pnpm install && pnpm -r typecheck && pnpm -r test` 全绿；
3. AGENTS.md 增补「包管理器仅 pnpm」一行。

---

## T6 · 实施票：C1 护栏先行（净新增，不动布局）

**标题草案**：C1 护栏先行：metadata 快照 + 公共面双面测试 + 全仓六根聚合 import 计数器

**类型**：实施·净新增文件（「先立护栏再动刀」，C1 迁移步 1 + Q5 回收闸口径 + 复核两项补丁）

**交付物**（全部新增，不改既有文件）：
1. **test_models_metadata_snapshot**：`sorted(Base.metadata.tables.keys())` 与提交内快照 diff=∅；同时覆盖 `migrations/env.py:30` 与 `tests/conftest.py:44,354-449` 两条装配路径（conftest 的 `_strip_dangling_fks` 会静默吞缺失 FK，快照是唯一能抓「表悄悄消失」的机械闸——守 C1 I1/E1）。
2. **test_models_public_surface（双面）**：`__all__` 101 名字逐个可导入（I4a）+ **直达兼容面**（≈96 个直达 import 名字，含 `Review`、`EXTRACTION_STEP_TYPES` 等 `__all__` 外名字）逐个可从原子模块路径导入（I4b/E4）。**名字清单在测试内动态扫描源码生成（rg 同口径），不得硬编码快照**——否则新增直达 import 不被网捕获，恰好复现它要防的洞（复核补丁）。
3. **CI 聚合 import 计数器**：**范围 = 全仓 rg，六个消费根**——`apps/api/{src,tests,scripts,e2e}` + 根级 `{scripts,e2e}` + **`apps/mcp-server`**（复核补丁：第六根 `apps/mcp-server/tests/test_tools_integration.py:357` 直达 import KGEdge/KGNode，且它是独立 uv 工作区、不在 apps/api 的 PR CI 范围内——计数器不数它，收尾删直达路径时若它是唯一残留，崩且 CI 不红，正是 E4 错误模式实例）。按根分列公示基线值，写入本票 body。

**AC**：
1. 三护栏落地，各根计数器当前基线值记录在本票（后续 C1 消费面改写 PR 计数单调递减可见）；
2. 全量 pytest + ruff 绿；
3. 计数器 CI job 干跑一次成功（batch1/2/3 路径过滤先例形态）。

**依赖**：无。C1 步 2+（模型包移动）以本票 + T1 Accepted 为前置；Q5 回收闸（计数归零或满两个 minor 版本，先到者）由此计数器承载。

---

## 全批通用约定（各票 body 引用）

- 每票独立 NFM 工单，commit subject 挂票号（commit-ref-gate，`[no-issue]` 计 KR-2 结构性浪费——移动/删除类 PR 在 body 说明理由，对冲计量）；
- 移动类 PR 附移动映射表 + `git log --follow` 查史约定（ADR-026 §4）；
- 删除票一律先删后移（Q2），删除面零残留以 rg 亲验为 AC；
- 本批不含任何大布局移动（C1 步 2+、C4 入巢、C2 步 4+ 均待 T1 Accepted）。
