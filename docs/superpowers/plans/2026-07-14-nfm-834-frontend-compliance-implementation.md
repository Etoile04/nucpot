# Plan: NFM-834 Frontend Compliance Implementation

**日期**: 2026-07-14
**规模**: M 级
**分支策略**: `feat/nfm-834-frontend-compliance` off main

## Task Graph

### Batch 1 — P0 (并行)

- [ ] T1: 创建 `/materials/[id]` 详情页 — 30 min
  - files: `apps/web/src/app/materials/[id]/page.tsx`, `apps/web/src/app/materials/[id]/MaterialDetailContent.tsx`
  - impl: Server Component, fetch `GET /api/v1/materials/{id}`, 展示 name/formula/crystal_structure/description + aliases table + composition table + 导航链接
  - test: 手动验证 `curl http://localhost:3000/materials/{uuid}` 返回 200

- [ ] T2: Docker 镜像重建 — 15 min (与 T1 并行)
  - files: 无代码变更
  - impl: `docker compose -f docker-compose.prod.yml build web && docker compose -f docker-compose.prod.yml up -d web`
  - test: 验证容器包含 kg/nodes 页面

### Batch 2 — P1 (T1 完成后)

- [ ] T3: 创建 `(dashboard)` 路由组 — 45 min
  - files: `apps/web/src/app/(dashboard)/layout.tsx`, 移动 rag/chat, review/* 到 (dashboard)/
  - impl: 创建路由组目录 + layout.tsx 共享 DashboardHeader, 移动认证页面
  - test: 验证 /rag/chat, /review/kg 路由仍可访问
  - deps: [T1]

- [ ] T4: middleware.ts JWT 守卫 — 30 min
  - files: `apps/web/src/middleware.ts`
  - impl: Edge Runtime middleware, matcher 匹配认证路径, 检查 JWT cookie/header
  - test: 未认证访问 /review/kg → 重定向 /admin/login
  - deps: [T3]

- [ ] T5: `/admin/kg` 管理页面 — 20 min
  - files: `apps/web/src/app/admin/kg/page.tsx`
  - impl: KG 节点统计 + 审核队列快捷链接
  - test: `curl http://localhost:3000/admin/kg` 返回 200
  - deps: []

### Batch 3 — P2 (T3 完成后, 与 T4/T5 并行)

- [ ] T6: ExtractionStatusBadge 组件 — 15 min
  - files: `apps/web/src/components/extraction/ExtractionStatusBadge.tsx`
  - impl: 提取状态展示组件, 支持 4 种状态
  - deps: []

- [ ] T7: SemanticSearchInput 组件 — 20 min
  - files: `apps/web/src/components/search/SemanticSearchInput.tsx`
  - impl: 防抖输入 + 模式切换
  - deps: []

- [ ] T8: 文档偏差记录 — 10 min
  - files: `docs/design/NFM-826-frontend-addendum.md`
  - impl: 记录路由/组件命名偏差和理由
  - deps: []

## Subagent 派发策略

| Task | 派发方式 | 理由 |
|------|---------|------|
| T1 (详情页) | delegate_task | 需要查 API schema + 写组件 |
| T2 (Docker) | terminal 直接执行 | 纯命令行 |
| T3 (路由组) | delegate_task | 需要移动文件 + 创建 layout |
| T4 (middleware) | delegate_task | 需要理解 Next.js Edge Runtime |
| T5 (admin/kg) | delegate_task | 简单页面 |
| T6+T7+T8 | delegate_task 批量 | 独立小任务 |

## 并行计划

```
Wave 1: T1 (详情页) + T2 (Docker)         ← 并行
Wave 2: T3 (路由组) + T5 (admin/kg)        ← T3 完成后 T4
Wave 3: T4 (middleware) + T6 + T7 + T8     ← 并行
```

## Replan Triggers
- 路由组迁移导致测试失败 > 3 个 → 回退为方案 B (只加 middleware)
- MaterialDetailResponse 字段不足 → 降级为最小详情页
- Docker 构建失败 → 检查 docker-compose.prod.yml 配置
