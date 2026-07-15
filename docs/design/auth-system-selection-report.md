# 认证系统选型分析报告

**项目**: NucPot 核材料势函数库  
**日期**: 2026-07-14  
**分析人**: 项目经理 (dev-pm)  
**决策类型**: 架构选型 — 统一认证方案

---

## 1. 背景与问题

NucPot 平台当前存在**两套独立的认证系统**，分别服务于不同的子功能：

| 子功能 | 认证系统 | 登录入口 | 数据存储 |
|--------|---------|---------|---------|
| 管理后台 / Review 队列 / KG 审核 / Extraction | FastAPI JWT | `/admin/login` | 本地 PostgreSQL `users` 表 |
| 用户中心 / 势能上传 / 反馈 / 博客 | Supabase Auth | `/login` | Supabase Cloud (`gzhiqyopzlmnkdzammhx.supabase.co`) |

**导致的实际问题**：
- 用户 `lwj280@gmail.com` 在 `/admin/login` 登录后无法在 `/profile` 修改密码（两套密码不同步）
- 权限配置需要维护两套用户角色（FastAPI `blog_role` + Supabase `profiles.role`）
- Supabase 7 个数据表（`profiles`、`contributions`、`feedback`、`potentials`、`reference_values`、`review_audit_log`、`auth-proofs`）的数据与本地 PostgreSQL 中的 36 张表不互通
- 后续功能开发需同时考虑两套认证状态

---

## 2. 系统现状

### 2.1 技术栈

| 组件 | 技术栈 | 版本 |
|------|--------|------|
| 前端 | Next.js 16 (App Router, Turbopack) | 16.2.10 |
| 后端 API | FastAPI (Python 3.12, uvicorn) | 0.115+ |
| 数据库 | PostgreSQL 16 (Docker) | 16 |
| 缓存/队列 | Redis 7 (Celery broker) | 7-alpine |
| KG/RAG | LightRAG sidecar | 1.5.4 |
| 外部认证 | Supabase Cloud | Free 层 |

### 2.2 资源占用 (Mac Studio 16C/128G)

| 容器 | CPU | 内存 |
|------|-----|------|
| nucpot-prod-api (FastAPI) | 3.85% | 91 MB |
| nucpot-prod-web (Next.js) | 0.00% | 163 MB |
| nucpot-prod-db (PostgreSQL) | 0.00% | 62 MB |
| nucpot-prod-redis | 0.55% | 20 MB |
| nucpot-prod-worker (Celery) | 0.09% | 52 MB |
| nucpot-prod-lightrag | 0.23% | 212 MB |
| **合计** | **~5%** | **~600 MB / 128 GB** |

### 2.3 代码规模

| 维度 | 数量 |
|------|------|
| FastAPI API 端点 | 99 个（13 个有 auth 依赖） |
| Next.js API Routes (Supabase) | 23 个 route.ts |
| Supabase 数据表 | 7 张（profiles, contributions, feedback, potentials, reference_values, review_audit_log, auth-proofs） |
| PostgreSQL 数据表 | 36 张 |
| PostgreSQL 数据量 | 64 MB |

---

## 3. 安全性对比

### 3.1 Token 存储与传输

| 安全维度 | FastAPI JWT (当前) | Supabase Auth |
|---------|-------------------|---------------|
| Token 存储 | `localStorage` (JS 可读) | **HttpOnly Cookie** (JS 不可读) |
| XSS 防护 | 🔴 高风险 — 任何 XSS 可窃取 token | ✅ 低风险 — JS 无法读取 |
| CSRF 防护 | ✅ 不需要 — localStorage 不自动发送 | 🟡 需要 — Cookie 自动发送 |
| Token 过期 | 30 分钟，无 refresh | 1 小时 + **自动 refresh** |

### 3.2 暴力破解防护

| 维度 | FastAPI | Supabase |
|------|---------|---------|
| 登录速率限制 | ❌ **无** — `/auth/login` 无限重试 | ✅ **内置** — IP 级限流 |
| 账户锁定 | ❌ 无 | ✅ 自动锁定 |
| SlowAPI 已安装 | ✅ 是（已安装但未启用） | N/A |
| 密码强度校验 | 仅 `min_length=8` | ✅ 策略可配置 |

