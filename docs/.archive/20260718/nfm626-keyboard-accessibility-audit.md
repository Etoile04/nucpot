# NFM-626: V4 Keyboard Accessibility Audit Report

**Auditor:** UXDesigner  
**Date:** 2026-07-04  
**Scope:** Code-level audit of 4 V4 extraction pages + shared layout  
**Methodology:** Static code analysis against WCAG 2.1 AA keyboard / screen reader criteria  
**Note:** Dynamic-content testing (API responses, status polling live updates) deferred until NFM-624 ships.

---

## Executive Summary

| Severity | Count |
|----------|-------|
| Critical | 0 |
| Major | 7 |
| Minor | 12 |

No critical (blocker) issues found. The app uses Ant Design components which provide baseline keyboard accessibility (radio groups, selects, modals, menus). The **7 Major findings** all concern missing ARIA live regions for dynamic content, keyboard shortcut conflicts on the validation page, and missing semantic landmarks in the 3-panel browse layout.

---

## Page 1: Submit (`/admin/v4-extraction/submit`)

### M1 — Form validation errors not announced to screen readers [Major]

**File:** `components/v4-extraction/submit-form.tsx:81-105`  
**Issue:** Ant Design `Form.Item` validation errors appear visually but lack `aria-invalid` and `aria-describedby` associations. When a required field fails validation, screen readers are not notified.  
**WCAG:** 3.3.1 Error Identification, 4.1.3 Status Messages  
**Remediation:**  
```tsx
<Form.Item
  label="来源类型 / Source Type"
  name="source_type"
  rules={[{ required: true, message: "请选择来源类型" }]}
  validateStatus={...} // Antd tracks this internally
  help={<span id="source-type-error" role="alert">...</span>}
>
  <Radio.Group
    aria-describedby="source-type-error"
    aria-invalid={!!errors.source_type}
    optionType="button"
    buttonStyle="solid"
  >
```
Apply the same pattern to `source_reference` (line 95) and all required fields.

### m1 — Antd `message` API not screen-reader-visible [Minor]

**File:** `submit-form.tsx:47-61`  
**Issue:** `message.success()` / `message.error()` uses toast notifications without ARIA live regions. Success/error messages after form submission are invisible to screen readers.  
**WCAG:** 4.1.3 Status Messages  
**Remediation:** Wrap the `{contextHolder}` in an `aria-live="polite"` container, or add a visually-hidden live region:
```tsx
<div aria-live="polite" aria-atomic="true" className="sr-only">
  {/* Antd message context rendered here */}
  {contextHolder}
</div>
```

### ✓ Submit page positive findings
- `htmlType="submit"` button enables Enter-key submission from any form field — correct
- `Radio.Group` with `optionType="button"` has proper `role="radio"` and arrow-key navigation via Antd
- `Select` with `mode="tags"` is keyboard accessible via built-in combobox
- Tab order follows visual top-to-bottom form layout

---

## Page 2: Browse (`/admin/v4-extraction/browse`)

### M2 — Table rows not keyboard-activatable [Major]

**File:** `app/admin/v4-extraction/browse/page.tsx:522-525`  
**Issue:** `onRow={{ onClick: () => handleRowClick(record) }}` opens the Drawer on click, but there is no keyboard equivalent. Keyboard-only users cannot open the property detail drawer by pressing Enter/Space on a row. The eye icon button (line 291-300) IS keyboard accessible, but it's a small target and not obvious.  
**WCAG:** 2.1.1 Keyboard, 2.5.7 Dragging Movements (analogous)  
**Remediation:**  
```tsx
onRow={(record) => ({
  onClick: () => handleRowClick(record),
  onKeyDown: (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault()
      handleRowClick(record)
    }
  },
  tabIndex: 0,
  role: "button",
  "aria-label": `View details for ${record.property}: ${record.value} ${record.unit ?? ""}`,
  style: { cursor: "pointer" },
})}
```

### M3 — 3-panel layout lacks semantic landmarks [Major]

