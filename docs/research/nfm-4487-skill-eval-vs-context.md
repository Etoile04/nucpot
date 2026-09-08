# NFM-4487 — nuclear-materials-skills-v4 vs CONTEXT.md 七术语 & 双通道原则

> *LE 研究产出,喂 #1252(召回路线决策)。不重复 #1264 已出的技能盘点与实测数字,只补其未覆盖的 CONTEXT.md / 双通道 / seam 视角。*
>
> 基线证据:Etoile04/nucpot#1264 — `research/extraction-skill-eval.md`(commit `a865071d4`)
> 父票:NFM-4485 CONTEXT.md 双通道原则 + 「录入通道」术语(`origin/main` @ `4b96d4ec2`)
> 同源文档:CONTEXT.md「验证数据集与数据供给」节(NFM-4482 #1260+#1261 入册)
> 配套工程票:NFM-2564 C1 vlm_complete / C2 ingest_service seam / C3 ingest_service / C4 task_dispatcher

## 0. 一句话结论(喂 #1252)

`nuclear-materials-skills-v4` 在**数值属性域**已经等同于 LLM 裸基线(实测 100% vs 平台本体管线 25%,见 #1264 §2.4)——但它的输出契约**只覆盖七术语中的两条**(`来源标注` + `材料数据 schema` 的弱形态),**不携带**验证数据集标签、知识图谱节点、缺口标识、人工校对闸门或数据挖掘工作流;且写库目标是**外部 Supabase**,与平台「双通道录入共用同一数据后端」原则直接冲突。

→ **建议 #1252 选项 ③ 双路并存(KG 精确 + RAG 兜底)的 KG 路径里,把技能当作「数值属性抽取 prompt-as-skill」复用为 ingest_service seam 的上游 prompt 模板,通过条件 schema 扩展 + 源标注/人工校对闸门落到平台本体 0.5.0 的 11 类/74 属性**;不要把技能当作独立数据后端接入(违反双通道原则),也不要把技能的「目录外」逃生舱当作长期契约(它解决了 nucpot 本体无 escape hatch 的内容债,但同时引入了 PropertyType 漂移风险)。

## 1. 召回数字(继承 #1264)

| 域 | GT | 平台本体管线 | LLM 裸基线 | **技能 v4** |
|---|---|---|---|---|
| 数值属性(Beeler 2018) | 4 | 25% | 100% | **100%** |
| 定性/机制/枚举(Calhoun + Zhu) | 10 | 0% | 100% | **0%** |
| 合计 | 14 | 7% | 100% | **29%** |

- 数值域 = LLM 裸基线,机制是技能自带「`其他性能` + `property_note:"目录外"`」逃生舱——TDE 全过、平台本体 v0.4.0 静默丢 3 条(§7.5.5-C 根因)。
- 定性域 = 本体管线同档(0%),RAG 兜底。技能契约(§12 不抽 + Q3 须可量化)主动拒收,**按设计本就不在输出域**。
- 耗时:32.8–140.9s/篇(27b MLX,~11.6k prompt tokens 规则集);3 次 LLM 调用零重试。

## 2. 七术语对齐(本票新增视角)

CONTEXT.md「验证数据集与数据供给」节锁定的七个术语,逐条对账:

| 术语 | 技能是否携带 | 形态/缺口 |
|---|---|---|
| **验证数据集 (validation dataset)** | ✗ 不携带 | 技能不标数据归属哪个验证数据集(按材料组织的实验测量集合)。落地前必须由 mapper 补 `dataset_id`。 |
| **材料知识图谱 (material knowledge graph)** | ✗ 不构建 | 技能输出无图谱节点/关系,只有「material_name + composition」字符串。需要在 adapter 内经 lookup-or-create 进入 `materials` 表后才能形成图谱边。 |
| **来源标注 (source attribution)** | ✓ 携带(部分) | `source_file`(文件级,**无页/段/quote**)+ `reference`(`作者, 标题` 串,缺 DOI/期刊/年)。平台 `data_sources` 要求结构化 doi/title/journal/year/authors,需 adapter 解析或按标题/DOI 回查文献库 API 建档。**比既有平台数据更弱。** |
| **人工校对 (human verification)** | ✗ 不自动触发 | 技能有三个 CHECKPOINT(批量抽取前/JSON→导入前/dry-run 后),但都是**编排层人工确认**,不是**逐条提取结果的人工校对**。平台 `review_status` 默认 `pending` 字段天然契合,只要数据从技能入到 ingest_service seam,自动进入 review queue——这部分是 seam 责任,不是技能责任。 |
| **材料数据 schema (material data schema)** | ✓ 携带(11 类枚举 + 74 标准属性) | 与平台本体 v0.4.0/0.5.0 的 11 类/74 属性**完全同构**(逐条对照 property_catalog.md vs ontology_seed.py),且多「目录外」逃生舱。**有界 crosswalk = 11 行枚举表 + 74 行属性名表。** |
| **缺口标识 (gap mark)** | ✗ 不携带 | 技能不评估数据完整性,只产出「有/无」记录。要支撑缺口标识,需要在 ingest_service seam 里挂 `ontology_coverage_report.py`(已存在,scripts/ontology_coverage_report.py)做 schema 完整性评估。 |
| **数据挖掘工作流 (data mining workflow)** | ✗ 不在技能 | 技能无 agent-driven gap-closure 循环。闭环需要外部(数据挖掘工作流票)驱动,技能只负责「给我一篇 md,我吐一个 JSON」。 |

**对齐率 = 2/7 携带 + 1/7 经 seam 自然带入 = 3/7 实质覆盖**。剩余 4 项(验证数据集、知识图谱、缺口标识、数据挖掘)必须经 ingest_service seam + 既有平台组件补齐,**不能依赖技能自带**。

## 3. 双通道原则对账(本票新增视角)

NFM-4485 owner 口径:网页人工录入与智能体自动提取,共用同一数据后端、同一质量门槛。

| 维度 | 网页录入通道(平台现行) | 技能通道(当前形态) | 一致? |
|---|---|---|---|
| 数据后端 | `nfm_db` Postgres(`property_measurements` + `data_sources` + `materials` + `measurement_conditions` + `review_status`) | **外部 Supabase** `qffltopymihbndyrwhvj.supabase.co::nuclear_properties_v4`,硬编码 `service_role` JWT(已 commit) | ✗ **不一致 — 违反「同一数据后端」** |
| 写入路径 | ingest_service seam(C3)→ `map_and_persist` → ontology lookup → 持久化 → `review_status=pending` | 直接 `supabase.table('nuclear_properties_v4').insert(rows)`,**绕过** ingest_service、task_dispatcher、ontology lookup、review_queue | ✗ **不一致 — 绕过 seam** |
| 质量门槛 | confidence ≥ medium 才入 review;`review_status` 流转 | 无 review queue 概念,`confidence` 仅三档枚举,无数值置信度 | ✗ **不一致 — 缺数值置信度** |
| 概念契约 | 13 字段 + `property_note`(同技能) | 13 字段 + `property_note`(同平台) | ✓ 字段一致 |
| Schema 同构 | 11 类/74 属性(本体 v0.4.0) | 11 类/74 属性(property_catalog.md) | ✓ 同构 |
| 人工校对闸门 | `review_status` pending→approved/rejected;操作页并排展示提取+原文 | 无,需手工建审校页面 | ✗ 不一致 |

**判据:技能当前形态不满足「双通道」,原因是后端错配 + seam 绕过 + 闸门缺失。** 这三条不解决,接入即破坏原则。

## 4. NFM-2564 seam 对齐(本票新增视角)

NFM-2564 C1-C4 是平台既定的接入 seam,逐条对照:

| Seam | 位置 | 技能当前形态 | 接入点(若采纳) |
|---|---|---|---|
| C1 vlm_complete | `apps/api/src/nfm_db/services/vision_client.py::vlm_complete` | 技能不调用此 seam,直接跑宿主 LLM(本地 Ollama / OpenAI) | 改造:技能 prompt 走 vlm_complete adapter,获得 multimodal 支持(技能当前只吃 MD,丢 PDF/图) |
| C2 ingest service seam | (NFM-2564 C2 命名) | 完全不经过,直接 HTTP POST 到 Supabase | **强制走**:把 `import_to_supabase_v4.py` 替换为 ingest_service.batch(等价 `ExtractionIngestRequest`),所有数据经 `map_and_persist` 落到 `property_measurements` |
| C3 ingest service | `apps/api/src/nfm_db/services/ingest_service.py::ingest_extraction_batch` | 同上,不经过 | 走 `ingest_extraction_batch(AsyncSession, payload, caller, mapper)`,`caller.is_service_account=True` 触发 `is_auto_created` corpus 路径 |
| C4 task_dispatcher | `apps/api/src/nfm_db/services/task_dispatcher.py::dispatch` | 技能无异步任务概念 | 重构:把「一篇 md 一次抽取」改成 Celery task,经 `dispatch("nfm_db.extraction.extract_paper", kwargs={"md_path": ...})`,获得重试/队列/可观测 |

**判据:四条 seam 当前**全部绕过**——这是#1264 §3「差一个 adapter」结论的展开。** 实际不是「一个 adapter」,而是 C1+C2+C3+C4 四条 seam 都需接通,工作量在 3–5 天(LE 估计)。** 同时带来复用 vlm_complete 的视觉通路(技能当前完全忽略 PDF/表格图)。

## 5. 给 #1252 的建议(三条路线选边)

#1252 三选项(本体目录扩充 / LightRAG-first / 双路并存)的**数据点**而非**决策**:

1. **本体目录扩充(选项 ①)**:与本票直接相关——#1264 已证平台本体 v0.4.0 的「无 escape hatch」是 Beeler 25% 召回的根因。建议方向是把技能 property_catalog.md 的「`其他性能` + `property_note:"目录外"`」机制**反向**回填到 nucpot 本体 v0.5.0:加 `PropertyType.is_catalog_external: bool` + 入 review queue 强制建档。这是#1264 暗示的内容债修复路径,**值得单独立票**,不依赖技能接入决策。

2. **LightRAG-first(选项 ②)**:与本票间接相关——技能对**定性/机制/枚举**类事实主动拒收(0%),这正是 RAG 兜底的应用域。技能接入决策**不应**决定 RAG 路线是否成立;两者在域上正交。

3. **双路并存(选项 ③)**:本票**核心建议**——把技能当作 KG 路径里的「数值属性抽取 prompt-as-skill」,走 C1→C2→C3→C4 全 seam,落地到 `property_measurements` + `review_status=pending`,与网页录入共用同一后端;RAG 路径照旧兜底定性事实。**这是符合双通道原则的唯一组合。**

**不要做的**:把技能当作独立数据后端接入(违反双通道);把 `目录外` 当长期契约(它会让 `property_types` 表漂移失控);忽略技能对 PDF/图的零支持(绕开了 vlm_complete seam 提供的视觉通路)。

## 6. 喂 #1252 的事实清单(供 ADR 起草引用)

- 数值域 100% / 定性域 0% — 域边界清晰,按技能设计意图分配。
- schema 同构(11 类 / 74 属性)— crosswalk 工作量 = 11 行表 + 74 行属性名 + 2 行物相映射。
- 接入成本 = 1 adapter + 1 conditions schema 扩展 + 0.5 phase 列扩展,**C1+C2+C3+C4 seam 全接通(LE 估 3–5 天)**。
- 14s/篇单 LLM 调用 + 零重试,后台跑通(off-peak 不争 staging 资源)。
- 后端错配 = 唯一阻塞项:Supabase JWT 已 commit 是安全债,无论选哪条路线都要先撤回。
- 不携带验证数据集/知识图谱/缺口标识/数据挖掘工作流标签 — 这些由 seam 与既有平台组件补,不是技能责任。

## 7. 留给 #1252 owner 与 CPO 的开放问题

1. 「`目录外` + `property_note`」逃生舱是否要进 nucpot 本体 v0.5.0?(#1264 §7.5.5-C 内容债修复路径,如答是,建议独立票,不绑技能接入)
2. `measurement_conditions` 是否扩列?扩 JSONB 还是加固定列?(决定 conditions 字段落地形态,影响所有抽取管线不止技能)
3. `phase` 列是否在 `property_measurements` 落?(半个重构点,核数据负载字段)
4. 外部 Supabase + 硬编码 JWT 是否回退 + 转 secrets?(安全债,blocking 项)
5. 技能接入实施票(本票阻塞解除后另开)的 scope:仅做数值域、还是含 RAG 兜底协同?(影响 C1/C2/C3/C4 全接 vs 仅 C2/C3)

## 8. 交叉引用

- 父票:NFM-4485(#1263 PR 双通道原则)
- 同源研究票:#1264 实测 + commit `a865071d4`(`research/extraction-skill-eval.md`)
- 决策票(本票喂):Etoile04/nucpot #1252 [G1·grilling] 召回路线决策
- 地图:Etoile04/nucpot #1249 wayfinder map Notes
- 工程票:NFM-2564 C1/C2/C3/C4 seam 系列
- 平台组件:ingest_service seam `apps/api/src/nfm_db/services/ingest_service.py`、vlm_complete `apps/api/src/nfm_db/services/vision_client.py`、task_dispatcher `apps/api/src/nfm_db/services/task_dispatcher.py`、ontology_coverage_report `apps/api/scripts/ontology_coverage_report.py`
- 技能源:`/tmp/nuclear-materials-skills-v4` @ main(浅克隆,2026-09-08 实测)

---

*LE 立场:研究产出,不构成对 #1252 选项权重投票;owner 在 #1252 上有最终裁决权。*