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
 */

import { expect, test } from "@playwright/test"

import { setupDataLossMocks } from "./fixtures/data-loss-notice-mock-server"
import { MOCK_LOST_MEASUREMENT_ID } from "./fixtures/data-loss-notice-mock-data"

// The property table renders on the properties sub-route, not on the
// material detail page (NFM-4204).
const PROPERTY_DETAIL_URL = "/materials/FeCrAl/properties"

test.describe("DataLossNotice backstop (spec §7)", (): void => {
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
