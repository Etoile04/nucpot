---
name: nfm676-phase3-blueprint
description: Phase 3 CTO architecture blueprint status (visualization, sharing, desensitization)
metadata:
  type: project
---

# NFM-676 Phase 3 Architecture Blueprint

**Status**: `in_review` — awaiting board approval of plan document
**Interaction ID**: `a87fd04a-de1f-4599-809e-79421e9b433a`
**Plan Revision**: `216eb8f9-61fe-4bc3-a9cc-8916fe04710f`

## Key Decisions
- **ADR-R1**: Apache ECharts for visualization (over Recharts/Plotly/D3)
- **ADR-R2**: PostgreSQL RLS + API middleware hybrid for desensitization
- **Correction**: Codebase is Next.js 15 (not Vue/Nuxt as parent plan says)

## 9 Child Issues Planned (not yet created — awaiting approval)
- 3A.1-3A.4: Visualization (browser, charts, graph, dashboard)
- 3B.5-3B.7: Export + Sharing (export API, shared datasets, API keys)
- 3C.8-3C.9: Security (desensitization middleware, RBAC/audit)

## Next Step After Approval
Create 9 child issues via Paperclip API, assigned to CPO. 4 parallel tracks can start immediately.

## Parent: [[nfm-project-overview]]