**File:** `app/admin/v4-extraction/browse/page.tsx:418-594`  
**Issue:** The left nav (material systems), center content (table), and right panel (visualization) are all plain `<div>` elements with no landmark roles. Screen reader users cannot skip between sections.  
**WCAG:** 1.3.1 Info and Relationships, 2.4.1 Bypass Blocks  
**Remediation:**
```tsx
// Left panel (line 420)
<nav aria-label="Material Systems Navigation" style={{...}}>

// Center panel (line 458)
<main role="main" style={{...}}>

// Right panel (line 557)
<aside aria-label="Data Visualization" style={{...}}>
```

### m2 — Drawer focus management [Minor]

**File:** `browse/page.tsx:585-593`  
**Issue:** Antd's `Drawer` handles Escape-to-close and focus trap correctly. No issue — documented as positive. ✓

### m3 — Visualization panel toggle has no `aria-expanded` [Minor]

**File:** `browse/page.tsx:484-491`  
**Issue:** The "收起图表 / 展开图表" toggle button doesn't communicate the expanded/collapsed state to screen readers.  
**Remediation:** Add `aria-expanded={!vizCollapsed}` and `aria-controls="viz-panel"` to the button, and `id="viz-panel"` on the visualization container.

### m4 — Filter bar Slider lacks `aria-label` [Minor]

**File:** `components/v4-extraction/browse-filter-bar.tsx:130-138`  
**Issue:** The temperature range `Slider` has no `aria-label` or `aria-valuetext`. Screen readers hear a generic "slider" with no context.  
**Remediation:**
```tsx
<Slider
  range
  aria-label="Temperature range in Kelvin"
  aria-valuetext={`${tempMin}K to ${tempMax}K`}
  // ...existing props
/>
```

### ✓ Browse page positive findings
- `MaterialSystemNav` uses Antd `Menu` with built-in arrow-key navigation
- `Search` input in nav has `allowClear` and is keyboard accessible
- Antd `Table` has built-in keyboard pagination (Tab to pager, Enter to navigate)
- `Drawer` Escape-to-close works automatically

---

## Page 3: Status (`/admin/v4-extraction/status/[jobId]`)

### M4 — Status updates not announced to screen readers [Major]

**File:** `app/admin/v4-extraction/status/[jobId]/page.tsx:52-59, 124-128`  
**Issue:** The page polls for status updates via `refetchInterval`, but when the status Tag changes (e.g., "运行中" → "完成"), there is no `aria-live` region to announce the change. Keyboard users waiting on a long-running job won't know it completed without re-reading the entire page.  
**WCAG:** 4.1.3 Status Messages  
**Remediation:**  
```tsx
<div aria-live="polite" aria-atomic="true">
  <Tag color={JOB_STATUS_COLORS[status.status]}>
    {JOB_STATUS_LABELS[status.status]}
  </Tag>
</div>
```

### M5 — Live counters not announced on update [Major]

**File:** `components/v4-extraction/live-counters.tsx:26-65`  
**Issue:** `extractedCount`, `stagedCount`, and `rejectedCount` update during polling but have no `aria-live` regions. Screen readers won't hear count changes.  
**WCAG:** 4.1.3 Status Messages  
**Remediation:** Wrap each `Statistic` in an `aria-live="polite"` container:
```tsx
<div aria-live="polite" aria-atomic="true" aria-label="Extracted count">
  <Statistic title="已提取 / Extracted" value={extractedCount} ... />
</div>
```

### m5 — Loading spinner lacks `aria-label` [Minor]

**File:** `status/[jobId]/page.tsx:85`  
**Issue:** `<Spin spinning={isLoading}>` wrapping the content has no `aria-label`. Screen readers won't be told content is loading.  
**Remediation:** Add `aria-label="Loading extraction status"` or `aria-busy="true"` to the Spin wrapper.

### ✓ Status page positive findings
- Antd `Alert` for errors has `role="alert"` by default — screen readers will hear error messages ✓
- "View Results" button is a standard keyboard-accessible Button ✓
- "Submit New Job" button is a standard keyboard-accessible Button ✓

---

## Page 4: Validate (`/admin/v4-extraction/validate/[validationId]`)