### 3.3 数据访问控制

| 维度 | FastAPI | Supabase |
|------|---------|---------|
| 控制模型 | 应用层 (`Depends(get_current_user)`) | 数据库层 (Row Level Security) |
| 安全冗余 | 🔴 忘记加 `Depends` = 未授权访问 | ✅ DB 层强制，即使 API 有 bug |
| 授权覆盖率 | **13/99 端点有 auth**（86 个无保护） | RLS 覆盖所有表 |
| 写操作暴露面 | ~20 个写操作端点无 auth | RLS 保护 |

### 3.4 安全小结

**Supabase Auth 的安全机制整体优于当前 FastAPI 的手动实现**，主要体现在 HttpOnly cookie 存储、内置速率限制、RLS 数据库层授权和 refresh token 机制。

但 FastAPI 方案的安全问题**均为实施缺陷，非架构缺陷**，可通过升级修复。

---

## 4. 并发处理能力对比

### 4.1 请求路径

```
FastAPI 统一:
  用户 → Cloudflare → nginx → FastAPI (本地 Docker)
  认证: JWT decode (~0.1ms) + DB 查询 (~0.5ms)

Supabase:
  用户 → Cloudflare → Next.js API Route → Supabase Cloud (东京/新加坡)
  认证: supabase.auth.getUser(token) 远程网络往返
```

### 4.2 性能指标

| 维度 | FastAPI 统一 | Supabase |
|------|-------------|----------|
| 认证延迟 (已登录) | **~1ms** (JWT decode) | **50-200ms** (远程 getUser) |
| 网络跳数 | 1 (Docker 内网) | **3** (→ 公网 → Supabase → 返回) |
| DB 连接池 | 可调 (默认 5+10=15) | 受 Supabase 约束 |
| Worker 数量 | 当前 1 (可加 `--workers 4`) | Next.js 单进程 |
| 登录瓶颈 | bcrypt ~50ms/次 (CPU 密集) | Supabase 远程处理 |
| 可扩展性 | 水平: 加 worker / 垂直: 调连接池 | 受 Supabase 计划限制 |

### 4.3 估算 QPS

| 配置 | 已认证请求 QPS | 登录 QPS |
|------|---------------|---------|
| FastAPI (当前: 1 worker) | ~200 | ~20 |
| FastAPI (4 workers + Redis 缓存 user) | **~2,000** | ~80 |
| Supabase (远程认证) | ~50-100 | ~50-100 |

### 4.4 硬件利用率

当前 Mac Studio (16 核 / 128 GB) 资源远未饱和：
- CPU 使用率 ~5%
- 内存使用 ~600 MB / 128 GB
- PostgreSQL 数据仅 64 MB
- 单 worker 只用了 1/16 核

---

## 5. 上云兼容性对比

### 5.1 部署架构

```
方案 A (FastAPI 统一):
  Cloudflare → [nginx → Next.js + FastAPI + PostgreSQL + Redis]
  全部在同一服务器，Docker Compose 编排

方案 B (保留 Supabase):
  Cloudflare → [nginx → Next.js]  ← 服务器
                 ↓ (Auth/DB)
              Supabase Cloud ← 外部托管
```

### 5.2 云平台兼容性

| 云平台 | FastAPI 统一 | Supabase |
|--------|-------------|----------|
| 阿里云 / 腾讯云 ECS | ✅ 直接 docker compose up | ⚠️ Supabase 在中国访问需翻墙 (200-500ms) |
| AWS / GCP / Azure | ✅ EC2 / Compute Engine | ✅ Supabase 有 AWS 部署 |
| **国内合规 (等保/信创)** | ✅ **数据完全自有** | ❌ **认证数据出境，不合规** |
| 离线 / 内网部署 | ✅ 完全离线可用 | ❌ 必须访问 Supabase Cloud |
| K8s 迁移 | ✅ Compose → Helm Chart 平滑迁移 | ⚠️ Supabase 自托管需 10+ 容器 |
| Serverless (Cloud Run) | ✅ FastAPI 打包为容器镜像 | ⚠️ 依赖外部 Supabase |
| 当前 Mac Studio | ✅ 零改动 | ✅ 保持现状 |

