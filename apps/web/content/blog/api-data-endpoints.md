---
title: API 数据端点详解
date: 2026-06-29
summary: NFMD 平台核心数据端点的完整文档，包括材料势函数浏览、参考数据管理、本体图谱、NVL 端点以及通用筛选/分页/排序参数。
tags:
  - api
author: NFMD 开发团队
status: published
---

# API 数据端点详解

本文档详细描述 NFMD API 的核心数据端点，包括请求/响应示例和参数说明。所有端点的通用格式参见 [API 参考概览](/blog/api-reference-overview)。

---

## 1. 材料势函数端点

管理材料间势函数（EAM、Buckingham、MEAM 等）。

### 列表浏览

```bash
curl "https://{host}/api/v1/potentials?page=1&limit=10&type=EAM&elements=U,Zr&sort=updated"
```

**请求参数**（Query String）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `page` | `int` | `1` | 页码 |
| `limit` | `int` | `20` | 每页条数 |
| `type` | `string` | — | 势函数类型：`EAM`、`Buckingham`、`MEAM` |
| `elements` | `string` | — | 元素筛选，逗号分隔：`"U,Zr"` |
| `q` | `string` | — | 全文搜索关键词 |
| `sort` | `string` | — | 排序字段：`updated`、`name`、`type` |

**响应**

```json
{
  "success": true,
  "data": {
    "potentials": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "name": "Zr-U EAM potential (Mendelev 2020)",
        "type": "EAM",
        "elements": ["U", "Zr"],
        "description": "Embedded atom method potential for U-Zr system",
        "functional_form": "EAM (alloy)",
        "citation": "Mendelev et al., Phys. Rev. B (2020)",
        "tags": ["U-Zr", "reactor", "validated"],
        "file_path": "/uploads/potentials/zr-u-eam.json",
        "file_size": 45000,
        "created_at": "2026-01-15T10:30:00Z",
        "updated_at": "2026-03-10T14:20:00Z"
      }
    ],
    "total": 42,
    "page": 1,
    "limit": 10
  },
  "error": null,
  "meta": null
}
```

### 获取详情

```bash
curl "https://{host}/api/v1/potentials/550e8400-e29b-41d4-a716-446655440000"
```

返回单个势函数的完整信息，结构同上。

### 创建势函数

```bash
curl -X POST "https://{host}/api/v1/potentials" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "UO2 Buckingham potential",
    "type": "Buckingham",
    "elements": ["U", "O"],
    "description": "Buckingham potential for UO2 nuclear fuel",
    "functional_form": "Buckingham + Morse",
    "citation": "Catlow (1977)",
    "tags": ["UO2", "fuel", "classical"]
  }'
```

### 上传势函数文件

```bash
curl -X POST "https://{host}/api/v1/potentials/{potential_id}/file" \
  -H "Authorization: Bearer {token}" \
  -F "file=@potential.dat"
```

**响应**

```json
{
  "success": true,
  "data": {
    "filename": "potential.dat",
    "size": 12840,
    "content_type": "application/octet-stream",
    "uploaded_at": "2026-06-29T12:00:00Z"
  },
  "error": null,
  "meta": null
}
```

---

## 2. 参考数据端点

管理参考物性值的暂存、审核和导出。

### 批量写入暂存

```bash
curl -X POST "https://{host}/api/v1/reference-values/bulk" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "values": [
      {
        "element_system": "UO2",
        "phase": "FCC",
        "property_name": "lattice_constant",
        "value": 5.471,
        "unit": "angstrom",
        "method": "DFT",
        "source": "MD-Sim 2024",
        "confidence": "high",
        "temperature": 300,
        "cache_level": "L1"
      }
    ]
  }'
```

**请求体字段**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `element_system` | `string` | ✅ | 元素体系：`"U"`、`"UO2"`、`"Zr"` |
| `phase` | `string` | — | 晶体相：`"BCC"`、`"FCC"`、`"alpha"` |
| `property_name` | `string` | ✅ | 属性名称 |
| `value` | `float` | ✅ | 数值 |
| `unit` | `string` | ✅ | 单位：`angstrom`、`GPa`、`eV/atom` |
| `method` | `string` | — | 计算方法：`"DFT"`、`"EXP"` |
| `source` | `string` | ✅ | 数据来源标识 |
| `source_doi` | `string` | — | DOI 引用 |
| `confidence` | `string` | — | 置信度：`"high"`、`"medium"`、`"low"` |
| `uncertainty` | `float` | — | 不确定度 |
| `temperature` | `float` | — | 温度（K） |
| `cache_level` | `string` | — | 缓存层级：`"L1"`、`"L2"`、`"L3A"`、`"L3B"` |

