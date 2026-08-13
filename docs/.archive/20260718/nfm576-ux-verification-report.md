# NFM-576: E2E UX Verification Report — V4 Extraction Flow

**Date:** 2026-06-29
**Verifier:** UXDesigner (agent)
**Environment:** Production — https://nucpot.dpdns.org
**Browser:** Chromium (Chrome DevTools Protocol)
**Viewports tested:** 1440×900 (desktop), 375×844 (mobile)

---

## Executive Summary

**Visual QA: PARTIAL PASS** — All three v4 extraction pages render correctly with proper bilingual text, layout hierarchy, and responsive behavior. However, **all client-side API calls fail** because `NEXT_PUBLIC_API_URL` is not configured in the production Vercel deployment, causing every `fetch()` to target `http://localhost:8000` (blocked by Chrome Private Network Access from a public origin).

**Verdict: BLOCKED on full E2E flow test.** Steps 2–5 (Status, Results, Browse data, Validate action) cannot be verified until the API connectivity is fixed.

---

## Test Results by Acceptance Criterion

### 1. Submit Form — ✅ RENDER OK / ❌ SUBMIT FAILS

| Check | Result | Notes |
|-------|--------|-------|
| Page loads | ✅ PASS | Clean render, no console errors on initial load |
| Form fields present | ✅ PASS | Source Type (DOI/URL/文件路径/内部ID), Source Reference, Element Systems, Cache Level, Min Confidence, Priority |
| Bilingual labels | ✅ PASS | All labels follow `中文 / English` format consistently |
| Character counter | ✅ PASS | Shows "29 / 500" after filling DOI |
| Empty state | ✅ PASS | "暂无提交记录 / No recent submissions" with placeholder image |
| Submit action | ❌ FAIL | "任务提交失败: Failed to fetch" — API call to `localhost:8000` blocked by CORS/loopback policy |
| Console errors after submit | ❌ FAIL | `ERR_FAILED` on `POST http://localhost:8000/api/v4/extraction/submit` |

**Screenshots:**
- Desktop: `nfm576-submit-desktop.png`
- Mobile (375px): `nfm576-submit-mobile.png`
- Filled form: `nfm576-submit-filled.png`

### 2. Job Status Polling — ⚠️ CANNOT TEST

No job ID available (submission failed). The status page route exists at `/admin/v4-extraction/status/[jobId]` but requires a valid job ID.

### 3. Results Page — ⚠️ CANNOT TEST

No completed job to view. The results page route exists at `/admin/v4-extraction/result/[jobId]`.

### 4. Browse Page — ✅ RENDER OK / ⚠️ DATA UNAVAILABLE

| Check | Result | Notes |
|-------|--------|-------|
| Page loads | ✅ PASS | Clean render, no JS errors |
| Material systems sidebar | ⚠️ STUCK | Shows "加载中..." (loading) — API call to `localhost:8000/api/v4/material-systems` fails silently |
| Main content area | ✅ PASS | Placeholder: "请从左侧选择材料体系开始浏览" with bilingual subtitle |
| Chart toggle button | ✅ PASS | "bar-chart 收起图表" present |
| Bilingual labels | ✅ PASS | Consistent `中文 / English` throughout |

**Screenshots:**
- Desktop: `nfm576-browse-desktop.png`
- Mobile (375px): `nfm576-browse-mobile.png`

### 5. Validate Page — ✅ RENDER OK / ⚠️ ACTION UNTESTABLE

| Check | Result | Notes |
|-------|--------|-------|
| Page loads | ✅ PASS | Clean render, no JS errors |
| Empty state | ✅ PASS | "暂无待审核项目 / No pending review items" with placeholder image |
| Validation workflow | ⚠️ CANNOT TEST | No data to trigger validation |

**Screenshots:**
- Desktop: `nfm576-validate-desktop.png`
- Mobile (375px): `nfm576-validate-mobile.png`

### 6. Bilingual Content — ✅ PASS

All pages render Chinese/English bilingual labels consistently:
- Navigation: "浏览", "本体", "高级检索", "对比", "反馈", "关于", "博客", "登录"
- Sidebar: "V4 提取系统", "提交任务 / Submit", "数据浏览 / Browse", "人工审核 / Validate"
- Form fields: "来源类型 / Source Type", "来源标识 / Source Reference", etc.
- No garbled text or encoding issues detected.

### 7. Responsive Layout (375px mobile) — ✅ PASS

| Page | Desktop (1440px) | Mobile (375px) | Issues |
|------|-------------------|-----------------|--------|
| Submit | ✅ Clean form layout | ✅ Form stacks vertically | No overlap, no overflow |
| Browse | ✅ Sidebar + content | ✅ Sidebar collapses/stacks | Material list still visible |
| Validate | ✅ Centered empty state | ✅ Centered empty state | No issues |

**Note:** No overlapping elements, no horizontal overflow, and no broken layouts detected at 375px width.

---

## Root Cause Analysis

**All client-side API calls fail** because `NEXT_PUBLIC_API_URL` is not set in the production Vercel environment.

### Evidence
```
Console: Access to fetch at 'http://localhost:8000/api/v4/extraction/submit'
         from origin 'https://nucpot.dpdns.org' has been blocked by CORS policy:
         Permission was denied for this request to access the `loopback` address space.
```

### Code path
```typescript
// apps/web/src/lib/v4-extraction/api.ts:23-24
const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
```

The `.env.production.example` correctly documents `NEXT_PUBLIC_API_URL=https://nucpot.dpdns.org`, but this variable is **not set** in the actual Vercel production deployment.

### Affected API modules (all client-side fetch)
| Module | File | Fallback URL |
|--------|------|-------------|
| V4 Extraction | `lib/v4-extraction/api.ts` | `localhost:8000` |
| Reference Data | `lib/admin/reference-data-api.ts` | `localhost:8000` |
| MD Verification | `lib/md-verification-api.ts` | `localhost:8000` |
| Reference Gaps | `lib/reference-gaps/api.ts` | `localhost:8000` |
| Ontology RecordRef | `components/ontology/OntologyRecordRef.tsx` | `localhost:8000` |
| Validate page inline | `app/admin/v4-extraction/validate/page.tsx` | `localhost:8000` |

---

## Recommendations

### P0: Fix Production Env Configuration
Set `NEXT_PUBLIC_API_URL` in the Vercel production deployment environment variables. The correct value should point to the actual backend API endpoint (not the frontend domain).

**Note:** The `.env.production.example` shows `NEXT_PUBLIC_API_URL=https://nucpot.dpdns.org`, but this is the frontend domain. The backend API likely needs a different URL or a reverse proxy path (e.g., `https://nucpot.dpdns.org/api` if proxied through the same domain).

### P1: Verify Backend API Is Publicly Reachable
After setting the env var, confirm the backend API is accessible from the internet (not just localhost). The backend may need its own public URL or must be reverse-proxied through the frontend domain.

### P2: Re-run E2E Verification After Fix
Once API connectivity is restored, re-verify:
- Job submission → status polling → results display
- Browse page material systems loading
- Validate workflow triggering

---

## Accessibility Notes

- ✅ Semantic navigation (`<nav>`, `<main>`, `<footer>`)
- ✅ Form inputs have visible labels
- ✅ Radio groups have accessible names
- ✅ Empty states use descriptive alt text
- ⚠️ Did not test keyboard navigation (Tab/Enter flow) — deferred to post-API-fix verification
