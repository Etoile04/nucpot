# G1 抽取价值呈现 — 实现 spec

> wayfinder #1249 → #1257 坍缩 · G1 区
> 来源决议:[Etoile04/nucpot#1250](https://github.com/Etoile04/nucpot/issues/1250) · [#1251](https://github.com/Etoile04/nucpot/issues/1251) · [#1252](https://github.com/Etoile04/nucpot/issues/1252) · [#1253](https://github.com/Etoile04/nucpot/issues/1253) · [#1264](https://github.com/Etoile04/nucpot/issues/1264) · [#1280](https://github.com/Etoile04/nucpot/issues/1280)
> 关联 ADR:[ADR-016 技能引擎](#) · [ADR-017 数据集生命周期](#)
> 状态:**可建**(所有开放决策已锁;实现票分拆见 §10)

## 1. 目标

让文献详情页**真正能读**——把抽取出来的属性值、置信度、原文段落回链、公式以「可看、可核、可改」三形态同时呈现,而不是堆在数据库里等检索命中。具体:

1. **召回改进**:用 `nuclear-materials-skills-v4` 替入生产抽取 prompt,数值域召回从 25% 提到 100%(基线:LlBeeler2018)。
2. **展示形态**:文献页默认布局 B(图谱+属性侧栏),校对/批量场景切到布局 A(校对表格)。
3. **校对闭环**:domain_expert 角色做五动作校对决策;公式入 `value_expression` 一等公民。
4. **数据形态**:每文献私有数据集 + 快照式版本 + 双回退(整版/按源剔除);发布版/工作版双读。

## 2. 不在本 spec 范围

- 抽取路径 B/C 的彻底收敛(另线,三路并存保留);
- 抽取 prompt 本体(上游独立仓库演进,平台锁版引用);
- RAG 开放策略与超时(见 [RAG 开放与质量承诺 spec](./RAG-anonymous-open-and-quality.md));
- LightRAG 基础设施修复(由 NFM-4492 / #1258 已闭环,本 spec 不复述);
- 校对页完整 UI 重建(本 spec 只定数据契约 + 校对抽屉接口,UI 演进跟随 AC);
- BUG-15 本体内容债清理(由 catalog 维护者按缺口分布决策);
- BUG-08 角色枚举落地(domain_expert 是校对角色,实装依赖 BUG-08 收尾;过渡期 admin 兼任)。

## 3. 数据契约

### 3.1 property_measurements 行级契约(校核后落库)

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | uuid | Y | 主键 |
| `dataset_id` | uuid | Y | 所属数据集(每文献私有集,见 ADR-017) |
| `dataset_version_id` | uuid | Y | 所属快照版本(发布/工作二选一,见 §3.3) |
| `material_id` | uuid | Y | 材料标识(解析后,**严禁**用 UUID 冒充) |
| `property_type_id` | uuid | Y | 属性类型(`property_types.id`);目录外条目→新建 PropertyType + `pending_review=true` |
| `value_numeric` | numeric | N | 数值;与 `value_text`/`value_expression` 至少其一非空 |
| `value_text` | text | N | 文本值(枚举/材料名) |
| `value_expression` | text | N | **公式一等存储**(KaTeX 兼容语法),与数值列互不替代 |
| `unit` | text | N | 单位;`UNITS` 字典不存在 → 警告但接受 |
| `conditions` | jsonb | Y | **JSONB 开放测量条件**;高频键(`simulation_method`/`model_name`/`temp_K`/`pressure_GPa`/`method`)升固定列,其余键入 JSONB |
| `phase` | text | N | **入测量条件**(同值不同相属不同测量),不入材料表 |
| `source_id` | uuid | Y | `data_sources.id`(DOI 优先,内容哈希兜底) |
| `source_span` | jsonb | N | **段落级溯源**:`{file, page, char_start, char_end, snippet_hash}`(过渡期 adapter 启发式匹配) |
| `confidence` | numeric(0–1) | Y | LLM 自评置信度;<阈值(见 §5) → 标 `pending_review=true` |
| `extraction_skill_version` | text | Y | 技能版本(锁版引用,如 `nuclear-property-extraction-v4@v1.7.2`) |
| `review_status` | enum | Y | `pending` / `confirmed` / `modified` / `invalid` / `disputed` / `skipped`(六态,见 §5) |
| `reviewer_id` | uuid | N | domain_expert(admin 兼任过渡期) |
| `reviewer_note` | text | N | 校对备注 |
| `reviewed_at` | timestamptz | N | 校对时间 |
| `validity_check` | jsonb | N | 属性级 `valid_range` 校验结果:`{status: "ok"\|"warn"\|"fail", reason: str\|null}` |
| `dedupe_key` | text | N | **(dataset, property_type, source_id, value_hash)** 复合键,唯一约束,摄取 upsert 依赖;mapper 写入路径必填(`extraction_to_db_mapper` 必须填充),legacy 行允许 NULL(部分 UNIQUE 索引允许多个 NULL) |

### 3.2 技能输出 ↔ 行级契约 adapter(13 → 20 字段)

```
[skill output: 13 fields]
  property       ──→ property_categories 有界 crosswalk ──→ property_type_id
  value         ──→ value_numeric | value_text | value_expression (按 type 路由)
  unit          ──→ unit (warn if not in UNITS)
  reference     ──→ data_sources 回查建档 ──→ source_id
  conditions        ──→ 4 固定列映射 + 余入 conditions JSONB(扩迁移)
  phase            ──→ conditions.phase (无材料表落点)
  confidence       ──→ confidence (透传)
  [缺: source_span] ──→ adapter 启发式匹配(file + char window 推断)
  [缺: dedupe_key]  ──→ (dataset, property_type, source_id, value_hash) 服务端合成
  [缺: dataset_id]  ──→ 由摄取层 (dataset 解析 + 版本创建) 注入
```

### 3.3 数据集版本契约(快照式,见 ADR-017)

- `datasets`(每文献一私有集):`id`, `literature_doi`(唯一), `literature_content_hash`, `created_at`, `status`(active/superseded)。
- `dataset_versions`(快照式):`id`, `dataset_id`, `version_no`, `created_at`, `row_ids jsonb`, `source_ids jsonb`, `parent_version_id`, `status`(draft/released/rolled_back)。
- 双读路径:API 接收 `?dataset_version={released|draft|<uuid>}`;默认 `released`。
- 回退:整版本回退 = 切 `released` 指针;按源剔除 = 新建 `rolled_back` 副本(移除指定 source 贡献)→ 切指针。

### 3.4 校对动作集(六态,写回 review 列)

| 动作 | review_status | 副作用 |
|---|---|---|
| 确认通过 | `confirmed` | `reviewer_id`, `reviewed_at` 写入;validity_check 重跑 |
| 需修改 | `modified` | 进入编辑态(value/conditions 可改)→ PATCH 行级 + 审计备注 |
| 标记无效 | `invalid` | 物理域失败 → 标红原因;不参与合并 |
| 来源存疑 | `disputed` | 行挂起,不入合并集;`reviewer_note` 必填 |
| 跳过 | `skipped` | 临时跳过,后续仍可恢复;不阻塞版本合并 |
| 低置信度自动 | `pending` | `<` 阈值自动标 pending;**不参与自动合并**,必经人工 |

## 4. 展示形态契约

### 4.1 布局 B(默认,文献详情页)

- 骨架:共享 `GraphCanvas`,中心材料节点 + 属性类型卫星(带计数)。
- 交互:点属性节点 → 侧栏列具体值(按 `confidence desc` 排序);点值 → 校对抽屉(§4.3)。
- 数据:消费 `property_measurements` + `value_expression`(KaTeX 渲染)+ `source_span`(悬停原文段落)。

### 4.2 布局 A(校对队列/批量校对视图)

- 形态:全量平铺表格,按 `confidence asc` 排序(低置信度在前)。
- 列:属性/值/单位/置信度/来源段落摘要/状态。
- 行点击:进校对抽屉。
- 入口:`/admin/review/queue`(domain_expert) + 文献页"切到校对视图"按钮。

### 4.3 校对抽屉(共享,布局 A/B 共用)

- 五按钮:**确认通过 / 需修改 / 标记无效 / 来源存疑 / 跳过**(六态含"低置信度自动",UI 不显式按钮)。
- 编辑态:行内表单改 value / conditions(不新建 draft,直接 PATCH)。
- 物理无效标红:`validity_check.status='fail'` → 红行 + 悬停原因。
- 自动合并徽章:同 `dedupe_key` 多行合并时显"已合并 N 行"(原型 v4 已演示)。

## 5. 阈值与合并规则

| 项 | 阈值/规则 |
|---|---|
| 低置信度阈值 | `confidence < 0.7` → 自动标 `pending`,必经人工;过渡期 admin 可临时下调到 0.5(基线是原型 v3 用值) |
| 自动合并 | `(dataset_id, property_type_id, source_id, value_hash)` 唯一;**无条件差异才自动合并**;有条件差异保留各行 |
| 物理有效域 | 属性级 `valid_range` 在 material_data_schema(见 ADR-017 §3.2);落库时校验,`fail` → `review_status='invalid'` 候选 + 标红原因 |
| Owen 重抽试点 | 写入新私有数据集(`Owen2023-amorphous-UO2`)→ 校核 → 通过后快照替换;**存量 92 行保留至替换完成作对照** |

## 6. 召回验收基线

| 文献 | 既有管线 | 目标(技能接入后) |
|---|---|---|
| Beeler 2018 | 25% (1/4) | **100%** (4/4,已实测) |
| Calhoun 2018 | 0% (0/5) | 0%(数值域外;定性事实入 KG,不入 property_measurements) |
| Zhu 2024 | 0% (0/5) | 0%(同上) |

回归门槛:**任何 production dataset 在技能路径下的数值域召回 ≮ LLM 裸基线**;低于即触发 P1 调查。

## 7. 集成点

- 抽取 prompt 装配:`apps/api/src/nfm_db/services/extraction_pipeline.py`(`ontofuel_extract` 替换点);技能 prompt 由 `extract_skill_prompt(skill_version)` 工厂函数装配,版本由环境变量 `EXTRACTION_SKILL_VERSION` + `EXTRACTION_SKILL_REPO_PIN` 锁定。
- 技能仓库治理:`packages/skills-catalog` 注册技能元数据;CI 校验 `EXTRACTION_SKILL_REPO_PIN` 与 lock file 一致。
- 摄取判重:`apps/api/src/nfm_db/services/literature_service.py`(`process_literature` 内 DOI + content_hash 判重);同文献重复 → 触发新版本流程(§8.3)。
- 校对页:`apps/web/src/app/literature/[id]/...`(布局 B/A 切换)+ `apps/web/src/app/admin/review/...`(校对队列);不在本 spec 范围。
- 校验:`apps/api/src/nfm_db/services/validation.py`(新增 `validity_check` 计算函数)。

## 8. 行为规约

### 8.1 抽取路径(技能替入)

1. 文献入库(`POST /api/v1/literature` 或外部 ingestion) → 触发 `process_literature_task`。
2. 路径 A prompt 装配:`build_ontology_extraction_prompt(ontology_version)` → `extract_skill_prompt(skill_version, ontology_version)`。
3. 技能执行 → 13 字段输出 → adapter(§3.2) → property_measurements 候选行。
4. 低置信度阈值以下 → `pending`;物理有效域 fail → 候选 `invalid`;其余 `pending` 待人工。
5. 落库:同 `(dataset, property_type, source_id, value_hash)` upsert;`dataset_version_id` 指向新 draft 快照。

### 8.2 校对流程

1. 校对队列视图(布局 A):domain_expert 拉取 `review_status='pending'` 行。
2. 校对抽屉:五动作按钮 + 编辑态。
3. 写回:PATCH `property_measurements`(同一行,无 draft 表);修改路径写 `audit_log`。
4. 自动校验:`validity_check` 在 PATCH 时重跑;`fail` 阻断 `confirmed` 但允许 `invalid`。
5. 合并集:`review_status IN ('confirmed')` 且 `validity_check.status='ok'` 进入可合并集。
6. 快照:合并集 → 新 `dataset_version.status='draft'` → domain_expert 触发发布 → `status='released'`;指针切。

### 8.3 同文献重抽

1. 同一文献再次入库(DOI/content_hash 命中)→ 既有 `datasets.id` 复用,新建 `dataset_version.version_no = N+1, status='draft'`。
2. 既有 released 版本不受影响;校对页工作于 draft。
3. 重抽完成 → 校对通过 → draft 发布 → released 指针切;旧 released 标记 `superseded` 但保留(可回退)。

### 8.4 物理无效拦截

- 落库时跑 `validity_check`;`fail` 直接 `review_status='invalid'`,原因写入 `validity_check.reason`。
- 校对页红行展示原因(组件读 `reason`);domain_expert 可改回 `disputed`(举证)或确认无效。

## 9. 不变量与边界

- **每文献一数据集**:同文献不允许多 dataset;重抽 = 新版本。
- **快照式版本**:版本即行集快照;不允许行级 delta(增量将破坏可回退性)。
- **目录外条目**走 `pending_review=true` 新建 PropertyType,**不入 verification 主表**直至人工校对通过。
- **定性事实入 KG,不入 property_measurements**:验证精度只收可量化值。
- **技能版本锁版**:`EXTRACTION_SKILL_REPO_PIN` 在 CI 固定;不可运行中切换(避免半重抽)。
- **Owen 重抽不动存量**:试点数据集独立,合并完成前存量 92 行保留作对照。

## 10. 验收(AC)

- **AC-1**:`Beeler 2018` 在技能路径下召回 = 100%(≥4/4)。
- **AC-2**:Calhoun 2018 / Zhu 2024 定性事实入 KG 节点,property_measurements = 0%(数值域外)。
- **AC-3**:文献详情页默认布局 B;`/admin/review/queue` 布局 A。
- **AC-4**:domain_expert 角色可完成五动作(确认/修改/无效/存疑/跳过),admin 兼任过渡期。
- **AC-5**:`value_expression` 列可存 + KaTeX 渲染公式;与数值列互不替代。
- **AC-6**:`dataset_versions` 表就位;按源剔除操作可审计回退。
- **AC-7**:Owen 2023 重抽试点数据集存在,校核后合并;存量 92 行保留对照。
- **AC-8**:`EXTRACTION_SKILL_REPO_PIN` 在 prod env 锁定;CI fail-closed on mismatch。
- **AC-9**:`dedupe_key` 唯一约束生效,Owen 92 行同值重复场景下不再产生新行。
- **AC-10**:物理无效(键长 0.3Å / 密度 0.05)落库时 `validity_check.status='fail'` + reason。

## 11. 实现票分拆(给下一棒)

> 见 [NFM-4535 实现票群](https://github.com/Etoile04/nucpot/issues/1257)(将由实现 owner 按依赖关系挂):

| 票 | 范围 | 依赖 |
|---|---|---|
| G1-A 技能接入 | prompt 装配 + adapter + lock file | #1258(已完成) |
| G1-B schema 迁移 | conditions JSONB 扩列 + dataset_versions 表 |  |
| G1-C 摄取判重 | DOI/content_hash 主键 + dedupe_key 唯一约束 |  |
| G1-D validity_check | 属性 valid_range 落库校验 + 标红 |  |
| G1-E 布局 B | 文献页默认布局(GraphCanvas + 侧栏 + 校对抽屉) | G1-A,B,C,D |
| G1-F 布局 A | 校对队列视图 + 行级五动作 | G1-B,D |
| G1-G Owen 重抽试点 | 新数据集 + 校核 + 合并 + 存量对照 | G1-A,B,C |
| G1-H ADR/术语入库 | ADR-016/017 + CONTEXT.md 新术语 | (当前 PR) |

## 12. 依据链

- [wayfinder #1249 地图](https://github.com/Etoile04/nucpot/issues/1249) — 两区整体设计入口
- [#1250 抽取管线现状](https://github.com/Etoile04/nucpot/issues/1250) — 三路径 / ExtractionGap / 召回基线事实
- [#1251 展示形态原型](https://github.com/Etoile04/nucpot/issues/1251) — B/A 双布局决议
- [#1252 召回路线](https://github.com/Etoile04/nucpot/issues/1252) — 技能为引擎 + 8 项决策
- [#1253 核实与修改交互](https://github.com/Etoile04/nucpot/issues/1253) — 五动作 + 公式一等 + Owen 重抽
- [#1264 技能实测](https://github.com/Etoile04/nucpot/issues/1264) — Beeler 100% / Calhoun,Zhu 定性域外
- [#1280 数据集隔离与版本管理](https://github.com/Etoile04/nucpot/issues/1280) — 每文献集 + 快照版 + 双回退
- 根 `CONTEXT.md` — 平台工作流总览 + 验证数据集族术语(本 spec 扩展)