**响应**（201 Created）

```json
{
  "success": true,
  "data": {
    "accepted": 1,
    "rejected": 0,
    "results": [
      {
        "status": "accepted",
        "staging_id": "stg_001"
      }
    ]
  },
  "error": null,
  "meta": null
}
```

### 查询待审核记录

```bash
curl "https://{host}/api/v1/reference-values/pending-review?element_system=UO2&phase=FCC&confidence=high&page=1&per_page=20"
```

**筛选参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `element_system` | `string` | 元素体系 |
| `phase` | `string` | 晶体相 |
| `property_name` | `string` | 属性名称 |
| `confidence` | `string` | 置信度筛选 |
| `status` | `string` | 暂存状态 |
| `page` | `int` | 页码 |
| `per_page` | `int` | 每页条数 |

### 审批记录

```bash
# 通过
curl -X POST "https://{host}/api/v1/reference-values/{staging_id}/approve" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"review_note": "DFT 计算结果与实验吻合"}'

# 驳回
curl -X POST "https://{host}/api/v1/reference-values/{staging_id}/reject" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"review_note": "不确定度过大，需重新计算"}'
```

**审批响应**

```json
{
  "success": true,
  "data": {
    "staging_id": "stg_001",
    "status": "APPROVED",
    "property_measurement_id": "pm_12345"
  },
  "error": null,
  "meta": null
}
```

### 批量导出

```bash
curl -X POST "https://{host}/api/v1/reference-values/export" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "filters": {
      "element_system": "UO2",
      "phase": "FCC"
    },
    "limit": 100,
    "offset": 0
  }'
```

### 验证回调

用于外部验证服务将结果回传至平台：

```bash
curl -X POST "https://{host}/api/v1/reference-values/verify-callback" \
  -H "Content-Type: application/json" \
  -d '{
    "results": [
      {
        "staging_id": "stg_001",
        "verified": true,
        "confidence_score": 0.92
      }
    ]
  }'
```

---

## 3. 参考数据缺口端点

查询和填充参考数据中的覆盖缺口。

### 查询缺口列表

```bash
curl "https://{host}/api/v1/reference-gaps?element_system=UO2&property_name=lattice_constant&sort_by=property_name&page=1&per_page=50"
```

**筛选参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `element_system` | `string` | 元素体系 |
| `phase` | `string` | 晶体相 |
| `property_name` / `property` | `string` | 属性名称（两个别名均可用） |
| `sort_by` | `string` | 排序字段 |
| `page` | `int` | 页码 |
| `per_page` | `int` | 每页条数 |

### 缺口覆盖率摘要

```bash
curl "https://{host}/api/v1/reference-gaps/summary"
```

**响应**

```json
{
  "success": true,
  "data": {
    "total_target_tuples": 312,
    "covered": 187,
    "gaps": 125,
    "coverage_percent": 59.9,
    "by_system": [
      { "element_system": "UO2", "covered": 45, "gaps": 12 },
      { "element_system": "Zr", "covered": 32, "gaps": 8 }
    ],
    "staging_pending": 15,
    "staging_approved": 7
  },
  "error": null,
  "meta": null
}
```

### 触发缺口扫描

```bash
curl -X POST "https://{host}/api/v1/reference-gaps/scan" \
  -H "Content-Type: application/json" \
  -d '{"element_systems": ["UO2", "Zr"]}'
```

### 触发缺口填充

```bash
curl -X POST "https://{host}/api/v1/reference-gaps/fill" \
  -H "Content-Type: application/json" \
  -d '{
    "element_system": "UO2",
    "phase": "FCC",
    "property_name": "lattice_constant",
    "cache_levels": ["L1", "L2"],
    "dry_run": false
  }'
```

> 返回 `202 Accepted`，表示异步任务已启动。

**Python 示例**

```python
import requests

BASE_URL = "https://{host}/api/v1"

# 查看缺口覆盖率
resp = requests.get(f"{BASE_URL}/reference-gaps/summary")
summary = resp.json()["data"]
print(f"总覆盖率: {summary['coverage_percent']:.1f}%")

for sys in summary["by_system"]:
    print(f"  {sys['element_system']}: {sys['covered']} 已覆盖, {sys['gaps']} 缺口")

# 扫描特定体系的缺口
resp = requests.post(f"{BASE_URL}/reference-gaps/scan", json={
    "element_systems": ["UO2", "Zr", "U"]
})
scan = resp.json()["data"]
print(f"发现 {scan['total_gaps_found']} 个缺口")

# 填充缺口（试运行）
resp = requests.post(f"{BASE_URL}/reference-gaps/fill", json={
    "element_system": "UO2",
    "phase": "FCC",
    "property_name": "bulk_modulus",
    "cache_levels": ["L1"],
    "dry_run": True
})
fill = resp.json()["data"]
print(f"试运行: 找到 {fill['values_found']} 个候选值")
```

