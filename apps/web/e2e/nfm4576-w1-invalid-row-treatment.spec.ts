// @nfmd
/**
 * NFM-4576 W1 — AC-10 invalid-row treatment on Layout B (computed styles).
 *
 * NFM-4553 E2E QA W1 (P2): the `.g1-row-invalid > td` selectors in
 * globals.css never matched Layout B's `<div>` sidebar rows, so the red
 * wash + 4px error bar + ⚠ glyph + 500ms shake never fired — only the
 * inline red text colour did. This spec proves the fix at the layer the
 * bug lived in: the real cascade of the real page. jsdom cannot apply
 * stylesheets, so these assertions cannot live in vitest.
 *
 * Verifies per AC (desktop 1440x900 + mobile 390x844):
 *   • red wash: background-color resolves to --alert-error-bg
 *   • 4px red left bar: border-left 4px --alert-error-border
 *   • ⚠ glyph: row ::before content
 *   • 500ms one-shot shake: animation-name g1-row-invalid-shake
 *   • prefers-reduced-motion: reduce → animation suppressed, colour
 *     treatment retained
 *   • valid sibling rows keep the plain treatment (no false positives)
 *
 * Auth + literature/review endpoints stubbed via route interception; the
 * dev server is the live build at apps/web (PORT 5576, worktree-isolated
 * — see playwright.config.ts note on port squatting).
 *
 * Output: apps/web/qa-artifacts/nfm-4576-{desktop,mobile}-invalid-row.png
 *
 * Spec: docs/specs/G1-extraction-value-presentation.md §4.3 (AC-10)
 */
import { test, expect, type Page } from "@playwright/test"

// Worktree-isolated port (NFM-4204 lesson: reuseExistingServer would
// silently test another worktree's bundle on a shared port).
const PORT = Number(process.env.PORT) || 5576
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

const LITERATURE_ID = "beeler-2018-uo2-point-defects-pdf-mock-uuid-32c" // ≥ 32 chars (page guard)

const INVALID_ROW_ID = "pm-lat-1" // validity_check.status='fail'
const VALID_ROW_ID = "pm-tc-1" // validity_check.status='ok'

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

async function setupMocks(page: Page) {
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

interface RowCascade {
  backgroundColor: string
  borderLeftWidth: string
  borderLeftColor: string
  animationName: string
  beforeContent: string
}

async function readRowCascade(page: Page, testId: string): Promise<RowCascade> {
  return page.getByTestId(testId).evaluate((el) => {
    const cs = getComputedStyle(el)
    return {
      backgroundColor: cs.backgroundColor,
      borderLeftWidth: cs.borderLeftWidth,
      borderLeftColor: cs.borderLeftColor,
      animationName: cs.animationName,
      beforeContent: getComputedStyle(el, "::before").content,
    }
  })
}

/** Normalise any engine's colour serialisation (`rgb(a)/rgb( / …)`) to
 * components, so the same expectation holds on chromium, firefox and
 * webkit. */
function colorComponents(serialized: string): number[] {
  return (serialized.match(/[\d.]+/g) ?? []).map(Number)
}

async function expectInvalidTreatment(invalid: RowCascade) {
  // --alert-error-bg = rgba(127, 29, 29, 0.55)
  expect(colorComponents(invalid.backgroundColor).slice(0, 3)).toEqual([127, 29, 29])
  // --alert-error-border = #b91c1c = rgb(185, 28, 28)
  expect(colorComponents(invalid.borderLeftColor).slice(0, 3)).toEqual([185, 28, 28])
  expect(parseFloat(invalid.borderLeftWidth)).toBe(4)
  // Redundant glyph (WCAG 1.4.1) — engines serialise content as `"⚠"`.
  expect(invalid.beforeContent).toContain("⚠")
}

for (const vp of [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
] as const) {
  test(`invalid sidebar row paints the full AC-10 treatment (${vp.name} ${vp.width}x${vp.height})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height })
    // Pin the motion preference explicitly so an OS-level reduce setting
    // cannot make the shake assertion flaky.
    await page.emulateMedia({ reducedMotion: "no-preference" })
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
    await expect(page.getByTestId("property-sidebar")).toBeVisible({ timeout: 15_000 })
    const invalidRow = page.getByTestId(`measurement-row-${INVALID_ROW_ID}`)
    await expect(invalidRow).toBeVisible({ timeout: 10_000 })
    // 悬停原因 (AC-10)
    await expect(invalidRow).toHaveAttribute("title", /outside valid_range/)

    const invalid = await readRowCascade(page, `measurement-row-${INVALID_ROW_ID}`)
    await expectInvalidTreatment(invalid)
    // One-shot 500ms shake on first paint (AC-10 / globals.css §4.3).
    expect(invalid.animationName).toBe("g1-row-invalid-shake")

    // No false positives: the ok sibling keeps the plain row treatment.
    const valid = await readRowCascade(page, `measurement-row-${VALID_ROW_ID}`)
    expect(colorComponents(valid.backgroundColor).slice(0, 3)).not.toEqual([127, 29, 29])
    expect(valid.beforeContent).toBe("none")

    await page.screenshot({
      path: `qa-artifacts/nfm-4576-${vp.name}-invalid-row.png`,
      fullPage: false,
    })

    // prefers-reduced-motion: reduce — shake suppressed, colour
    // treatment retained (the redundancy requirement is why the wash,
    // bar and glyph must survive motion reduction).
    await page.emulateMedia({ reducedMotion: "reduce" })
    const reduced = await readRowCascade(page, `measurement-row-${INVALID_ROW_ID}`)
    await expectInvalidTreatment(reduced)
    expect(reduced.animationName).toBe("none")
  })
}