### M6 — Global keyboard shortcuts conflict with Antd widgets [Major]

**File:** `app/admin/v4-extraction/validate/[validationId]/page.tsx:332-364`  
**Issue:** The `useEffect` keyboard handler intercepts single-key presses (A, R, M, S) globally. The guard only excludes `HTMLInputElement` and `HTMLTextAreaElement`, but does NOT exclude:
- Antd `Select` dropdown (open `<ul role="listbox">`)
- Antd `Radio.Group` (active radio `<span role="radio">`)
- ContentEditable elements

Pressing 'A' while a Select dropdown is open will fire the approve action instead of selecting the "A" option.  
**WCAG:** 2.1.1 Keyboard (no unintended key activation)  
**Remediation:**
```tsx
useEffect(() => {
  const handler = (e: KeyboardEvent) => {
    // Exclude input/textarea
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
    // Exclude Antd open dropdowns, radios, and role-based widgets
    const target = e.target as HTMLElement
    if (target.closest('[role="listbox"]') ||
        target.closest('[role="option"]') ||
        target.closest('[role="combobox"]') ||
        target.closest('[role="dialog"]') ||
        target.closest('.ant-select-dropdown') ||
        target.closest('.ant-modal')) return
    // Exclude modifier combos (Ctrl+A, Cmd+A)
    if (e.metaKey || e.ctrlKey || e.altKey) return
    // ...existing switch
  }
```

### M7 — Emoji icons in action buttons are ambiguous to screen readers [Major]

**File:** `components/v4-extraction/validation-card.tsx:237-261`  
**Issue:** Action buttons use raw emoji (`✓`, `✕`, `✏️`, `⏭`) as `<span>` icon content. Screen readers announce these as Unicode character names which are inconsistent across platforms ("check mark", "multiplication X", "memo", "fast-forward button").  
**WCAG:** 1.1.1 Non-text Content (text alternative needed)  
**Remediation:** Add `aria-hidden="true"` to the emoji spans:
```tsx
<Button onClick={onApprove}>
  <span aria-hidden="true">✓</span>
  批准 (A)
</Button>
```
Or replace emojis with Ant Design icons: `<CheckOutlined />`, `<CloseOutlined />`, `<EditOutlined />`, `<ForwardOutlined />`.

### m6 — Inline edit form labels lack `htmlFor`/`id` association [Minor]

**File:** `components/v4-extraction/inline-edit-form.tsx:60, 70, 80, 90, 103`  
**Issue:** `<label>` elements without `htmlFor` matching input `id`s. Clicking a label won't focus its input field.  
**Remediation:** Add matching `id` to each `Input`/`Select`/`Radio.Group` and `htmlFor` to each `<label>`.

### m7 — Reject Modal keyboard accessibility [Minor]

**File:** `validate/[validationId]/page.tsx:526-564`  
**Issue:** None — Antd `Modal` handles Escape-to-close, focus trap, and `aria-modal` correctly. The quick-select buttons and TextArea are standard keyboard-accessible controls. ✓ (Positive finding)

### m8 — Keyboard shortcuts overlay uses `&nbsp;` spacing [Minor]

**File:** `components/v4-extraction/keyboard-shortcuts-overlay.tsx:38-39`  
**Issue:** `&nbsp;` entities create extra spacing that some screen readers announce as blank spaces.  
**Remediation:** Use CSS spacing (`gap`, `margin`) instead of `&nbsp;` entities.

### m9 — Auto-approve and export messages not screen-reader-visible [Minor]

**File:** `validate/[validationId]/page.tsx:294, 327`  
**Issue:** Same as m1 — `message.success()` not announced. See m1 remediation.

### ✓ Validate page positive findings
- Well-designed keyboard shortcut system (A/R/M/S/arrows) — once the conflict is fixed, this is excellent ✓
- Escape key exits edit mode (line 359) — correct ✓
- Enter key approves (line 338) — correct ✓
- Modal focus management handled by Antd ✓

---

## Cross-Cutting / Shared

### M-X1 — No skip navigation link [Major]

