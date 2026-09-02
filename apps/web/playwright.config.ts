import { defineConfig, devices } from "@playwright/test"

/**
 * Playwright E2E test configuration for NucPot.
 *
 * Environment variables:
 * - BASE_URL: Target URL (default: http://localhost:3000)
 * - CI: Set automatically by GitHub Actions; enables retries, serial workers
 * - E2E_TARGET: "live" to test against production (skips webServer)
 */

const isCI = !!process.env.CI
const isLiveTarget = process.env.E2E_TARGET === "live"
const useChromeChannel = !isCI && process.env.USE_CHROME === "1"

/**
 * NFMD-only E2E specs — excluded from CI live E2E job (NFM-2396).
 * These specs test NFMD-domain features not present on the live blog site.
 * Local runs (no E2E_TARGET) still discover all specs.
 *
 * `gap-review` was added here by NFM-3766 (2026-08-28) as a hotfix when
 * its live smoke run was failing; the underlying race + drawer-modal
 * fixture bug is fixed in NFM-3798, so we remove it from this list and
 * re-exercise the spec under live E2E on every merge to main.
 *
 * `data-loss-notice` was added by NFM-4204 (2026-09-03): the spec is
 * mock-based (route interception seeds the lost-row fixture) so it can
 * never exercise real prod data, and the live site has no `lost` rows
 * with the flag off until the NFM-4177 rollout. Like the other mock-based
 * specs above, it is a permanent local-only resident of this list, not a
 * temporary hotfix.
 */
const NFMD_SPEC_PATTERN =
/(?:review-queue-auth|review-conflicts|rag-chat|md-verification(?:-workflow|-hpc)?|ontology-record-ref|ontology|ontology-management-list|ontology-management-detail|ontology-management-edit|verification-linkage|review-api-smoke|nfm625-v4-visual-qa|design-workspace|design-responsive|nav-tablet-wrap|reauth-return-to|search|gap-review|data-loss-notice)\.spec\.ts$/

// Local webServer port. Configurable so concurrent worktrees (each with
// their own `next dev`) don't collide on the default — a squatting dev
// server on :3000 makes Playwright's `reuseExistingServer` silently test
// ANOTHER worktree's bundle (NFM-4204). Set PORT=3xxx to isolate.
const webServerPort = Number(process.env.PORT) || 3000

const baseURL =
  process.env.BASE_URL ||
  (isLiveTarget ? "https://nucpot.dpdns.org" : `http://localhost:${webServerPort}`)

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: isCI,
  retries: isCI ? 2 : 0,
  workers: isCI ? 1 : undefined,
  reporter: isCI
    ? [
        ["html", { outputFolder: "playwright-report" }],
        ["junit", { outputFile: "playwright-results.xml" }],
        ["json", { outputFile: "playwright-results.json" }],
      ]
    : "html",
  outputDir: "test-results",
  timeout: 30_000,
  testIgnore: isLiveTarget ? [NFMD_SPEC_PATTERN] : undefined,

  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
  },

  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(useChromeChannel ? { channel: "chrome" } : {}),
      },
    },
    {
      name: "firefox",
      use: { ...devices["Desktop Firefox"] },
    },
    {
      name: "webkit",
      use: { ...devices["Desktop Safari"] },
    },
    {
      name: "mobile-chrome",
      use: { ...devices["Pixel 5"] },
      testMatch: /.*-mobile.spec.ts$/,
    },
  ],

  ...(isLiveTarget
    ? {}
    : {
        webServer: {
          // --port must be explicit: when the port is taken this Next
          // version exits with EADDRINUSE instead of hopping, which is
          // the failure mode we want (see webServerPort above).
          command: `pnpm exec next dev --turbopack --port ${webServerPort}`,
          url: `http://localhost:${webServerPort}`,
          // Reuse only servers this config started on OUR port. A dev
          // server from another worktree squatting the port would
          // otherwise be silently reused (NFM-4204 lesson).
          reuseExistingServer: !isCI,
          timeout: 120_000,
          // NFM-4204 — enable the DataLossNotice feature flag for local
          // e2e runs so the backstop spec exercises the flag-ON render
          // path (trigger, popover, analytics). Prod keeps the flag off
          // until the NFM-4177 rollout; locally the flag only affects
          // rows with attribution.status === "lost", which exist solely
          // in the data-loss-notice fixture, so no other spec changes
          // behavior. The env var must be present when the dev server
          // COMPILES the page: feature-flag.ts reads the static
          // `process.env.NEXT_PUBLIC_DATA_LOSS_NOTICE` expression, which
          // Next.js inlines into the browser bundle at compile time —
          // setting it per-test in the browser would be too late. That
          // is why this lives on the webServer rather than in the spec.
          env: {
            ...process.env,
            NEXT_PUBLIC_DATA_LOSS_NOTICE: "on",
          },
        },
      }),
})
