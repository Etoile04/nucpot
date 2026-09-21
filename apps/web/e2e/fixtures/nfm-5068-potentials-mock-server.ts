/**
 * Mock API server for the NFM-5068 browse-page e2e assertions (NFM-5070 R1).
 *
 * The 「对比」 compare-affordance assertion in nfm-5068-more-menu.spec.ts is
 * per-card: it only renders when the potentials list has ≥1 row, so a
 * dataless backend (empty corpus or unreachable API) leaves /potentials on
 * the 暂无势函数数据 / 加载失败 state and the spec would time out. This
 * fixture intercepts the same-origin BFF list route and returns exactly one
 * row, making the spec hermetic regardless of backend state. Follows the
 * project convention from data-loss-notice-mock-server.ts (NFM-4204).
 *
 * The browser-facing wire shape is the BFF route's own JSON
 * (src/app/api/potentials/route.ts): {potentials, total, page, limit,
 * totalPages} — camelCase totalPages, NOT the client interface's
 * total_pages field (the real BFF leaves that field unsatisfied too;
 * no consumer reads either key, so the mock mirrors the wire, not the
 * type). The glob's trailing `*` matches the query string but not a
 * `/`, so detail-route fetches (`/api/potentials/<id>`) pass through.
 *
 * Usage:
 *   import { setupPotentialsListMock } from "./fixtures/nfm-5068-potentials-mock-server"
 *
 *   await setupPotentialsListMock(page)
 *   await page.goto("/potentials")
 *
 * Spec: NFM-5068 AC3 / NFM-5070 R1
 */

import type { Page, Route } from "@playwright/test"

/** Minimal one-row list payload (see PotentialSummary for row fields). */
const MOCK_POTENTIALS_RESPONSE = {
  potentials: [
    {
      id: "nfm-5068-e2e-mock-eam-fe",
      name: "EAM_Fe_NFM5068_E2E_Mock",
      display_name: "NFM-5068 e2e mock (EAM Fe)",
      type: "EAM",
      format: "LAMMPS",
      elements: ["Fe"],
      description:
        "Route-interception fixture row: guarantees a card so the per-card 对比 affordance is assertable in dataless environments.",
      version: "1.0",
      tags: ["e2e-mock"],
    },
  ],
  total: 1,
  page: 1,
  limit: 20,
  totalPages: 1,
}

function jsonResponse(route: Route, body: unknown, status = 200): void {
  void route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
    headers: { "Access-Control-Allow-Origin": "*" },
  })
}

/** Install the potentials-list mock on a page. */
export async function setupPotentialsListMock(page: Page): Promise<void> {
  await page.route("**/api/potentials*", (route) => {
    jsonResponse(route, MOCK_POTENTIALS_RESPONSE)
  })
}
