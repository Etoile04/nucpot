# Auth Unification — 认证系统统一

统一两套认证系统（FastAPI JWT + Supabase Auth）到 FastAPI，并升级安全至行业最佳实践。

## 基本信息
- **规模**: L 级（12 步流程）
- **预计工期**: 5 天 (38h)
- **技术栈**: FastAPI, PostgreSQL, Redis, Next.js, Docker
- **仓库**: ~/Projects/nucpot (Etoile04/nucpot)
- **选型报告**: `docs/design/auth-system-selection-report.md`

## 里程碑
1. Sprint 1: 安全升级 (Cookie + 限流 + CSRF + 密码策略) — 1 天 — ⏳
2. Sprint 2: 授权审计 (20+ 写操作 endpoint + Redis 缓存 + Workers) — 1 天 — ⏳
3. Sprint 3: Supabase 迁移 + 统一登录页 — 3 天 — ⏳

## 决策记录
- D001: 统一到 FastAPI（废弃 Supabase Auth）
- D002: localStorage → HttpOnly Cookie
- D003: 启用 SlowAPI 速率限制
- D004: 数据先行迁移策略

## 前置分析
- 选型报告: 加权总分 FastAPI 8.55 vs Supabase 4.95
- 安全差距: XSS 暴露面、无限流、86/99 端点无 auth、无 refresh token
- 并发差距: FastAPI ~1ms vs Supabase ~100ms+ 认证延迟
