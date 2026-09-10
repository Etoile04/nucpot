# Spec:G1 抽取价值呈现——技能引擎 × 校对闭环 × 数据集生命周期

**Status**: Accepted(wayfinder #1257 坍缩产物,2026-09-10)
**决策来源**: 地图 #1249 全部 G1 决策票(#1250 研究 / #1252 召回路线 / #1251 原型 / #1253 校对交互 / #1280 数据集生命周期 / #1264 技能实测)
**配套 ADR**: `ADR-NFM-4535-extraction-skill-engine.md`、`ADR-NFM-4535-dataset-lifecycle.md`
**术语**: 根 `CONTEXT.md`「验证数据集与数据供给」「数据集生命周期」节

---

## 1. 目标

让文献库的抽取产物以「材料属性值 + 置信度 + 原文回链」语义进入验证数据集,支撑势函数验证;数据全生命周期可校核、可版本化、可回退。

## 2. 决策清单(全部 owner 亲答)

| # | 决策 | 来源 |
| --- | --- | --- |
| 引擎 = nuclear-materials-skills 规则集替入生产路径 A;本体降为词表 + 目录外逃生舱 | #1252 Q1 |
| 逃生舱条目 → 待策展 + 人工校对(不入主表) | #1252 Q2 |
| 定量入表、定性入图(KG 承担防混淆语义) | #1252 Q3 |
| 只换路径 A;三路径收敛(BUG-25)另票 | #1252 Q4 |
| conditions:高频键升列(simulation_method/model_name/temp_K)+ JSONB 兜底 | #1252 Q5 |
| phase 入测量条件(材料表 crystal_structure 不动) | #1252 Q6 |
| 溯源:技能加 source_span;过渡期 adapter 启发式匹配 | #1252 Q7 |
| 技能仓库上游独立 + 平台锁版引用 | #1252 Q8 |
| 展示:布局 B(图谱+属性侧栏)为主,布局 A(校对表格)为辅(校对队列) | #1251 |
| 校对:domain_expert 角色;五动作(确认/需修改/标记无效/来源存疑/跳过);写回现有 review 列 | #1253 Q1/Q2 |
| 公式一等公民入 value_expression + KaTeX | #1253 Q3 |
| Owen 2023 重抽试点:新隔离集 → 校核 → 版本替换 | #1253 Q4 |
| 每文献私有数据集;DOI 判重(内容哈希兜底);重抽=新版本 | #1280 Q5 |
| 校核门:行级五动作 + 有效域通过 → 快照合并;低置信度必须人工 | #1280 Q6 |
| 版本:快照式 dataset_versions;双回退(整版 / 按源剔除) | #1280 Q7 |
| 消费方:发布版/工作版分离;AutoVC 验证记版本号 | #1280 Q8 |

## 3. 架构变更

### 3.1 抽取引擎替换(路径 A)
- `ontofuel_extract` 的 prompt 替换为 nuclear-materials-skills v4 规则集(部署时分发 prompt 资产,CI 锁版本)
- 受控词表 = 现有本体目录;目录外属性走逃生舱 → `review_status='pending_curation'`,进校对队列
- 输出 13 字段 JSON → adapter 落 `property_measurements`(9 字段直映;conditions 按 3.2;phase 入条件;source_span 暂由启发式段落匹配补,技能侧加字段后切换)

### 3.2 schema 迁移(一次)
- `measurement_conditions` 升列:simulation_method、model_name、temp_K(现固定列保留),加 `extra JSONB` 兜底 + composition 列(成分,按源回退与判重的关键维度)
- `property_measurements.value_expression` 启用为公式一等存储(KaTeX 渲染)
- `property_types` 加 `valid_range`(min/max/unit + 提示文案)——物理有效域判据(材料数据 schema 的属性级承载,#1256 衔接)

### 3.3 数据集生命周期(详见 ADR-数据集生命周期)
- 每文献私有数据集;摄取判重 DOI 优先/内容哈希兜底;落库 upsert(同源+属性+值+单位+conditions 唯一)
- `dataset_versions`(版本号/材料/行集引用/来源清单/时间);合并=校核通过行的快照
- 双回退:整版本回退 + 按源剔除(移除某文献贡献后再快照),管理端动作+审计
- API 双读:发布版(验证/物性页/检索/导出)vs 工作版(校对页);AutoVC 结果记版本号

### 3.4 校对 UI(原型已验证:prototype/literature-extraction-display @ 9f9a571)
- 文献详情页:布局 B(共享 GraphCanvas 小规模变体:材料中心+属性卫星+侧栏值列表,成分/环境分行)
- 校对队列:布局 A(全列排序+筛选:属性/成分/环境/状态;自动合并青徽章;物理无效红行+原因)
- 校对抽屉:提取结果 ⟷ 原文段落并排(source_span 回链),五动作按钮 → PATCH review_status/reviewer_note

## 4. Owen 2023 重抽试点(首个端到端验证)

技能引擎 → 新每文献私有集(不动存量 92 行)→ 五动作校核 + 有效域 → 版本合并替换 → 存量对照归档。验收含:重复自动合并(同值同源无条件差异)、D₀=0 类物理无效拦截、成分/温度条件补齐(实证基线:存量 40/92 条件 null、13×0.3 跨 10 dataset 重复)。

## 5. 验收标准

- [ ] Beeler 2018 数值召回 100%(技能实测已证);Calhoun/Zhu 定性事实入 KG 计数
- [ ] Owen 重抽试点产出:带条件、带 source_span、零重复、零物理无效的发布版数据集
- [ ] 校对五动作经 UI 写回 review 列;domain_expert 角色门生效(过渡期 admin 兼任)
- [ ] 摄取判重:同 DOI 二次摄取零新增;按源剔除可完整移除某文献贡献并出审计记录
- [ ] 版本可溯:AutoVC 验证结果携带数据版本号
- [ ] 文献页布局 B 上线,校对队列(布局 A)可用

## 6. 边界与依赖

- **不在本 spec**:三路径收敛(BUG-25 另票)、LightRAG-first(已否)、KG 桥接覆盖率(NFM-4093)、管理端权限体系重构
- **依赖**:BUG-08 domain_expert 角色落地;#1256 材料数据 schema(缺失评估消费本 spec 的数据模型);技能仓库 source_span 上游演进
- **衔接**:AutoVC 参考值读取切发布版(与 G3 线协调)

## 7. 实施切片(交 to-tickets)

| 切片 | 内容 | 依赖 |
| --- | --- | --- |
| G1a | 技能引擎替入路径 A + 逃生舱 + 版本锁定 | — |
| G1b | schema 迁移(conditions 升列/composition/valid_range/value_expression)+ adapter | — |
| G1c | Owen 重抽试点(端到端) | G1a+G1b+G1d |
| G1d | 摄取判重 + dataset_versions + 双回退 + 发布/工作版双读 | — |
| G1e | 校对 UI(布局 B/A + 抽屉五动作 + 有效域标红) | G1b |
| G1f | 材料数据 schema 缺失评估接 valid_range/目录 | G1b |
