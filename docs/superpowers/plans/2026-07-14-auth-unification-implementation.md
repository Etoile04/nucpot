# Plan: Auth Unification Implementation

**日期**: 2026-07-14
**规模**: L 级
**分支**: `feat/auth-unification` off main

## Task Graph (GOAP)

### Goal State
```json
{
  "cookie_auth": true,
  "rate_limit_active": true,
  "password_policy": true,
  "csrf_protection": true,
  "all_endpoints_protected": true,
  "redis_user_cache": true,
  "multi_worker": true,
  "supabase_data_migrated": true,
  "routes_migrated": true,
  "unified_login": true,
  "supabase_removed": true
}
```

### Initial State
```json
{
  "cookie_auth": false,
  "rate_limit_active": false,
  "password_policy": false,
  "csrf_protection": false,
  "all_endpoints_protected": false,
  "redis_user_cache": false,
  "multi_worker": false,
  "supabase_data_migrated": false,
  "routes_migrated": false,
  "unified_login": false,
  "supabase_removed": false
}
```

---

## Sprint 1: 安全升级 (Day 1)

### Wave 1A: 后端认证重构 (并行)

- [ ] **T1**: HttpOnly Cookie 认证 — 2h
  - preconditions: spec_approved
  - files: `auth_endpoints.py` (login set_cookie), `core/auth.py` (read from cookie), `api-client.ts` (credentials:include)
  - effects: cookie_auth
  - assign: subagent

- [ ] **T2**: 启用 SlowAPI 速率限制 — 30min
  - files: `auth_endpoints.py` (@limiter.limit)
  - effects: rate_limit_active
  - assign: exec

- [ ] **T3**: 密码强度校验 — 30min
  - files: `schemas/auth.py` (validator)
  - effects: password_policy
  - assign: exec

### Wave 1B: 前端适配 (T1 完成后)

- [ ] **T4**: 前端 credentials:"include" + 移除 localStorage — 1h
  - deps: [T1]
  - files: `api-client.ts`, `AuthGuard.tsx`, `BlogAuthGuard.tsx`, `middleware.ts`
  - effects: cookie_auth (frontend side)

- [ ] **T5**: CSRF double-submit cookie — 1h
  - deps: [T1]
  - files: `middleware.ts` (读取+验证 X-CSRF-Token header)
  - effects: csrf_protection

### Wave 1C: 部署验证

- [ ] **T6**: Docker 重建 + 路由验证 — 30min
  - deps: [T1, T2, T3, T4, T5]
  - effects: sprint1_deployed

---

## Sprint 2: 授权审计 + 性能 (Day 2)

### Wave 2A: 端点授权 (独立)

- [ ] **T7**: 审计 86 个无 auth 端点 + 补 Depends — 4h
  - preconditions: sprint1_deployed
  - files: ~20 endpoint files under `api/v1/`
  - effects: all_endpoints_protected
  - assign: subagent (大批量机械改动)

### Wave 2B: 性能优化 (与 T7 并行)

- [ ] **T8**: Redis user 缓存 — 1h
  - files: `core/auth.py` (get_current_user 加 Redis lookup), `auth_service.py`
  - effects: redis_user_cache

- [ ] **T9**: uvicorn 4 workers — 15min
  - files: `docker/prod-api.Dockerfile` (CMD 加 --workers 4)
  - effects: multi_worker

---

## Sprint 3: Supabase 迁移 (Day 3-5)

### Wave 3A: 数据迁移 (最先)

- [ ] **T10**: Supabase 数据导出 — 1h
  - preconditions: sprint2_deployed
  - 用 Supabase SDK 导出 7 张表为 JSON
  - effects: supabase_data_exported

- [ ] **T11**: 本地 PG 建表 + 数据导入 — 2h
  - deps: [T10]
  - 创建 migration: profiles, contributions, feedback, reference_values, review_audit_log, auth-proofs, potentials(Supabase 版)
  - 数据映射：Supabase UUID → FastAPI users.id 关联
  - effects: supabase_data_migrated
  - assign: subagent

