# LightRAG 现状盘点:索引覆盖 / 查询成本 / 限流基建

> 研究票:[Etoile04/nucpot#1254](https://github.com/Etoile04/nucpot/issues/1254)(wayfinder:research,Part of #1249)
> 分支:`research/lightrag-status`(基于 origin/main @ `2fe31261d`)
> 实测日期:2026-09-08 · Mac Studio 本机 docker(`nucpot-prod-lightrag`,端口 9621,LightRAG Server **v1.5.4/0313**)
> 纪律:只找事实,不做产品决策。所有结论附命令或代码引用,可复现。

## TL;DR(一行结论 × 3)

| 研究问题 | 一行结论 |
| --- | --- |
| ① 索引覆盖 | sidecar 17 个 processed 文档(0 pending/failed)= 14 篇文献衍生 + 3 条非文献内容,与文献库 14 条 completed 记录计数吻合;同步纯钩子驱动(process_literature 完成时 inline ingest),**无任何周期调度/对账任务**;但 PGVector 三张向量表为空、embedding 模型 404,「覆盖」只存在于 KV/图存储层。 |
| ② 查询成本 | 实测恰好一次 hybrid 查询:**HTTP 200、63.68s、返回 "No relevant context found"**——keyword LLM(本地 qwen3.8:27b-mlx)耗 ~60s 后 embedding 404 失败;API 侧客户端预算仅 8s(NFM-3404 后默认),该查询在任何用户路径都会先超时回退,63.7s 本地算力被白白算完。 |
| ③ 限流基建 | 已有 slowapi 全局限流(100/min + 20/s burst,按 IP,`memory://` 存储,未接 Redis);端点级限流**仅 auth 三条**;`/api/v1/lightrag/query` 现要求 editor 角色(匿名到不了 sidecar),但 **sidecar 9621 本身无任何认证**,是绕过路径;前端 /search 文本模式有 300ms debounce,语义模式为提交驱动 + 单并发 + 45s abort,无前端限次。 |

---

## ① 索引覆盖现状

### 数字对齐

| 口径 | 数值 | 来源(2026-09-08) |
| --- | --- | --- |
| LightRAG 已索引文档(processed) | **17** | `curl -s http://localhost:9621/documents` → `{"statuses": {"processed": [17 docs]}}`,无 pending/processing/failed 键 |
| 文献库总数(生产 API) | **14**(全部 `status=completed`) | `curl -s -A "Mozilla/5.0" "https://nucpot.dpdns.org/api/v1/literature?page=1&page_size=100"` → `data.total=14` |
| 图谱规模 | 736 nodes / 950 edges / 41 text_chunks / 232 LLM-cache | 容器启动日志(`docker logs nucpot-prod-lightrag`) |

issue #1254 写的「文献库 17 篇」实为 **sidecar 文档总数**。对 17 个文档逐个看 `content_summary`:其中 **3 个是非文献内容**——

1. `doc-0920f13d` "UO2 (uranium dioxide) is the most widely used nuclear fuel…"(EN 知识片段)
2. `doc-2803b93d` "二氧化铀(UO₂)是轻水反应堆中最常用的核燃料材料。它在室温(300K)下的热导率约为8 W/(m·K)…"(CN 知识片段)
3. `doc-1fecbffd` "[Condition] temp_C=300 - condition_key: temp_C …"(结构化属性数据,格式即 `kg_lightrag_sync.serialize_kg_node` 的输出,见 `apps/api/src/nfm_db/services/kg_lightrag_sync.py:63-101`)

剩下 **14 个文献衍生文档与文献库 14 条记录计数吻合**(注:计数 + 摘要级核对,未做 ID 级对账;两条口径完全对上的例证见下节时间线)。

### 同步机制:纯钩子,无调度

- **唯一常态入口**:`process_literature` 走到 completed、commit 之后 inline 调 `ingest_kg_to_lightrag`(`apps/api/src/nfm_db/services/literature_service.py:1025-1066`)。这正是 BUG-04(NFM-4083)的修复:Celery worker 路径下 fire-and-forget 任务挂在即将被 `asyncio.run` 关闭的 loop 上被静默取消("13 stale docs, zero auto-syncs")。
- **后台任务路径**:`fire_ingest_to_lightrag`(`kg_lightrag_sync.py:251-292`),由 `dispatch_build_result` 侧调用(`apps/api/src/nfm_db/services/kg_re.py:126`)。
- **无周期任务**:`apps/api/src/nfm_db/services/celery_app.py`(260 行)无任何 beat schedule;全仓 grep 无 lightrag 相关周期对账。**文献若处理时同步失败,没有任何兜底机制会再试**——修复 BUG-04 之前的存量就是靠 2026-08-29 的人工批量回填(见下)。
- 手动/E2E 路径:`scripts/e2e_lightrag_10papers.py`(NFM-1763,支持 `--direct` 无认证直连 sidecar 灌 10 篇论文)。

### 时间线(取自 /documents 的 created_at 与文献 API 的 created_at,均为 UTC)

| 时间 | 事件 |
| --- | --- |
| 2026-08-29T17:41:25Z | **13 个文档在同一秒内灌入**(17:41:25.314–.397)——程序化批量回填,对应代码注释里的 "13 stale docs, zero auto-syncs" 存量修复 |
| 2026-08-30T13:04–13:35Z | 文献库新增 3 条记录(displacement energy α/γ U、neutron diffraction、CALPHAD) |
| 2026-08-30T13:25–13:54Z | sidecar 新增 4 个文档;其中 3 个与上述 3 条文献对应,**管道延迟 ~19 分钟**(13:35:50 创建 → 13:54:17 入库),说明当时同步链路在工作 |
| 2026-09-01T15:56:34Z | 文献库最新一条:"Determination of Thermal Expansion, Defect Formation Energy…"(doi 10.3389/fmats.2021.661387)。sidecar 无晚于 08-30 13:54 的文档;08-30T13:25 的 TDE 文档摘要疑似即此文的 KG 摘要(记录重建时间晚于入库时间的解释存疑,**未做 ID 级确认**) |
| 2026-09-03/09-05 | BUG-04 修复提交 `c4c6b4fa8`(09-03 16:55 +0800)、合入 main `eb47773ae`(PR #1141,09-05 00:13 +0800) |
| 2026-09-08(今日) | prod 容器 2 小时前重启;重启后 worker/api/lightrag 日志**无任何 sync 活动**——因 09-01 后无新文献,属预期,**BUG-04 修复后的同步行为尚无生产样本验证** |

### 覆盖里的隐藏断层:向量层是空的

sidecar 启动日志每次都告警:

```
WARNING: PostgreSQL: workspace data in table 'LIGHTRAG_VDB_ENTITY_text_embedding_3_small_768d' is empty…
WARNING: … 'LIGHTRAG_VDB_RELATION_…' is empty…
WARNING: … 'LIGHTRAG_VDB_CHUNKS_…' is empty…
```

即:17 个文档的图抽取(KV/graphml,736 实体)在,但 **PGVector 向量表一条都没有**。"覆盖"只到达了图存储层;依赖向量召回的查询模式(local/hybrid/mix 的 vector 侧)实际无数据可用。原因见 ② 的 embedding 404——向量从未写成功过。

---

## ② 查询成本实测

### 恰好一次 hybrid 查询(2026-09-08,遵守「不发第二次」)

```
POST http://localhost:9621/query
{"query": "UO2 热导率", "mode": "hybrid"}

→ HTTP 200, time_total = 63.677672s, 71 bytes
   {"response":"No relevant context found for the query.","references":[]}
```

服务端日志(`DOCKER_HOST=unix:///var/run/nfm-g2/docker-ro.sock docker logs nucpot-prod-lightrag`)还原的 63.7s 花在哪:

```
INFO:  == LLM cache == saving: hybrid:keywords:05bf3c48…      ← keyword LLM 先跑(本地 27B 模型,占 ~60s 大头)
INFO:  Query nodes: UO2, 二氧化铀, 热导率 (top_k:40, cosine:0.2)   ← 关键词抽取成功
ERROR: Embedding func: Error code: 404 - {'message': 'model "text-embedding-3-small" not found, try pulling it first'}
ERROR: Query failed: Error code: 404 - …
INFO:  "POST /query HTTP/1.1" 200                            ← 服务端 ERROR 仍返回 HTTP 200 + 空上下文
```

### 上游 LLM 依赖:100% 本地,无云 API

启动横幅(容器日志):

- LLM(extract/keyword/query/vlm 四角色):`openai/qwen3.8:27b-mlx` @ `http://host.docker.internal:11434/v1`,每角色 max_async=4、timeout=240s
- Embedding:binding=openai、model=`text-embedding-3-small`、dim=768,同样指向本机 Ollama
- 服务器:1 worker、server timeout 300、Reranking disabled、`reasoning_effort: none`
- LLM cache:232 条记录;`apps/web/src/lib/rag-api.ts:84-99` 注释记载**缓存命中 <0.2s**,冷 mix/hybrid 在旧模型(qwen3.5:4b-nvfp4)下 ~25-35s——模型之后换成了现在的 27B,冷延迟进一步拉长(本次实测 63.7s)

**故障根因是 embedding 配置漂移**:仓库默认 `BAAI/bge-m3`@1024d(`docker/lightrag/start.sh:82`、`.env.lightrag.example:89`),prod 实跑 `text-embedding-3-small`@768d(env 覆盖),而 **Ollama 上没有这个名字的模型**(它是 OpenAI 模型名)→ 每次 embedding 调用 404。`start.sh:80` 自己写着:"Changing EMBEDDING_MODEL requires a full rebuild of the RAG index" —— 与 VDB 三张空表互为印证:换模型后从未重建,向量层从空到空。

### 超时配置核对(NFM-3404 拆分后的现值)

`apps/api/src/nfm_db/services/lightrag_client.py`:

| 参数 | 现值 | 覆盖链 |
| --- | --- | --- |
| 查询预算 `_DEFAULT_QUERY_TIMEOUT` | **8.0s** | `query_timeout=` > legacy `timeout=` > env `NFM_LIGHTRAG_QUERY_TIMEOUT_S` > 8.0(:44, :91-107) |
| 连接预算 `_DEFAULT_QUERY_CONNECT_S` | **5.0s** | env `NFM_LIGHTRAG_QUERY_CONNECT_S`(NFM-3367,把 connect 从 read 拆开,:200-211) |
| 摄入预算 `_DEFAULT_INGEST_TIMEOUT` | 300.0s | 后台任务无人等待,刻意长(:45) |
| 传输层 legacy 默认 | 60.0s | 仅属性兼容 |

repo 内(compose/env 样例)**无任何覆盖** → 生产按 8s 跑。`api/v1/lightrag.py:37-43` 的 `_get_client()` 不传 timeout,`LightRAGProvider.query`(`services/rag_provider.py:114-115`)也不传 → 全部走 8s 默认。

**推论(事实层面)**:实测 63.7s 的查询在任何 API 用户路径上都会在 8s 处被客户端掐断,然后 `kg.py:151-202` 的 `_semantic_query` 捕获异常回退 ILIKE 结构化检索;前端 `/search` 语义模式 / `/rag/chat` 的 AbortController 预算是 45s(`rag-api.ts:99`,env `NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS`;沿革:硬编码 60s → 14s(NFM-3426)→ 45s)。sidecar 服务端不知道客户端已放弃,照常算完 63.7s——单 worker + max_async=4 的本地 27B 推理被白白占用。

补充:产品链路实际用的 mode 是 **`mix`**(前端 `rag-api.ts:225` 默认、`LightRAGProvider.query` 不传 mode 落到 client 默认 mix),本次按票面用 hybrid;两者同为向量依赖模式,故障面一致。

---

## ③ 限流/配额基建

### 后端(API 层)

**已有:全局 slowapi 限流中间件(NFM-1087)** — `apps/api/src/nfm_db/middleware/rate_limit.py`,挂载于 `apps/api/src/nfm_db/main.py:269-271`:

- 策略:`application_limits = [100/minute, 20/second burst]`,key = `get_remote_address`(远端 IP)
- 范围:仅 `/api/*`;health 经 `@limiter.exempt` 豁免;429 返回标准 envelope(`RATE_LIMIT_EXCEEDED`)+ `X-RateLimit-Limit/Remaining/Reset` 头注入
- 存储:默认 **`memory://`(进程内存)**;`RATE_LIMIT_STORAGE_URI` 可指 Redis,但 **repo 内无任何 env/compose 配置它**——prod 虽有 `nucpot-prod-redis` 容器,限流器是否接到它无法从仓库验证(middleware 自己的 docstring 也承认 multi-instance 需要 Redis)

**端点级限流:仅 auth 三条** — `apps/api/src/nfm_db/api/v1/auth_endpoints.py:96`(login 20/minute)、`:179`(token 路由 30/minute)、`:234`(register 3/minute)。

**没有的**:`/api/v1/lightrag/query`、`/api/v1/kg`(mode=lightrag 语义桥)、`/api/v1/literature` 等**都没有端点级限流**——RAG 查询只受全局 100/min/IP 约束,没有按用户/按查询次数的配额,没有 Redis 计数器化的 quota。

**认证门现状**:`/api/v1/lightrag/query` 要求 `require_editor`(`apps/api/src/nfm_db/api/v1/lightrag.py:160`),`/lightrag/ingest` 同样 editor-only;前端对应文案「语义检索需要登录」(NFM-4307/BUG-31,`rag-api.ts:113`)。即:**今天匿名访客经 API 根本到不了 sidecar**。
**旁路事实**:sidecar 本身 API Key 未设、JWT disabled(启动横幅),直连 9621 无任何认证/限流——`scripts/e2e_lightrag_10papers.py --direct` 演示了无认证直连查询/灌库。讨论「匿名开放」时这是绕过 API 配额的既有通道。

### 前端(/search 与 /rag/chat)

| 页面/组件 | 触发方式 | 防抖/限次 |
| --- | --- | --- |
| `/search` 文本模式(`apps/web/src/app/search/SearchView.tsx:19,57`) | 输入驱动 | `useDebounce(keyword, 300ms)` |
| `/search` 语义模式(`apps/web/src/components/search/RagSearchView.tsx:105-112`) | 提交驱动 | 无 debounce(不需要);按钮在 `loading` 期 disabled → **单并发**;45s AbortController |
| `/kg/search`(`apps/web/src/app/kg/search/KgSearchContent.tsx:19,88`) | 输入驱动 | `useDebounce(300ms)` |
| `/rag/chat`(`apps/web/src/app/(dashboard)/rag/chat/page.tsx:16-48`) | 提交驱动 | 单并发(loading 门);无前端限次 |

前端**没有**查询次数配额/冷却;对登录用户也没有客户端侧的用量概念。

---

## 对 #1249 决策地图的输入(事实,非建议)

1. 「匿名开放」的当前基线不是"无限流",而是"**RAG 编辑者专属 + 全局 IP 限流(100/min,memory 存储)+ sidecar 裸奔**"三层混合体;任何承诺都要先决定这三层各自的口径。
2. 「登录后质量承诺」的当前基线:**hybrid/mix 查询事实上返回空结果**(embedding 404 + VDB 三表全空),且 8s API 预算 ≪ ~60s 冷查询实测时延(缓存命中 <0.2s 除外)——质量承诺前置依赖:修 embedding 模型 → 全量重建索引 → 重校三档超时(8s/45s/63s+ 实测)。
3. 索引"覆盖"数字(17 docs / 14 文献 / 736 实体)在修复前只代表图层,向量层覆盖为 0。

## 附录:复现命令

```bash
# ① 索引覆盖
curl -s http://localhost:9621/documents | python3 -c "import json,sys; print({k: len(v) for k,v in json.load(sys.stdin)['statuses'].items()})"
curl -s -A "Mozilla/5.0" "https://nucpot.dpdns.org/api/v1/literature?page=1&page_size=100" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['data']['total'], len(d['data']['items']))"

# ① 同步调度(代码层)
grep -rn "kg_lightrag_sync" apps/api/src --include="*.py"
grep -n "beat\|schedule" apps/api/src/nfm_db/services/celery_app.py   # → 无

# ① 只读日志
DOCKER_HOST=unix:///var/run/nfm-g2/docker-ro.sock docker logs --tail 200 nucpot-prod-lightrag

# ② 查询成本(⚠️ 每次都是 ~60s 真实本地推理,勿连发)
curl -s -o /dev/null -w "HTTP=%{http_code} time_total=%{time_total}s\n" \
  --max-time 120 -X POST http://localhost:9621/query \
  -H "Content-Type: application/json" -d '{"query": "UO2 热导率", "mode": "hybrid"}'

# ③ 限流
grep -rn "limiter" apps/api/src/nfm_db/api/v1 --include="*.py"
grep -rn "useDebounce\|DEBOUNCE_MS" apps/web/src/app/search apps/web/src/app/kg/search
```

## 引用清单

- 代码:`apps/api/src/nfm_db/services/literature_service.py:1022-1066` · `services/kg_lightrag_sync.py:176-292` · `services/kg_re.py:126` · `services/lightrag_client.py:26-64,91-107,145-211` · `services/rag_provider.py:114-115` · `services/celery_app.py` · `api/v1/lightrag.py:37-43,105-195` · `api/v1/kg.py:88-103,151-202` · `middleware/rate_limit.py` · `main.py:253-271` · `api/v1/auth_endpoints.py:96,179,234`
- 前端:`apps/web/src/lib/rag-api.ts:84-113,221-251` · `app/search/SearchView.tsx:19,57` · `app/search/SearchPageContent.tsx:52` · `components/search/RagSearchView.tsx:105-112` · `app/kg/search/KgSearchContent.tsx:19,88` · `app/(dashboard)/rag/chat/page.tsx`
- 配置:`docker/lightrag/start.sh:80-83` · `.env.lightrag.example:86-89` · `apps/api/.env.example:17`
- 提交:`c4c6b4fa8`(NFM-4082..4085,2026-09-03)· `eb47773ae`(PR #1141,2026-09-05)
- 脚本:`scripts/e2e_lightrag_10papers.py`(NFM-1763)
- 实测:本文所有 curl/docker logs 输出,2026-09-08,Mac Studio 本机
