# Spec: Auth Unification — FastAPI 认证统一与安全升级

**日期**: 2026-07-14
**决策**: D001-D004 (见 DECISIONS.md)
**依据**: `docs/design/auth-system-selection-report.md`

## 目标

1. 将 Token 存储从 localStorage 升级为 HttpOnly Cookie
2. 启用速率限制、密码强度校验、CSRF 防护
3. 补齐 86 个无 auth 保护端点的授权
4. 迁移 Supabase 的 7 张表 + 23 个 API Route 到 FastAPI
5. 统一登录页，废弃 Supabase SDK

## 验收标准

- [ ] Token 通过 HttpOnly Cookie 传递（不再出现在 localStorage）
- [ ] `/auth/login` 限流 5 次/分钟
- [ ] 密码要求 ≥8 位 + 含数字 + 字母
- [ ] CSRF double-submit cookie 生效
- [ ] 所有写操作端点有 auth 依赖
- [ ] Redis 缓存 user 对象（减少 DB 查询）
- [ ] uvicorn 4 workers
- [ ] Supabase 7 张表数据迁移到本地 PG
- [ ] 23 个 Next.js API Route 改写为 FastAPI endpoint
- [ ] 统一登录页（合并 /admin/login 和 /login）
- [ ] Supabase SDK 从前端移除
- [ ] 现有测试全部通过
