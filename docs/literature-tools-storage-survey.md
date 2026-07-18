# 文献工具存储架构调研：Zotero / Mendeley 借鉴与 NFMDI 适配评估

> **配套文档**：本报告是技术路线图 v1.6 [§3.2 技术选型](technical-roadmap-nuclear-fuel-data-platform-1.6.md#存储后端) 与 [§7.8 轨道 B B-Phase 1 / B-Phase 4A](technical-roadmap-nuclear-fuel-data-platform-1.6.md#b-phase-1-存储基础设施与合规前置设计与-sprint-4-7-并行) 的详细支撑材料。相关调研另见 [对象存储可行性](object-storage-feasibility.md)、[国内合规评估](domestic-storage-compliance.md)、[存储合规路线图](storage-compliance-roadmap.md)。

| 评估项 | 内容 |
|--------|------|
| 评估日期 | 2026-07-18 |
| 核心问题 | 本地 Zotero + MCP server 能否替代 NFMDI 缺失的源文件存储后端？以及从主流文献工具能借鉴哪些存储架构设计？ |
| 结论 | ❌ Zotero 不适合作为存储后端（合规、架构、协议层均不匹配）；但 Zotero/Mendeley 的存储架构设计（CAS 去重、乐观锁、FTS 全文索引、CSL-JSON 导出、Crowd Catalog）值得 NFMDI 借鉴 |

---

## 1. Zotero 作为存储后端的可行性评估

### 1.1 结论：不推荐

Zotero 不能替代 NFMDI 的源文件存储后端。可能的次要角色是 Phase 2+ 的低优先级上游导入源。

### 1.2 否决理由——致命机制

#### (1) 本地 API 只读；Web API 文件路由经过 zotero.org

- 本地 API（`localhost:23119`）——**只读**、无需认证。不能上传文件。
- Web API（`api.zotero.org`）——支持读/写/文件上传，但上传流程是 3 步授权，最终文件落在 **Zotero 自有 S3**（zotero.org 基础设施）。
- 对 NFMDI（核燃料材料）：PDF 离境到国外云服务 = **合规违规**。仅此一条就排除 Web API。
- 所有现有 MCP server 的写操作都依赖 Web API。没有任何 MCP server 仅通过本地 API 写文件。

#### (2) 是桌面 GUI 应用，不是服务

- Zotero 是 XUL/Firefox 引擎的桌面应用，无真正的 headless/服务模式。
- 在生产 Docker 主机上运行意味着要长期开着 GUI 窗口——服务器端后端不可行。
- Local API 要求桌面应用运行并开启"允许其他应用通信"。

#### (3) MCP 是错误的协议层（用于 server-to-server）

- GitHub 上所有 Zotero MCP server（86 个仓库）都面向**客户端 AI 助手**（Claude Desktop、Cursor）经 stdio 通信。
- 对 FastAPI 后端，正确集成是直接用 `pyzotero` Python 库——中间加 MCP 是无谓开销。
- MCP server 假设：单用户、单机、用户在场、AI 助手本地连接。

#### (4) 无多租户/权限模型

- Zotero 的单位是个人库或群组库（最多约 10 个群组）。
- NFMDI 有 `require_editor` RBAC、按用户所有权、审计追踪。
- 单个 Zotero 实例 = 一个用户的库。无法表达 NFMDI 的多用户权限模型。

#### (5) 数据模型无法表达 NFMDI 的文件生命周期

- NFMDI 需要：`uploaded → parsing → extracting → completed → failed` 状态机，外加 `file_path`、`file_hash`（sha256）、`content_md`、`parse_status`、`parse_error`。
- Zotero 的 attachment item 字段：`md5`、`mtime`、`filename`、`contentType`——通用文件元数据，无处理流水线状态。
- 变通方案（用 tag/note 当状态）脆弱且不可搜索。

### 1.3 生态调研（2026-07-18）

| 项目 | Stars | 写支持 | 备注 |
|------|-------|--------|------|
| 54yyyu/zotero-mcp | 4.3k | ✅ 混合（本地读 + Web 写） | 最成熟。`add file`、`add doi`、语义搜索。仍是客户端导向 |
| kujenga/zotero-mcp | 158 | ❌ 只读（3 工具） | 仅 search/metadata/fulltext |
| introfini/ZotSeek | 156 | ❌ | 本地语义搜索 MCP |
| TonybotNi/ZotLink | 138 | ✅（经 Web API 导入 Zotero） | arXiv/bioRxiv → Zotero，导入导向 |
| PiaoyangGuohai1/cli-anything-zotero | 109 | ✅ 经 JS Bridge 插件 | 70+ CLI 命令，桌面 CLI |

**没有任何一个**面向服务器端文件存储管理。

### 1.4 Zotero 的合理（次要）角色

如果团队研究员已经在用 Zotero，它可以作为**个人侧批量导入源**——不是存储后端：

```
研究员的 Zotero 库
    ↓（pyzotero 导出 / Better BibTeX / ZotLink MCP）
    ↓ 拉取选中条目的元数据 + PDF
NFMDI POST /api/v1/literature/upload  ← 仍用仓库内存储方案
    ↓
DataSource(file_path, file_hash, content_md, ...)
```

此处 Zotero 只是众多上游数据源之一（像 DOI/Unpaywall fetcher），不是记录系统。PDF 仍落在 NFMDI 自有 Docker 卷。**推迟到 Phase 2+**。

### 1.5 NFMDI 应做的替代方案（已采纳）

依据项目既有设计（NFM-1474 / NFM-817）：

1. **存储后端**：本地磁盘 + Docker 卷（`prod-uploads:/app/uploads/literature/`），复用 `upload_service.py` 的 sha256-hash 模式。生产环境升级为 MinIO/S3，详见 [对象存储可行性 §5](object-storage-feasibility.md#5-s3storage-生产级实现骨架)。
2. **数据模型**：在 `DataSource` 添加 `file_path`、`file_hash`、`file_size`、`content_md`、`parse_status`、`parse_error` + Alembic 迁移。
3. **DOI 定位**：辅助 fetcher（用户提供 DOI → 自动拉 PDF → 走相同 upload→parse→extract 流程）。
4. **本场景不用 Zotero、不用 MCP**。

---

## 2. 行业存储架构横向对比

对主流文献管理工具（Zotero、Mendeley、Paperpile、ReadCube、JabRef、EndNote）的调研证实：**每一个云端文献管理器都用 S3 作为文件存储后端**。这验证了 MinIO/S3 路线是行业标准选择。

| 工具 | 文件存储后端 | SDK 方式 | 去重机制 |
|------|-------------|----------|----------|
| Zotero | Amazon S3（presigned URL 直传） | 自定义 HTTP 客户端 | (md5, filesize) 即时发送 + 内容寻址存储 |
| Mendeley | AWS S3 | 自定义客户端 | 内容 hash 自动去重 |
| Paperpile | Google Drive（S3 兼容） | Drive API | DOI + 文件 ID |
| ReadCube Papers | S3 | 自定义客户端 | DOI/标题自动去重 |

---

## 3. NFMDI 已对齐的 6 个行业通用设计模式

1. **元数据/二进制分离**——PG 存元数据，对象存储存 PDF
2. **基于 ID 的附件组织**——`{datasource_id}/{filename}` key 布局
3. **内容 hash 去重**——`file_hash`（sha256，未来 SM3）
4. **异步解析**——Celery worker 派发，不阻塞 HTTP 线程
5. **本地元数据库**——PostgreSQL（未来可切人大金仓）
6. **附件按引用而非内嵌**——DB 只存 key，二进制在对象存储

---

## 4. 可借鉴设计（B-Phase 1 与 B-Phase 4A 增量）

以下设计模式来自行业工具调研，可在 NFMDI 后续迭代中按需引入。

### 4.1 CAS（Content-Addressed Storage）物理去重

**借鉴对象**：Zotero 的 (md5, filesize) 即时发送 + 内容寻址存储

**两层布局**：

```
语义层：nfm-literature/{datasource_id}/{filename}    ← 用户可见路径
物理层：nfm-literature/_cas/{sha256}/                ← 实际字节存储
```

- 语义层 key 指向物理层（通过软引用表或对象元数据）
- 同一 PDF 被多个 DataSource 引用时，物理层只存一份
- 上传前先查 hash 是否存在 → 命中则只创建语义引用，零字节传输（Zotero 的"instant-send"）
- NFMDI 的 `DataSource.file_hash` 列已具备 hash 基础设施，只需加一张 CAS 引用表

**路线图位置**：B-Phase 4A（非涉密优化期）

### 4.2 乐观锁（Optimistic Locking）

**借鉴对象**：Zotero 的 `libraryVersion` 字段

- 每个 DataSource 增加 `version` 整数列
- 写入时带 `If-Match: <version>` header
- 服务端 `UPDATE ... WHERE version = :expected`，affected_rows=0 即冲突
- 冲突时返回 409，客户端决定合并/重试
- 比悲观锁简单、无死锁风险，适合"写少读多 + 偶发冲突"的科研数据场景

**路线图位置**：B-Phase 1（与 Sprint 4–7 并行）

### 4.3 全文索引（FTS）

**借鉴对象**：Zotero 的 SQLite FTS5

- PostgreSQL 原生支持 FTS：`tsvector` + GIN 索引
- NFMDI 的 `content_md` 列（PyMuPDF 解析后的 Markdown）已是天然的 FTS 输入
- 配方：

```sql
ALTER TABLE datasource ADD COLUMN search_vector tsvector
  GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content_md, ''))) STORED;
CREATE INDEX idx_datasource_search ON datasource USING GIN(search_vector);
```

- 查询：`WHERE search_vector @@ plainto_tsquery('关键词')`
- 中文分词需额外配置（zhparser/jieba），简单场景可先 `'simple'` 配置跑通

**路线图位置**：B-Phase 1（与 Sprint 4–7 并行）

### 4.4 标准格式导出（避免数据锁定）

**借鉴对象**：Zotero 的 CSL-JSON 导出 + Better BibTeX

- 提供 CSL-JSON / BibTeX / RIS 三种导出格式
- 任何时刻用户都能把数据无损带出 NFMDI
- 这是"数据可携带"原则的具体落地，也是数据要素流通的合规要求
- 实现成本低：DataSource 字段已覆盖 CSL-JSON 所需的 doi/title/journal/year/volume/pages

**路线图位置**：B-Phase 4A（非涉密优化期）

### 4.5 Crowd Catalog（远期）

**借鉴对象**：Mendeley 的跨用户元数据 catalog

- 当多个用户上传同一 DOI 的 PDF 时，元数据只入库一次
- 物理字节去重 + 元数据去重
- 远期可演化成"院内共享文献库"，用户上传即自动加入共享池（按权限分级）
- 这是 NFMDI 双轨数据流动治理（路线图 §3.2）的一个具体落地场景

**路线图位置**：B-Phase 4A 之后，远期规划

---

## 5. 验证依据

- `mcp__gitnexus__query`——确认 `DataSource` 模型无文件字段；`upload_literature()` 是占位
- `read_file` 检查 `apps/api/src/nfm_db/models/source.py`——确认当前字段：doi/title/journal/year/volume/pages/source_type/abstract/external_url。无文件生命周期字段。
- `read_file` 检查 `apps/api/src/nfm_db/api/v1/literature.py:109-135`——确认 `upload_literature` 创建空 DataSource，无文件处理（"将在 NFM-817 实现"）
- `browser_navigate` + `browser_console` 检查 Zotero Web API file_upload 文档——确认 3 步上传流程终止于 Zotero 的 S3

---

## 6. 总结：调研产出对照表

| 借鉴设计 | 来源工具 | NFMDI 落地方式 | 路线图阶段 |
|----------|----------|----------------|------------|
| CAS 物理去重 | Zotero | `_cas/{hash}/` 物理层 + 语义层引用 | B-Phase 4A |
| 乐观锁 | Zotero libraryVersion | DataSource.version + If-Match | B-Phase 1 |
| 全文索引（FTS） | Zotero FTS5 | PG tsvector + GIN 索引 | B-Phase 1 |
| CSL-JSON/BibTeX/RIS 导出 | Zotero Better BibTeX | 复用现有 DataSource 字段 | B-Phase 4A |
| Crowd Catalog | Mendeley | 元数据 + 字节双重去重，远期共享池 | 远期 |
| S3 文件后端 | 全行业 | MinIO（B-Phase 1）→ XSKY（B-Phase 3） | 已采纳，详见 [对象存储可行性](object-storage-feasibility.md) |