### 5.3 供应商锁定

| 维度 | FastAPI 统一 | Supabase |
|------|-------------|----------|
| 技术栈 | Python + PG + Redis (标准技术) | Supabase SDK + RLS + Auth API |
| 迁移难度 | ✅ 无锁定 | 🔴 高 — 23 个 API Route + 7 张表深度依赖 |
| 数据导出 | ✅ 标准 SQL | ⚠️ 需从 Supabase 导出 |

---

## 6. 经济性对比

### 6.1 方案 A: FastAPI 统一 (全自管)

| 规模阶段 | 配置 | 月成本 | 说明 |
|---------|------|--------|------|
| 当前 (< 100 用户) | Mac Studio 本地 | **¥0** | 已有硬件，当前状态 |
| 小规模 (100-1K 用户) | 2C4G 阿里云 ECS | **~¥80** | Docker Compose 全栈 |
| 中规模 (1K-10K 用户) | 4C8G ECS + 云数据库 | **~¥300** | API+Web 在 ECS，PG 用 RDS |
| 大规模 (10K+ 用户) | 8C16G × 2 + RDS + SLB | **~¥1,500** | 多副本负载均衡 |

### 6.2 方案 B: 保留 Supabase

| 规模阶段 | 配置 | 月成本 | 说明 |
|---------|------|--------|------|
| 当前 (< 50K MAU) | Supabase Free + Mac Studio | **$0 (~¥0)** | 免费层 |
| 小规模 (50K-100K MAU) | Supabase Pro | **$25 (~¥180)** | 8GB DB + 250GB egress |
| 中规模 (100K+ MAU) | Pro + Compute add-on | **$35-75 (~¥250-540)** | 加 Compute |
| 大规模 (500K+ MAU) | Supabase Team | **$599 (~¥4,300)** | SOC2 + SSO |
| 数据出口超量 | 额外 | **$0.09/GB** | 超 250GB 后 |

### 6.3 隐性成本

| 维度 | FastAPI 统一 | Supabase |
|------|-------------|----------|
| 运维 | 自管 PG 备份/升级/监控 | Supabase 管数据库 |
| 数据出口费 | 无 | $0.09/GB (超量) |
| 停机风险 | 自己控制 | Free 项目 1 周不活跃自动暂停 |
| 双系统维护 (当前) | — | 隐性高: 调试 + 数据不一致 + 双依赖 |
| 供应商锁定 | 无 | 高 |

### 6.4 成本趋势图

```
月成本
¥5,000 ┤
       │                                          ┌── Supabase Team ($599)
¥3,000 ┤                                         /
       │                                        /
¥1,500 ┤                          ┌── FastAPI ─ ─ ─ ─/
       │                         /    (大规模)
¥1,000 ┤                        /
       │                       /
  ¥500 ┤          ┌──────────/──────── Supabase Pro+Compute
       │         /
  ¥300 ┤        / FastAPI (中规模)
       │       /
  ¥100 ┤── FastAPI (小规模)
       │
    ¥0 ┤──┬───────────────┬─────────────── Supabase Free
       └──┴───┴───┴───┴───┴───→ 用户规模
         当前  100   1K    10K   100K
```

---

## 7. 方案综合评分

| 评估维度 | 权重 | FastAPI 统一 (升级安全) | 保留 Supabase |
|---------|------|----------------------|--------------|
| 安全性 (可升级) | 20% | **8/10** (升级后) | 8/10 (开箱即用) |
| 并发性能 | 15% | **9/10** (本地 ~1ms) | 5/10 (远程 ~100ms+) |
| 中国访问延迟 | 15% | **10/10** (< 10ms) | 2/10 (200-500ms) |
| 数据合规 | 15% | **10/10** (数据不出境) | 1/10 (认证数据出境) |
| 经济性 (大规模) | 10% | **9/10** (¥80-1500) | 4/10 ($25-599) |
| 云平台兼容 | 10% | **9/10** (标准容器) | 5/10 (绑定 Supabase) |
| 供应商锁定 | 5% | **10/10** (无锁定) | 3/10 (深度绑定) |
| 初始迁移工作量 | 5% | 4/10 (~5 天) | **8/10** (保持现状) |
| 运维复杂度 | 5% | 7/10 (自管 PG) | **8/10** (Supabase 托管) |
| **加权总分** | 100% | **8.55** | **4.95** |

