// @nfmd
/**
 * /datasets list responsive tests (NFM-5330).
 *
 * Out-of-scope finding of the NFM-5321 re-verdict (verdict 52fb974b): at
 * 390×844 the fixed-width trailing columns (审核 96 / 测量日期 120 /
 * 更新时间 120) rendered past the viewport edge with `overflow: visible` on
 * `.ant-table-content` — the 未审核 badges were unreachable and nothing
 * hinted at horizontal scrolling. The 标题 column squeezed to ~114px and
 * wrapped up to four lines (e.g. "UO2 - Lambertson and Handwerk, 1956").
 * Pre-existing behaviour from NFM-5007; 1440×900 desktop was unaffected.
 *
 * The fix (NFM-5330) sets `scroll={{ x: 840 }}` — antd's scroll container,
 * the same pattern as every other table in the app — wraps the Table in
 * TableScrollFade for a swipe affordance, and keeps the three text columns
 * single-line via `ellipsis` (antd also mirrors the cell value into the td
 * `title` attribute, so truncated titles stay reachable as native tooltips).
 *
 * Mock-based (route intercepts the /api/datasets list route), so this spec
 * is a permanent NFMD_SPEC_PATTERN resident — it must never run against live.
 * Invariants pinned here, not markup:
 *   - mobile: the antd scroll container is engaged (content wider than box)
 *   - mobile: every trailing header becomes reachable by scrolling right
 *   - mobile: title cells stay single-line (ellipsis + title attr)
 *   - mobile: the fade affordance appears on the overflowing edge and
 *     swaps sides as the user scrolls — and never renders when the table fits
 *   - desktop: no scroll container, no fades, 审核 header fully visible
 */

import { test, expect, type Page } from "@playwright/test";

/** 390×844 is the viewport from the NFM-5321 re-verdict attachment. */
const MOBILE = { width: 390, height: 844 } as const;
const DESKTOP = { width: 1440, height: 900 } as const;

/**
 * Single-line height budget for a `size="middle"` antd cell: 2×12px padding
 * + 22px line = 46px. The pre-fix 标题 cell measured 179px (4 wrapped
 * lines); 56px admits sub-pixel/font-metric slack without re-admitting any
 * wrap (two lines would already exceed 60px).
 */
const MAX_SINGLE_LINE_CELL_PX = 56;

/**
 * The title that wrapped to four lines in the finding — long latin run,
 * no CJK break opportunities, so any column width regression wraps it.
 */
const LONG_TITLE = "UO2 - Lambertson and Handwerk, 1956";

const DATASETS_BODY = {
  success: true,
  data: {
    items: [
      {
        id: "ds-uo2-lambertson-handwerk-1956-0001",
        material_id: "mat-uo2-0001",
        material_name: "UO2",
        source_id: "src-lambertson-1956",
        source_title: "Lambertson & Handwerk — US AEC Report BMI-1240, 1956",
        title: LONG_TITLE,
        measurement_date: "1956-05-01",
        is_verified: false,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-09-01T08:00:00Z",
      },
      {
        id: "ds-zry4-matsuo-1997-0002",
        material_id: "mat-zry4-0002",
        material_name: "Zircaloy-4",
        source_id: "src-matsuo-1997",
        source_title: "Matsuo, J. Nucl. Mater. 248 (1997) 185-191",
        title: "Zircaloy-4 — Matsuo thermal expansion dataset",
        measurement_date: "1997-08-15",
        is_verified: true,
        created_at: "2026-01-02T00:00:00Z",
        updated_at: "2026-09-02T08:00:00Z",
      },
    ],
    total: 2,
    page: 1,
    limit: 20,
    pages: 1,
    truncated: false,
  },
};

async function mockDatasetsAndGoto(page: Page, viewport: { width: number; height: number }) {
  await page.route("**/api/datasets*", (route) =>
    route.fulfill({ json: DATASETS_BODY }),
  );
  await page.setViewportSize(viewport);
  await page.goto("/datasets", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "数据集" })).toBeVisible();
  await expect(page.locator(".ant-table-row")).toHaveCount(2);
  // Column widths (and therefore scroll geometry) settle with the font.
  await page.evaluate(() => document.fonts.ready);
}

/** The element antd v5 uses as its horizontal scroll container with scroll.x. */
function scrollContainer(page: Page) {
  return page.locator(".ant-table-content");
}