---

## 4. 本体图谱端点

NVL（Named Value Lists）本体数据的图谱查询。该端点实施速率限制：**30 次/分钟/IP**。

### 获取语料库图谱

```bash
curl "https://{host}/api/v1/ontology/corpora/nuclear_materials/graph?max_nodes=50"
```

**路径参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `corpus_id` | `string` | 语料库标识，正则限制 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` |

**查询参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_nodes` | `int` | — | 最大节点数（受服务端 HARD_MAX_NODES 限制） |
| `cursor` | `string` | — | 分页游标（用于大型图谱的增量加载） |

**响应**（200 OK）

```json
{
  "success": true,
  "data": {
    "schema_version": "1.0",
    "corpus_id": "nuclear_materials",
    "generated_at": "2026-06-29T10:00:00Z",
    "source_ontology": "NVL-NFM",
    "source_digest": "sha256:abc123...",
    "stats": {
      "total_nodes": 156,
      "total_relationships": 342,
      "node_types": { "class": 45, "individual": 111 },
      "relationship_types": { "subclassOf": 67, "hasProperty": 120 }
    },
    "nodes": [
      {
        "id": "node_001",
        "type": "class",
        "name": "NuclearFuelMaterial",
        "label": "核燃料材料",
        "comment": "用于核反应堆燃料的材料类别",
        "uri": "http://nvl.example.org/NuclearFuelMaterial",
        "color": "#4A90D9",
        "size": 1.5,
        "record_ref": null
      }
    ],
    "relationships": [
      {
        "source": "node_001",
        "target": "node_002",
        "type": "subclassOf"
      }
    ],
    "pagination": {
      "has_more": false,
      "next_cursor": null
    }
  },
  "error": null,
  "meta": null
}
```

**缓存头**：`Cache-Control: public, max-age=60`、`ETag`、`Last-Modified`

> **注意**：本体数据为不可变的科学参考数据，每次 corpus 更新后 digest 会变化。

---

## 5. 可视化端点

为前端提供预处理的图谱可视化数据。

### NVL 可视化数据

```bash
curl "https://{host}/api/v1/viz/nvl?class=NuclearFuelMaterial&search=uranium&max_nodes=80"
```

**查询参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `class` | `string` | 按类别筛选节点 |
| `search` | `string` | 搜索关键词 |
| `max_nodes` | `int` | 最大返回节点数 |

**响应**

```json
{
  "success": true,
  "data": {
    "nodes": [
      { "id": "n1", "label": "UO2", "color": "#4A90D9", "size": 2.0 },
      { "id": "n2", "label": "Zr", "color": "#E67E22", "size": 1.2 }
    ],
    "relationships": [
      { "source": "n1", "target": "n2", "type": "relatedTo" }
    ]
  },
  "error": null,
  "meta": null
}
```

### 本体统计信息

```bash
curl "https://{host}/api/v1/viz/stats"
```

**响应**

```json
{
  "success": true,
  "data": {
    "node_count": 156,
    "relationship_count": 342,
    "class_distribution": {
      "NuclearFuelMaterial": 12,
      "StructuralProperty": 23,
      "ThermodynamicProperty": 18
    }
  },
  "error": null,
  "meta": null
}
```

---

## 6. 提取管线端点

触发和跟踪文献数据提取任务。

### 触发提取（v1）

```bash
curl -X POST "https://{host}/api/v1/extraction/trigger" \
  -H "Content-Type: application/json" \
  -d '{
    "source_reference": "10.1016/j.nucengdes.2024.01.001",
    "source_type": "doi",
    "element_systems": ["UO2"],
    "cache_level": "L1",
    "max_confidence": "medium"
  }'
```

> 返回 `202 Accepted`。

**有效 `source_type` 值**：`doi`、`url`、`file`、`internal_id`

### 查询任务状态

```bash
curl "https://{host}/api/v1/extraction/status/{job_id}"
```

**响应**

