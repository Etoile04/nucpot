# RAG 匿名开放与质量承诺 — 实现 spec

> wayfinder #1249 → #1257 坍缩 · RAG 区
> 来源决议:[Etoile04/nucpot#1254](https://github.com/Etoile04/nucpot/issues/1254) · [#1255](https://github.com/Etoile04/nucpot/issues/1255) · [#1256](https://github.com/Etoile04/nucpot/issues/1256) · [#1258](https://github.com/Etoile04/nucpot/issues/1258)
> 关联 ADR:无独立 ADR(#1255 决议可逆、不立;三档超时框架 ADR-NFM-3404 承载)
> 状态:**可建**(全部 owner 亲答;前置 #1258 已闭环 NFM-4492)

## 1. 目标

把检索定位为 P0 公开功能,让**匿名访客能直接用 LightRAG**——不挂登录墙、不做条件渲染。同时给出**承诺过的质量等级**:用户拿不到结果时知道拿到的是什么,运营侧有 SLA 可追,失败有兜底有审计。

具体:

1. **开放策略**:匿名 + 登录一致体验,端点级限次(~5/min/IP,可配)。
2. **覆盖保障**:100% completed 文献入库即索引;Celery beat 每日对账兜底 hook 静默失败。
3. **超时降级**:超时 → 自动 ILIKE 文本检索 + 透明标注,不静默。
4. **分层 SLA**:已索引秒级 / fresh <30s 收紧到 <10s / 空结果诚实文案。

## 2. 不在本 spec 范围

- 抽取价值呈现(见 [G1 spec](./G1-extraction-value-presentation.md));
- 技能引擎与 adapter(同上,owner 隔离);
- LightRAG 基础设施修复(由 #1258 / NFM-4492 / NFM-4501 闭环;本 spec 不复述);
- 知识图谱构建(KG 是抽取侧的产出,检索消费但本 spec 不改 KG schema);
- 用户体系差异化(决策明确不立差异化,#1255 Q3);
- 重建 LightRAG 服务认证(sidecar 9621 直连零认证是 G5 边界内观察,不在 RAG 开放票范围)。

## 3. 鉴权与限流契约

### 3.1 后端:端点级限次

| 项 | 现值 | 目标 |
|---|---|---|
| `require_editor` on `/api/v1/lightrag/query` | 强制 | **移除** |
| 端点限流(slowapi per-route) | 无 | **5/min/IP**(可配 `NFM_RAG_QUERY_RATE_LIMIT` 默认 5) |
| 全局限流(已有) | 100/min/IP + 20/s burst, `memory://` | 维持;**不接 Redis**(本 spec 范围外) |
| `X-RateLimit-*` headers | 取决于 slowapi 默认 | 必须暴露(便于前端诚实提示) |

### 3.2 前端:移除登录墙 + 诚实中态

- `/search` 页面移除"需登录"拦截;匿名用户可直达搜索框。
- 查询中态显示**真实预期**(已索引/缓存 < 1s · fresh 可能较长)。
- 结果为空时:**"知识库暂未覆盖该问题"**(UAT-6 诚实呈现)。
- 超时回退触发时:UI 徽标"语义检索超时,已回退文本检索"。

### 3.3 限流响应

- HTTP 429 + `Retry-After` header(slowapi 默认行为)。
- 前端:不重试;提示"请求过快,请稍候"。
- 后端:`access_log` 记录 `query_kind`, `result_count`, `time_total`, `mode`, `was_fallback`, `was_cached`。

## 4. 同步与覆盖契约

### 4.1 实时索引(已有 hook)

- 触发:`process_literature` 完成时 inline ingest(`literature_service.py:1025-1066`,BUG-04/NFM-4083 修复,PR #1141)。
- SLA:处理完成 → 分钟级内索引可查。
- 已知风险:hook 静默失败(BUG-04 教训);由 §4.2 对账兜底。

### 4.2 每日对账(新增 Celery beat)

- 任务:`rag_audit_index_coverage`(每日 03:30 UTC,见 NFM-4257 自动化窗口)
- 逻辑:
  1. `GET /api/v1/literature?status=completed` 拉全量(分页)。
  2. 对每条 `literature.id` 比对 `lightrag /documents`(`GET /documents`,按 `data_source:<uuid>` 标的内容摘要级核对)。
  3. diff = 已 completed 但未索引 → 走既有 `process_literature` 摘录侧触发 ingest(避免双写);记录 `audit_log` 行。
  4. 失败告警:`nfm_db` 已有 NFM-4406 类 watchdog 模式;超阈值(SLA miss > 1%)→ 告警。

### 4.3 索引健康指标(可选看板,本 spec 不强制)

- `lit_completed_total` / `lit_indexed_total` / `lit_diff_count`(看板指标,监控 backlog)。
- 不入实时告警;只在周报/异常巡检看。

## 5. 超时降级契约(三档超时框架)

| 档位 | env | 现值(NFM-4492) | 收紧触发 |
|---|---|---|---|
| API client 预算 | `NFM_LIGHTRAG_QUERY_TIMEOUT_S` | 30.0 | NFM-4525 修后改 10.0(NFM-4525 backlog follow-up) |
| Sidecar 内部 | upstream `LIGHTRAG_TIMEOUT` | 60.0 | 维持 |
| Frontend abort | `NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS` | 45000 | NFM-4525 修后改 15000 |

降级链路:

```
query 发起
  ├─ < 1s + 缓存命中 → 直接返回(秒级,验收 P95<1s)
  ├─ < 30s(API 预算)+ 成功 → 返回(验收 P95<30s,NFM-4525 修后改 <10s)
  ├─ ≥ 30s 超时 → 触发 ILIKE 文本检索回退 + 透明标注
  └─ ILIKE 也无结果 → "知识库暂未覆盖" 诚实文案
```

回退标注契约:

- 响应体:`{response, references, fallback: {used: true|false, kind: "iliKE"|null, original_error: str|null}}`。
- 前端:fallback.used=true → 徽标"语义检索超时,已回退文本检索"。
- 审计:`access_log.fallback_kind='iliKE'` 计数;周报看板。

## 6. 分层 SLA 承诺

| 档 | 触发条件 | 承诺 | 实测基线(NFM-4492 闭环) | 验收 |
|---|---|---|---|---|
| **Tier-1 秒级** | 缓存命中 / 已索引 + 简单查询 | **P95 < 1s** | 0.1s 冷 / 0.1s 暖(NFM-4503 实测 02:55Z) | `access_log.time_total` P95 over 7d < 1s |
| **Tier-2 fresh** | 新文献 / 罕见实体 / 长尾问题 | **P95 < 30s**(NFM-4525 修后收紧 **< 10s**) | 8s 预算时代 63.7s 失败;30s 后 0.2-1.5s | `access_log.time_total` P95 over 7d < 30s |
| **Tier-3 透明回退** | 任意超时 | **ILIKE 兜底 + 徽标** | 8s API 必超时即回退(NFM-3404 经验) | `fallback.used=true` 计数 + 周报 |
| **空结果** | 索引未覆盖 | **诚实文案 + 引用数**(0 时明示) | 已实现(NFM-4307) | UAT-6 验收 |

承诺兑现节奏:

- **当前(Tier-1/2)**:已闭环 NFM-4492(PR #1272 + PR #1276 + PR #1277);Tier-2 验收 30s 线先放,**收紧到 10s 等待 NFM-4525 修复**。
- **未来(NFM-4525 修后)**:Tier-2 P95 改 <10s,前端 loading 文案相应缩短预期。

## 7. 集成点

- 后端:
  - `apps/api/src/nfm_db/main.py` — 移除 `/api/v1/lightrag/query` 的 `require_editor` 依赖。
  - `apps/api/src/nfm_db/middleware/rate_limit.py` — 新增 `rag_query_rate_limit` slowapi 装饰器(5/min/IP)。
  - `apps/api/src/nfm_db/services/rag_audit.py` — 新增每日对账任务 `rag_audit_index_coverage`。
  - `apps/api/src/nfm_db/services/lightrag_query.py` — 响应体加 `fallback` 字段(见 §5 契约)。
  - Celery beat 配置:加 `rag_audit_index_coverage` 到 beat schedule(03:30 UTC)。
- 前端:
  - `apps/web/src/app/search/...` — 移除登录拦截;加超时徽标组件 `<RagFallbackBadge>`。
  - `apps/web/src/lib/rag.ts` — 维持 NFM-4525 后的 abort 阈值改动。
- 配置(env 模板):
  - `NFM_RAG_QUERY_RATE_LIMIT`(默认 5)
  - `NFM_RAG_AUDIT_INTERVAL_HOURS`(默认 24)
  - `NEXT_PUBLIC_RAG_FALLBACK_LABEL`(默认"语义检索超时,已回退文本检索")

## 8. 行为规约

### 8.1 匿名查询

1. 用户进 `/search` → 任意输入 → 直接发请求(无登录拦截)。
3. 后端:`require_editor` 已无 → 进 slowapi 限流(5/min/IP) → 命中则 429 + Retry-After;未命中则放行。
4. 走 LightRAG 查询路径,响应体带 `fallback` 字段。
5. 前端:渲染结果;若 `fallback.used=true` → 显徽标;若 `references=[]` → 显诚实文案。

### 8.2 索引对账

1. Celery beat 触发(每日 03:30 UTC)→ `rag_audit_index_coverage`。
2. 拉文献库全量 vs LightRAG 索引 diff。
3. diff 非空 → 对每条触发既有 ingest 路径(幂等,DOI/content_hash 命中即跳过)。
4. 写 `audit_log`:每条 diff 一行(`audit_kind='rag_index_drift'`,`action='reingest'` 或 `'noop'`)。
5. 失败 → 抛异常入告警链(NFM-4406 watchdog 模式)。

### 8.3 超时回退

1. LightRAG 查询 ≥ `NFM_LIGHTRAG_QUERY_TIMEOUT_S` → sidecar 抛 timeout。
2. API 捕获 → 触发 ILIKE 文本检索(已有回退路径,见 NFM-3404)。
3. ILIKE 返回空 → "知识库暂未覆盖该问题"(UAT-6)。
4. 响应体填充 `fallback` 字段,写 `access_log`。

## 9. 不变量与边界

- **开放不可逆决策不算,但实施不可抢跑**:前置 #1258 必须已合入生产(已闭环 NFM-4492 2026-09-09)。
- **限流是底线**:移除登录墙不等于无任何护栏;端点级 5/min/IP 必须保留(slowapi fail-closed)。
- **回退必须透明**:超时即降级,但绝不能静默——徽标 + `fallback` 字段 + `access_log` 三处一致。
- **覆盖承诺 = hook + 对账双轨**:hook 失败由对账兜底;不立"hook 必成功"承诺。
- **诚实优于完整**:空结果宁可明示,不要"看起来有内容"。
- **NFM-4525 维持 backlog**:fresh 短板是已知状态,本 spec 不试图修复,只承诺 + 透明。

## 11. 验收(AC)

- **AC-1**:`/api/v1/lightrag/query` 移除 `require_editor`;匿名 + 已登录响应一致。
- **AC-2**:端点限流 5/min/IP 生效;超限 429 + `Retry-After`。
- **AC-3**:Celery beat 每日 03:30 UTC 跑 `rag_audit_index_coverage`;diff 行写入 `audit_log`。
- **AC-4**:超时(≥30s)→ ILIKE 兜底 + 响应 `fallback.used=true` + UI 徽标。
- **AC-5**:Tier-1 P95 < 1s over 7d;Tier-2 P95 < 30s over 7d(NFM-4525 修后改 < 10s)。
- **AC-6**:空结果响应带诚实文案 + `references=[]`(UAT-6)。
- **AC-7**:`access_log` 新字段:`mode`, `was_fallback`, `was_cached`, `query_kind`, `result_count`, `time_total`。
- **AC-8**:本周看板(可选)暴露 `lit_completed_total` / `lit_indexed_total` / `lit_diff_count`。

## 13. 实现票分拆

| 票 | 范围 | 依赖 |
|---|---|---|
| RAG-A 鉴权开放 | 移除 `require_editor` + 端点限流 | #1258 闭环 ✅ |
| RAG-B 响应契约 | `fallback` 字段 + `access_log` 扩展 |  |
| RAG-C 前端去墙 | `/search` 移除登录拦截 + 徽标组件 | RAG-A,B |
| RAG-D 每日对账 | `rag_audit_index_coverage` task + Celery beat 注册 |  |
| RAG-E 看板指标 | Tier P95 / lit_diff 计数暴露 | RAG-B |
| RAG-F NFM-4525 触发线 | backlog follow-up;修后改 `NFM_LIGHTRAG_QUERY_TIMEOUT_S=10.0` + 前端预期文案 | NFM-4525 done |

## 14. 依据链

- [wayfinder #1249 地图](https://github.com/Etoile04/nucpot/issues/1249) — RAG 区整体设计入口
- [#1254 LightRAG 现状盘点](https://github.com/Etoile04/nucpot/issues/1254) — 17 docs / 63.7s / 8s 预算 / sidecar 裸奔事实
- [#1255 匿名开放策略](https://github.com/Etoile04/nucpot/issues/1255) — 全开 + 端点限次 + 一致体验
- [#1256 RAG 质量承诺](https://github.com/Etoile04/nucpot/issues/1256) — 分层 SLA + hook/对账双轨 + 透明回退
- [#1258 基础设施修复](https://github.com/Etoile04/nucpot/issues/1258) — embedding 对齐 + VDB 重建 + 三档超时(NFM-4492 已闭环)
- ADR-NFM-3404 — 三档超时框架(本 spec 不重立)
- NFM-4257 — prune 自动化窗口 03:30 UTC(对账任务复用)
- NFM-4307 — UAT-6 诚实文案