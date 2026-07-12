---
title: NFMD API 参考概览
date: 2026-06-29
summary: NFMD 平台 REST API 的完整参考指南，包括基础 URL、认证机制、速率限制、响应格式、错误代码及快速入门示例。
tags:
  - api
  - getting-started
author: NFMD 开发团队
status: published
---

# NFMD API 参考概览

本文档为 NFMD（核燃料材料数据库）REST API 的完整参考指南。所有端点均基于 **FastAPI** 框架构建，返回一致的 JSON 响应格式。

## 基础 URL

API 采用版本化路径前缀：

```
https://{host}/api/v1/    # 稳定版本
https://{host}/api/v4/    # 实验性提取管线
```

## 认证

NFMD API 使用 **JWT Bearer Token** 认证。

### 获取 Token

```bash
curl -X POST "https://{host}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "your_username", "password": "your_password"}'
```

响应：

```json
{
  "success": true,
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "bearer"
  },
  "error": null,
  "meta": null
}
```

### 使用 Token

在后续请求的 Header 中携带 Token：

```bash
curl -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  "https://{host}/api/v1/reference-values/pending-review"
```

### Python 示例

```python
import requests

BASE_URL = "https://{host}/api/v1"

# 登录获取 Token
resp = requests.post(f"{BASE_URL}/auth/login", json={
    "username": "your_username",
    "password": "your_password"
})
token = resp.json()["data"]["access_token"]

# 携带 Token 发起请求
headers = {"Authorization": f"Bearer {token}"}
resp = requests.get(
    f"{BASE_URL}/reference-values/pending-review",
    headers=headers,
    params={"page": 1, "per_page": 20}
)
data = resp.json()
print(f"共 {data['data']['total']} 条待审核记录")
```

> **注意**：Token 默认有效期为 30 分钟，过期后需重新获取。

## 速率限制

API 对高开销操作实施了基于 IP 的滑动窗口速率限制：

| 端点类别 | 限制 | 触发条件 |
|----------|------|----------|
| 本体图谱 | 30 次/分钟 | `/api/v1/ontology/*` |
| MD 验证任务 | 5 次/分钟 | `/api/v1/md-verification/jobs` (POST) |
| 通用端点 | 无限制 | 其他所有端点 |

超出限制时返回：

```json
{
  "success": false,
  "data": null,
  "error": "Rate limit exceeded. Please retry after 60 seconds.",
  "meta": null
}
```

HTTP 状态码 `429 Too Many Requests`，响应头包含 `Retry-After` 字段（秒）。

## 响应格式

所有端点返回统一的 **JSON 信封格式**：

```json
{
  "success": true,
  "data": { ... },
  "error": null,
  "meta": null
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | `bool` | 请求是否成功 |
| `data` | `T \| null` | 响应数据负载 |
| `error` | `string \| null` | 错误描述（成功时为 `null`） |
| `meta` | `object \| null` | 分页等元数据 |

### 分页响应

列表端点在 `data` 中返回分页对象，`meta` 字段携带分页元数据：

```json
{
  "success": true,
  "data": {
    "items": [ ... ],
    "total": 156,
    "page": 1,
    "limit": 20,
    "pages": 8
  },
  "error": null,
  "meta": null
}
```

部分端点使用 `per_page` 替代 `limit`，两者含义相同。

## 错误代码

| HTTP 状态码 | 含义 | 典型场景 |
|------------|------|----------|
| `200` | 成功 | GET 数据、健康检查 |
| `201` | 已创建 | POST 批量暂存、创建势函数 |
| `202` | 已接受 | 触发提取任务、验证工作流 |
| `204` | 无内容 | DELETE 博客文章 |
| `400` | 请求错误 | 无效的 source_type、校验失败 |
| `401` | 未授权 | 缺失或无效的 JWT Token |
| `403` | 禁止访问 | 权限不足 |
| `404` | 未找到 | Job ID 不存在、语料库不存在 |
| `409` | 冲突 | 任务未完成、无效状态转换 |
| `422` | 不可处理 | Schema 校验失败、无效筛选参数 |
| `429` | 请求过多 | 超出速率限制 |
| `500` | 服务器错误 | 管线失败、数据库异常 |
| `503` | 服务不可用 | Celery 任务队列不可用 |

### 错误响应示例

```json
{
  "success": false,
  "data": null,
  "error": "Job not found: abc123",
  "meta": null
}
```

## 通用查询参数

### 分页

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `page` | `int` | `1` | 页码（≥ 1） |
| `limit` / `per_page` | `int` | `20` | 每页条数（1–200） |

### 排序

| 参数 | 类型 | 说明 |
|------|------|------|
| `sort_by` | `string` | 排序字段（如 `created_at`、`updated`、`name`、`type`） |
| `sort_order` | `string` | `asc` 或 `desc` |

### 筛选

根据端点不同，支持以下筛选参数：

| 参数 | 类型 | 示例 |
|------|------|------|
| `element_system` | `string` | `"UO2"`, `"Zr"` |
| `phase` | `string` | `"FCC"`, `"BCC"` |
| `property_name` | `string` | `"lattice_constant"` |
| `status` | `string` | `"pending"`, `"approved"` |
| `confidence` | `string` | `"high"`, `"medium"`, `"low"` |
| `elements` | `string` | 逗号分隔：`"U,Zr"` |
| `element_systems` | `string[]` | 数组形式多值筛选 |
| `q` | `string` | 全文搜索关键词 |

## 快速开始

### 1. 健康检查

```bash
curl "https://{host}/api/v1/health"
```

```json
{ "status": "ok" }
```

### 2. 浏览材料势函数

```bash
curl "https://{host}/api/v1/potentials?page=1&limit=10&type=EAM"
```

### 3. 查询参考数据缺口

```bash
curl "https://{host}/api/v1/reference-gaps?element_system=UO2&property_name=lattice_constant"
```

### 4. 获取本体图谱

```bash
curl "https://{host}/api/v1/ontology/corpora/nuclear_materials/graph?max_nodes=100"
```

### 5. Python 完整示例

```python
import requests

BASE_URL = "https://{host}/api/v1"

# 浏览势函数
resp = requests.get(f"{BASE_URL}/potentials", params={
    "page": 1,
    "limit": 10,
    "elements": "U,Zr"
})

if resp.json()["success"]:
    potentials = resp.json()["data"]["potentials"]
    for p in potentials:
        print(f"{p['name']} ({p['type']}) — 元素: {', '.join(p['elements'])}")

# 查询参考数据缺口摘要
resp = requests.get(f"{BASE_URL}/reference-gaps/summary")
summary = resp.json()["data"]
print(f"覆盖率: {summary['coverage_percent']:.1f}%")
print(f"已覆盖: {summary['covered']} / 总计: {summary['total_target_tuples']}")
```

## 下一步

- [API 数据端点详解](/blog/api-data-endpoints) — 核心数据端点的完整文档

## 缓存策略

本体图谱端点使用 HTTP 缓存头优化性能：

| Header | 值 | 说明 |
|--------|-----|------|
| `Cache-Control` | `public, max-age=60` | 公开缓存 60 秒 |
| `ETag` | 内容摘要 | 用于条件请求 |
| `Last-Modified` | 时间戳 | 用于条件请求 |

## 版本说明

当前生产版本为 **v1**，适用于所有核心数据端点。**v4** 为实验性提取管线版本，提供更细粒度的提取、验证和浏览功能。
