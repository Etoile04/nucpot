# 决策日志：nfm-834-frontend-compliance

> 每条决策格式：D{NNN} — {date} — {title}
> 包含：触发问题、考虑方案、选择、理由、失效条件

## D001 — 2026-07-14 — Dashboard 路由组迁移策略
**触发问题：** NFM-826 规范要求 (dashboard) 路由组 + middleware 统一 JWT 校验，当前靠组件级 AuthGuard
**考虑方案：** A) 全面迁移到路由组 B) 只加 middleware 不迁移
**选择：** A — 全面迁移
**理由：** 用户选择完整对齐规范；路由组提供清晰的认证边界和布局共享
**验证状态：** ⏳ 待 Phase 7 验证
**失效条件：** 如果迁移导致大量测试失败且无法快速修复，回退为方案 B

## D002 — 2026-07-14 — 材料详情页内容范围
**触发问题：** /materials/[id] 缺失，需要决定页面内容深度
**考虑方案：** A) 完整详情页 B) 最小详情页
**选择：** A — 完整详情页
**理由：** API 已支持 MaterialDetailResponse，规范 §2.1 要求完整详情页
**验证状态：** ⏳ 待 Phase 7 验证
**失效条件：** MaterialDetailResponse 字段不满足前端展示需求时调整
