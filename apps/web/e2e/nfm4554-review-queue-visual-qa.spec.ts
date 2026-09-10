// @nfmd
/**
 * NFM-4554 G1-F Layout A — Visual QA capture.
 *
 * Captures desktop (1440x900) + mobile (390x844) screenshots of
 * /admin/review/queue + the shared 5-action drawer.
 *
 * Auth + review endpoints are stubbed via Playwright route interception;
 * the dev server is the live build at apps/web (PORT 3456). No real
 * backend required.
 *
 * Output: apps/web/qa-artifacts/nfm-4554-{desktop,mobile}{,-drawer}.png
 */
import { test, expect } from "@playwright/test"

const BASE = "http://localhost:3456"

const MOCK_ADMIN = {
  success: true,
  data: {
    id: "user-mock-001",
    username: "reviewer",
    email: "reviewer@nucpot.test",
    full_name: "Test Reviewer",
    blog_role: "admin",
    is_active: true,
  },
}

const MOCK_QUEUE = {
  success: true,
  data: {
    items: [
      {
        id: "row-001-verylow",
        item_type: "measurement",
        item_data: {
          value_scalar: 0.34,
          unit_id: "unit-W-per-mK",
          unit_symbol: "W/(m·K)",
          notes: null,
          property_type_id: "pt-thermal-conductivity",
          property_type_name: "thermal conductivity",
          dedupe_key: "dedupe-row-001-002",
          validity_check: { status: "unknown", reason: null },
        },
        confidence: 0.32,
        review_status: "pending",
        source: {
          paragraph: "The thermal conductivity of UO2 at 1000 K is 0.34 W/(m·K) as reported in Table 3.",
          page: 3,
          doi: "10.1234/beeler.2018",
        },
        created_at: "2026-09-10T00:00:00Z",
      },
      {
        id: "row-002-low",
        item_type: "measurement",
        item_data: {
          value_scalar: 4.95,
          unit_id: "unit-angstrom",
          unit_symbol: "Å",
          notes: null,
          property_type_id: "pt-lattice-a",
          property_type_name: "lattice parameter a",
          dedupe_key: "dedupe-row-001-002",
          validity_check: { status: "unknown", reason: null },
        },
        confidence: 0.45,
        review_status: "pending",
        source: {
          paragraph: "Lattice constant a0 = 4.95 Å for the BCC phase at room temperature.",
          page: 5,
          doi: "10.1234/beeler.2018",
        },
        created_at: "2026-09-10T01:00:00Z",
      },
      {
        id: "row-003-midfail",
        item_type: "measurement",
        item_data: {
          value_scalar: 0.05,
          unit_id: "unit-g-per-cm3",
          unit_symbol: "g/cm³",
          notes: null,
          property_type_id: "pt-density",
          property_type_name: "density",
          dedupe_key: null,
          // NFM-4554 G1-F §4.3 红行 — this row exercises the
          // physically-invalid red-row path.
          validity_check: {
            status: "fail",
            reason: "density 0.05 g/cm³ — below valid_range floor 1.0 g/cm³",
          },
        },
        confidence: 0.55,
        review_status: "pending",
        source: {
          paragraph: "Density reported as 0.05 g/cm³ in the amorphous region of the sample.",
          page: 7,
          doi: "10.5678/calhoun.2018",
        },
        created_at: "2026-09-10T02:00:00Z",
      },
      {
        id: "row-004-mid",
        item_type: "measurement",
        item_data: {
          value_scalar: 1200,
          unit_id: "unit-K",
          unit_symbol: "K",
          notes: null,
          property_type_id: "pt-melting-T",
          property_type_name: "melting temperature",
          dedupe_key: null,
          validity_check: { status: "ok", reason: null },
        },
        confidence: 0.72,
        review_status: "pending",
        source: {
          paragraph: "Melting temperature measured at 1200 K under argon atmosphere.",
          page: 9,
          doi: "10.5678/calhoun.2018",
        },
        created_at: "2026-09-10T03:00:00Z",
      },
      {
        id: "row-005-high",
        item_type: "measurement",
        item_data: {
          value_scalar: 5.4,
          unit_id: "unit-eV",
          unit_symbol: "eV",
          notes: null,
          property_type_id: "pt-cohesive-E",
          property_type_name: "cohesive energy",
          dedupe_key: null,
          validity_check: { status: "ok", reason: null },
        },
        confidence: 0.88,
        review_status: "pending",
        source: {
          paragraph: "Cohesive energy of 5.4 eV/atom for the UO2 fluorite structure.",
          page: 11,
          doi: "10.9101/zhu.2024",
        },
        created_at: "2026-09-10T04:00:00Z",
      },
      {
        id: "row-006-veryhigh",
        item_type: "measurement",
        item_data: {
          value_scalar: 10.97,
          unit_id: "unit-g-per-cm3",
          unit_symbol: "g/cm³",
          notes: null,
          property_type_id: "pt-theoretical-density",
          property_type_name: "theoretical density",
          dedupe_key: null,
          validity_check: { status: "ok", reason: null },
        },
        confidence: 0.96,
        review_status: "approved",
        source: {
          paragraph: "Theoretical density of UO2 fluorite phase is 10.97 g/cm³.",
          page: 1,
          doi: "10.9101/zhu.2024",
        },
        created_at: "2026-09-10T05:00:00Z",
      },
    ],
    total: 6,
    page: 1,
    limit: 50,
    pages: 1,
  },
}

