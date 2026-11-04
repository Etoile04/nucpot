import { describe, expect, it } from "vitest"

// NFM-5863: the site-monitor probe cadence (~320-400s observed) exceeds the
// old s-maxage=300, so every probe landed on (or near) the edge-expiry path
// and occasionally caught the cold-fill latency class (5.3-9.4s P1 singles,
// NFM-5862 ledger). The route-level revalidate window IS the emitted
// s-maxage for these ISR pages, so this contract pins the chosen TTL:
//
//   - exactly 1800s (30 min): comfortably above the worst observed probe
//     gap (~401s, 4.5x margin) while staying clear of the NFM-5418
//     stale-pin lane ceiling (>3600s WARN) and bounding post-deploy edge
//     staleness to 30 min (deploy_prod.sh purges everything post-cutover,
//     NFM-5420/NFM-5426).
//
// /browse is the NFM-5228 afterFiles alias of /potentials, so the
// potentials module contract covers both paths.
//
// These import the actual page modules so the assertion tracks the config
// Next.js really uses, not a copy of the source text.
import * as homePage from "./page"
import * as potentialsPage from "./potentials/page"
import * as materialsPage from "./materials/page"
// NFM-5863 AC3: /datasets is the fifth site-monitor SCAN_PAGES edge-HTML
// route and carries the same short-TTL condition — treated identically.
import * as datasetsPage from "./datasets/page"
// The home page's emitted s-maxage is min(route revalidate, inner fetch
// revalidate), so the home-data fetch window must match the route window —
// pinned here in-lockstep (see HOME_DATA_REVALIDATE_S in home-data.ts).
import { HOME_DATA_REVALIDATE_S } from "@/lib/home/home-data"

const NFM_5863_TTL_S = 1800
const NFM_5418_LANE_CEILING_S = 3600
const AC2_MIN_S = 900

function revalidateOf(mod: Record<string, unknown>): number {
  const v = mod.revalidate
  expect(v, "route must export a numeric `revalidate` (ISR route segment config)").toEqual(
    expect.any(Number),
  )
  return v as number
}

describe.each([
  ["home / (and monitored heavy route)", homePage],
  ["potentials /potentials (+ /browse alias)", potentialsPage],
  ["materials /materials", materialsPage],
  ["datasets /datasets (5th SCAN_PAGES route, AC3)", datasetsPage],
])("heavy-route edge TTL (%s)", (_label, mod) => {
  it("uses the NFM-5863 1800s edge-cache window", () => {
    expect(revalidateOf(mod)).toBe(NFM_5863_TTL_S)
  })

  it("stays >= AC2 floor (900s) and under the NFM-5418 stale-pin lane ceiling (<3600s)", () => {
    const revalidate = revalidateOf(mod)
    expect(revalidate).toBeGreaterThanOrEqual(AC2_MIN_S)
    expect(revalidate).toBeLessThan(NFM_5418_LANE_CEILING_S)
  })
})

describe("home-data fetch window (clamps / emitted s-maxage)", () => {
  it("joins the same NFM-5863 1800s window as the / route segment", () => {
    expect(HOME_DATA_REVALIDATE_S).toBe(NFM_5863_TTL_S)
  })
})
