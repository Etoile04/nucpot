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
