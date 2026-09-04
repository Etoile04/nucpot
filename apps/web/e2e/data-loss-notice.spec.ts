/**
 * Playwright backstop tests for DataLossNotice (NFM-4146, spec §7).
 *
 * Encodes the §7 anti-regression checklist:
 *   1. variant="full" is banned — no element with that attribute set
 *      can ever appear in the DOM, even when the flag is on.
 *   2. Cohort scope is enforced — a row whose `attribution.status` is
 *      `"intact"` never renders the notice.
 *   3. Property-detail row layout is stable across reloads (no layout
 *      shift). Asserted by comparing bounding-box heights of the lost
 *      row before and after a reload — the inline trigger sits on a
 *      single line and must not change the measurement-row height.
 *      (This does NOT toggle the flag within the test; the mocked
 *      flag-service evaluation keeps it on for the whole run.)
 *   4. The `dataloss_notice_shown` analytics event fires on first
 *      popover open.
 *
 * NFM-4204 — this spec previously failed deterministically in every
 * environment: it asserted row-level selectors (`tr[data-attribution-
 * status]`) that no component emitted, targeted the material DETAIL page
 * (no property table renders there — the table lives on the properties
 * sub-route), and relied on a fixture that was never shipped. It is now
 * mock-based: `setupDataLossMocks` intercepts the properties API and
 * seeds exactly one lost row. NFM-4180: the flag is enabled for local
 * runs by the same mock server, which intercepts
 * `/api/v1/feature-flags/DATA_LOSS_NOTICE/evaluate`; the spec is
 * excluded from the CI live-E2E job
 * (`NFMD_SPEC_PATTERN`) because prod has no lost rows and the flag is
 * off there until the NFM-4177 rollout.
 *
 * NFM-4263 — local-run note: under `fullyParallel` against a single
 * `next dev` instance, saturated workers can starve the flag-gated
 * chip mount past the test wall clock (the failing test varies run to
 * run; it is always "trigger count 0", never a layout assertion). That
 * is dev-server contention, not a product race — serial runs are 100%
 * green. Run this spec as:
 *   PORT=3263 pnpm exec playwright test e2e/data-loss-notice.spec.ts \
 *     --project=chromium --workers=1
 * against a pre-warmed dev server (curl the route once first).
 */

import { expect, test } from "@playwright/test"

import { setupDataLossMocks } from "./fixtures/data-loss-notice-mock-server"
import { MOCK_LOST_MEASUREMENT_ID } from "./fixtures/data-loss-notice-mock-data"

// The property table renders on the properties sub-route, not on the
// material detail page (NFM-4204).
const PROPERTY_DETAIL_URL = "/materials/FeCrAl/properties"

