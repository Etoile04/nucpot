// @vitest-environment node
/**
 * NFM-5228: /browse alias-serves the potentials page (afterFiles rewrite in
 * next.config.ts) instead of 308-redirecting to it, so both /browse and
 * /potentials return 200 with identical content. The page MUST declare a
 * canonical URL so search engines keep consolidating on /potentials —
 * without it the alias becomes a duplicate-content page and regresses the
 * NFM-4990 IA consolidation the redirect originally provided.
 */

import { describe, it, expect, afterEach, vi } from "vitest"
import type { Metadata } from "next"

const originalEnv = process.env.NEXT_PUBLIC_APP_URL

async function loadMetadata(): Promise<Metadata> {
  // Bust the module cache — page.tsx reads NEXT_PUBLIC_APP_URL at module
  // scope, so each scenario needs a fresh evaluation.
  vi.resetModules()
  const mod = await import("../page")
  return mod.metadata
}

describe("potentials page canonical metadata (NFM-5228)", () => {
  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.NEXT_PUBLIC_APP_URL
    } else {
      process.env.NEXT_PUBLIC_APP_URL = originalEnv
    }
    vi.resetModules()
  })

  it("declares /potentials as the canonical URL for the alias-served page", async () => {
    const metadata = await loadMetadata()
    expect(metadata.alternates?.canonical).toBe("/potentials")
  })

  it("anchors metadataBase to the deployed origin, defaulting to production", async () => {
    delete process.env.NEXT_PUBLIC_APP_URL
    const metadata = await loadMetadata()
    expect(metadata.metadataBase).toBeInstanceOf(URL)
    expect((metadata.metadataBase as URL).href).toBe("https://nucpot.dpdns.org/")
  })

  it("resolves metadataBase from NEXT_PUBLIC_APP_URL when set (staging/preview)", async () => {
    process.env.NEXT_PUBLIC_APP_URL = "https://staging.nucpot.dpdns.org"
    const metadata = await loadMetadata()
    expect((metadata.metadataBase as URL).href).toBe("https://staging.nucpot.dpdns.org/")
  })
})
