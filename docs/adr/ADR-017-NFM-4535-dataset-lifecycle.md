# ADR-017 — Dataset lifecycle: per-literature isolation + snapshot versioning (NFM-4535)

| Field | Value |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-09-10 |
| **Author** | Lead Engineer (wayfinder #1257 spec collapse), directed by 文杰 |
| **Scope** | Dataset model + lifecycle for `property_measurements` and derived validation sets |
| **Supersedes** | None |
| **See also** | [docs/specs/G1-extraction-value-presentation.md](../specs/G1-extraction-value-presentation.md), [ADR-016 技能引擎](./ADR-016-NFM-4535-skills-engine-as-extraction-engine.md), [CONTEXT.md](../../CONTEXT.md) |

---

## 1. Context

### 1.1 历史事故(09-02 + 09-09)

- 09-02 41 行 attribution 永久丢失事件 — 当时无版本模型,人工 SQL 回退代价高(NFM-4394/4395 先例)。
- 09-09 VDB 重启"修复"事件 — RE 重启导致 25→3 chunks 丢失,孤儿清理无版本快照可回退。
- Owen 2023 实测(wayfinder #1253 取证评论):同一文献在 08-31~09-01 间被反复摄取 8+ 次,每次新建 `data_source` + `dataset`,标题退化为 UUID("Unknown Material - <uuid>");同值重复 13 行分散于 10 个不同 dataset,**结构性问题**:文献源身份判重失败(无 DOI/内容哈希主键)+ 落库无 `(dataset, property_type, value)` 去重键。

### 1.2 既有约束

- 平台工作流总览(CONTEXT.md):势函数验证依赖实验测量数据集;验证状态 / 等级 / 质量等级衡量的是 MD 计算结果与验证数据集的偏差;每条数据需来源标注 + 人工校对(操作页并排展示提取结果与原文段落)。
- 既有 schema 关键列:`property_measurements.review_status`(`pending` / `confirmed` / ...),`reviewer_note`,`reviewed_at`(实查已存在,#1253 决议直接复用)。
- 既有 `materials_data_schema`(material_data_schema)已在使用,描述某类材料验证数据集应含哪些数据 + 自动评估完整性 → 缺口标识 → 数据挖掘工作流反哺验证。

### 1.3 待决问题(wayfinder #1280)

- 隔离与身份:每文献一数据集 vs 共享池?
- 校核门:行级 vs 整集?低置信度处理?
- 版本与回退:快照式 vs 增量?按源剔除支持?
- 消费方:发布版与工作版是否分离?AutoVC 验证结果记录哪个版本?

## 2. Decision

### §2.1 隔离与身份 — 每文献一个私有数据集

每篇文献入库时建一个**私有数据集**(per-literature private dataset)。身份判重:

- **DOI 优先**(精确匹配,带规范化:大小写 + 空白 + 前缀剥离)
- **内容哈希兜底**(PDF content hash;同 PDF 不同来源仍合并)

**同文献重抽 = 该数据集的新版本**,而不是新数据集——本次事故的"同文多源、标题退化 UUID"从此结构性不可能。`Unknown Material - <uuid>` 标题退化路径被关闭。

### §2.2 校核门 — 行级门 + 快照合并

- **行级门**:property_measurements 行级 `review_status` 必须为 `confirmed` 且 `validity_check.status='ok'` 才进入"可合并集"(mergeable set)。
- **低置信度行必须人工通过**:`confidence < 0.7` 自动标 `pending`,**不参与自动合并**(即使后续校验通过也必须经 domain_expert 确认)。
- **快照合并**:可合并集 = `dataset_version` 快照——**版本 = 可合并集快照**(不是单行 delta)。
- 行级门与 §2.3 快照版配对:任何快照必有"哪些行通过门"的 audit。

### §2.3 版本与回退 — 快照式版本表 + 双回退

**`dataset_versions` 表**(快照式):

| 字段 | 说明 |
|---|---|
| `id` | uuid |
| `dataset_id` | 所属数据集 |
| `version_no` | 版本号(per-dataset 单调递增) |
| `created_at` | 创建时间 |
| `row_ids` | jsonb(快照包含的 property_measurements.id 列表) |
| `source_ids` | jsonb(快照包含的 data_sources.id 列表) |
| `parent_version_id` | 父版本(可空) |
| `status` | `draft` / `released` / `rolled_back` / `superseded` |

**双回退**:

1. **整版本回退** = 切 `released` 指针到任一历史 `dataset_version`(不必是最新)。
2. **按源剔除** = 新建 `rolled_back` 副本,移除指定 `source_id` 贡献的行,再快照,切指针——owner 的"删掉某篇文章引入的数据"用例。

回退 = 数据集版本指针切换,**无需人工 SQL**;每次回退写 `audit_log`(`audit_kind='dataset_version_rollback'`,`action='whole'|'by_source'`)。

### §2.4 消费方 — 发布版/工作版分离

- **发布版(released)**:供验证 / 物性页 / 检索 / 导出 / AutoVC 验证消费。
- **工作版(draft)**:仅供校对页 + 内部 review;对外不可见。
- API 接收 `?dataset_version={released|draft|<uuid>}`;**默认 `released`**。
- AutoVC 验证结果记录**所用版本号**(等级可追溯到数据版本)。

### §2.5 摄取判重

摄取层(`literature_service.process_literature`)在入库时:

1. **DOI 规范化**(strip 空白 / 前缀 / 大小写)→ 查 `datasets.literature_doi`。
2. 命中 → 既有数据集,**新建 dataset_version**（`version_no = max+1`, `status='draft'`)。
3. 未命中 → 算 PDF content hash → 查 `datasets.literature_content_hash`。
4. 命中 → 既有数据集,新建版本(同 #2)。
5. 未命中 → **新建 dataset + 新建 version_v1**(单步原子事务)。

### §2.6 自动合并边界

`(dataset_id, property_type_id, source_id, value_hash)` 复合 `dedupe_key` 唯一约束。

- **无条件差异才自动合并**(同值 + 同语义源 + 无条件不同)→ upsert。
- **有条件差异保留各行**(不同 simulation_method / temp_K 等条件属不同测量,即使数值相同也不合并)。
- 物理有效域校验(`validity_check.status='fail'`) → 不合并,标红原因,进校对页。

### §2.7 与 ADR-016(技能引擎)的衔接

- 技能版本 → `extraction_skill_version`(property_measurements 列)→ 每个 `dataset_version` 隐式记录"哪些行是哪个技能版本抽的"。
- 同文献重抽可能跨技能版本 — 这是数据集版本模型的合理用法(可回退到旧版本的"老技能版本抽的行")。
- 召回验收基线 §2.8(ADR-016)→ 直接比对 `dataset_version=released` 下的 property_measurements。

## 3. Decision matrix (4 决 from #1280)

| # | 决议 |
|---|---|
| Q5 隔离与身份 | 每文献一私有数据集;身份 DOI 优先 + content_hash 兜底;同文重抽 = 新版本 |
| Q6 校核门 | 行级门(confirmed + validity ok)+ 快照合并;低置信度必经人工 |
| Q7 版本与回退 | 快照式 `dataset_versions` 表 + 双回退(整版 + 按源剔除);回退=切指针 |
| Q8 消费方 | 发布版/工作版分离;AutoVC 验证结果记所用版本号 |

## 4. Migration safety (A4)

1. **存量迁移**:Owen 2023 等现存数据集进入新模型——与 #1253 Owen 重抽试点配对(写入新私有数据集 → 校核 → 通过后快照替换);**存量 92 行保留至替换完成作对照**。
2. **双读期**:迁移期间保留旧路径(`?legacy=true`),默认走新路径;监控 `legacy` 调用计数,降到零后下线旧路径。
3. **可回退**:`DATASET_LIFECYCLE_ENABLED=false` env 旗标,关掉回退到既有无版本模型(只读兜底,不能写新行);紧急回退一行 env 改动。
4. **发布/工作版切流**:从单读 `released` 起步,工作版先内部用,UI 校对页对接稳定后再对外开放 `draft` 视图。

## 5. Reversibility & cost

- **可逆性**:中(env 旗标 + 双读期;`dataset_versions` 表是 schema 加法,不破坏既有数据)。
- **迁移成本**:中等(双 dedupe_key + dataset_versions + 存量迁移 + 双读兼容)。
- **不逆转成本**:低但累积——无版本模型下,09-02 类事故恢复永远靠人工 SQL,无审计,无回退秒级响应;每一次此类事故**永久损失数据 attribution**。
- **ADR-016 与本 ADR 同批**:同文献重抽产生新版本,新旧版本共存可对照验证(技能版本 A 抽的行 vs 技能版本 B 抽的行)。

## 6. Alternatives considered

| 替代 | 否决理由 |
|---|---|
| 共享池 + dataset_id 列(无每文献隔离) | "同文多源、标题退化 UUID"事故再发;无法支撑按源剔除 |
| 增量式版本(delta 表) | 复杂;回退代价高(反向叠加多个 delta);不审计友好 |
| 无版本(直接覆盖) | 09-02 类事故再发;无回退;无 AutoVC 等级追溯 |
| 仅按行级 review_status(无 dataset_version) | 校核门与版本耦合不清晰;按源剔除难实现 |

## 7. References

- wayfinder [#1249 地图](https://github.com/Etoile04/nucpot/issues/1249) / [#1257 终点票](https://github.com/Etoile04/nucpot/issues/1257)
- [#1253 核实与修改交互](https://github.com/Etoile04/nucpot/issues/1253) — Owen 取证 / 五动作 / 公式一等
- [#1280 数据集隔离与版本管理](https://github.com/Etoile04/nucpot/issues/1280) — 4 决议 owner 亲答
- ADR-016 — 技能引擎(本批同 PR 入库)
- ADR-013 — Prod mutation guardrails(回退操作走 audit_log 模式)
- NFM-4394 / NFM-4395 — 09-02 类事故回退先例(对比:本模型下回退即一行 env 改动)
- [G1-extraction-value-presentation spec](../specs/G1-extraction-value-presentation.md) §3.1, §3.3, §8 — 契约落地