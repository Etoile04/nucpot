import { expect, test } from "@playwright/test"

/**
 * NFM-5068 — 「更多」dropdown dedupe (CEO decision, 2026-09-21).
 *
 * 「势函数检索」 and 「势函数对比」 were REMOVED from the 更多 dropdown: both
 * capabilities are already embedded in 浏览势函数 (/potentials) — its header
 * quick-search form jumps to /potentials/search?q=, and its per-card compare
 * toggles + CompareBar drive /potentials/compare. The routes themselves stay
 * (deep links, legacy 308 targets); only the duplicate nav entries are gone.
 *
 * Failure of this spec means a duplicate nav entry came back, or the browse
 * page lost one of the two embedded entry points.
 *
 * Robustness note: the menu is a client component, so a click can land before
 * React hydration and silently do nothing — every menu assertion below is
 * gated on a kept entry becoming visible, and the open action is retried via
 * `toPass()` (a bare toHaveCount(0) would otherwise pass on a closed menu).
 */

const REMOVED = ["势函数检索", "势函数对比"]
const KEPT = ["图谱浏览", "KG 搜索", "本体", "博客", "反馈"]
const OPEN_ANCHOR = "图谱浏览" // first MORE_LINKS entry — proves the panel opened

test.describe("NFM-5068 更多 dropdown dedupe", () => {
  test("desktop 更多 dropdown drops 检索/对比 and keeps the other entries", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" })
    const nav = page.locator("nav")
    const trigger = page.getByRole("button", { name: "更多" }).filter({ visible: true })

    await expect(async () => {
      await trigger.click()
      await expect(nav.getByRole("link", { name: OPEN_ANCHOR })).toBeVisible({ timeout: 1500 })
    }).toPass({ timeout: 20000 })

    for (const label of REMOVED) {
      await expect(
        nav.getByRole("link", { name: label }),
        `「${label}」 must not be in the 更多 dropdown`,
      ).toHaveCount(0)
    }
    for (const label of KEPT) {
      await expect(
        nav.getByRole("link", { name: label }),
        `「${label}」 must stay in the 更多 dropdown`,
      ).toHaveCount(1)
    }
  })

  test("mobile menu drops 检索/对比 as well (shared MORE_LINKS)", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto("/", { waitUntil: "domcontentloaded" })
    const nav = page.locator("nav")

    await expect(async () => {
      await page.getByRole("button", { name: "打开导航菜单" }).click()
      // 移动端 MORE_LINKS 藏在「更多」子菜单里, 展开它
      await page.getByRole("button", { name: "更多" }).filter({ visible: true }).click()
      await expect(nav.getByRole("link", { name: OPEN_ANCHOR })).toBeVisible({ timeout: 1500 })
    }).toPass({ timeout: 20000 })

    for (const label of REMOVED) {
      await expect(
        nav.getByRole("link", { name: label }),
        `「${label}」 must not be in the mobile menu`,
      ).toHaveCount(0)
    }
    for (const label of KEPT) {
      await expect(nav.getByRole("link", { name: label })).toHaveCount(1)
    }
  })

  test("浏览势函数 still embeds both entry points (search form + compare toggle)", async ({ page }) => {
    await page.goto("/potentials", { waitUntil: "domcontentloaded" })

    // 页头快速检索(role=search) → /potentials/search?q=
    await expect(page.getByRole("search")).toBeVisible()
    // 卡片对比勾选(确认框 + 「对比」文案) → CompareBar → /potentials/compare
    await expect(page.getByText("对比", { exact: true }).first()).toBeVisible()
  })
})
