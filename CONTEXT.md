# NucPot 领域词汇表

核燃料与材料物性数据库平台的领域语言。势函数资产、文献、验证与质量评级相关术语以本表为准。

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
该势函数是否经过自动验证闭环。`unverified`(未验证)是正常初始态,不是缺陷。

**验证等级 (Verification grade)**:
自动验证产生的逐属性与总体等级(A–F),反映单个性质计算值与参考值的偏差。

**质量等级 (Quality level)**:
势函数的整体评级,1–5 共五级,5 最好。由验证等级线性映射自动打底(A→5、B→4、C→3、D→2、F→1),允许人工覆盖;等级来源(自动/人工)随值记录。未验证的势函数为「未评级」,不占数值。
_Avoid_: 星级、评分

**下载通道 (Download channel)**:
用户获取势函数文件的唯一规范途径:平台代理下载。对象存储直链不作为对外契约,不对第三方暴露。
_Avoid_: 直链、外链

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
