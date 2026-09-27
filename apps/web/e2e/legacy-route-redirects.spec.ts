import { expect, test } from "@playwright/test"

/**
 * Legacy route handling (NFM-4990 IA-REFACTOR P1, supersedes NFM-4987;
 * /browse amended by NFM-5228).
 *
 * The potential-function library moved to a first-level path family,
 * literature moved to /publications, and search became a secondary
 * route under /potentials:
 *   /browse         →  alias-served /potentials content (NFM-5228, was 308)
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
 * NFM-5228 EXEMPTION: /browse is an afterFiles REWRITE, not a redirect.
 * The 308 chain (redirect hop + page render, both through Cloudflare)
 * measured 3.0–5.3s on the sentinel and intermittently breached the 5s
 * P1 budget, so /browse now answers 200 directly with the /potentials
 * content and a canonical link back to /potentials.
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
  // NFM-5228: /browse serves the /potentials page directly via an
  // afterFiles rewrite — 200, no Location header, no redirect hop. This
  // is the arm of the fix that removes the extra Cloudflare round trip.
  test("/browse serves the potentials page directly (200, no redirect) — NFM-5228", async () => {
    const res = await getRedirect("/browse")
    expect(res.status).toBe(200)
    expect(res.headers.get("location")).toBeNull()
    expect(res.headers.get("content-type")).toContain("text/html")
  })

  test("/browse with filter params answers 200 directly (NFM-5228)", async () => {
    const res = await getRedirect("/browse?element=Fe&sort=updated&page=2")
    expect(res.status).toBe(200)
    expect(res.headers.get("location")).toBeNull()
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
    expect(res.headers.get("location")).toBe("/publications/e50dbbb2-0e14-4afb-88cb-33537bfd96f1")
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

  // NFM-5228: the browser must stay on /browse (no client-visible
  // redirect) while the potentials content renders, and the canonical
  // link keeps SEO consolidated on /potentials now that both paths
  // serve the same 200 document.
  test("browser renders the functional page at /browse without a redirect hop — NFM-5228", async ({
    page,
  }) => {
    await page.goto("/browse?element=Fe", { waitUntil: "domcontentloaded" })
    await expect(page).toHaveURL(/\/browse\?element=Fe/)
    await expect(page.locator("nav").first()).toBeVisible()
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", /\/potentials\/?$/)
  })
})
