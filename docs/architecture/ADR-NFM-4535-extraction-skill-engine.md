# ADR-NFM-4535: 抽取引擎 = nuclear-materials-skills 规则集 + 目录外逃生舱

**Status:** Accepted(2026-09-10,wayfinder #1252 决议,owner 八项亲答;坍缩入库随 #1257)
**Date:** 2026-09-10
**决策记录:** GitHub issue #1252(含完整 grilling 过程与依据链)
**实测依据:** #1264 技能实测(numeric 域 100% vs 管线 25%;qualitative 域 0% 系契约拒收;接入面 = 一个 adapter + 半处 schema 改造)

---

## 1. Context

势函数验证依赖实验测量数据集(owner 背景,CONTEXT.md「验证数据集与数据供给」),数据经抽取管线从文献进入。线上实测暴露三层问题:

1. **召回差距**:本体驱动管线数值域召回 25%(Beeler 2018:4 个 TDE 值抽到 1 个)/0%(Calhoun、Zhu);同模型 LLM 裸基线 100%。根因:本体 v0.4.x 目录不含 TDE 等专业属性且**无逃生舱机制**,目录外属性被静默丢弃(BUG-15 内容债)。
2. **三路径并存**(BUG-25):路径 A 为唯一生产摄取路径,B 为编排包装,C 为 v4 正则 stub。
3. **数据质量**:摄取层无判重(同文 8+ 次重复摄取,0.3 eV ×13 行跨 10 dataset)、无数值有效域校验(D₀=0、密度 0.05 入库)、conditions 映射缺失(40/92 行条件记录全 null)。

**备选方案**:①本体目录扩充(纯内容工程,无逃生舱则目录永远追不完);②LightRAG-first(基础设施当时全空,且输出为文本非结构化行);③双路并存(叠加于未收敛的三路径之上);④**技能为引擎**(nuclear-materials-skills v4:11 类轻量受控词表 + 目录外逃生舱,13 字段结构化输出含 conditions 三档置信度与文件级溯源)。

## 2. Decision

**采用④:技能为引擎 + 目录外逃生舱**,八项配套决策(owner 亲答,#1252):

1. 技能规则集替入生产路径 A 的抽取 prompt;本体目录降为受控词表 + 新增「目录外」逃生舱;目录扩充转为数据驱动(看逃生舱触发分布)
2. 逃生舱条目 → 待策展 + 人工校对队列(与 owner 校对工作流汇流),不入验证数据集主表
3. 定量入表、定性入图(KG 承担防混淆语义;验证精度由量化数据承担)
4. 只换路径 A;三路径收敛(BUG-25)另票
5. conditions schema:高频键升列(simulation_method/model_name/temp_K)+ JSONB 兜底
6. phase 入测量条件(材料表 crystal_structure 语义不动)
7. 段落级溯源:技能输出加 source_span;过渡期 adapter 启发式匹配
8. 技能仓库上游独立演进 + 平台锁版引用(prompt 资产部署分发,CI 固定版本)

## 3. Consequences

- **正向**:数值召回从 25% 跃至已证 100%;逃生舱杜绝静默丢弃;目录扩充有数据依据;双通道录入(网页×智能体)共用同一引擎与质量门槛。
- **代价**:外部仓库依赖(锁版缓解);conditions schema 一次迁移;存量数据需重抽(Owen 2023 试点先行)。
- **风险与缓解**:技能输出为 Agent 风格文本非纯 JSON → adapter 解析层;溯源止于文件级 → 上游 source_span 演进 + 过渡启发式。
- **验收基线**:Beeler 100%(已证);Calhoun/Zhu 定性入 KG 计数(契约性拒收是有意设计)。

## 4. 关联

- Spec:`docs/specs/g1-extraction-value.md`
- ADR-NFM-4535-dataset-lifecycle(数据集生命周期,本决策的数据容器)
- 依据:#1264 技能实测报告(research/extraction-skill-eval)、#1250 抽取管线现状