test.describe("/datasets — 390px table reachability (NFM-5330)", () => {
  test("mobile — antd scroll container engaged, right-edge fade hints at more content", async ({
    page,
  }) => {
    await mockDatasetsAndGoto(page, MOBILE);

    // Pre-fix: overflow was `visible` and the trailing columns rendered
    // past the viewport with no way to reach them.
    const geometry = await scrollContainer(page).evaluate((el) => ({
      overflowX: getComputedStyle(el).overflowX,
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
    }));
    expect(["auto", "scroll"]).toContain(geometry.overflowX);
    expect(geometry.scrollWidth).toBeGreaterThan(geometry.clientWidth);

    // Affordance: fade on the overflowing edge, none on the flushed edge.
    await expect(page.getByTestId("table-scroll-fade-right")).toHaveCount(1);
    await expect(page.getByTestId("table-scroll-fade-left")).toHaveCount(0);
  });

  test("mobile — trailing headers (审核/更新时间) become reachable by scrolling right", async ({
    page,
  }) => {
    await mockDatasetsAndGoto(page, MOBILE);

    await scrollContainer(page).evaluate((el) => {
      el.scrollLeft = el.scrollWidth;
    });

    // Fade swaps sides at the far end: right gone, left present.
    await expect(page.getByTestId("table-scroll-fade-right")).toHaveCount(0);
    await expect(page.getByTestId("table-scroll-fade-left")).toHaveCount(1);

    // The finding's clipped headers are now fully inside the viewport.
    for (const headerName of ["审核", "更新时间"]) {
      const box = await page
        .getByRole("columnheader", { name: headerName })
        .boundingBox();
      expect(box, `${headerName} header should have a box`).not.toBeNull();
      expect(box!.x + box!.width).toBeLessThanOrEqual(MOBILE.width);
    }

    // And the row badge the finding showed clipped (未审核, unverified row).
    const badge = page.getByRole("cell", { name: "未审核" }).first();
    const badgeBox = await badge.boundingBox();
    expect(badgeBox).not.toBeNull();
    expect(badgeBox!.x + badgeBox!.width).toBeLessThanOrEqual(MOBILE.width);
  });

  test("mobile — title cells are single-line ellipsis with the full text as tooltip", async ({
    page,
  }) => {
    await mockDatasetsAndGoto(page, MOBILE);

    const titleCell = page.locator(".ant-table-tbody td").first();
    await expect(titleCell).toHaveClass(/ant-table-cell-ellipsis/);

    // antd mirrors the cell value into the td title attribute — the
    // truncated title stays available as a native tooltip.
    expect(await titleCell.getAttribute("title")).toBe(LONG_TITLE);

    const shape = await titleCell.evaluate((el) => {
      const anchor = el.querySelector("a");
      const rects = [...(anchor?.getClientRects() ?? [])].map((r) =>
        Math.round(r.top),
      );
      return {
        height: el.getBoundingClientRect().height,
        // Chromium fragments an ellipsised inline box into several rects on
        // the SAME line, so the never-wrapped invariant is "all fragments
        // share one line", not "exactly one rect".
        distinctLines: new Set(rects).size,
      };
    });
    // Pre-fix this cell measured 179px (4 wrapped lines).
    expect(shape.height).toBeLessThanOrEqual(MAX_SINGLE_LINE_CELL_PX);
    // The anchor never wraps onto a second line.
    expect(shape.distinctLines).toBe(1);
  });
});

test.describe("/datasets — 1440px desktop unchanged (NFM-5330)", () => {
  test("desktop — table fits, no scroll container, no fades, 审核 fully visible", async ({
    page,
  }) => {
    await mockDatasetsAndGoto(page, DESKTOP);

    // No page-level horizontal overflow.
    const doc = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(doc.scrollWidth).toBeLessThanOrEqual(doc.clientWidth + 1);

    // Table content fits its box — antd's min-width:100% stretches the
    // table to the container, so nothing scrolls and no fade renders.
    const content = await scrollContainer(page).evaluate((el) => ({
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
    }));
    expect(content.scrollWidth).toBeLessThanOrEqual(content.clientWidth + 1);
    await expect(page.getByTestId("table-scroll-fade-right")).toHaveCount(0);
    await expect(page.getByTestId("table-scroll-fade-left")).toHaveCount(0);

    // The 390px finding's clipped column is fully visible on desktop.
    const reviewBox = await page
      .getByRole("columnheader", { name: "审核" })
      .boundingBox();
    expect(reviewBox).not.toBeNull();
    expect(reviewBox!.x + reviewBox!.width).toBeLessThanOrEqual(DESKTOP.width);
  });
});
