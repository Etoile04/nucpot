# NucPot 领域词汇表

核燃料与材料物性数据库平台的领域语言。势函数资产、文献、验证与质量评级相关术语以本表为准。

**平台背景(工作流总览)**:势函数的验证需要实验测量数据集支撑——验证状态、验证等级与质量等级衡量的都是「基于特定势函数的分子动力学计算结果」与「验证数据集」的偏差。数据集从文献库(本地 Zotero)与 Materials Project 等外部源获取;材料知识图谱由抽取管线对文献库 PDF 建立材料↔性能关联,帮助 LLM 理解成分/工艺/性能的映射、避免跨材料混淆;每条数据带来源标注,低置信度提取结果经人工校对(操作页并排展示提取结果与原文相关段落);材料数据 schema 描述数据集应含内容并自动评估完整性;缺失项在数据表做缺口标识,由智能体数据挖掘工作流按关键词从互联网搜索、下载文献入库后走既有抽取流程——闭环反哺验证。**平台坚持双通道录入**:同一套数据后端同时支撑「网页页面录入」(专家逐条精确提交)与「智能体驱动自动提取」(由专用抽取 skill 对文献 PDF 批量产出,如 `nuclear-materials-skills-v4`)两种录入方式;两条通道共用材料数据 schema、来源标注与人工校对闸门,产物入库口径一致,差异只在吞吐形态而非数据契约。

## Language

**势函数 (Potential)**:
描述原子间相互作用的参数化模型条目,平台的核心资产。
_Avoid_: 势、电位

**文献库条目 (Publication)**:
文献管理库中独立存在的论文记录(含 DOI、期刊、年份、抽取产物)。
_Avoid_: 文献(与「文献引用」歧义时)

**文献引用 (References)**:
内嵌在势函数条目上的出处引用列表(doi + citation),记录该势函数 published 出处。
_Avoid_: 关联文献、参考文献(歧义时)

**文献关联类型 (Relation type)**:
势函数与文献库条目之间关系的分类:primary(原始)/ validation(验证)/ application(应用)/ review(综述)。

**验证状态 (Verification status)**:
该势函数的分子动力学计算结果是否已对照验证数据集完成偏差评定。`unverified`(未验证)是正常初始态,不是缺陷;没有对应验证数据集的性质无法进入评定。

**验证等级 (Verification grade)**:
单个性质上,基于该势函数的分子动力学计算值与验证数据集参考值的偏差分级(A–F)。

**质量等级 (Quality level)**:
势函数的整体评级,1–5 共五级,5 最好。由验证等级线性映射自动打底(A→5、B→4、C→3、D→2、F→1),允许人工覆盖;等级来源(自动/人工)随值记录。未验证的势函数为「未评级」,不占数值。
_Avoid_: 星级、评分

**下载通道 (Download channel)**:
用户获取势函数文件的唯一规范途径:平台代理下载。对象存储直链不作为对外契约,不对第三方暴露。
_Avoid_: 直链、外链

### 验证数据集与数据供给

**验证数据集 (validation dataset)**:
用于评定势函数计算偏差的实验测量数据集合(晶格常数、弹性常数、缺陷形成能等),按材料组织。验证状态、验证等级、质量等级衡量的都是分子动力学计算结果与它的偏差;没有对应数据集支撑的等级不产生。
_Avoid_: 参考值(泛指时)、测试集

**材料知识图谱 (material knowledge graph)**:
对文献库 PDF 经抽取管线建立的「材料-成分/工艺-性能」关联网络。用途是让 LLM 在抽取与问答时把性质锚定到正确材料,避免不同材料的性质相互混淆;抽取结果同时服务于结构化入库与语义问答两条路。
_Avoid_: 图谱(泛指时)、OntoFuel 图(指本体可视化时)

**来源标注 (source attribution)**:
每条入库数据携带的出处信息(来源文献或外部源、原文位置)。没有来源标注的提取结果不可信;它是人工校对与溯源核实的入口。
_Avoid_: 引用(指势函数文献时)

**人工校对 (human verification)**:
对提取结果(尤其低置信度)的人工核实环节。校对操作页必须并排展示提取结果与原文相关段落;校对通过与否决定数据进入验证数据集的资格。
_Avoid_: 审核(泛指时)

**材料数据 schema (material data schema)**:
描述某类材料的验证数据集应包含哪些数据的规范。用于自动评估当前数据集的完整性,产出缺失清单。
_Avoid_: 数据模板、字段表

**缺口标识 (gap mark)**:
数据表中按材料数据 schema 评估出的缺失项标记。是数据挖掘工作流的触发信号。
_Avoid_: 空值、缺失值(指普通 NULL 时)

