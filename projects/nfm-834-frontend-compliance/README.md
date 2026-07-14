# nfm-834-frontend-compliance

NFM-826 设计规范合规改进 — 补齐缺失路由、组件和部署。

## 基本信息
- **规模**: M 级（7 步流程）
- **预计工期**: 2-3 天
- **技术栈**: Next.js 15+ App Router, TanStack Query, Tailwind CSS, JWT auth
- **仓库**: ~/Projects/nucpot (Etoile04/nucpot)

## 里程碑
1. P0: 材料详情页 + Docker 重建 — ⏳
2. P1: 路由组 + middleware + admin/kg — ⏳
3. P2: 共享组件提取 + 文档更新 — ⏳

## 改进项清单（10 项）
| # | 优先级 | 改进项 | 状态 |
|---|--------|--------|------|
| 1 | P0 | `/materials/[id]` 材料详情页 | ⏳ |
| 2 | P0 | Docker 镜像重建 | ⏳ |
| 3 | P1 | `(dashboard)` 路由组 | ⏳ |
| 4 | P1 | middleware JWT 统一守卫 | ⏳ |
| 5 | P1 | `/admin/kg` 管理页面 | ⏳ |
| 6 | P2 | `ExtractionStatusBadge` 组件 | ⏳ |
| 7 | P2 | `SemanticSearchInput` 组件 | ⏳ |
| 8 | P2 | `NodeDetailSidebar` 独立组件 | ⏳ |
| 9 | P2 | 文档偏差记录（路由命名） | ⏳ |
| 10 | P2 | 文档偏差记录（组件命名） | ⏳ |

## 设计规范
- 原始规范: `~/.paperclip/.../docs/design/NFM-826-frontend-interaction-design.md`
- 核查报告: 本次会话前序分析
