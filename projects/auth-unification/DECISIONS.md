# 决策日志：auth-unification

> 每条决策格式：D{NNN} — {date} — {title}

## D001 — 2026-07-14 — 认证系统统一方向
**触发问题：** 两套认证系统导致信息不互通和权限配置困难
**选择：** 统一到 FastAPI（废弃 Supabase Auth）
**理由：** 数据主权、国内访问延迟、并发性能、零供应商锁定
**失效条件：** 如果 FastAPI 安全升级无法达到 Supabase 等效水平

## D002 — 2026-07-14 — Token 存储升级
**选择：** localStorage → HttpOnly Cookie
**理由：** XSS 防护 — JS 不可读 cookie
**失效条件：** 如果 SSR 场景下 cookie 传递有不可解决的兼容性问题

## D003 — 2026-07-14 — 速率限制方案
**选择：** 启用已安装的 SlowAPI 中间件
**理由：** 已在 pyproject.toml 和 middleware/ 中，仅需激活
**失效条件：** 无

## D004 — 2026-07-14 — Supabase 数据迁移策略
**选择：** 导出 → 本地 PG 建表 → 数据导入 → 逐步改写 route.ts → 废弃 Supabase
**理由：** 数据先到位，再改代码，降低风险
**失效条件：** 如果 Supabase 数据结构与 FastAPI 模型不兼容，需设计映射层