async function setupMocks(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/auth/me", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_ADMIN),
    })
  })
  await page.route("**/api/v1/review/pending**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_QUEUE),
    })
  })
  await page.route("**/api/v1/review/**", (route) => {
    if (route.request().method() === "PATCH") {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ success: true, data: {} }),
      })
      return
    }
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_QUEUE),
    })
  })
  await page.route("**/api/v1/properties/**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        data: {
          id: "row-001-verylow",
          dataset_id: "ds-1",
          property_type_id: "pt-1",
          review_status: "pending",
          value_scalar: 0.34,
          unit_id: "unit-W-per-mK",
          notes: null,
        },
      }),
    })
  })
}

const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
] as const

for (const vp of viewports) {
  test(`captures ${vp.name} screenshot of /admin/review/queue (NFM-4554 G1-F)`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height })
    await setupMocks(page)
    // Inject token cookie so any future middleware check is happy.
    await page.context().addCookies([
      {
        name: "access_token",
        value: "eyJhbGciOiJIUzI1NiJ9.mock-token",
        domain: "localhost",
        path: "/",
      },
      {
        name: "blog_admin_token",
        value: "eyJhbGciOiJIUzI1NiJ9.mock-token",
        domain: "localhost",
        path: "/",
      },
    ])

    await page.goto(`${BASE}/admin/review/queue`, { waitUntil: "domcontentloaded" })
    // Wait for the queue table to render
    await expect(page.getByText("校对队列")).toBeVisible({ timeout: 15_000 })
    // Antd v5 emits a measure-cell mirror duplicating header text;
    // disambiguate by role (columnheader) so the strict-mode locator
    // resolves to exactly one element.
    await expect(
      page.getByRole("columnheader", { name: "属性" }),
    ).toBeVisible({ timeout: 15_000 })
    // Confidence asc: lowest first — 32% should be the first row's tag
    await expect(page.getByText("32%").first()).toBeVisible({ timeout: 15_000 })
    // And 96% last
    await expect(page.getByText("96%").first()).toBeVisible({ timeout: 15_000 })
    // NFM-4554 G1-F bounce-back assertion: property name MUST appear,
    // NOT a row UUID prefix.
    await expect(page.getByText("thermal conductivity").first()).toBeVisible({
      timeout: 5_000,
    })
    await expect(page.getByText("lattice parameter a").first()).toBeVisible({
      timeout: 5_000,
    })
    // 已合并 N 行 badge (spec §4.3 dedupe_key group) — first two rows
    // share dedupe_key "dedupe-row-001-002", so 2 badges must render.
    await expect(page.getByText("已合并 2 行").first()).toBeVisible({
      timeout: 5_000,
    })
    // §4.3 红行 — row-003 has validity_check.status='fail'; CSS class
    // applied + native title carries the reason.
    const invalidRow = page.locator("tr.review-row-physically-invalid")
    await expect(invalidRow).toHaveCount(1, { timeout: 5_000 })
    await expect(invalidRow).toHaveAttribute(
      "title",
      /density 0\.05 g\/cm³/,
    )
    // NFM-4560 — 单位 column shows resolved unit symbols (e.g.
    // "W/(m·K)", "Å", "g/cm³", "K", "eV"), NOT row-UUID prefixes.
    // Match via the data-testid so symbol-looking value text (0.34)
    // doesn't accidentally satisfy the assertion.
    const unitSymbols = page.locator("[data-testid='unit-symbol']")
    await expect(unitSymbols.first()).toBeVisible({ timeout: 5_000 })
    const symbolTexts = await unitSymbols.allTextContents()
    expect(symbolTexts.length).toBeGreaterThanOrEqual(6)
    for (const expected of [
      "W/(m·K)",
      "Å",
      "g/cm³",
      "K",
      "eV",
    ]) {
      expect(symbolTexts).toContain(expected)
    }
    // Sanity: the previous bug phrase "unit unit-" must NOT appear.
    expect(await page.locator("body").innerText()).not.toMatch(/unit unit-/)

    await page.screenshot({
      path: `qa-artifacts/nfm-4554-${vp.name}-queue.png`,
      fullPage: true,
    })

    // Open the drawer by clicking the first visible row. Antd v5
    // emits a hidden measure <tr> in the DOM (height: 0); target the
    // first row inside .ant-table-content > table to skip it.
    const firstVisibleRow = page.locator(
      ".ant-table-content .ant-table-tbody tr.ant-table-row",
    ).first()
    await firstVisibleRow.click({ timeout: 5_000 })
    await expect(page.getByTestId("review-drawer")).toBeVisible({ timeout: 10_000 })
    // All 5 §4.3 buttons present
    await expect(page.getByTestId("review-action-confirm")).toBeVisible()
    await expect(page.getByTestId("review-action-modify")).toBeVisible()
    await expect(page.getByTestId("review-action-invalid")).toBeVisible()
    await expect(page.getByTestId("review-action-dispute")).toBeVisible()
    await expect(page.getByTestId("review-action-skip")).toBeVisible()
    // NFM-4560 — 测量值 row in the drawer shows the resolved symbol
    // next to the value (e.g. "0.34 W/(m·K)"), not "0.34 unit unit-W-p".
    await expect(page.getByTestId("unit-symbol-drawer")).toHaveText(
      "W/(m·K)",
      { timeout: 5_000 },
    )
    // Give Ant Drawer time to finish its open animation/portal mount
    await page.waitForTimeout(800)
    // Confirm drawer panel is actually in the viewport bounds
    const drawerBox = await page.getByTestId("review-drawer").boundingBox()
    if (!drawerBox || drawerBox.width === 0) {
      throw new Error(
        `Drawer bounding box empty for ${vp.name}: ${JSON.stringify(drawerBox)}`,
      )
    }

    await page.screenshot({
      path: `qa-artifacts/nfm-4554-${vp.name}-drawer.png`,
      fullPage: false,
    })
  })
}