# RAG 功能全面测试报告(2026-09-11)

- **测试对象**:`https://nucpot.dpdns.org` 生产环境 RAG 功能(语义检索 / 问答 / 回退链路)
- **测试视角**:匿名用户(未登录),辅以生产容器/PG 只读观测(G2 授权通道)
- **测试方法**:web-gui-tester 四阶段方法论;实验矩阵设计 → 执行 → 证据固定 → 报告。API 层以 `POST /api/v1/lightrag/query` 实测为准,GUI 层与 API 响应双验证
- **执行日期**:2026-09-11(索引快照取证至 09-11 10:20 UTC+8;报告整理 2026-09-12)
- **关联票据**:NFM-4733(critical,已开票,缓解 PR #1316 验收通过)、NFM-4734(high,已开票)、NFM-4736(critical,同日规格回归开票:文献向量库二次清空 + 限流失效,↔ GitHub #1317)、NFM-4738(本测试的索引审计证据 relay)、背景票 NFM-4539(RAG 匿名开放线)、NFM-4525(fresh 慢)
- **前序报告**:[功能测试 2026-09-04](nucpot-site-functional-test-report-2026-09-04.md)、[回归测试 2026-09-05](nucpot-site-regression-test-2026-09-05.md)(BUG-31「匿名 RAG 登录墙」的修复即本轮的验收基线)

---

## 1. 实验矩阵

| 编号 | 维度 | 优先级 | 设计 |
|---|---|---|---|
| E1 | 匿名开放 | P0 | 无凭直接查询,期望 HTTP 200 + 答案(BUG-31 修复回归) |
| E2 | 语义检索质量 | P0 | 对库内**已确认索引**的内容提问(中英文各若干),期望语义命中 refs>0 且数值正确 |
| E3 | 回退与降级行为 | P1 | 观察语义层失败时回退是否发生、是否可感知、是否有原因码 |
| E4 | 性能与分层 SLA | P2 | 采样响应时间,对照 SLA:缓存 P95<1s / fresh P95<10s(修后目标) |
| E5 | 索引健康 | P2 | LightRAG 文档管线状态审计 + 索引↔文献库 diff |
| E6 | 匿名限流 / 每日对账 / 引用溯源 | P1 | 复用既有验证结论,本轮不重复压测(见 §4.6) |

**E2 测试集**(内容在库的证据:Owen 2023 已摄取,92 条 property_measurements + 激活能 0.3 eV 等;kg_node 含相变族/晶界族/扩散族标签):

| # | 查询 | 语言 | 库内应有答案 |
|---|---|---|---|
| Q1 | U-Mo相变 | 中文 | kg_node:相变体积变化/相变类型/相变潜热/相变临界温度/相变斜率 |
| Q2 | Cr 掺杂 晶界 | 中文 | kg_node:晶界厚度/晶界宽度/Cr掺杂扩散激活能/Cr掺杂扩散前指数因子 |
| Q3 | 扩散活化能 | 中文 | kg_node:扩散活化能(精确同名) |
| Q4 | activation energy | 英文 | Owen 2023 activation_energy 0.3 eV(多条)+ kg_node activation_energy |

## 2. 执行结果总览

| 维度 | 结果 | 判定 |
|---|---|---|
| E1 匿名开放 | 4/4 查询免登录 HTTP 200 | ✅ PASS(BUG-31 修复保持) |
| E2 语义检索质量 | **0/4 语义命中**:3 条中文 refs=0,1 条英文 refs=1 但答「cannot answer」 | ❌ **CRITICAL → NFM-4733** |
| E3 回退行为 | 全部静默回退 ILIKE,前端无原因码,用户无法区分「真答」与「兜底」 | ❌ HIGH → NFM-4734 |
| E4 性能 | Tier-2(回退路径)P95 **10026ms** vs 10s 目标,踩线 | ⚠️ 边缘 |
| E5 索引健康 | 文档状态表 processed 62 / failed 30(10 条真实失败且错误信息为**空**);**但同日规格回归证实实际向量库已被二次清空(VDB 144/644/645→4/21/27,→ NFM-4736)** | ❌ 见发现 F-3 + NFM-4736 |
| E6 限流/对账/引用 | 对账维持;**限流被同日回归证伪(8 连发全 200,预期 429 → NFM-4736)** | ⚠️ 见 §4.6 |

## 3. 关键发现(按严重度)

### F-1 [CRITICAL] 语义模式对库内已索引文献系统性不命中 → NFM-4733

4 次查询全部 `fallback=true, kind="iliKE"`,LightRAG 语义层一次都没有产出有效回答。典型响应(生产容器实测,修复前基线):

```json
{"success":true,"data":{"response":"No results found for query 'U-Mo相变'.",
 "references":[],"fallback":{"used":true,"kind":"iliKE","original_error":null}}}
```

- 3 条中文查询 refs=0;1 条英文查询虽 ILIKE 命中 1 ref,但语义回答仍为「cannot answer」——语义层对 92 条 measurements 与 13 字段技能输出完全不可见。
- **根因锚定(同日 16:34 规格回归,NFM-4736)**:文献向量库发生**第二次清空**(VDB chunks/entities/relations 144/644/645 → 4/21/27,幸存者仅为当日 KG 片段同步载荷)——语义不命中的主根因是**索引被清空**,叠加 F-3 的回填失败与 -2/±2 漂移;文档状态表(doc status)与向量库(vdb)表面计数不一致(doc_status 报 62 processed 而 VDB 近空),本身即是清空事故的旁证。

**修复状态(2026-09-12 更新)**:
- 缓解已就绪:PR [#1316](https://github.com/Etoile04/nucpot/pull/1316)(branch `NFM-4733-rag-semantic-miss` @ `3147385bd`)将回退提供者改为语言容忍的 `RuleBasedFallbackProvider`(Latin `simple` tsvector + CJK ILIKE 分支 + tsquery 操作符剥离)。QA E2E 10/10 PASS(4 条原失败查询 refs>0 + 5 条边界),CR 独立复验 32/32 pytest、95% 覆盖,CPO 产品验收 **PASSED**,待 RE 合并部署。
- **注意**:该 PR 修复的是**回退层**的中文命中,使 NFM-4733 验收标准(refs>0)可达;**LightRAG 语义层本身的根因**已由同日回归锁定为索引二次清空(→ NFM-4736 重建),F-3 管线失败是其次级成因——语义真答能力待 NFM-4736 重建索引后恢复。

### F-2 [HIGH] 回退静默降级,用户不可感知 → NFM-4734

- 语义失败后静默变为文本检索,响应时间 10-13s,前端(聊天面板布局 B/C)不显示回退徽标(仅校对抽屉有),用户把兜底文本匹配当成语义真答。
- 分层 SLA Tier-2 P95 10026ms 踩线 10s,意味着回退大概率成常态而非例外,可感知性问题被放大。

**修复状态**:branch `NFM-4734-rag-fallback-reason` @ `eed1c64fd`(17 files,+795/-25)已推,3 项 AC:①布局 B 回退徽标 + 原因感知文案;②API `FallbackInfo.reason`(semantic_timeout/semantic_empty/provider_error)+ `X-RAG-Fallback-Reason` 响应头;③AC-8 仪表盘 `tier_2_breach` 告警信号。96 后端 + 72 前端测试绿,ruff/tsc/eslint 净。现由 Code Reviewer 接手(in_progress),待开 PR 合并。

### F-3 [HIGH·新发现] LightRAG 索引管线健康度差,且失败不可观测

索引快照(2026-09-11 10:20,LightRAG documents API):

| 状态 | 数量 | 说明 |
|---|---|---|
| processed | 62 | 有效语义索引 |
| failed | 30 | **20 条为判重拒绝**(19×"Identical content already exists" + 1×"File name already exists"),**10 条为真实分块处理失败** |
| processing | 2 | 其中 `nfm-4505-postrestart-20260908224121-*` 自 09-08 起滞留 |
| pending | 1 | `nfm-4636-pipeline-probe-1`(管线探针) |

要点:

1. **10 条真实失败全部是 `nfm-4505-fresh-*` 文档**,错误信息为空串(形如 `C[1/13]: doc-…-chunk-003:` 后无内容)——完全不可行动。这些正是 NFM-4505 对账回填的文档,即**回填内容没有进语义索引**,直接解释 F-1 的部分不命中。
2. **failed 桶混杂判重拒绝与真实失败**:判重拒绝是去重机制在正常工作(此前「同一文献反复摄取」问题的后续效应),但落在 failed 状态污染健康度统计,也掩盖了 10 条真实失败。
3. 1 条 processing 文档跨 3 天滞留,无超时回收。

**建议**:并入 NFM-4733 根因排查范围(其任务①③),或独立开票:①重放 10 条失败文档并捕获真实错误;②failed 状态细分(`duplicate` vs `error`);③processing 超时回收;④对账 beat(03:30 UTC)增加 failed 桶健康度输出。

### F-4 [MEDIUM] Tier-2 性能踩线,与 NFM-4525 相关

回退路径响应 10-13s,Tier-2 P95 10026ms,压着 10s 目标线。已知根因线在 NFM-4525(fresh 慢)。回退高频化(E3)+ 踩线(E4)叠加,匿名用户体验为「每次提问等 10 秒拿到文本匹配」。建议 NFM-4734 AC-3 的 breach 告警上线后,以 NFM-4525 修复为前提复测,目标修后 fresh P95<10s。

## 4. 各维度明细

### 4.1 E1 匿名开放 ✅

4 次查询(3 中 1 英)均未携带凭据,全部 HTTP 200 返回 envelope(`success/data.response/references/fallback`)。BUG-31(匿名 RAG 登录墙,NFM-4307 线)的修复在生产保持生效,`require_editor` 移除无回归。

### 4.2 E2 语义检索质量 ❌(→ NFM-4733)

| # | 查询 | refs | 语义回答 | fallback |
|---|---|---|---|---|
| Q1 | U-Mo相变 | 0 | "No results found…" | used, iliKE |
| Q2 | Cr 掺杂 晶界 | 0 | "No results found…" | used, iliKE |
| Q3 | 扩散活化能 | 0 | "No results found…" | used, iliKE |
| Q4 | activation energy | 1 | "cannot answer" | used, iliKE |

库内证据:Owen 2023 在库、92 条 property_measurements(含 activation_energy 0.3 eV 多条)、kg_node 相变族/晶界族/扩散族标签齐全——**答案在库,语义层不可见**。

### 4.3 E3 回退与降级行为 ❌(→ NFM-4734)

全部查询的失败模式一致:语义层无结果 → 静默切 ILIKE → `original_error: null`(连超时/异常都未记录为错误)。前端聊天面板无任何回退提示。详见 F-2。

### 4.4 E4 性能采样 ⚠️

| 指标 | 实测 | 目标 | 判定 |
|---|---|---|---|
| 回退路径单次响应 | 10-13s | — | 慢但可用 |
| Tier-2 P95 | 10026ms | <10s(修后) | 踩线 |
| Tier-1(缓存)P95 | 本轮未单独采样(前值 p50 9.2ms 量级,见回归报告) | <1s | 维持前结论 |

### 4.5 E5 索引健康 ❌(→ F-3 + NFM-4736)

本测试的两次快照(09-11 09:56 / 10:20,documents API)显示文档状态表处于活动但亚健康态:`analyzing 6→0, processing 3→2, processed 60→62, failed 25→30`,失败增量全部为判重拒绝与空错误失败(见 F-3)。

**同日 16:34 规格回归(NFM-4736)推翻了表层健康度**:直接查 VDB 表发现文献向量库被二次清空(chunks/entities/relations 144/644/645 → 4/21/27,doc_full/doc_status/doc_chunks 一度全零,幸存者仅为当日 KG 片段),窗口锁定在 03:24 对账健康 → 08:29 KG-fragments-only 之间,嫌疑为 NFM-4719 内联摄取重构(#1311)/ kg_lightrag_sync 写路径。文档状态 API 报 62 processed 与 VDB 近空并存,说明**状态表面与实际向量内容脱钩**——以 `run-sql` 直查 `lightrag_vdb_*` + `rag_index_audit_log` 为准(证据三角法见回归记录)。索引↔文献库 diff -2/±2 漂移与 92 条 measurements 不可见,共同构成 F-1 的索引侧解释。

### 4.6 E6 限流 / 对账 / 引用溯源 ⚠️

- **匿名限流**:**同日规格回归证伪**——8 连发匿名查询全部 200,预期第 6 发起 429 未出现(代码有 `@limiter` + NFM-4681 Redis 接线;嫌疑为隧道 IP 归键全落 127.0.0.1 或存储未命中),已并入 NFM-4736 一并修。本测试 4 次查询未触发限流,当时误读为「预算内正常」,实为限流未生效。
- **每日对账**:beat 03:30 UTC(NFM-4539 AC-3)当日在生产健康运行(`rag_index_audit_log` 03:24 实跑 10 noop + 1 reingest);但 03:24 审计通过后索引仍被清空,说明对账覆盖面不足(→ F-3 建议④:对账应输出 VDB 表级计数而非仅 noop/reingest)。
- **引用溯源**:ILIKE 命中时 references 正常返回(Q4 refs=1 含文献定位);但语义层 0 命中时 refs=0,溯源能力随 F-1 一并失效。

## 5. 结论与后续序列

**结论**:匿名开放(BUG-31 修复)保持生效;但 RAG 的核心价值——语义检索——当前在生产**实质不可用**:索引被二次清空(NFM-4736)+ 回填失败(F-3)使语义层空转,用户拿到的是静默降级的文本匹配,还要等 10 秒;限流亦未生效。三票在途:NFM-4733(缓解,验收已过)、NFM-4734(可感知性)、NFM-4736(索引重建 + 限流,critical)。

**后续序列**(建议按序):

1. RE 合并 PR #1316 → 部署 → 生产复测 Q1-Q4(期望 refs>0,中英文均命中)→ 关 NFM-4733 缓解面;
2. **NFM-4736:索引重建(恢复 VDB 144/644/645 基线)+ 限流修复**,这是语义真答恢复的关键路径;
3. F-3 索引管线修复(重放 10 条失败文档 + failed 细分 duplicate/error + processing 超时回收 + 对账输出 VDB 计数)→ 以「语义回答含正确数值且无需回退」为标准复测 E2;
4. NFM-4734 分支开 PR → 合并 → 验证回退徽标/原因码/breach 告警;
5. NFM-4525 修后复测 Tier-2 P95<10s;
6. 本报告 §4.2 四查询纳入常规回归集。

## 6. 证据清单

| 证据 | 位置 |
|---|---|
| 修复前基线响应(U-Mo相变,iliKE 回退) | NFM-4733 票描述 + QA 报告引用 |
| QA E2E 矩阵 10/10(Q1-Q5,E1-E5) | NFM-4733 票评论(QA,2026-09-11T16:59Z,附件 smoke 脚本+结果) |
| CR 独立真 PG 冒烟 5 查询 | NFM-4733 票评论(CR,2026-09-11T16:52Z) |
| CPO 产品验收 PASSED | NFM-4733 票评论(2026-09-11T17:05Z) |
| 索引快照 ×2 | 本报告 §4.5(documents API statuses 计数) |
| Tier-2 P95 10026ms | NFM-4734 票描述 |
| 4734 分支与 AC 清单 | `NFM-4734-rag-fallback-reason` @ eed1c64fd,LE-RELAY 记录 |
| VDB 二次清空 + 限流失效 | 同日规格回归(09-11 16:34)→ NFM-4736↔#1317;`lightrag_vdb_*` / `rag_index_audit_log` 直查 |
| 索引审计证据(F-3) | relay 票 NFM-4738(父:NFM-4733) |
