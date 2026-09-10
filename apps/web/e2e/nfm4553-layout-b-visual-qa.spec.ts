// @nfmd
/**
 * NFM-4553 G1-E Layout B — Visual QA capture.
 *
 * Captures desktop (1440x900) + mobile (390x844) screenshots of
 * /literature/{id} with the new Layout B (GraphCanvas + PropertySidebar
 * + 5-action ReviewDrawer).
 *
 * Auth + literature/review endpoints are stubbed via Playwright route
 * interception; the dev server is the live build at apps/web
 * (PORT 3456). No real backend required.
 *
 * Output: apps/web/qa-artifacts/nfm-4553-{desktop,mobile}{,-drawer,-invalid}.png
 *
 * Spec: docs/specs/G1-extraction-value-presentation.md §4.1 + §4.3
 *       docs/specs/G1-extraction-value-presentation-design.md
 */
import { test, expect } from "@playwright/test"

// Use a worktree-isolated port so we don't squat on another
// worktree's `next dev` (playwright.config.ts sets `reuseExistingServer:
// !isCI`, which would otherwise silently test the WRONG bundle).
// 3553 = NFM-3553 was the earliest ticket to lay claim; NFM-4553 keeps
// the same prefix family — 5553 is free as of 2026-09-10.
const PORT = Number(process.env.PORT) || 5553
const BASE = `http://localhost:${PORT}`

const MOCK_USER = {
  success: true,
  data: {
    id: "user-mock-001",
    username: "reader",
    email: "reader@nucpot.test",
    full_name: "Test Reader",
    blog_role: "admin",
    is_active: true,
  },
}

const LITERATURE_ID =
  "beeler-2018-uo2-point-defects-pdf-mock-uuid-32c" // ≥ 32 chars (page guard)

const MOCK_LITERATURE = {
  success: true,
  data: {
    id: LITERATURE_ID,
    title: "Beeler 2018 — point defect properties in UO2",
    doi: "10.1234/beeler.2018",
    journal: "J. Nucl. Mater.",
    year: 2018,
    abstract:
      "Molecular dynamics study of point defect properties in uranium dioxide using the Beeler potential.",
    status: "completed",
    source_id: "ds-001",
    created_at: "2026-09-09T00:00:00Z",
    updated_at: "2026-09-10T00:00:00Z",
    extraction_results: [
      // Material nodes → drive the GraphCanvas centre
      {
        id: "kg-n-uo2",
        source_type: "kg_node",
        property_name: "UO2",
        item_type: "material",
        item_data: {},
        value: null,
        confidence: 0.95,
        source_page: 1,
        source_paragraph: "Uranium dioxide (UO2) is the standard nuclear fuel.",
        provenance: ["llm"],
      },
      // Property node → bound to UO2 via kg_edge
      {
        id: "kg-n-tc",
        source_type: "kg_node",
        property_name: "thermal_conductivity",
        item_type: "property",
        item_data: {},
        value: null,
        confidence: 0.9,
        source_page: 3,
        source_paragraph: "The thermal conductivity of UO2…",
        provenance: ["llm"],
      },
      // Edge binding
      {
        id: "kg-e-1",
        source_type: "kg_edge",
        property_name: "has_property",
        item_type: "edge",
        item_data: {},
        value: null,
        confidence: 0.9,
        source_node_id: "kg-n-uo2",
        source_target_id: "kg-n-tc",
        provenance: ["llm"],
      },
      // Manual measurement rows → sidebar rows
      {
        id: "pm-tc-1",
        source_type: "manual",
        property_name: "thermal_conductivity",
        item_type: "measurement",
        item_data: {
          value_expression: "\\frac{k}{T}",
          validity_check: { status: "ok", reason: null },
        },
        value: 0.34,
        confidence: 0.95,
        unit: "W/(m·K)",
        review_status: "pending",
        source_page: 3,
        source_paragraph:
          "The thermal conductivity of UO2 at 1000 K is 0.34 W/(m·K) as reported in Table 3.",
        provenance: ["manual"],
      },
      {
        id: "pm-lat-1",
        source_type: "manual",
        property_name: "lattice_constant",
        item_type: "measurement",
        item_data: {
          validity_check: {
            status: "fail",
            reason: "lattice 0.3Å outside valid_range [1.0, 10.0]Å",
          },
        },
        value: 0.3,
        confidence: 0.6,
        unit: "Å",
        review_status: "pending",
        source_page: 3,
        source_paragraph: "The lattice parameter is approximately 0.3 Å…",
        provenance: ["manual"],
      },
      {
        id: "pm-dens-1",
        source_type: "manual",
        property_name: "density",
        item_type: "measurement",
        item_data: {
          validity_check: { status: "warn", reason: null },
        },
        value: 10.96,
        confidence: 0.5,
        unit: "g/cm^3",
        review_status: "pending",
        source_page: 3,
        source_paragraph: "Density ~10.96 g/cm^3.",
        provenance: ["manual"],
      },
    ],
  },
}

