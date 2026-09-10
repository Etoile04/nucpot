# ADR-016 — Skills engine as production extraction engine (NFM-4535)

| Field | Value |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-09-10 |
| **Author** | Lead Engineer (wayfinder #1257 spec collapse), directed by 文杰 |
| **Scope** | Production extraction pipeline — path A (`ontofuel_extract`) only; paths B/C left as-is |
| **Supersedes** | None |
| **See also** | [docs/specs/G1-extraction-value-presentation.md](../specs/G1-extraction-value-presentation.md), [CONTEXT.md](../../CONTEXT.md) |

---

## 1. Context

`apps/api/src/nfm_db/services/extraction_pipeline.py:ontofuel_extract` 是
**生产唯一摄取路径**(路径 A,经 `process_literature_task` → Celery)。它的 prompt
由本体 entity_types/property_categories 动态装配(`extraction_pipeline.py:531-536`,
NFM-3258 + NFM-3004),强制要求已发布 `OntologyVersion`——无则 `raise ValueError`。

这条「本体驱动抽取」上线以来,召回基线一路下滑:

- **2026-08-30 实测**(wayfinder #1250,基线 `origin/main @ 2fe31261d`):Beeler 2018 = **25%**(1/4);Calhoun 2018 = **0%**(0/5);Zhu 2024 = **0%**(0/5)。
- **LLM 裸基线**:同模型(qwen3.8:27b-mlx,temp=0)三文献 **100%**(模型能答对,但本管线漏抽)。
- **nuclear-materials-skills-v4 实测**(wayfinder #1264,同模型):Beeler = **100%**(4/4);Calhoun / Zhu = 0%(数值域外——定性事实被技能契约主动拒收,不是技能能力问题)。

差距的根因是**目录封闭性**——本体 v0.4.0 不含 TDE、相图、势函数元数据等"目录外"专业属性,管线遇到目录外条目**静默丢弃**。技能 v4 有显式的"其他性能 + property_note 目录外"逃生舱,这是 25% vs 100% 的全部来源。

其他相关事实:

- BUG-25 三路径未收敛:A 生产主力 / B 仅 step-rerun + 本体升级重抽取队列 / C(v4)仅 `POST /api/v4/extraction/submit` 触达且 EntityExtractor 仍是正则 stub——任何"以管线 X 为基础"的方案必须先回答落在 A/B/C 哪一路。
- BUG-16 Phase 4 ExtractionGap 表已落地(`models/extraction_gap.py` + 迁移 047/053 + 5 个端点);漏抽可见性自 2026-09-03 起。
- 上游技能是 prompt-as-skill 规则集,不绑模型/CLI;无 PDF→MD 脚本(外部依赖)。

## 2. Decision

### §2.1 引擎选择 — 技能替入路径 A 的抽取 prompt

**主决策**:以 `nuclear-materials-skills-v4` 的 `nuclear-property-extraction-v4` 技能替入生产路径 A 的抽取 prompt;本体目录降为受控词表 + **新增「目录外」逃生舱**(目录外条目走 `pending_review=true` 新建 PropertyType,不进 verification 主表直至人工校对通过)。

替代方案已排除:

| 路线 | 否决理由 |
|---|---|
| 本体目录扩充(扩 v0.4.x catalog,修 BUG-15) | 工程债已清;但差距根因是"封闭性"而非"目录不全",纯扩目录治标不治本,扩什么由缺口分布决定 |
| LightRAG-first(RAG 索引为属性源) | RAG 是检索通路,不是属性源;且缺 §4.3 段落级溯源,校对不可做 |
| 双路并存(KG 精确 + RAG 兜底) | 维护成本 + 校对负担 + 数据契约不一致;与 owner 「验证精度只收可量化值」原则冲突 |

### §2.2 路径边界 — 只换路径 A

只替入路径 A(`ontofuel_extract`)。路径 B(`ExtractionOrchestrator`)维持重跑用途(单步重跑 + 本体升级重抽取队列);路径 C(`ExtractionOrchestratorV2`,v4 API)标 experimental。三路径彻底收敛另线跟踪,**不混入本次抽取引擎迁移**。

### §2.3 目录外逃生舱契约

- 技能输出含"目录外"标记 → 自动新建 `property_types.id` + `pending_review=true`。
- **不入 verification 主表**,进校对队列(与 owner 的低置信度校对设计汇流);校对通过后正式入表,**可反哺目录扩充**(决策数据驱动)。
- 与 #1253 校对动作集衔接(confirmed 后状态转换、validity_check 重跑)。

### §2.4 conditions 落点 — 高频键升列 + JSONB 兜底

测量条件不是开放 JSONB:

- 高频键升固定列:`simulation_method`, `model_name`, `temp_K`, `pressure_GPa`, `method`(一次迁移)。
- 其余开放键入 `measurement_conditions.conditions JSONB`(扩 schema)。
- 决策动机是结构化查询与报表(性能 + 可索引)与开放扩展(新实验方法)并存。

### §2.5 phase 落点 — 入测量条件,不动材料表

`phase`(相:α/β/液相 等)入 `conditions.phase`,**不动** `materials.crystal_structure`。理由:同一数值可在不同相下测得,属测量语境而非材料属性。材料表 `crystal_structure` 保留为材料级稳定态。

### §2.6 段落级溯源 — 技能加 `source_span` + 过渡期 adapter 启发式

- 长期:技能输出加 `source_span = {file, page, char_start, char_end, snippet_hash}`。
- 过渡期:adapter 在文件级溯源基础上,做启发式匹配(char window + 关键词锚定)兜底,**直到上游技能版本含 source_span**。
- 校对页需原文段落并排展示(owner 工作流,CONTEXT.md「人工校对」词条)——文件级不够,必须段落级。

### §2.7 技能仓库治理 — 上游独立演进 + 平台锁版引用

- 平台**不内化**技能仓库;**不修改**上游 prompt;只消费锁版本。
- 引用方式:`EXTRACTION_SKILL_REPO_PIN`(git SHA)在 CI 固定 + `EXTRACTION_SKILL_VERSION`(如 `v1.7.2`)作语义版本。
- 升级流程:PR 修改 `EXTRACTION_SKILL_REPO_PIN` → 触发 §2.8 召回回归 → 绿灯才合入。
- 不允许运行中切换版本(避免半重抽;数据集版本模型承担"哪个版本抽的"语义)。

### §2.8 召回回归门槛

- **基线**:`Beeler 2018` 数值域 = 100%(4/4,技能实测已达)。
- **门槛**:production dataset 在技能路径下的数值域召回 ≮ LLM 裸基线;低于即 P1 调查。
- **回归方式**:周跑 + 数据集扩 10 时跑(扩数据集内容变化的触发器;非时间触发器)。
- **定性事实域**(Calhoun / Zhu 滑移 / 5 MLIP 名)**不入 property_measurements**,入 KG 节点;验证精度只收可量化值。

## 3. Decision matrix (8 决 from #1252)

| # | 决议 |
|---|---|
| Q1 引擎选择 | 技能为引擎 + 目录外逃生舱 |
| Q2 逃生舱条目去向 | 待策展 + 人工校对;通过后可反哺目录扩充 |
| Q3 定性事实域 | 定量入表 / 定性入 KG(本管线拒收) |
| Q4 与 BUG-25 耦合 | 只换路径 A;B 维持重跑;C 标 experimental |
| Q5 conditions schema | 高频键升固定列 + JSONB 兜底(一次迁移) |
| Q6 phase 落点 | 入测量条件(同值不同相属不同测量);材料表不动 |
| Q7 段落级溯源 | 技能加 `source_span` + 过渡期 adapter 启发式 |
| Q8 技能仓库治理 | 上游独立演进 + 平台锁版引用 |

## 4. Migration safety (A4)

1. **渐进式**:技能 prompt 装配通过 `extract_skill_prompt(skill_version, ontology_version)` 工厂函数,**先暗后明**:暗后明(默认还是本体 prompt)→ 灰度(skill_version 配环境变量)→ 全量(默认技能 prompt);**全量前需 Beeler 100% 验证**。
2. **不破坏既有**:路径 A 的 `process_literature_task` seam 不动,只在 `ontofuel_extract` 内部替换 prompt 装配;摄取下游(adapter / 落库)按 §4.1 spec 增量改动。
3. **可回退**:`EXTRACTION_SKILL_ENABLED` env 旗标(默认 false),关掉即回退到既有本体 prompt;紧急回退一行 env 改动。
4. **数据集版本承接**:本 ADR 与 ADR-017(数据集隔离与版本管理)**同批实施**;同文献重抽产生新版本(见 ADR-017 §3),不破坏既有数据集。

## 5. Reversibility & cost

- **可逆性**:高(env 旗标 + lock file 一行改动)。但技能 prompt 升级 + 数据集版本切换需要 §2.8 召回回归。
- **迁移成本**:中等(本体 prompt → 技能 prompt 装配 + adapter + 一次 schema 迁移 + 召回回归测试)。
- **不逆转成本**(不实施本 ADR):低,但**业务代价**——验证精度继续受限于目录封闭性,Owen 2023 之类数据集无法形成有效验证基线。
- **§2.8 召回回归不达标时**:立即回退到本体 prompt,数据保留(数据集版本模型允许跨 prompt 验证)。

## 6. Alternatives considered

| 替代 | 否决理由 |
|---|---|
| 本体目录扩充(扩 v0.4.x catalog) | 治标不治本;扩什么由缺口分布驱动(可作为 Q2 反哺路径而非主路径) |
| LightRAG-first(RAG 为属性源) | RAG 是检索通路不是属性源;缺段落级溯源,校对不可做;验证精度无法保证 |
| 双路并存(KG 精确 + RAG 兜底) | 维护成本 + 校对负担 + 数据契约不一致;与「验证精度只收可量化值」冲突 |
| 等 BUG-25 三路径收敛再做 | 收敛时间不可预期;召回短板阻塞业务价值;本 ADR 明确"只换 A",不混入收敛 |

## 7. References

- wayfinder [#1249 地图](https://github.com/Etoile04/nucpot/issues/1249) / [#1257 终点票](https://github.com/Etoile04/nucpot/issues/1257)
- [#1250 抽取管线现状](https://github.com/Etoile04/nucpot/issues/1250) / [#1252 召回路线 8 决](https://github.com/Etoile04/nucpot/issues/1252) / [#1264 技能实测](https://github.com/Etoile04/nucpot/issues/1264)
- ADR-017 — 数据集隔离与版本管理(本批同 PR 入库)
- [G1-extraction-value-presentation spec](../specs/G1-extraction-value-presentation.md)
- `apps/api/src/nfm_db/services/extraction_pipeline.py:531-536` — 本体驱动 prompt 装配
- `apps/api/src/nfm_db/services/extraction_orchestrator_v2.py` — 路径 C(独立演进)
- BUG-25 / NFM-3008 — 三路径分发层去 flag 化(已闭环)
- BUG-16 Phase 4 — ExtractionGap(已闭环)