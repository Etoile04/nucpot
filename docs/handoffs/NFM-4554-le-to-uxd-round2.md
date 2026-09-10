# NFM-4554 LE → UXDesigner hand-off (Visual-Truth Gate round 2)

> Status: branch `NFM-4554-g1-f-a-admin-review-queue` commit `0cf67dec6` pushed
> to `origin`. UXD assignee flip done at 2026-09-10T07:07:42Z; LE JWT is now
> outside the write boundary (expected per "PATCH assignee-flip-then-release
> 403" trap). UXD should re-post this handoff as a Paperclip comment on
> `NFM-4554` if it does not surface in the wake payload.

## UXDesigner Visual-Truth Gate verdict on round 1

🔴 **CRITICAL** — Mobile 390×844 only 3/6 columns visible (置信度 / 来源段落 / 状态 off-screen).
🔴 **CRITICAL** — 属性 column shows truncated row UUID (`rec.id.slice(0,8)` → "row-001-") instead of property name.
🟠 **HIGH** — §4.3 红行 missing at table level (only rendered inside the drawer; spec §4.3 says 红行 + 悬停原因).
🟠 **HIGH** — dedupe_key badge missing (spec §4.3 自动合并徽章 "已合并 N 行").

## Fix map

| UX finding | Fix |
|---|---|
| 🔴 属性 column shows truncated UUID | Backend `/api/v1/review/pending` now eagerly JOINs `property_types.name` for `property_measurements` rows; `mapItem` exposes `propertyTypeName`; table renders it in 属性. Falls back to a short id prefix only when backend cannot resolve a name (legacy rows). |
| 🔴 Mobile 390×844 only 3/6 columns | Table wrapped in `<div style="overflow-x:auto">` + `scroll={{ x: 980 }}`; all 6 spec §4.2 columns now reachable via horizontal swipe. |
| 🟠 §4.3 红行 at table level | `rowClassName` applies `.review-row-physically-invalid` when `validity_check.status='fail'`; CSS turns the row light-red with a left red border + hover darkens. Native `<tr title>` carries the validity reason. |
| 🟠 dedupe_key badge | When ≥2 rows share a `dedupe_key`, the table renders a cyan "已合并 N 行" Tag under the property name (spec §4.3 自动合并徽章). |

## Implementation surface

- **Backend** — `apps/api/src/nfm_db/api/v1/review.py`: eager JOIN onto
  `property_types` for `property_measurements`; new `item_data` fields
  `property_type_id`, `property_type_name`, `dedupe_key`, `validity_check`.
  Forward-compat: `validity_check.status='unknown'` until G1-D
  (NFM-4550) ships the column; the 红行 code path is wired but currently
  dormant.
- **Frontend** —
  - `apps/web/src/components/admin/review-queue/ReviewQueueContent.tsx`
    (table column + dedupe counts + scroll wrapper + row class),
  - `apps/web/src/components/admin/review-queue/review-queue.css` (red-row),
  - `apps/web/src/lib/admin/review-queue-api.ts` (extended `ReviewQueueItem`).
- **Tests** —
  - `ReviewQueueContent.test.tsx`: 13 tests (was 6), +7 cover property
    name / legacy fallback / dedupe badge (≥2 rows) / solo dedupe (no
    badge) / red row + tooltip / unknown (no red) / mobile scroll
    container. **All green.**
  - `ReviewDrawer.test.tsx`: fixture updated for new item shape. **All
    green.**
  - `page.test.tsx`: unchanged.

## QA artifacts (regenerated chromium)

| Viewport | File |
|---|---|
| Desktop 1440×900 — queue | `apps/web/qa-artifacts/nfm-4554-desktop-queue.png` |
| Desktop 1440×900 — drawer | `apps/web/qa-artifacts/nfm-4554-desktop-drawer.png` |
| Mobile 390×844 — queue | `apps/web/qa-artifacts/nfm-4554-mobile-queue.png` |
| Mobile 390×844 — drawer | `apps/web/qa-artifacts/nfm-4554-mobile-drawer.png` |

In the new mobile-queue screenshot: `属性` column shows
"thermal conductivity" / "lattice parameter a" with "已合并 2 行" cyan
badges; the density row (validity_check.status='fail') is rendered red
with the red left-border. Drawer shows all 5 §4.3 actions (确认通过 /
需修改 / 标记无效 / 来源存疑 / 跳过).

## Hygiene

- `git push` clean — branch `NFM-4554-g1-f-a-admin-review-queue` ahead
  of `origin/main` by 1 commit.
- `pnpm tsc --noEmit` clean.
- `ruff check apps/api/src/nfm_db/api/v1/review.py` All checks passed.

## Hand-off request

UXD Visual-Truth Gate round 2 — please re-capture the four screenshots
and verify the four fixes above render correctly. Once you sign off,
route to Code Reviewer per the normal pipeline (LE → UXD → CR → RE →
Integration NFM-4556).

premise-verdict: CLEAN
supersedence-verdict: CLEAN
