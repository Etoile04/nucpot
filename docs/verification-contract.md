# 势函数验证状态契约（Potential Verification Contract）

**版本**: 1.0
**生效日期**: 2026-06-19
**负责人**: Lead Engineer
**关联**: NFM-297 / NFM-303（实现）, NFM-298（nucpot-autovc 服务与接入，待建）

> 本文档定义势函数（potential）验证生命周期的数据契约与 `PATCH` 接入缝（seam），
> 使 [NFM-298](/NFM/issues/NFM-298) 中的 nucpot-autovc 服务可以作为"即插即用"组件接入，
> 无需再次协商字段或状态机。

---

## 1. 验证状态枚举（Status Enum）

`verification_status` 是 `potentials` 表的一等字段（`String(16)`，非空，
带数据库 `CHECK` 约束），不再埋藏在 `extra` JSON 中。

| 取值 | 中文标签 | 含义 | 写入者 |
|------|----------|------|--------|
| `unverified` | 未验证 | 初始状态。每条新记录（种子数据 + 未来 WS1 上传）自动获得此值（列级 `server_default`）。 | 数据库默认值（列 `server_default='unverified'`） |
| `pending` | 验证中 | autovc 已接收任务、异步验证进行中。 | nucpot-autovc（通过 PATCH） |
| `verified` | 已验证 | 验证通过，物性在容差范围内。 | nucpot-autovc（通过 PATCH） |
| `failed` | 验证失败 | 验证未通过或异常退出，附失败原因。 | nucpot-autovc（通过 PATCH） |

> **写入权限**：`unverified` 仅由列默认值写入；`pending` / `verified` / `failed`
> 由 nucpot-autovc 通过下方的 `PATCH` 端点写入。状态机见 §4。

---

## 2. PATCH 接入缝（Seam Endpoint）

autovc 在异步验证完成后回调此端点写回结果。

| 项 | 值 |
|----|----|
| 方法 | `PATCH` |
| 路径 | `/api/v1/potentials/{potential_id}/verification` |
| 鉴权 | **暂无**（按 ADR-2 推迟；接入前必须加上鉴权 —— 见 §6） |
| 幂等 | 否 —— 每次调用直接覆盖 `verification_status` 与审计字段 |
| 来源 | 仅 nucpot-autovc 调用（NFM-298 接入后） |

### 2.1 请求体（Request Body）— `VerificationUpdate`

```jsonc
{
  "verification_status": "verified",        // 必填，三选一: pending | verified | failed
  "message": "all properties within tolerance", // 可选，自由文本说明
  "evidence_url": "https://example.org/report/1" // 可选，验证报告/证据链接
}
```

- `verification_status` 由 Pydantic `Literal["pending", "verified", "failed"]`
  校验。`unverified` 不在允许集合中（它是插入默认值，不是 PATCH 目标）；
  其他非法值（如 `"bogus"`）返回 **422**。
- `message` 与 `evidence_url` 按不可变更新写入 `extra` JSON 的审计区
  （`extra.verification_message`、`extra.verification_evidence_url`），
  不覆盖 `extra` 中的其他键。

### 2.2 响应（Response）

**成功（200）** —— `ApiResponse<PotentialDetail>`：

```jsonc
{
  "success": true,
  "data": {
    "id": "...",
    "name": "...",
    "verification_status": "verified",
    "extra": {
      "verification_message": "all properties within tolerance",
      "verification_evidence_url": "https://example.org/report/1"
    }
    // ...其余 PotentialDetail 字段
  }
}
```

**未找到（404）** —— `potential_id` 不存在：

```json
{ "detail": "Potential not found" }
```

**校验失败（422）** —— `verification_status` 非法：

```json
{ "detail": [ { "loc": ["body", "verification_status"], "msg": "...", "type": "literal_error" } ] }
```

---

## 3. WS1 → autovc → PATCH-back 流程（NFM-298 接入后）

```
[WS1 上传 POST]                (本 issue 未建；Slice A)
   │
   │  势函数记录入库，verification_status = 'unverified'  ← 列默认值
   ▼
[上传服务 → autovc]            (NFM-298 接线)
   │  提交异步验证任务
   ▼
[autovc PATCH /verification]   status='pending'
   │
   │  异步运行验证
   ▼
[autovc PATCH /verification]   status='verified' | 'failed'  (+ message / evidence_url)
   │
   ▼
[详情页 PotentialOverview]      渲染验证状态 Tag（中文标签 + 语义颜色）
```

> 本 issue（NFM-303）只交付**深色方框之外**的全部组件：列/迁移、模型、
> schema、service helper、PATCH 端点、详情页渲染。深色方框（autovc 服务本体、
> 上传→autovc 接线、WS1 POST）属于 [NFM-298](/NFM/issues/NFM-298) 与 Slice A。
> 在 autovc 接入前，PATCH 端点是"防御性"的 —— 存在、可校验、无人调用。

---

## 4. 有效状态迁移（State Transitions）

```
        unverified ──(autovc 接收)──▶ pending
            │                            │
            │                            ├──(验证通过)──▶ verified
            │                            └──(验证失败)──▶ failed
            │
            └──(列默认值；不可经 PATCH 退回)
```

- `unverified → pending → {verified | failed}` 为预期路径。
- 允许从终态重新触发（例如 `verified → pending` 表示重新验证）：PATCH 直接覆盖，
  无状态机硬阻断 —— autovc 自行决定是否重跑。
- **不可**经 PATCH 写回 `unverified`（schema `Literal` 集合不含它）。

---

## 5. 轮询兜底说明（Poll Fallback）

若 autovc 无法发起回调（网络隔离、回调失败），WS1/上传服务可改为**轮询**
autovc 的查询接口（NFM-298 提供）以获取结果，再通过同一 PATCH 端点写回。
即：autovc ↔ nucpot 的唯一写回通道始终是此 `PATCH` 端点，无论触发方式是
"回调"还是"轮询"。这保证状态写入路径单一、可审计。

---

## 6. 安全 / 待办（Open Items）

- **鉴权（ADR-2）**：PATCH 端点当前无鉴权。**接入 autovc 前**必须加鉴权
  （API key / mTLS / 服务账户任一）。在此之前，端点仅在内部网络暴露，
  不得对公网开放。
- **速率限制**：接入后应给 PATCH 端点配置速率限制（参考既有 ontology 端点模式）。
- **审计**：`message` / `evidence_url` 存于 `extra`；若后续需要完整审计轨迹
  （谁/何时/旧值），考虑独立的 `verification_events` 表（Phase 3）。

---

## 7. 数据库契约（迁移 005）

迁移文件：`apps/api/migrations/versions/005_add_verification_status.py`

- 列：`verification_status String(16) NOT NULL DEFAULT 'unverified'`
- 约束：`CHECK (verification_status IN ('unverified','pending','verified','failed'))`
- `down_revision = '004'`，单向线性，支持 `upgrade` / `downgrade`。