test.describe("DataLossNotice backstop (spec §7)", (): void => {
  // Every test here waits on the flag-gated chip mount; see the
  // NFM-4263 local-run note in the file header for why the budget is
  // tripled rather than the default 30s.
  test.slow()
  test.beforeEach(async ({ page }): Promise<void> => {
    await setupDataLossMocks(page)
  })

  test("variant=full never appears in the DOM", async ({ page }): Promise<void> => {
    await page.goto(PROPERTY_DETAIL_URL)
    // Wait for the fixture rows so the assertion runs against a fully
    // rendered table, not an empty loading state.
    await expect(
      page.locator('tr[data-attribution-status="lost"]'),
    ).toHaveCount(1)
    const fullElements = await page.locator('[data-variant="full"]').count()
    expect(fullElements).toBe(0)
  })

  test("intact rows never render the notice", async ({ page }): Promise<void> => {
    await page.goto(PROPERTY_DETAIL_URL)
    // The fixture seeds exactly one `lost` row; every other row is
    // intact or unadjudicated and must not render the notice. The
    // expect() forms retry — the table renders after an async client
    // fetch, so a raw count() right after goto would race the fetch.
    await expect(
      page.locator('tr[data-attribution-status="lost"]'),
    ).toHaveCount(1)
    await expect(
      page.locator("tr.material-property-row--data-loss"),
    ).toHaveCount(1)
    await expect(
      page.locator('[data-testid="data-loss-notice-trigger"]'),
    ).toHaveCount(1)

    // Cohort equality: rows marked --data-loss are exactly the lost
    // rows (sampled after the retrying expects above synchronized the
    // render).
    const lostRows = await page
      .locator('tr[data-attribution-status="lost"]')
      .count()
    const rowsWithNotice = await page
      .locator("tr.material-property-row--data-loss")
      .count()
    expect(rowsWithNotice).toBe(lostRows)
  })

  test("property-detail row height is stable across reload (no layout shift)", async ({
    page,
  }): Promise<void> => {
    await page.goto(PROPERTY_DETAIL_URL)
    // Diagnostic precondition: the fixture must seed exactly one lost
    // row before we measure anything. Failing here — with a selector in
    // the message — distinguishes "fixture/mock drift or DOM-contract
    // regression" from a height mismatch measured against nothing.
    await expect(
      page.locator('tr[data-attribution-status="lost"]'),
      "fixture must seed exactly one lost row",
    ).toHaveCount(1)
    const row = page.locator('tr[data-attribution-status="lost"]').first()
    await expect(row).toBeVisible()
    const beforeHeight = (await row.boundingBox())?.height ?? 0
    // Reload re-renders the row through the same path (fixture + flag
    // via the webServer env). Wait for the row to be visible again
    // before measuring — a raw boundingBox() right after reload can
    // sample mid-render.
    await page.reload()
    await expect(row).toBeVisible()
    const afterHeight = (await row.boundingBox())?.height ?? 0
    expect(Math.abs(beforeHeight - afterHeight)).toBeLessThan(2)
  })

  test("analytics event dataloss_notice_shown fires on first popover open", async ({
    page,
  }): Promise<void> => {
    const events: Array<{ name: string; props: Record<string, unknown> }> = []
    await page.exposeFunction(
      "__captureDataLossEvent",
      (name: string, props: Record<string, unknown>): void => {
        events.push({ name, props })
      },
    )

    await page.addInitScript((): void => {
      window.addEventListener(
        "paperclip-data-loss-notice",
        ((e: CustomEvent<{ name: string; props: Record<string, unknown> }>): void => {
          window.__captureDataLossEvent(e.detail.name, e.detail.props)
        }) as EventListener,
      )
    })

    await page.goto(PROPERTY_DETAIL_URL)
    const trigger = page.locator('[data-testid="data-loss-notice-trigger"]').first()
    await trigger.click()
    await expect(
      page.locator('[data-testid="data-loss-notice-popover"]'),
    ).toBeVisible()
    expect(
      events.some((e): boolean => e.name === "dataloss_notice_shown"),
    ).toBe(true)
    // The shown event must carry the fixture's lost measurement id so
    // analytics stay correlatable to the row.
    const shown = events.find(
      (e): boolean => e.name === "dataloss_notice_shown",
    )
    expect(shown?.props.measurementId).toBe(MOCK_LOST_MEASUREMENT_ID)
  })
})

