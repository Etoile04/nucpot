/**
 * Material Properties flow — Phase 2 E2E (NFM-1401)
 *
 * Exercises /materials/{id}/properties:
 *   - Table renders all fixture rows from a mocked backend.
 *   - Column header clicks trigger Ant Design's in-memory sorter
 *     (asc → desc on successive clicks).
 *   - Filter input narrows the visible rows and clears them.
 *
 * Deterministic waits only — every assertion uses Playwright's auto-wait
 * (expect().toBeVisible() / expect().toHaveCount()) rather than
 * sleep/waitForTimeout. The flow is fully independent of every other
 * E2E spec — the mock routes only handle /api/v1/materials/*
 * (matched by URL regex) and the fixtures are scoped per-spec.
 *
 * Epic Branch: feat/nfm-834-phase2-e2e-base
 */

import { test, expect, type Route, type Page } from "@playwright/test"

import {
  MATERIAL_PROPERTY_LIST,
  MATERIAL_SUMMARY_001,
} from "./fixtures/material-properties-fixture"

const MATERIAL_ID = "m_001"
const PROPERTIES_URL = (id: string) =>
  new RegExp(`/api/v1/materials/${id}/properties(?:\\?.*)?$`)
const SUMMARY_URL = (id: string) =>
  new RegExp(`/api/v1/materials/${id}(?:\\?.*)?$`)

async function fulfillJson(route: Route, body: unknown): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  })
}

/**
 * Install the per-test route mock for /api/v1/materials/{id}/*
 * (summary + properties). The properties payload is the unsorted
 * fixture — Ant Design Table performs the sort in-memory on every
 * header click, so the spec exercises that path directly.
 */
async function mockPropertiesRoutes(page: Page): Promise<void> {
  await page.route(SUMMARY_URL(MATERIAL_ID), (route) =>
    fulfillJson(route, MATERIAL_SUMMARY_001),
  )
  await page.route(PROPERTIES_URL(MATERIAL_ID), (route) =>
    fulfillJson(route, MATERIAL_PROPERTY_LIST),
  )
}

async function gotoProperties(page: Page): Promise<void> {
  await page.goto(`/materials/${MATERIAL_ID}/properties`)
  // Deterministic — wait for the table root that the component renders
  // after its initial fetch resolves (mocked above).
  await expect(page.locator(".material-property-table")).toBeVisible()
}

test.describe("Material Properties flow", { tag: "@e2e" }, () => {
  const rows = ".material-property-table tbody tr[data-row-key]"

  test("renders the table with all fixture properties", async ({ page }) => {
    await mockPropertiesRoutes(page)
    await gotoProperties(page)

    // Header shows the material summary's formula (deterministic text
    // sourced from the mock — no real backend required).
    await expect(page.getByText("化学式：ZrO2")).toBeVisible()

    // All 8 fixture rows render — asserted by tbody row count.
    const dataRows = page.locator(rows)
    await expect(dataRows).toHaveCount(MATERIAL_PROPERTY_LIST.data.length)

    // Spot-check the first / last rows in fixture-raw order. The
    // table starts unsorted, so the rows are returned in the order
    // the backend delivered them (Density first, Fracture Toughness
    // last).
    await expect(dataRows.first()).toContainText("Density")
    await expect(dataRows.nth(7)).toContainText("Fracture Toughness")
  })

  test("sorts by name column header click — asc then desc", async ({ page }) => {
    await mockPropertiesRoutes(page)
    await gotoProperties(page)

    const dataRows = page.locator(rows)
    await expect(dataRows).toHaveCount(8)

    // Ant Design Table starts unsorted; clicking the 属性名称 header
    // sorts asc by `name` (uses localeCompare per the component spec).
    await page
      .locator(".material-property-table thead th")
      .filter({ hasText: "属性名称" })
      .click()

    await expect(dataRows.first()).toContainText("Density")
    await expect(dataRows.nth(7)).toContainText("Young's Modulus")

    // Click again to flip to desc — same deterministic first/last
    // expectation, just swapped.
    await page
      .locator(".material-property-table thead th")
      .filter({ hasText: "属性名称" })
      .click()

    await expect(dataRows.first()).toContainText("Young's Modulus")
    await expect(dataRows.nth(7)).toContainText("Density")
  })

  test("sorts by confidence column numerically", async ({ page }) => {
    await mockPropertiesRoutes(page)
    await gotoProperties(page)

    const dataRows = page.locator(rows)
    await expect(dataRows).toHaveCount(8)

    // Click the 置信度 column header. Sorter is a numeric comparator
    // (a.confidence - b.confidence). Click once for asc, once more
    // for desc — assert that the lowest-confidence row is first
    // asc, last desc.
    const confidenceHeader = page
      .locator(".material-property-table thead th")
      .filter({ hasText: "置信度" })

    await confidenceHeader.click()
    await expect(dataRows.first()).toContainText("Vickers Hardness")
    await expect(dataRows.nth(7)).toContainText("Melting Point")

    await confidenceHeader.click()
    await expect(dataRows.first()).toContainText("Melting Point")
    await expect(dataRows.nth(7)).toContainText("Vickers Hardness")
  })

  test("filters rows by search input", async ({ page }) => {
    await mockPropertiesRoutes(page)
    await gotoProperties(page)

    const searchInput = page.getByPlaceholder("筛选属性...")
    await expect(searchInput).toBeVisible()
    await searchInput.fill("heat")

    const dataRows = page.locator(rows)
    await expect(dataRows).toHaveCount(1)
    await expect(dataRows.first()).toContainText("Specific Heat Capacity")

    // The component renders a "筛选结果 N 条" hint when filtered.
    await expect(page.getByText(/筛选结果 1 条/)).toBeVisible()
  })

  test("clears filter and restores all rows", async ({ page }) => {
    await mockPropertiesRoutes(page)
    await gotoProperties(page)

    const searchInput = page.getByPlaceholder("筛选属性...")
    // "mpa" matches the only property whose unit contains the lowercase
    // substring "mpa" — Fracture Toughness ("MPa·m^0.5"). Young's Modulus
    // uses "GPa" and is therefore excluded from this filter.
    await searchInput.fill("mpa")

    const dataRows = page.locator(rows)
    await expect(dataRows).toHaveCount(1)
    await expect(dataRows.first()).toContainText("Fracture Toughness")

    // Clear via the input element directly (allowClear behaviour does
    // not produce a DOM event the sorter flow exercises, so we type
    // an empty value rather than click the clear icon).
    await searchInput.fill("")
    await expect(dataRows).toHaveCount(8)
  })
})
