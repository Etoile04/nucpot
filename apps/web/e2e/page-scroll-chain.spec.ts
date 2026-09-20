import { expect, test } from "@playwright/test"

/**
 * NFM-5026 — full-page scrollability regression wall.
 *
 * Root cause this guards against: antd <App> (AntdProvider) inserts a
 * .ant-app wrapper between <body class="h-screen flex flex-col
 * overflow-hidden"> and <main class="flex-1 overflow-y-auto">. A wrapper
 * with default min-height:auto does not shrink, breaking the flex height
 * chain: main grows to full content height, overflow-y-auto never engages,
 * and body's overflow-hidden clips everything below the fold. Symptom was
 * "homepage/lists frozen above the fold, pagination unreachable"
 * (2026-09-20 production, all three pages).
 *
 * Fix under test: `body > .ant-app { display:flex; flex-direction:column;
 * min-height:0 }` (globals.css) + `min-h-0` on main (layout.tsx).
 *
 * These specs fail if ANY layer between body and main stops main from
 * becoming the viewport scroll container — the exact class of breakage
 * that shipped to production unnoticed because the e2e suite had no
 * scroll-assertions.
 */

const PAGES = ["/", "/potentials", "/materials"]

test.describe("NFM-5026 scroll chain", () => {
  for (const path of PAGES) {
    test(`${path}: main is a real scroll container (content taller than viewport is reachable)`, async ({ page }) => {
      await page.goto(path, { waitUntil: "domcontentloaded" })
      // Client components (filters/pagination) hydrate after load.
      await page.waitForTimeout(1500)

      const result = await page.evaluate(() => {
        const main = document.querySelector("main")
        if (!main) return { mainFound: false as const }
        //程序化滚动等价于用户滚轮: 只有 main 真是滚动容器时 scrollTop 才会变
        main.scrollTop = 400
        return {
          mainFound: true as const,
          scrollHeight: main.scrollHeight,
          clientHeight: main.clientHeight,
          scrollTopAfter: main.scrollTop,
          bodyOverflowY: getComputedStyle(document.body).overflowY,
        }
      })

      expect(result.mainFound).toBe(true)
      if (!result.mainFound) return

      // 页面内容超过一屏时: main 必须是滚动容器(scrollHeight > clientHeight),
      // 且 scrollTop 赋值必须生效(说明 flex 链没被中间层撑爆)。
      // 首页在矮视口下必然超屏; 下面统一用 900 视口跑, 三页均 >900px。
      expect(result.scrollHeight).toBeGreaterThan(result.clientHeight)
      expect(result.scrollTopAfter).toBeGreaterThan(0)
      // body 保持裁剪(设计意图): 唯一滚动出口是 main。
      expect(result.bodyOverflowY).toBe("hidden")
    })
  }

  test("/potentials: pagination control is reachable by scrolling", async ({ page }) => {
    await page.goto("/potentials", { waitUntil: "domcontentloaded" })
    const pagination = page.locator(".ant-pagination").first()
    await pagination.waitFor({ state: "visible", timeout: 15000 })

    // 把分页滚进视口(真实用户路径), 然后点第 2 页
    await pagination.scrollIntoViewIfNeeded()
    await expect(pagination).toBeInViewport()

    const page2 = page.locator(".ant-pagination .ant-pagination-item-2").first()
    if (await page2.count()) {
      await page2.click()
      // URL 或列表应反映翻页(页面为客户端分页: 等 active 页类名变化即可)
      await expect(
        page.locator(".ant-pagination .ant-pagination-item-active", { hasText: "2" }),
      ).toBeVisible({ timeout: 5000 })
    }
  })
})
