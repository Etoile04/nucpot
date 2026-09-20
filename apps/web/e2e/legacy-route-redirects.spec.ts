import { expect, test } from "@playwright/test"

/**
 * Legacy route redirects (NFM-4990 IA-REFACTOR P1, supersedes NFM-4987).
 *
 * The potential-function library moved to a first-level path family,
 * literature moved to /publications, and search became a secondary
 * route under /potentials:
 *   /browse         → /potentials
 *   /potential/:id  → /potentials/:id   (bare /potential → /potentials)
 *   /compare        → /potentials/compare
 *   /literature/*   → /publications/*
 *   /search         → /potentials/search
 *
 * The redirects are declared in next.config.ts with `permanent: true`,
 * so every legacy URL must answer 308 with the new Location, and the
 * original query string must survive the hop (Next.js appends it to the
 * redirect destination automatically).
 *
 * Status/Location assertions use Node's global fetch with
 * `redirect: "manual"` — undici returns the raw redirect response with
 * readable headers, unlike the browser's opaque-redirect filtering. The
 * base URL mirrors playwright.config.ts so local, CI, and live targets
 * all work without the Playwright request context's auto-following.
 */

const webServerPort = Number(process.env.PORT) || 3000
const BASE_URL =
  process.env.BASE_URL ||
  (process.env.E2E_TARGET === "live"
    ? "https://nucpot.dpdns.org"
    : `http://localhost:${webServerPort}`)

async function getRedirect(path: string): Promise<Response> {
  // Browser-like UA: Cloudflare in front of the live target rejects
  // bare node-fetch UA strings (CF 1010).
  return fetch(new URL(path, BASE_URL), {
    redirect: "manual",
    headers: {
      "User-Agent":
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    },
  })
}

test.describe("Legacy route permanent redirects (NFM-4990)", { tag: "@smoke" }, () => {
  test("/browse → /potentials is a 308 with Location", async () => {
    const res = await getRedirect("/browse")
    expect(res.status).toBe(308)
    expect(res.headers.get("location")).toBe("/potentials")
  })

  test("/browse query params survive the redirect", async () => {
    const res = await getRedirect("/browse?element=Fe&sort=updated&page=2")
    expect(res.status).toBe(308)
    const loc = new URL(res.headers.get("location")!, BASE_URL)
    expect(loc.pathname).toBe("/potentials")
    expect(loc.searchParams.get("element")).toBe("Fe")
    expect(loc.searchParams.get("sort")).toBe("updated")
    expect(loc.searchParams.get("page")).toBe("2")
  })

  test("/potential/<id> → /potentials/<id> is a 308 with Location", async () => {
    const res = await getRedirect("/potential/smoke-test-id")
    expect(res.status).toBe(308)
    expect(res.headers.get("location")).toBe("/potentials/smoke-test-id")
  })

  test("bare /potential → /potentials (BUG-10 guard, now permanent)", async () => {
    const res = await getRedirect("/potential")
    expect(res.status).toBe(308)
    expect(res.headers.get("location")).toBe("/potentials")
  })

  test("/compare → /potentials/compare is a 308 keeping ids", async () => {
    const res = await getRedirect("/compare?ids=pot-001,pot-002")
    expect(res.status).toBe(308)
    // Next.js re-encodes the Location query (comma → %2C); compare the
    // parsed param so the assertion checks semantics, not encoding.
    const loc = new URL(res.headers.get("location")!, BASE_URL)
    expect(loc.pathname).toBe("/potentials/compare")
    expect(loc.searchParams.get("ids")).toBe("pot-001,pot-002")
  })

  test("/literature → /publications is a 308 with Location", async () => {
    const res = await getRedirect("/literature")
    expect(res.status).toBe(308)
    expect(res.headers.get("location")).toBe("/publications")
  })

  test("/literature/<id> deep link → /publications/<id> is a 308", async () => {
    const res = await getRedirect("/literature/e50dbbb2-0e14-4afb-88cb-33537bfd96f1")
    expect(res.status).toBe(308)
    expect(res.headers.get("location")).toBe(
      "/publications/e50dbbb2-0e14-4afb-88cb-33537bfd96f1"
    )
  })

  test("/literature query params survive the redirect", async () => {
    const res = await getRedirect("/literature?search=uo2&year=2021")
    expect(res.status).toBe(308)
    const loc = new URL(res.headers.get("location")!, BASE_URL)
    expect(loc.pathname).toBe("/publications")
    expect(loc.searchParams.get("search")).toBe("uo2")
    expect(loc.searchParams.get("year")).toBe("2021")
  })

  test("/search → /potentials/search is a 308 keeping q", async () => {
    const res = await getRedirect("/search?q=UO2&mode=text")
    expect(res.status).toBe(308)
    const loc = new URL(res.headers.get("location")!, BASE_URL)
    expect(loc.pathname).toBe("/potentials/search")
    expect(loc.searchParams.get("q")).toBe("UO2")
    expect(loc.searchParams.get("mode")).toBe("text")
  })

  test("browser follows /browse?filters to the functional page", async ({ page }) => {
    await page.goto("/browse?element=Fe", { waitUntil: "domcontentloaded" })
    await expect(page).toHaveURL(/\/potentials\?element=Fe/)
    await expect(page.locator("nav").first()).toBeVisible()
  })
})