---

## 8. 推荐方案

### 推荐: 方案 A — 统一到 FastAPI，升级安全至行业最佳实践

**核心理由**:

1. **数据主权**: 核材料数据库涉及敏感数据，认证数据不能放在境外云服务
2. **国内用户**: 目标用户集中在国内，Supabase 200-500ms 延迟不可接受
3. **并发性能**: FastAPI 本地认证 ~1ms vs Supabase 远程 ~100ms+，差 100 倍
4. **成本可控**: 当前 Mac Studio 零成本，上云后 ¥80/月 vs ¥180+/月
5. **架构一致性**: 统一后只有 Python + PG + Redis，消除双系统维护负担

### 实施路径

| Sprint | 范围 | 工时 | 安全收益 |
|--------|------|------|---------|
| **Sprint 1** (立即) | HttpOnly Cookie + 速率限制 + CSRF 防护 | 1 天 | 解决 XSS + 暴力破解 |
| **Sprint 2** (本周) | 写操作授权审计 (20+ endpoint) | 1 天 | 补齐 86 个无 auth 端点 |
| **Sprint 3** (下周) | Supabase 数据迁移 + 统一登录页 | 3 天 | 消除双系统 |

### 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| Supabase 历史数据迁移丢失 | 低 | 中 | 迁移前完整备份 Supabase 数据 |
| Next.js API Route 改写引入 bug | 中 | 低 | 逐个改写 + 测试，保留旧 route 作为 fallback |
| 登录页变更影响现有用户 | 低 | 低 | 旧 Supabase 用户通过密码重置流程迁移 |

---

## 附录 A: 当前 Supabase 依赖清单

| 文件/目录 | 用途 | 迁移动作 |
|----------|------|---------|
| `src/lib/supabase.ts` | Supabase 客户端 | 删除，替换为 api-client |
| `src/components/AuthProvider.tsx` | Supabase session 管理 | 重写为 JWT session provider |
| `src/app/login/page.tsx` | Supabase 登录/注册 | 合并到 `/admin/login` 或新统一登录页 |
| `src/app/profile/page.tsx` | 用户资料 + 密码修改 (Supabase) | 改写到 FastAPI |
| `src/app/upload/UploadForm.tsx` | 势能上传 (Supabase auth) | 改用 FastAPI JWT |
| `src/app/api/auth/*` (8 个 route) | Supabase 认证 API Routes | 迁移到 FastAPI endpoint |
| `src/app/api/admin/*` (7 个 route) | Supabase 管理端 Routes | 迁移到 FastAPI endpoint |
| `src/app/api/feedback/route.ts` | 反馈提交 | 迁移到 FastAPI endpoint |
| `src/app/api/potentials/*` (2 个 route) | 势能提交 | 迁移到 FastAPI endpoint |

## 附录 B: FastAPI 安全升级清单

| # | 升级项 | 当前状态 | 目标状态 | 文件影响 |
|---|--------|---------|---------|---------|
| 1 | Token 存储 | localStorage | HttpOnly Cookie | `auth_endpoints.py`, `api-client.ts` |
| 2 | 速率限制 | SlowAPI 已装未启用 | `@limiter.limit("5/minute")` | `auth_endpoints.py` |
| 3 | 密码强度 | min_length=8 | +数字+字母+符号 | `schemas/auth.py` |
| 4 | Refresh Token | 无 | access + refresh 双 token | `auth_endpoints.py`, `api-client.ts` |
| 5 | CSRF | 不需要 | Double-submit cookie | `middleware.ts` |
| 6 | 写操作 auth | 13/99 | 99/99 (按需) | ~20 个 endpoint 文件 |
| 7 | Redis 用户缓存 | 无 | user_id → User (TTL 30min) | `core/auth.py`, `auth_service.py` |
| 8 | Worker 数量 | 1 | 4 (匹配 CPU 核心数) | `prod-api.Dockerfile` |

---

*报告结束。建议立即启动 Sprint 1 (HttpOnly Cookie + 速率限制 + CSRF)，这是安全 ROI 最高的改进。*