**数据挖掘工作流 (data mining workflow)**:
智能体驱动的补缺闭环:按缺口标识生成关键词 → 从互联网搜索并下载相关文献至文献库(或从 Materials Project 等外部源取数)→ 走既有抽取、来源标注与人工校对流程入库。产出回到验证数据集,闭环反哺势函数验证。
_Avoid_: 爬虫(指整站抓取时)、ETL(指无智能体决策时)

**录入通道 (ingestion channel)**:
平台向验证数据集写入材料的两种规范途径——网页页面录入(专家在前端表单逐条精确提交)与智能体驱动自动提取(由专用抽取 skill 对文献 PDF 批量产出)。两条通道共用同一套数据后端、material data schema、来源标注与人工校对闸门,产物入库口径一致;区分通道用途与吞吐形态,不区分产出来源——同一份材料数据,网页录入与自动提取可由校对关合并入同一数据集。
_Avoid_: 数据来源(泛指时)、前端录入(指通道时)、抽取管线(指整段后台时)

### Wayfinder 同步

数据供给闭环的完整设计在 wayfinder 地图 [NFM-3830](/NFM/issues/NFM-3830) (PILOT-C G3 数据采集闭环修复设计地图) 的 Notes / 决策注释中沉淀;七个数据供给族术语加「录入通道」共同给那张地图上的 G3 闭环与双通道录入原则提供领域语言入口。后续 wayfinder 地图(数据挖掘 / 缺口闭环 / 录入通道相关)开 PR 时,Notes 应锚定本节术语而非另起本地词汇——尤其当 Notes 描述哪条通道产生了什么产物时,必须用「网页页面录入 / 智能体驱动自动提取」与「录入通道」而不是泛称「数据来源」或「前端录入」。