```json
{
  "success": true,
  "data": {
    "job_id": "job_abc123",
    "source_reference": "10.1016/j.nucengdes.2024.01.001",
    "status": "completed",
    "extracted_count": 23,
    "staged_count": 18,
    "rejected_count": 5,
    "error_message": null,
    "created_at": "2026-06-29T10:00:00Z",
    "started_at": "2026-06-29T10:00:05Z",
    "completed_at": "2026-06-29T10:02:30Z"
  },
  "error": null,
  "meta": null
}
```

### 浏览提取的属性（v4）

v4 端点提供更细粒度的属性浏览功能：

```bash
curl "https://{host}/api/v4/properties/UO2?property_category=structural&confidence=high&phase=FCC&page=1&limit=20&sort_by=property_name&sort_order=asc"
```

**筛选参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `property_category` | `string` | 属性类别 |
| `confidence` | `string` | 置信度 |
| `phase` | `string` | 晶体相 |
| `temperature_min` | `float` | 温度下限（K） |
| `temperature_max` | `float` | 温度上限（K） |
| `staging_status` | `string` | 暂存状态 |
| `page` / `limit` | `int` | 分页 |
| `sort_by` / `sort_order` | `string` | 排序 |

### 获取材料体系列表（v4）

```bash
curl "https://{host}/api/v4/material-systems?has_pending_review=true&category=oxide"
```

---

## 7. 验证端点

领域专家级别的验证工具。

### 参考值验证

```bash
curl -X POST "https://{host}/api/v1/verification/check-gap" \
  -H "Content-Type: application/json" \
  -d '{
    "element_system": "UO2",
    "property_name": "lattice_constant",
    "value": 5.471,
    "unit": "angstrom",
    "source": "Catlow 1977",
    "source_type": "doi",
    "source_doi": "10.1016/0022-3115(77)90047-5",
    "method": "EXP",
    "temperature": 300,
    "phase": "FCC"
  }'
```

**响应**

```json
{
  "success": true,
  "data": {
    "validation_id": "val_001",
    "validated_at": "2026-06-29T12:00:00Z",
    "confidence_score": 0.89,
    "is_validated": true,
    "needs_escalation": false,
    "literature_matches": [
      { "source": "Smirnov 2014", "value": 5.468, "method": "DFT" }
    ],
    "estimated_uncertainty": 0.003,
    "source_credibility_score": 0.85,
    "notes": "与已发表 DFT 结果一致，偏差 < 0.1%"
  },
  "error": null,
  "meta": null
}
```

### 验证模块健康检查

```bash
curl "https://{host}/api/v1/verification/health"
```

```json
{
  "success": true,
  "data": {
    "status": "healthy",
    "module": "verification",
    "version": "1.0.0",
    "timestamp": "2026-06-29T12:00:00Z"
  },
  "error": null,
  "meta": null
}
```

---

## 通用 Python SDK 模式

以下是一个可复用的 Python 封装模式，用于 NFMD API 交互：

```python
import requests
from typing import Any

class NFMDClient:
    """NFMD API 客户端封装"""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self._authenticate(username, password)

    def _authenticate(self, username: str, password: str) -> None:
        resp = self.session.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"username": username, "password": password}
        )
        resp.raise_for_status()
        token = resp.json()["data"]["access_token"]
        self.session.headers.update({
            "Authorization": f"Bearer {token}"
        })

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        resp = self.session.request(
            method, f"{self.base_url}{path}", **kwargs
        )
        resp.raise_for_status()
        return resp.json()

    def list_potentials(self, **params: Any) -> dict:
        return self._request("GET", "/api/v1/potentials", params=params)

    def get_reference_gaps_summary(self) -> dict:
        return self._request("GET", "/api/v1/reference-gaps/summary")

    def submit_extraction(self, source_reference: str, source_type: str) -> dict:
        return self._request("POST", "/api/v1/extraction/trigger", json={
            "source_reference": source_reference,
            "source_type": source_type
        })

    def get_ontology_graph(self, corpus_id: str, **params: Any) -> dict:
        return self._request(
            "GET",
            f"/api/v1/ontology/corpora/{corpus_id}/graph",
            params=params
        )

# 使用示例
client = NFMDClient("https://{host}", "your_username", "your_password")

# 浏览势函数
potentials = client.list_potentials(type="EAM", limit=10)
print(f"找到 {potentials['data']['total']} 个 EAM 势函数")

# 查看覆盖率
summary = client.get_reference_gaps_summary()
print(f"参考数据覆盖率: {summary['data']['coverage_percent']:.1f}%")
```

---

## 相关文档

- [API 参考概览](/blog/api-reference-overview) — 基础 URL、认证、速率限制、错误代码
