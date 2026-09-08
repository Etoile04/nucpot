# NucPot 领域词汇表

核燃料与材料物性数据库平台的领域语言。势函数资产、文献、验证与质量评级相关术语以本表为准。

**平台背景(工作流总览)**:势函数的验证需要实验测量数据集支撑——验证状态、验证等级与质量等级衡量的都是「基于特定势函数的分子动力学计算结果」与「验证数据集」的偏差。数据集从文献库(本地 Zotero)与 Materials Project 等外部源获取;材料知识图谱由抽取管线对文献库 PDF 建立材料↔性能关联,帮助 LLM 理解成分/工艺/性能的映射、避免跨材料混淆;每条数据带来源标注,低置信度提取结果经人工校对(操作页并排展示提取结果与原文相关段落);材料数据 schema 描述数据集应含内容并自动评估完整性;缺失项在数据表做缺口标识,由智能体数据挖掘工作流按关键词从互联网搜索、下载文献入库后走既有抽取流程——闭环反哺验证。

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
