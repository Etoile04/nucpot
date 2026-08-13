# Spec: NFM-834 Frontend Compliance

**日期**: 2026-07-14
**规范依据**: NFM-826 前端网站交互设计
**决策**: D001 (路由组迁移) + D002 (完整详情页)

## 目标

补齐 NFM-826 规范中缺失的前端路由、组件和部署，使生产环境完全符合设计规范。

## 改进范围

### Batch 1 — P0 功能缺口

#### 1.1 `/materials/[id]` 材料详情页
- **API**: `GET /api/v1/materials/{material_id}` → `MaterialDetailResponse`
  - 字段: id, name, formula, crystal_structure, description, is_active, created_at, updated_at
  - 关联: aliases[] (alias_name, alias_type, source), composition[] (element, fraction)
- **页面**: Server Component，展示材料基本信息 + 别名 + 成分 + 导航链接到 /graph 和 /properties
- **路由**: `apps/web/src/app/materials/[id]/page.tsx`

#### 1.2 Docker 镜像重建
- 重建 nucpot-prod-web 镜像以包含最新 main 代码（20+ commits 落后）
- 命令: `docker compose -f docker-compose.prod.yml build web && up -d web`

### Batch 2 — P1 安全/结构

#### 2.1 `(dashboard)` 路由组迁移
- 创建 `apps/web/src/app/(dashboard)/` 目录
- 移入: rag/chat, review/kg, review/conflicts, review/layout.tsx
- 创建 `apps/web/src/app/(dashboard)/layout.tsx` 统一布局

#### 2.2 middleware.ts JWT 守卫
- 创建 `apps/web/src/middleware.ts`
- matcher: `/rag/*`, `/review/*`, `/extraction/*`, `/admin/*`
- 校验 localStorage JWT → 重定向到 /admin/login?redirect=
- 注意: Next.js middleware 在 Edge Runtime，不能用 Node.js API

#### 2.3 `/admin/kg` 管理页面
- 创建 `apps/web/src/app/admin/kg/page.tsx`
- 展示: KG 节点统计 + 最近审核队列 + 快捷链接到 /review/kg 和 /kg/search

### Batch 3 — P2 组件提取

#### 3.1 ExtractionStatusBadge
- 提取到 `apps/web/src/components/extraction/ExtractionStatusBadge.tsx`
- 支持: pending/running/completed/failed 状态

#### 3.2 SemanticSearchInput
- 提取到 `apps/web/src/components/search/SemanticSearchInput.tsx`
- 防抖 300ms + 关键词/语义模式切换

#### 3.3 文档偏差记录
- 更新 NFM-826 设计文档（或创建 addendum）记录:
  - `/kg/explore` → `/kg/search` (语义更清晰)
  - `/extraction/*` → `/admin/v4-extraction/*` (admin 归属)
  - `CitationTooltip` → `CitationCard` (功能等价)

## 验收标准
- [ ] `/materials/[id]` 返回 200 + 展示材料详情
- [ ] Docker 容器包含最新代码
- [ ] `(dashboard)` 路由组存在且认证页面在内
- [ ] middleware.ts 校验 JWT
- [ ] `/admin/kg` 返回 200
- [ ] 新提取的组件可独立 import
- [ ] 现有测试全部通过
- [ ] 新代码有对应测试