**File:** `app/admin/v4-extraction/layout.tsx`  
**Issue:** No "Skip to main content" link. On every page, keyboard users must Tab through the entire sidebar menu (~5 items) before reaching the main content area.  
**WCAG:** 2.4.1 Bypass Blocks  
**Remediation:** Add a visually-hidden skip link as the first focusable element:
```tsx
<a
  href="#main-content"
  className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[9999] focus:bg-white focus:p-2 focus:shadow"
  style={{ position: 'absolute', left: '-9999px' }}
>
  跳到主要内容 / Skip to main content
</a>
```
And add `id="main-content"` to the `<Content>` element.

### m10 — Sidebar header not a semantic heading [Minor]

**File:** `layout.tsx:97-108`  
**Issue:** The "V4 提取系统" header is a plain `<div>`, not an `<h1>` or `<h2>`. Screen readers cannot navigate to it as a heading landmark.  
**Remediation:** Replace with `<h2 style={...}>V4 提取系统</h2>`.

### m11 — Sidebar menu lacks `aria-label` [Minor]

**File:** `layout.tsx:109-114`  
**Issue:** The navigation `Menu` has no `aria-label`.  
**Remediation:** Add `aria-label="V4 Extraction navigation"` to the `<Menu>`.

### m12 — Validation badge link lacks `aria-label` [Minor]

**File:** `layout.tsx:66-79`  
**Issue:** The Badge count on "人工审核 / Validate" is visual-only. Screen readers don't know the count exists unless it has an `aria-label`.  
**Remediation:**  
```tsx
<span aria-label={`人工审核, ${pendingReviewCount} items pending`}>
  <Badge count={pendingReviewCount} size="small" />
</span>
```

---

## Priority Remediation Order

| Priority | Finding | Effort |
|----------|---------|--------|
| 1 | M6: Keyboard shortcut conflict with Antd widgets | Low (guard clause) |
| 2 | M2: Table rows not keyboard-activatable | Low (add onKeyDown) |
| 3 | M4+M5: Status/counters missing aria-live | Low (wrap in divs) |
| 4 | M1: Form errors not announced | Medium (antd integration) |
| 5 | M-X1: Skip navigation link | Low |
| 6 | M3: Landmark roles on browse panels | Low |
| 7 | M7: Emoji icons text alternatives | Low |

All Major findings are low-to-medium effort. The highest impact fix is M6 (keyboard shortcut conflict) because it affects the core validation workflow for keyboard users.

---

## Re-verification Needed After NFM-624

The following findings require a running API to fully verify:
- M4: Status update announcement timing with real polling data
- M5: Live counter updates during active extraction
- M1: Form submission success/error messages with real API calls
- m1, m9: `message.success()` announcements with real API responses

---

## Files Audited

| File | Lines |
|------|-------|
| `app/admin/v4-extraction/layout.tsx` | 130 |
| `app/admin/v4-extraction/submit/page.tsx` | 30 |
| `components/v4-extraction/submit-form.tsx` | 179 |
| `components/v4-extraction/recent-jobs-table.tsx` | 24 |
| `app/admin/v4-extraction/browse/page.tsx` | 597 |
| `components/v4-extraction/material-system-nav.tsx` | 109 |
| `components/v4-extraction/browse-filter-bar.tsx` | 170 |
| `components/v4-extraction/property-detail-drawer.tsx` | 166 |
| `app/admin/v4-extraction/status/[jobId]/page.tsx` | 191 |
| `components/v4-extraction/extraction-steps.tsx` | 90 |
| `components/v4-extraction/live-counters.tsx` | 66 |
| `app/admin/v4-extraction/validate/page.tsx` | 47 |
| `app/admin/v4-extraction/validate/[validationId]/page.tsx` | 571 |
| `components/v4-extraction/validation-card.tsx` | 266 |
| `components/v4-extraction/validation-progress.tsx` | 70 |
| `components/v4-extraction/inline-edit-form.tsx` | 132 |
| `components/v4-extraction/keyboard-shortcuts-overlay.tsx` | 44 |
