/**
 * NFM-5026 — page scroll regression mock server.
 *
 * Production-style data volume so that body > .ant-app > main has a
 * content height that *clearly exceeds* the viewport height. The bug
 * (main.scrollHeight == main.clientHeight == contentHeight) only
 * manifests when content overflows the viewport; without this
 * hydration the spec can't tell the fix from the broken state on
 * /potentials and /materials at 1440×900.
 *
 * Two endpoints are intercepted per page:
 *   /potentials        — Next BFF route (browser → /api/potentials)
 *   /materials         — direct FastAPI call (browser → /api/v1/materials[/search])
 *
 * Both shapes follow the production contract:
 *   /api/potentials              → { potentials, total, page, limit, totalPages }
 *   /api/v1/materials[/search]   → { success, data: { items, total, page, per_page } }
 *
 * Why this returns the FULL dataset on every page:
 *
 *   The list views (apps/web/src/app/potentials/BrowseView.tsx,
 *   apps/web/src/app/materials/MaterialsListView.tsx) request only 12–20
 *   items per page and the row heights are compact (~30–55px each). 12
 *   potentials × ~55px = ~660px; 20 materials × ~36px = ~720px. After
 *   the layout-shell fix bounds main.clientHeight to the flex chain
 *   (≈725px on 1440×900, ≈630px on 375×812), the per-page slice fits
 *   *exactly* inside the bounded viewport — scrollHeight == clientHeight,
 *   no wheel response, and the "main scrolls" assertion dies even though
 *   the fix itself is correct.
 *
 *   To force main.scrollHeight > main.clientHeight on every page, this
 *   fixture ignores the `per_page` / `limit` request parameter and always
 *   returns the full 60-item payload. The list views still display the
 *   full 60 rows (antd Table / pagination component just renders every
 *   item we send); pagination state still works because the response
 *   envelope carries `total=60`, so clicking "page 2" still fires a
 *   request with `page=2` and the assertion can observe it. Production
 *   data shape is unchanged — only the per-page slice length differs.
 *
 * The homepage (`/`) is server-rendered and its data fetch is not
 * reachable from Playwright's browser-level route interception. The
 * /-page assertions therefore intentionally avoid requiring
 * scrollHeight > clientHeight on desktop; they only verify the
 * *invariant* — main is bounded to the viewport, not stretched to
 * content height — which is the real NFM-5026 fix signal.
 */

import type { Page, Route } from "@playwright/test"

/**
 * 60 items × always-full-page slice = enough to overflow 1440×900 (~725px
 * clientHeight) and 375×812 (~630px clientHeight). 60 rows × ~36px
 * ≈ 2160px ≫ both viewports. Three pages of 20 keep the page-2 click
 * assertion's pagination state coherent.
 */
const POTENTIALS_TOTAL = 60
const POTENTIALS_PER_PAGE = 20

const MATERIALS_TOTAL = 60
const MATERIALS_PER_PAGE = 20

function makePotential(i: number) {
  return {
    id: `pot-${String(i + 1).padStart(3, "0")}`,
    name: `反应堆压力容器用奥氏体不锈钢 ${String(i + 1).padStart(3, "0")}号势函数`,
    display_name: `EAM-Fe-Cr-Ni-${String(i + 1).padStart(3, "0")}`,
    type: i % 3 === 0 ? "EAM" : i % 3 === 1 ? "MEAM" : "Tersoff",
    format: "lammps",
    elements: ["Fe", "Cr", "Ni"],
    description: "测试数据(NFM-5026 滚动断言)",
    version: "1.0",
    tags: ["NFM-5026"],
    file_url: null,
  }
}

function makeMaterial(i: number) {
  return {
    id: `mat-${String(i + 1).padStart(3, "0")}`,
    name: `反应堆压力容器用奥氏体不锈钢 ${String(i + 1).padStart(3, "0")}号`,
    formula: `(Fe0.${(70 + (i % 9)).toString()}Cr0.${(10 + (i % 5)).toString()}Ni0.${(10 - (i % 3)).toString()}Mo0.0${2 + (i % 3)}Mn0.0${1 + (i % 2)})`,
    crystal_structure: i % 2 ? "fcc" : "bcc",
    description: "测试数据(NFM-5026 滚动断言)",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  }
}

function parsePageAndPerPage(
  reqUrl: string,
  defaultPerPage: number,
): { page: number; perPage: number } {
  // Support both `per_page=` (production materials endpoint) and
  // `limit=` (legacy BFF parameter name used by /api/potentials BFF).
  const pm = reqUrl.match(/[?&]page=(\d+)/)
  const lm = reqUrl.match(/[?&]limit=(\d+)/)
  const qm = reqUrl.match(/[?&]per_page=(\d+)/)
  return {
    page: pm ? Number(pm[1]) : 1,
    perPage: lm ? Number(lm[1]) : qm ? Number(qm[1]) : defaultPerPage,
  }
}

function potentialsPayload(page: number, perPage: number) {
  // Return the full dataset on every page so the list view overflows the
  // bounded main viewport (see file header for the rationale). Pagination
  // envelope (page, limit, totalPages) is still reported using the
  // request's `perPage` so the page-2 click assertion's URL match works.
  const slice = Array.from({ length: POTENTIALS_TOTAL }, (_, k) => makePotential(k))
  return {
    potentials: slice,
    total: POTENTIALS_TOTAL,
    page,
    limit: perPage,
    totalPages: Math.ceil(POTENTIALS_TOTAL / perPage),
  }
}

function materialsPayload(page: number, perPage: number) {
  // See potentialsPayload() — same "always return the full dataset"
  // rationale, applied to the materials list. Forces 60 rows × ~36px
  // ≈ 2160px of content past the ~725px bounded clientHeight.
  const slice = Array.from({ length: MATERIALS_TOTAL }, (_, k) => makeMaterial(k))
  return {
    success: true,
    data: {
      items: slice,
      total: MATERIALS_TOTAL,
      page,
      per_page: perPage,
    },
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

export async function setupNfm5026ScrollMocks(page: Page): Promise<void> {
  // /api/potentials — Next BFF (browser → /api/potentials?page=N&per_page=20).
  // The BFF then calls FastAPI; the browser never sees /api/v1/potentials
  // because Playwright fulfils the BFF request directly.
  await page.route(/\/api\/potentials(\?|$)/, (route) => {
    const { page, perPage } = parsePageAndPerPage(route.request().url(), POTENTIALS_PER_PAGE)
    jsonResponse(route, potentialsPayload(page, perPage))
  })

  // /api/v1/materials/search — direct FastAPI (browser → FastAPI).
  await page.route(/\/api\/v1\/materials\/search/, (route) => {
    const { page, perPage } = parsePageAndPerPage(route.request().url(), MATERIALS_PER_PAGE)
    jsonResponse(route, materialsPayload(page, perPage))
  })

  // /api/v1/materials — direct FastAPI list endpoint.
  await page.route(/\/api\/v1\/materials(\?|$)/, (route) => {
    const { page, perPage } = parsePageAndPerPage(route.request().url(), MATERIALS_PER_PAGE)
    jsonResponse(route, materialsPayload(page, perPage))
  })

  // /api/v1/material-categories — keep the category filter dropdown alive
  // so the /materials layout matches prod shape (mirrors
  // materials-viewport-mock-server.ts).
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
