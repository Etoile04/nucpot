---
title: MCP Server 集成指南
date: 2026-07-25
summary: NFM MCP Server 将核燃料与材料属性数据库暴露为 9 个标准 MCP 工具，支持 Claude Code、OpenClaw、Hermes 等框架直接查询材料数据、属性、文献、本体和知识图谱。
tags:
  - integration
  - mcp
  - api
  - developer-guide
---

# MCP Server 集成指南

NFM 平台提供标准的 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) Server，让 AI 编码助手和 agent 框架能够直接查询核燃料与材料数据库，无需手动调用 REST API。

## 架构概览

```
┌─────────────┐     stdio / HTTP      ┌──────────────────┐     async     ┌──────────────┐
│ Claude Code │ ◄──────────────────► │  nfm-mcp-server  │ ◄──────────► │  NFM 数据库   │
│ OpenClaw    │     MCP Protocol      │  (FastMCP SDK)   │   SQLAlchemy  │  PostgreSQL   │
│ Hermes      │                        │  9 tools, 0 deps  │              │              │
└─────────────┘                        └──────────────────┘              └──────────────┘
```

- **位置**: `apps/mcp-server/`（monorepo 内独立 Python 包）
- **SDK**: `mcp[cli]` (FastMCP)
- **传输**: stdio（本地 Claude Code）、Streamable HTTP、SSE（远程部署）
- **数据库**: 复用 `nfm_db.services` 层，零业务逻辑重复

## 可用工具

| # | 工具名 | 域 | 只读 | 说明 |
|---|--------|-----|------|------|
| 1 | `search_materials` | 材料 | ✅ | 按名称、成分、别名全文搜索材料 |
| 2 | `get_material` | 材料 | ✅ | 按 UUID 获取材料完整记录（成分、晶体结构、别名） |
| 3 | `query_properties` | 属性 | ✅ | 查询材料热导率、密度、比热、弹性模量等属性数据 |
| 4 | `search_sources` | 文献 | ✅ | 搜索期刊文章、技术报告、手册等文献来源 |
| 5 | `query_potentials` | 热力学势 | ✅ | 查询 Gibbs 能、焓、熵、热容等热力学势模型 |
| 6 | `browse_ontology` | 本体 | ✅ | 浏览核燃料材料领域本体分类树 |
| 7 | `query_knowledge_graph` | 知识图谱 | ✅ | 跨实体语义关联查询（材料↔属性↔文献） |
| 8 | `trigger_extraction` | 抽取 | ❌ | 提交文档进行自动化数据抽取 |
| 9 | `get_extraction_status` | 抽取 | ✅ | 监控抽取任务进度 |

## 快速开始

### Claude Code 集成

1. 确保已安装 [uv](https://docs.astral.sh/uv/)
2. 在项目根目录创建 `.mcp.json`：

```json
{
  "mcpServers": {
    "nfm-db": {
      "command": "uv",
      "args": ["run", "--directory", "apps/mcp-server", "nfm-mcp-server"],
      "env": {
        "NFM_MCP_DATABASE_URL": "postgresql+asyncpg://nfm:nfm@localhost:5432/nfm"
      }
    }
  }
}
```

3. 重启 Claude Code，工具会自动发现。

参考项目中 `apps/mcp-server/.mcp.json.example` 获取完整配置模板。

### 独立启动

```bash
cd apps/mcp-server
uv sync
uv run nfm-mcp-server                           # stdio（默认）
uv run nfm-mcp-server --transport streamable_http --port 8002  # HTTP
```

## 配置项

所有配置使用 `NFM_MCP_` 前缀，支持 `.env` 文件：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NFM_MCP_DATABASE_URL` | `postgresql+asyncpg://nfm:nfm@localhost:5432/nfm` | 异步数据库连接串 |
| `NFM_MCP_DATABASE_POOL_SIZE` | `5` | 连接池大小 |
| `NFM_MCP_API_BASE_URL` | `http://localhost:8000/v1` | REST API 回退地址 |
| `NFM_MCP_KG_SERVICE_URL` | `http://localhost:8001` | 知识图谱服务地址 |
| `NFM_MCP_TRANSPORT` | `stdio` | 传输方式：`stdio`、`streamable_http`、`sse` |
| `NFM_MCP_HOST` | `127.0.0.1` | HTTP/SSE 监听地址 |
| `NFM_MCP_PORT` | `8002` | HTTP/SSE 监听端口 |

## 使用示例

### 搜索材料

```
→ search_materials(query="UO2")
← [{"id": "...", "name": "Uranium Dioxide", "formula": "UO2", ...}]
```

### 查询属性

```
→ query_properties(material_id="<uuid>", property_name="thermal_conductivity")
← [{"temperature": 300, "value": 8.5, "unit": "W/(m·K)", ...}]
```

### 浏览本体

```
→ browse_ontology(query="fuel")
← {"nodes": [...], "relationships": [...], "stats": {...}}
```

## 测试

```bash
cd apps/mcp-server
uv sync --extra dev
env -u PYTHONPATH -u VIRTUAL_ENV uv run --extra dev python -m pytest tests/ \
  --no-cov -k 'not integration and not client and not zotero and not embeddings and not e2e and not stdio'
```

当前 127 个单元测试全部通过（基于 `origin/main` commit `7e7fa19`）。

## 技术细节

### 依赖关系

- `nfm-db-api`（monorepo 内 `apps/api`）作为 sibling 依赖安装
- `mcp[cli]>=1.9.0` — MCP SDK
- `pydantic>=2.10.0` + `pydantic-settings` — 配置管理
- `httpx` — 异步 HTTP 客户端

### 代码结构

```
apps/mcp-server/
├── src/nfm_mcp/
│   ├── server.py          # FastMCP 实例创建 + 传输选择
│   ├── deps.py            # Settings + DB session 依赖注入
│   ├── embeddings.py      # 嵌入向量工具
│   ├── tools/
│   │   ├── materials.py       # search_materials, get_material
│   │   ├── properties.py      # query_properties
│   │   ├── sources.py         # search_sources
│   │   ├── potentials.py      # query_potentials
│   │   ├── ontology.py        # browse_ontology
│   │   ├── knowledge_graph.py # query_knowledge_graph
│   │   ├── extraction.py      # trigger_extraction, get_extraction_status
│   │   └── zotero.py          # Zotero 文献管理工具
│   └── zotero/
│       └── client.py       # Zotero API 客户端
├── tests/                  # 127 个单元测试 + 集成测试
├── docs/
│   ├── CLAUDE-CODE.md      # Claude Code 集成详细指南
│   └── TOOL-REFERENCE.md   # 完整工具参考文档
└── pyproject.toml
```

## 已知限制

1. **Resources 未实现** — 原设计定义了 `nfm://ontology/classes`、`nfm://ontology/properties`、`nfm://stats` 三个 MCP Resources，当前以 `browse_ontology` 工具替代了前两个，`stats` 尚未暴露
2. **`get_material` 仅支持 UUID** — 原设计的 slug/名称查找未实现
3. **`query_knowledge_graph` 不接受 Cypher** — 原设计允许直接 Cypher 查询，当前仅支持关键词 + 实体类型过滤
4. **pyzotero 为可选依赖** — Zotero 工具需要额外安装 `pyzotero`，未包含在主依赖中
5. **CI 未覆盖** — GitHub Actions 工作流未包含 mcp-server 测试