async function setupMocks(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/auth/me", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_USER),
    })
  })
  await page.route(`**/api/v1/literature/${LITERATURE_ID}`, (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_LITERATURE),
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
      body: JSON.stringify({
        success: true,
        data: { items: [], total: 0, page: 1, limit: 50, pages: 1 },
      }),
    })
  })
}

const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
] as const

for (const vp of viewports) {
  test(`captures ${vp.name} Layout B screenshot (NFM-4553 G1-E)`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height })
    await setupMocks(page)
    await page.context().addCookies([
      {
        name: "access_token",
        value: "eyJhbGciOiJIUzI1NiJ9.mock-token",
        domain: "localhost",
        path: "/",
      },
    ])

    await page.goto(`${BASE}/literature/${LITERATURE_ID}`, {
      waitUntil: "domcontentloaded",
    })

    // Sidebar is the load-bearing signal — the GraphCanvas lazy-loads
    // inside a Skeleton so waiting on the sidebar gives a stable
    // paint signal across all viewports.
    await expect(page.getByTestId("property-sidebar")).toBeVisible({
      timeout: 15_000,
    })
    await expect(
      page.getByTestId("literature-graph-canvas"),
    ).toBeVisible({ timeout: 15_000 })

    // §4.1 default-Layout-B: title rendered in header
    await expect(
      page.getByText(/Beeler 2018 — point defect properties/),
    ).toBeVisible({ timeout: 10_000 })

    // §4.3 red row: pm-lat-1 carries validity_check.status='fail'
    const invalidRow = page.locator(".g1-row-invalid").first()
    await expect(invalidRow).toBeVisible({ timeout: 10_000 })
    await expect(invalidRow).toHaveAttribute(
      "title",
      /lattice 0\.3Å/,
    )

    // AC-5: value_expression rendered through KaTeX
    await expect(page.locator(".katex").first()).toBeVisible({
      timeout: 5_000,
    })

    // Capture the empty-state Layout B
    await page.screenshot({
      path: `qa-artifacts/nfm-4553-${vp.name}.png`,
      fullPage: false,
    })

    // Open the shared 5-action drawer for the first sidebar row,
    // then capture the drawer-open state (Layout A/B share).
    await page.getByTestId("measurement-row-pm-tc-1").click()
    // Wait for the antd drawer slide-in animation to settle before
    // capturing. The drawer assertion passes on first DOM mount
    // (mid-animation), but the screenshot needs the settled state.
    await expect(page.getByTestId("review-drawer")).toBeVisible({
      timeout: 5_000,
    })
    await page.waitForTimeout(500)
    await page.screenshot({
      path: `qa-artifacts/nfm-4553-${vp.name}-drawer.png`,
      fullPage: false,
    })
  })
}