// ── NFM-4262 — narrow-viewport source-cell acceptance (D1/D2/D3) ───────
//
// Pixel-truth gate for the ratified NFM-4262 spec §6 AC-2:
//   • 390: no horizontal clip on the trigger or the stacked line; the
//     date reads in full without opening the popover.
//   • 390, popover open: |left gutter − right gutter| ≤ 1px, both ≥ 12.
//   • 767 vs 768 boundary: 来源 column header absent at 767, present
//     at 768.
//   • Single-mount invariant at BOTH regimes: trigger count === cohort
//     row count (the fixture seeds exactly one lost row).
//   • 320 floor: popover never wider than 100vw − 24px; no page-level
//     horizontal scrollbar.
test.describe("NFM-4262 narrow-viewport source-cell (D1/D2/D3)", (): void => {
  // Scope-level: every test asserts on the flag-gated chip, so every
  // test inherits the contention headroom (see file header note).
  test.slow()
  test.beforeEach(async ({ page }): Promise<void> => {
    await setupDataLossMocks(page)
  })

  async function waitForCohort(page: import("@playwright/test").Page): Promise<void> {
    await expect(page.locator('tr[data-attribution-status="lost"]')).toHaveCount(
      1,
      { timeout: 30_000 },
    )
  }

  // The chip mounts only after the (mocked) flag evaluation resolves and
  // re-renders the provider tree. `test.slow()` above only raises the
  // test budget — each expect() still defaults to 5s, which a contended
  // dev server can outlast, so every chip await goes through here with
  // an explicit timeout instead.
  async function waitForTrigger(
    page: import("@playwright/test").Page,
    count = 1,
  ): Promise<void> {
    await expect(
      page.locator('[data-testid="data-loss-notice-trigger"]'),
    ).toHaveCount(count, { timeout: 30_000 })
  }

  test("1440: one trigger per cohort row (column mount) and 来源 header present", async ({
    page,
  }): Promise<void> => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(PROPERTY_DETAIL_URL)
    await waitForCohort(page)
    // Single-mount invariant at ≥768: the column cell owns the chip.
    await waitForTrigger(page)
    // `getByRole("columnheader")` avoids antd's hidden measure cell
    // (`tbody th.ant-table-measure-cell`), which mirrors the column
    // title text and would break strict-mode locators.
    await expect(
      page.getByRole("columnheader", { name: "来源" }),
    ).toBeVisible()
  })

  test("390: stacked line owns the chip; no horizontal clip; date fully readable", async ({
    page,
  }): Promise<void> => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(PROPERTY_DETAIL_URL)
    await waitForCohort(page)
    // Single-mount invariant at <768: antd unmounted the 来源 column,
    // the stacked SourceMetaLine owns the (only) chip.
    await waitForTrigger(page)
    await expect(
      page.locator('[data-testid="source-meta-line"]'),
    ).toHaveCount(5)
    await expect(
      page.locator(
        '[data-testid="source-meta-line"] [data-testid="data-loss-notice-trigger"]',
      ),
    ).toHaveCount(1, { timeout: 30_000 })

    // Pixel assertion (not eyeball): neither the trigger nor the
    // stacked line may overflow its own box.
    const clips = await page.evaluate((): boolean => {
      const trigger = document.querySelector(
        ".data-loss-notice__trigger",
      ) as HTMLElement | null
      const line = document.querySelector(
        '[data-testid="source-meta-line"]',
      ) as HTMLElement | null
      const triggerClips =
        trigger !== null && trigger.scrollWidth > trigger.clientWidth + 1
      const lineClips =
        line !== null && line.scrollWidth > line.clientWidth + 1
      return triggerClips || lineClips
    })
    expect(clips).toBe(false)

    // The date is fully visible without opening the popover: the
    // wrap-below rule puts it on its own line and the ellipsis
    // backstop never engages (no truncation → scrollWidth fits).
    const date = page.locator(".data-loss-notice__label-date")
    await expect(date).toBeVisible()
    await expect(date).toHaveText(/^ · 2026-08-01$/)
    const dateClips = await page.evaluate((): boolean => {
      const el = document.querySelector(
        ".data-loss-notice__label-date",
      ) as HTMLElement | null
      return el !== null && el.scrollWidth > el.clientWidth + 1
    })
    expect(dateClips).toBe(false)
  })

  test("390 popover open: gutters symmetric (≤1px delta, both ≥12px)", async ({
    page,
  }): Promise<void> => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(PROPERTY_DETAIL_URL)
    await waitForCohort(page)
    const trigger = page
      .locator('[data-testid="data-loss-notice-trigger"]')
      .first()
    await expect(trigger).toBeVisible({ timeout: 30_000 })
    await trigger.click()
    const popover = page.locator('[data-testid="data-loss-notice-popover"]')
    await expect(popover).toBeVisible()
    const gutters = await page.evaluate((): {
      left: number
      right: number
    } => {
      const el = document.querySelector(
        '[data-testid="data-loss-notice-popover"]',
      ) as HTMLElement | null
      if (el === null) return { left: -1, right: -1 }
      const rect = el.getBoundingClientRect()
      return { left: rect.left, right: window.innerWidth - rect.right }
    })
    expect(Math.abs(gutters.left - gutters.right)).toBeLessThanOrEqual(1)
    expect(gutters.left).toBeGreaterThanOrEqual(12)
    expect(gutters.right).toBeGreaterThanOrEqual(12)
  })

  test("767→768 boundary: 来源 column header absent then present", async ({
    page,
  }): Promise<void> => {
    await page.setViewportSize({ width: 767, height: 1024 })
    await page.goto(PROPERTY_DETAIL_URL)
    await waitForCohort(page)
    await expect(
      page.getByRole("columnheader", { name: "来源" }),
    ).toHaveCount(0)
    await waitForTrigger(page)

    // Cross the boundary in-place: antd's responsive listener remounts
    // the column (and the stacked chip unmounts via useIsBelowMd). The
    // retrying expect() covers the async matchMedia transition; the
    // count assertions re-check the single-mount invariant on the far
    // side of the boundary.
    await page.setViewportSize({ width: 768, height: 1024 })
    await expect(
      page.getByRole("columnheader", { name: "来源" }),
    ).toHaveCount(1)
    await waitForTrigger(page)
  })

  test("320 floor: popover ≤ 100vw − 24px and no page-level horizontal scrollbar", async ({
    page,
  }): Promise<void> => {
    await page.setViewportSize({ width: 320, height: 844 })
    await page.goto(PROPERTY_DETAIL_URL)
    await waitForCohort(page)
    const trigger = page
      .locator('[data-testid="data-loss-notice-trigger"]')
      .first()
    await expect(trigger).toBeVisible({ timeout: 30_000 })
    await trigger.click()
    await expect(
      page.locator('[data-testid="data-loss-notice-popover"]'),
    ).toBeVisible()
    const widths = await page.evaluate((): {
      popover: number
      pageScrolls: boolean
    } => {
      const el = document.querySelector(
        '[data-testid="data-loss-notice-popover"]',
      ) as HTMLElement | null
      const doc = document.documentElement
      return {
        popover: el?.offsetWidth ?? 0,
        pageScrolls: doc.scrollWidth > doc.clientWidth + 1,
      }
    })
    expect(widths.popover).toBeLessThanOrEqual(320 - 24)
    expect(widths.pageScrolls).toBe(false)
  })
})
