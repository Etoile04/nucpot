/**
 * /about/data-integrity responsive bullet-list tests (NFM-4252).
 *
 * QA finding (E2E QA W1 on NFM-4248): in the "发生了什么" section the bullet
 * prose collapsed into 1-character-wide columns at 375 px and the inline
 * `<span class="font-mono">` runs pushed past the viewport.
 *
 * Root cause: each `<li>` is `flex`, and raw text placed directly in a flex
 * container becomes an *anonymous flex item* per run. An inline `<span>` in
 * mid-sentence therefore split one sentence into three or four items sharing a
 * single nowrap flex line, each shrinking to min-content — and CJK min-content
 * is one character. Wrapping the sentence in one `<span>` restores normal
 * inline flow; `whitespace-nowrap` on the mono runs keeps identifiers atomic.
 *
 * ADR-012 §3's final copy (NFM-4249, PR #1136) landed with the vulnerable
 * pattern in ALL THREE bullet sections — 恢复措施 carries the longest tokens
 * (e.g. `property_measurements_backup_070`). NFM-4252's reconciliation re-lands
 * that copy character-for-character inside the fixed structure, so these tests
 * now cover every bullet section, not just 发生了什么 (CPO hazard note on
 * NFM-4252): re-landing raw text in the flex rows fails the height assertion.
 *
 * These tests pin the observable invariants rather than the markup, so they
 * still hold if the copy or the wrapper element changes:
 *   - no horizontal overflow at any viewport
 *   - every inline mono run occupies exactly one line box (never split)
 *   - no bullet grows tall enough to indicate a column collapse
 */

import { test, expect, type Page } from "@playwright/test";

const VIEWPORTS = [
  { name: "mobile", width: 375, height: 812 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "desktop", width: 1440, height: 900 },
] as const;

/** Every bullet section of the page, with its expected bullet count. */
const BULLET_SECTIONS = [
  { heading: "发生了什么", bullets: 2 },
  { heading: "恢复措施", bullets: 4 },
  { heading: "对使用者意味着什么", bullets: 4 },
] as const;

/**
 * Height above which a bullet must be considered column-collapsed. The
 * regression rendered li[1] at 330 px on a 375 px viewport; the fixed layout
 * renders the longest final-copy bullet at 132 px (6 lines) on 375 px. 200 px
 * sits clear of both, leaving room for legitimate copy growth without
 * re-admitting the bug.
 */
const MAX_BULLET_HEIGHT_PX = 200;

/** Locate a section's bullet list by its heading, without depending on DOM position. */
function sectionList(page: Page, heading: string) {
  return page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: heading }) })
    .locator("ul");
}

test.describe("/about/data-integrity — responsive bullet list", () => {
  for (const vp of VIEWPORTS) {
    test(`${vp.name} (${vp.width}px) — no horizontal overflow`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto("/about/data-integrity", {
        waitUntil: "domcontentloaded",
      });
      await expect(
        page.getByRole("heading", { name: "数据完整性说明" }),
      ).toBeVisible();

      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(overflow.scrollWidth).toBeLessThanOrEqual(
        overflow.clientWidth + 1,
      );
    });

    test(`${vp.name} (${vp.width}px) — inline mono runs are never split across lines`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto("/about/data-integrity", {
        waitUntil: "domcontentloaded",
      });
      await expect(
        page.getByRole("heading", { name: "发生了什么" }),
      ).toBeVisible();
      // Mono metrics depend on the real face, not the fallback.
      await page.evaluate(() => document.fonts.ready);

      // Every mono run on the page, across all bullet sections.
      const monos = page.locator("main .font-mono");
      const count = await monos.count();
      expect(count).toBeGreaterThanOrEqual(18);

      for (let i = 0; i < count; i += 1) {
        const run = monos.nth(i);
        const shape = await run.evaluate((el) => ({
          text: el.textContent?.trim() ?? "",
          // >1 client rect means the inline box wrapped onto another line,
          // i.e. the identifier was broken mid-token. (Padding spaces must
          // live OUTSIDE the span as siblings: edge spaces inside a nowrap
          // run both fragment into extra rects and remove the break
          // opportunities that let the line wrap BEFORE a long identifier.)
          rects: el.getClientRects().length,
          // Clipped-tail guard: layout.tsx sets `body { overflow: hidden }`,
          // so a nowrap run past the viewport edge is silently CLIPPED and
          // documentElement.scrollWidth never grows — an overflow-only
          // assertion cannot see this class (measured on the pre-fix tree:
          // runs reaching x=454 on a 375 px viewport). The bound is the
          // VIEWPORT, not the li box: engines differ by ~a space width on
          // where mixed CJK/latin lines may end (WebKit encroaches a few px
          // into main's px-6 gutter with nothing clipped), while real
          // clipping is what crosses the viewport.
          runRight: Math.max(
            ...Array.from(el.getClientRects()).map((r) => r.right),
          ),
          viewportRight: document.documentElement.clientWidth,
        }));
        expect(
          shape.rects,
          `mono run "${shape.text}" must occupy one line box`,
        ).toBe(1);
        expect(
          shape.runRight,
          `mono run "${shape.text}" crosses the viewport's right edge ` +
            `(${shape.runRight} > ${shape.viewportRight}) — its tail is clipped`,
        ).toBeLessThanOrEqual(shape.viewportRight + 1);
      }
    });

    test(`${vp.name} (${vp.width}px) — bullets do not collapse into narrow columns`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto("/about/data-integrity", {
        waitUntil: "domcontentloaded",
      });
      await expect(
        page.getByRole("heading", { name: "发生了什么" }),
      ).toBeVisible();
      await page.evaluate(() => document.fonts.ready);

      for (const section of BULLET_SECTIONS) {
        const bullets = sectionList(page, section.heading).locator("li");
        const count = await bullets.count();
        expect(count, `section ${section.heading} bullet count`).toBe(
          section.bullets,
        );

        for (let i = 0; i < count; i += 1) {
          const box = await bullets.nth(i).boundingBox();
          expect(box).not.toBeNull();
          expect(
            box!.height,
            `${section.heading} bullet ${i} is ${box!.height}px tall — text has collapsed into columns`,
          ).toBeLessThan(MAX_BULLET_HEIGHT_PX);
        }
      }
    });
  }
});
