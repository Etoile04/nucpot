# ADR-NFM-4076: session-provider seam —— database.py 原地深化,删除「测试探测」式会话构造

**Status**: Accepted(2026-09-06,owner 确认 grilling 六项决策)
**Date**: 2026-09-06
**Authors**: Hermes Agent(经 /improve-codebase-architecture 扫描 → C1 候选 → grilling),owner 批准
**Supersedes**: none
**Related**: [NFM-4076](/NFM/issues/NFM-4076)(BUG-22 worker loop 修复,本 ADR 是其深化), [ADR-013](../adr/ADR-013-NFM-4266-prod-mutation-guardrails.md)(prod 只读约束), [ADR-NFM-2737](./ADR-NFM-2737-strangler-fig-extraction-dispatch.md)(无冲突,见 §4), [ADR-NFM-2739](./ADR-NFM-2739-extraction-job-dual-class.md)(无冲突)

---

## 1. Context

BUG-22(NFM-4076)之后,`process_literature_sync` 留下了一套自证 seam 缺位的机制:`_factory_is_patched()` 在**生产路径**上探测「模块属性是否被测试 monkeypatch」,据此决定用 task-local NullPool engine 还是脏的模块工厂;失败兜底再开第三个 engine 发裸 SQL。这不是孤例——`async_session_factory` 模块属性在 src 有 9 个文件直接触达(routers 之外的 `tasks/gap_literature_task.py`、`services/md_tasks.py`、`kg_lightrag_sync.py`、`extraction_pipeline.py`、`health_event_emitter.py`、`cli/*` 等),8 个测试文件靠 monkeypatch 它注入 SQLite。

**`tasks/gap_literature_task.py` 已实证带同类潜伏 bug**:它是 `@celery_app.task`,任务体 `asyncio.run(...)` 跑在共享工厂上——共享 asyncpg 池绑定首次使用的 loop,prefork worker 后续任务面临与 BUG-22 相同的 `Future attached to a different loop` 风险,只是尚未爆发。

另有一个反方向事实:仓库已存在真 seam 的样板(`services/providers/` 的 PotentialProvider、`kg_graph.py` 的 session 注入),本决策是把同一 idiom 推广到会话管理,不是发明新模式。

## 2. Decision

### D1. 修类,不修实例;分两阶段

一个 session-provider seam,收编全部 9 处模块属性触达 + `get_db`。Phase A:立 provider + 迁移 `literature_service`(BUG-22 桥、NFM-3322 中毒会话恢复、失败标记)。Phase B:机械迁移其余 7 处,`gap_literature_task` 优先(带潜伏 bug)。

### D2. 原地深化 `database.py`,engine 惰性化

seam 落在 `nfm_db/database.py`,不另立 module。import 时建 engine 的副作用改为惰性首用创建(AGE 扩展加载器语义保留)。`async_session_factory` 与 `get_db` 两个旧名保留为兼容出口。已核实:无任何文件直接 import `engine`,兼容面仅此二名。

### D3. 行为保真:池策略本次不动

Celery adapter 原样保留 BUG-22 语义:每任务 task-local NullPool engine + 用后 dispose。池策略(如 pool_pre_ping、引擎复用)是独立决策,不混入本次——避免回归时无法归因。

### D4. 失败标记与恢复会话收进 implementation

`_mark_failed_async`(裸 SQL UPDATE data_sources)与 NFM-3322 fresh-session 恢复收为 session-provider implementation 的方法/通道。按 one adapter = hypothetical seam 规则,不为它们立 port。

### D5. 依赖经参数接受

`process_literature_sync` 增加可选 session-factory 参数,默认走 provider 的 task-scoped 路径。测试注入 SQLite factory,monkeypatch 模块属性退役。Celery 注册处零改动。

### D6. 验收不变式

1. `process_literature_sync` 用注入的 SQLite factory 跑通,全程零 monkeypatch;
2. adapter 层 BUG-22 回归测试:同一 worker 连续两个 `asyncio.run` 任务不共享任何池状态;
3. 迁移全程现有测试全绿,API 契约逐字节不变;
4. 不动 prod schema;验证只在本地/docker(ADR-013)。

## 3. Considered & rejected

- **另立 `session_provider.py`**:干净但多一次全库 import 迁移,且 `database.py` 已是名义落点——拒绝。
- **顺带改池策略**:污染归因——拒绝,留作独立决策。
- **为失败标记/恢复会话立 port**:各只有一条实现路径,假想 seam——拒绝。
- **只修 `process_literature_sync`(修实例)**:gap task 的潜伏 bug 仍在,seam 退化为单 adapter 假想 seam——拒绝。

## 4. ADR 关系

不触碰 ADR-NFM-2737 的 flag 语义(本决策不涉及抽取 dispatch 入口)与 ADR-NFM-2739 的 `_extraction_job_to_dict` 24-key 序列化边界。迁移 Phase B 涉及 `extraction_pipeline.py` 时只动其会话获取行,不动管线逻辑。`kg_to_staging_bridge` 的 `_PROPERTY_SLUGS` 分解(ADR-NFM-4000)不受影响。