数据集生命周期与抽取形态在 wayfinder 地图 [#1249](https://github.com/Etoile04/nucpot/issues/1249) 终点票 [#1257](https://github.com/Etoile04/nucpot/issues/1257) 坍缩入库(对应 Paperclip [NFM-4535](/NFM/issues/NFM-4535));G1 抽取价值呈现区 6 决 + RAG 开放区 3 决 + RAG 质量承诺区 4 决 + 数据集生命周期区 4 决合并为两份可建 spec([G1-extraction-value-presentation](./docs/specs/G1-extraction-value-presentation.md)、[RAG-anonymous-open-and-quality](./docs/specs/RAG-anonymous-open-and-quality.md))、两份 ADR([ADR-016 技能引擎](./docs/adr/ADR-016-NFM-4535-skills-engine-as-extraction-engine.md)、[ADR-017 数据集生命周期](./docs/adr/ADR-017-NFM-4535-dataset-lifecycle.md))与本节「数据集生命周期」术语集。后续 wayfinder 地图涉及"每文献数据集 / 快照式版本 / 整版本回退 / 按源剔除 / 技能引擎 / 目录外逃生舱 / 分层 SLA / 透明回退"等概念时,Notes 应锚定本节术语而非另起本地词汇。

### 数据访问 seam

**会话提供者 (session-provider)**:
nfm_db 中唯一决定「谁、何时、以何种池策略获得数据库会话」的 module(`nfm_db/database.py`)。所有会话经它的 seam 获取——FastAPI 请求经 `get_db` adapter,Celery 任务经 task-scoped adapter;测试在 seam 上注入替身,不 patch 模块属性(见 ADR-NFM-4076)。
_Avoid_: database utils、DB helper、调用点自建 engine

**parse 失败标记 (parse failure mark)**:
抽取管线崩溃时对 DataSource 行的 best-effort 兜底写入(`parse_status='failed'`),绝不掩盖原始异常。它是 session-provider implementation 的应急通道,不是独立 module。
_Avoid_: failure reporter、status writer

### 图谱视图

**图谱视图 (graph view)**:
以节点-连线呈现知识图谱(或其邻域)的页面能力。图谱视图消费共享的图谱画布,不各自实现布局、渲染与交互。
_Avoid_: 图谱页面(泛指时)、图谱组件(指实现时)

**图谱画布 (graph canvas)**:
所有图谱视图背后的共享深 module:布局模拟、渲染、视口控制与状态信号的唯一 owner。消费方经其 interface 获得行为,不绕过它直接操作模拟或 DOM。
_Avoid_: 画布组件、图表容器

**布局收敛 (converged)**:
力导向模拟自然达到稳定、停止迭代的状态。收敛信号由图谱画布发出,消费方不需要也不应该自行判断「算完了没」。

**布局定格 (settled)**:
布局模拟达到超时上限后强制停止、以当前布局交付使用的状态。「收敛」与「定格」都是可用态,交互均已解锁;区别仅在布局质量,消费方可选择是否对用户作轻量区分提示。
_Avoid_: 超时(指故障时)、失败

**视口控制 (viewport control)**:
对图谱画布的缩放、平移、适配视野等操作。视口控制的 interface 由图谱画布暴露,页面级工具栏是其 adapter 之一,不各写一份控制逻辑。
_Avoid_: 缩放工具、画布操作

**viewportApi (viewportApi)**:
图谱画布经 `ref` 暴露的命令式视口句柄(zoomIn/zoomOut/fit/reset),由 `GraphCanvas` 通过 `forwardRef` 提供给 `/kg/explore` 等页面的工具栏消费,不再各写一份 `useGraphControls`。源:`apps/web/src/components/graph/GraphCanvas.tsx`、`apps/web/src/app/kg/explore/KgExploreView.tsx`。

**GraphViewportApi (GraphViewportApi)**:
`viewportApi` 的 TypeScript 类型,4 个方法(zoomIn/zoomOut/fit/reset)与运行时同名;`fit` 与 `reset` 当前等价(spec parity)。源:`apps/web/src/components/graph/types.ts`。

**useGraphView (useGraphView)**:
图谱视图统一的 5 态数据状态机 hook(`loading` | `fetch` | `error` | `empty` | `retry`),内部封装 TanStack Query `useQuery`,供 `KgExploreView` / `MaterialGraphView` / `MaterialSubgraphView` 共用同一 `data / status / retry` 形态。源:`apps/web/src/hooks/useGraphView.ts`。

**layoutStatus (layoutStatus)**:
力导向布局的 3 态收敛信号(`running` | `converged` | `settled`),由 `useForceGraph` 导出,`running` 表示模拟在飞、`converged` 为自然结束、`settled` 为超时定格或空数据/错误兜底;与历史 `isRunning` 兼容。源:`apps/web/src/components/graph/useForceGraph.ts`。

### 图谱标识与列表

**图谱节点标识 (graph node id)**:
知识图谱节点的标识,由图谱体系自行分配,不是材料标识。携带图谱节点标识的响应若涉及材料,必须同时携带材料标识或显式无桥标记;由消费方自行猜测两套标识的关系是禁止的。
_Avoid_: 节点 uuid(与材料 uuid 混称时)

**材料标识 (material id)**:
材料在材料库中的主键,材料详情页 URL 的身份来源。图谱场景下从图谱节点标识经桥接获得;无桥接的节点不产生导航。

**分页视图 (paged list)**:
以固定页大小浏览长列表的能力。页码是可分享状态(进 URL);翻页、URL 同步与滚动复位由共享的分页模块统一持有,列表页面不自建分页内脏。
_Avoid_: 翻页控件(指能力时)

**提交状态 (submit state)**:
表单提交的生命周期状态:idle | submitting | success | error。由共享的提交模块统一持有与呈现,表单不自写三布尔变体。
_Avoid_: loading 旗标、submitting 布尔

### 数据集生命周期(NFM-4535 入库,wayfinder #1257 坍缩)

`property_measurements` 行的存在形态与流转形态——按 ADR-017 「每文献私有数据集 + 快照式版本」落实。七个数据供给族术语(见上)描述"为什么有数据";本节描述"数据如何被组织、如何被验证、如何被回退"。

**数据集 (dataset)**:
某篇文献抽取产物的容器,每文献一个私有集;身份由文献 DOI 优先 + 内容哈希兜底判定。同文献重抽不产生新数据集,只产生新版本。
_Avoid_: 资料库(泛指时)、验证集(指共享池时)

**每文献私有数据集 (per-literature dataset)**:
数据集的归属模型——每篇入库文献独占一个 `datasets.id`,不与同材料、同成分的他文献共享。共享池是消费层视图(同一材料下多数据集查询),不是存储层形态。
_Avoid_: 多对一合并(指存储时)、共享池(指存储时)

**数据集版本 (dataset version)**:
数据集在某一时点的快照式版本,由通过校核门的行集合 + 来源清单 + 元数据组成(`dataset_versions` 表,wayfinder #1280 Q7 决议)。版本号单调递增;**不允许行级 delta**(增量将破坏可回退性)。验证 / 物性页 / 检索 / 导出消费发布版;校对页消费工作版。

**发布版 (released)**:
数据集的稳定对外版本,默认 API 视图(`?dataset_version=released`)。验证等级、AutoVC 验证结果均记录所用发布版号,等级可追溯到数据版本。
_Avoid_: 稳定版(泛指时)、最新版(指快照语境时)

**工作版 (draft)**:
数据集的校对进行中版本,仅校对页可见;不对外、不参与验证、不入检索。domain_expert 校对通过 → 触发发布 → 升级为发布版。
_Avoid_: 草稿(泛指时)、dev 版(指通道时)

**按源剔除 (exclude-by-source)**:
数据集版本回退操作之一——新建副本并移除指定 `source_id` 贡献的行(替代今日人工 SQL 路径,见 NFM-4394/4395)。owner 用例:删掉某篇文章引入的数据。每一次按源剔除写 `audit_log`,审计可追。

**整版本回退 (whole-version rollback)**:
数据集版本回退操作之二——切换 `released` 指针到任一历史版本(不必是最新)。代价低、审计完整,9-02 类事故的根治路径。

**目录外逃生舱 (out-of-catalog escape hatch)**:
抽取管线遇到本体目录未覆盖的属性类别时,显式新建 PropertyType + 标记 `pending_review`,不经 verification 主表直至人工校对通过(ADR-016 §2.3 + wayfinder #1252 Q2 决议)。它是「目录封闭性」的解药,不是目录扩充的替代品;通过条目可反哺目录扩充决策。
_Avoid_: 新增属性(泛指时)、白名单机制(指实现时)

**技能引擎 (skill engine)**:
生产路径 A 的抽取引擎——以 `nuclear-materials-skills-v4` 的 `nuclear-property-extraction-v4` 技能替入既有本体驱动抽取 prompt(ADR-016 §2.1,wayfinder #1252 主决策)。平台不内化技能仓库;锁版引用(`EXTRACTION_SKILL_REPO_PIN`),上游独立演进。
_Avoid_: LLM 直调(指生产路径时)、prompt 工程(指实现时)

**source_span (段落级溯源)**:
property_measurements 行级溯源,定位到原文段落:`{file, page, char_start, char_end, snippet_hash}`(ADR-016 §2.6 + wayfinder #1252 Q7)。校对页需原文段落并排展示(操作页标准);文件级溯源不够。技能版本含 source_span 之前由 adapter 启发式匹配兜底。

**validity_check (validity_check)**:
落库时按属性 `valid_range` 跑的物理有效域校验结果,落在行的 `validity_check` jsonb 列:`{status: "ok"|"warn"|"fail", reason: str|null}`。`fail` 直接标 `review_status='invalid'`,校对页红行 + 悬停原因;`warn` 不阻断但显提示(wayfinder #1253 取证 §②)。防止键长 0.3Å、密度 0.05 g/cm³ 之类物理无效值混入主表。

**dedupe_key (dedupe key)**:
property_measurements 行的复合去重键 `(dataset_id, property_type_id, source_id, value_hash)`,唯一约束,摄取 upsert 依赖。`value_hash` 同值 + 同源 + 无条件差异才合并;有条件差异保留各行(不同测量)(wayfinder #1253 取证 §①)。根治 Owen 92 行同值重复事故的存储层契约。

**校对动作集 (review actions)**:
domain_expert 在校对抽屉执行的六态决策集:`pending`(低置信度自动)/ `confirmed`(确认通过)/ `modified`(需修改)/ `invalid`(标记无效)/ `disputed`(来源存疑)/ `skipped`(跳过)(wayfinder #1253 Q2 决议)。写回 property_measurements 既有列(`review_status`/`reviewer_note`/`reviewed_at`),不新建 draft 表。

**分层 SLA (tiered SLA)**:
RAG 检索的三档质量承诺(Tier-1 已索引秒级 P95<1s / Tier-2 fresh P95<30s,NFM-4525 修后收紧 <10s / Tier-3 超时透明回退)(wayfinder #1256 Q4)。每档触发条件明确,周报可追。

**匿名开放 (anonymous open)**:
RAG 检索的开放策略——移除 `require_editor`、端点级限次 5/min/IP、匿名与登录一致体验、不做差异化(wayfinder #1255 Q1/Q3 三决议)。实施前置 #1258(NFM-4492 已闭环)。可逆:随时重挂登录墙,故不立独立 ADR。

**透明回退 (transparent fallback)**:
RAG 检索超时(≥`NFM_LIGHTRAG_QUERY_TIMEOUT_S` = 30s)→ ILIKE 文本检索兜底 + 响应 `fallback.used=true` + UI 徽标"语义检索超时,已回退文本检索",绝不静默(NFM-3404 + wayfinder #1256 Q2)。审计 `access_log.fallback_kind='iliKE'` 计数。