### Wave 3B: API Route 迁移 (T11 完成后, 并行)

- [ ] **T12**: 迁移 auth routes (8 个) — 2h
  - deps: [T11]
  - `/api/auth/login` → FastAPI `/auth/login` (已有，适配返回 cookie)
  - `/api/auth/register` → FastAPI `/auth/register`
  - `/api/auth/profile` GET/PATCH → FastAPI `/auth/me` + `/auth/me/profile`
  - `/api/auth/my-contributions` → FastAPI `/auth/me/contributions`
  - `/api/auth/template` → FastAPI `/auth/template`
  - `/api/auth/upload-proof` → FastAPI `/auth/upload-proof`
  - effects: auth_routes_migrated
  - assign: subagent

- [ ] **T13**: 迁移 admin routes (7 个) — 1h
  - deps: [T11]
  - `/api/admin/contributions` → FastAPI `/admin/contributions`
  - `/api/admin/stats` → FastAPI `/admin/stats`
  - `/api/admin/ref-values/*` → FastAPI `/admin/ref-values/*`
  - effects: admin_routes_migrated
  - assign: subagent

- [ ] **T14**: 迁移业务 routes (8 个) — 1h
  - deps: [T11]
  - `/api/feedback` → FastAPI `/feedback`
  - `/api/potentials/*` → FastAPI `/potentials/*`
  - `/api/stats` → FastAPI `/stats`
  - effects: business_routes_migrated
  - assign: subagent

### Wave 3C: 前端统一 (T12-T14 完成后)

- [ ] **T15**: 统一登录页 — 1h
  - deps: [T12]
  - 合并 `/login` 和 `/admin/login`
  - 新登录页用 FastAPI JWT + HttpOnly Cookie
  - `/login` 重定向到 `/admin/login` 或替换
  - effects: unified_login

- [ ] **T16**: 移除 Supabase SDK — 30min
  - deps: [T15, T12, T13, T14]
  - 删除 `lib/supabase.ts`
  - 删除 `AuthProvider.tsx` (替换为 JWT session check)
  - 移除所有 `import { supabase }` 引用
  - 从 package.json 移除 @supabase/supabase-js
  - effects: supabase_removed

---

## 并行计划

```
Day 1 (Sprint 1):
  Wave 1A: T1(cookie) + T2(limit) + T3(password)  ← 并行
  Wave 1B: T4(frontend) + T5(CSRF)                  ← T1 完成后并行
  Wave 1C: T6(deploy verify)

Day 2 (Sprint 2):
  Wave 2A: T7(86 endpoints audit)                   ← 独立
  Wave 2B: T8(Redis cache) + T9(workers)            ← 与 T7 并行

Day 3-5 (Sprint 3):
  Wave 3A: T10(export) → T11(import)                ← 串行
  Wave 3B: T12(auth) + T13(admin) + T14(business)   ← T11 后并行 (3 subagents)
  Wave 3C: T15(login) → T16(cleanup)                 ← 串行
```

## Subagent 派发策略

| Task | 方式 | 理由 |
|------|------|------|
| T1 (Cookie auth) | delegate | 认证逻辑重构 |
| T2+T3 (限流+密码) | exec | 小改动 |
| T7 (86 endpoints) | delegate | 大批量机械改动 |
| T11 (建表+迁移) | delegate | 需要查 Supabase schema |
| T12+T13+T14 (route 迁移) | delegate × 3 并行 | 独立模块 |

## Replan Triggers

- HttpOnly Cookie 在 Cloudflare tunnel 下不工作 → 回退为 Authorization header + 短 TTL
- Supabase 数据结构与 FastAPI 不兼容 → 设计中间映射层
- 86 个端点 auth 补丁导致测试大面积失败 → 逐模块合并
- uvicorn 4 workers 导致内存不足 → 降到 2 workers
