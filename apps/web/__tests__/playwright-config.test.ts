/**
 * Tests for apps/web/playwright.config.ts — NFMD_SPEC_PATTERN live-mode
 * exclusion (NFM-4786).
 *
 * E2E Post-Deploy (Live) runs on ubuntu-latest against
 * https://nucpot.dpdns.org; specs that hardcode localhost ports or fulfil
 * every route with mock fixtures can never exercise real prod data there.
 * NFMD_SPEC_PATTERN is the `testIgnore` list that keeps such specs local/CI
 * only (NFM-2396). The NFM-4553/4554 specs (landed 2026-09-10/11) were
 * never added, so live E2E failed on every firing from 2026-09-11T00:13Z
 * with ERR_CONNECTION_REFUSED against localhost:3456/5553 — first failing
 * run 34498236137, last green 2026-09-10T04:27:57Z @ 692e90675.
 */
import { describe, expect, it } from "vitest"

import { NFMD_SPEC_PATTERN } from "../playwright.config"

describe("NFMD_SPEC_PATTERN (live-mode exclusion, NFM-4786)", () => {
  it("matches the NFM-4553/4554 mock-based specs that broke live E2E", () => {
    const mockBasedSpecs = [
      "nfm-4554-five-actions-contract.spec.ts", // hardcoded :3456, MOCK_ADMIN/MOCK_QUEUE fixtures
      "nfm-4554-skip-ux-failure.spec.ts", // hardcoded :3456
      "nfm4553-layout-b-visual-qa.spec.ts", // hardcoded :5553, beeler mock fixture
      "nfm4554-review-queue-visual-qa.spec.ts", // hardcoded :3456
    ]
    for (const spec of mockBasedSpecs) {
      expect(NFMD_SPEC_PATTERN.test(spec), `${spec} must be excluded from live runs`).toBe(true)
    }
  })

  it("keeps matching the pre-existing permanent residents", () => {
    const residents = [
      "data-loss-notice.spec.ts", // NFM-4204 mock-based precedent
      "review-queue-auth.spec.ts", // NFM-2396 original resident
      "search.spec.ts",
    ]
    for (const spec of residents) {
      expect(NFMD_SPEC_PATTERN.test(spec), `${spec} must stay excluded`).toBe(true)
    }
  })

  it("does not exclude specs that must keep running live", () => {
    const liveSpecs = [
      "api-health.spec.ts",
      "homepage.spec.ts",
      "literature-drawer.spec.ts",
      "feedback.spec.ts",
    ]
    for (const spec of liveSpecs) {
      expect(NFMD_SPEC_PATTERN.test(spec), `${spec} must stay in live runs`).toBe(false)
    }
  })
})
