/**
 * Mock API for the NFM-4444 /materials viewport-overflow regression guard.
 *
 * Intercepts /api/v1/materials and /api/v1/materials/search so the page
 * sees 108 items / 6 pages — the exact shape from the UAT-2 regression
 * report §8. We want the layout to be exercised against the production-
 * equivalent data volume; with the default seeded catalogue the
 * pagination bar is a single "共 N 条" line and the layout's worst
 * case is never reached.
 *
 * Pattern follows ./data-loss-notice-mock-server.ts: route interception
 * only, no real backend required.
 */

import type { Page, Route } from "@playwright/test"

const TOTAL = 108
const PER_PAGE = 20

function makeItem(i: number) {
  // Worst-case cell content: long Chinese name + long formula. Production
  // catalogue has similar entries, and the bug was reported under
  // 100% zoom with a non-trivial dataset — the longest practical
  // values are what we want to exercise here.
  return {
    id: `mat-${String(i + 1).padStart(3, "0")}`,
    name: `反应堆压力容器用奥氏体不锈钢 ${String(i + 1).padStart(3, "0")}号`,
    formula: `(Fe0.${(70 + (i % 9)).toString()}Cr0.${(10 + (i % 5)).toString()}Ni0.${(10 - (i % 3)).toString()}Mo0.0${2 + (i % 3)}Mn0.0${1 + (i % 2)})`,
    crystal_structure: i % 2 ? "fcc" : "bcc",
    description: "测试数据",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  }
}

function listPayload(page: number, perPage: number) {
  const start = (page - 1) * perPage
  const slice = Array.from({ length: Math.min(perPage, TOTAL - start) }, (_, k) =>
    makeItem(start + k),
  )
  return {
    success: true,
    data: {
      items: slice,
      total: TOTAL,
      page,
      per_page: perPage,
    },
  }
}

function parseParams(reqUrl: string): { page: number; perPage: number } {
  const pm = reqUrl.match(/[?&]page=(\d+)/)
  const qm = reqUrl.match(/[?&]per_page=(\d+)/)
  return {
    page: pm ? Number(pm[1]) : 1,
    perPage: qm ? Number(qm[1]) : PER_PAGE,
  }
}

function jsonResponse(route: Route, body: unknown): void {
  route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
    headers: { "Access-Control-Allow-Origin": "*" },
  })
}

export async function setupMaterialsViewportMocks(page: Page): Promise<void> {
  // /api/v1/materials/search?... — search endpoint (with or without query)
  await page.route(/\/api\/v1\/materials\/search/, (route) => {
    const { page, perPage } = parseParams(route.request().url())
    jsonResponse(route, listPayload(page, perPage))
  })

  // /api/v1/materials?... — list endpoint
  await page.route(/\/api\/v1\/materials(\?|$)/, (route) => {
    const { page, perPage } = parseParams(route.request().url())
    jsonResponse(route, listPayload(page, perPage))
  })

  // Categories: keep the filter dropdown alive so the layout matches prod
  await page.route(/\/api\/v1\/material-categories/, (route) => {
    jsonResponse(route, {
      success: true,
      data: {
        items: [
          {
            id: "11111111-1111-1111-1111-111111111111",
            name: "Oxide Fuel",
            slug: "oxide_fuel",
            description: null,
            parent_id: null,
            sort_order: 1,
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
        ],
      },
    })
  })
}
