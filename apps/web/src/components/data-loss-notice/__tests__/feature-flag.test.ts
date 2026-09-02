/**
 * Unit + build-contract coverage for the DATA_LOSS_NOTICE feature flag
 * (NFM-4207).
 *
 * Why a *source* contract test: the shipped defect was invisible to
 * behaviour tests. The env read used to be rooted at `globalThis`, which
 * Next.js build-time inlining (DefinePlugin) never replaces — and browsers
 * have no `process` global — so in the production client bundle the flag
 * always resolved to `undefined` (default OFF). Vitest/jsdom run under
 * Node where `globalThis.process` exists, so every behaviour test passed
 * anyway. The static assertions below pin the real contract: the env read
 * must be a *free* `process.env.NEXT_PUBLIC_*` member expression so the
 * bundler inlines the value at build time.
 */

import { readFileSync } from "node:fs"
import { join } from "node:path"

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { resolveFeatureFlag, setRuntimeOverride } from "../feature-flag"

const MODULE_SOURCE = readFileSync(
  join(import.meta.dirname, "..", "feature-flag.ts"),
  "utf8",
)

describe("feature-flag build contract (NFM-4207)", (): void => {
  it("reads the flag via a free process.env.NEXT_PUBLIC_* member expression", (): void => {
    // This is the exact expression Next.js DefinePlugin replaces at build
    // time. If it is absent (e.g. rewritten as `x?.process?.env?.…`) the
    // value never reaches the browser bundle.
    expect(MODULE_SOURCE).toMatch(
      /process\s*\.\s*env\s*\.\s*NEXT_PUBLIC_DATA_LOSS_NOTICE/,
    )
  })

  it("never roots the env read at another object", (): void => {
    // `process` must appear as a bare identifier. Member access like
    // `globalThis?.process`, `window.process` or `foo.process` is NOT
    // replaced by DefinePlugin, and browsers have no `process` global —
    // the flag would silently read `undefined` in every production
    // browser (NFM-4207 Finding 1).
    expect(MODULE_SOURCE).not.toMatch(/[$\w)\]]\s*\.\s*process\b/)
    expect(MODULE_SOURCE).not.toMatch(/\?\.\s*process\b/)
  })
})

describe("resolveFeatureFlag", (): void => {
  beforeEach((): void => {
    setRuntimeOverride(null)
    delete process.env.NEXT_PUBLIC_DATA_LOSS_NOTICE
  })

  afterEach((): void => {
    setRuntimeOverride(null)
    vi.unstubAllEnvs()
    delete process.env.NEXT_PUBLIC_DATA_LOSS_NOTICE
  })

  it("defaults to OFF when the env var is unset", (): void => {
    expect(resolveFeatureFlag()).toEqual({
      enabled: false,
      source: "default-off",
    })
  })

  it.each(["on", "true", "1", " ON ", "True"])(
    "enables via env value %j",
    (value: string): void => {
      vi.stubEnv("NEXT_PUBLIC_DATA_LOSS_NOTICE", value)
      expect(resolveFeatureFlag()).toEqual({ enabled: true, source: "env" })
    },
  )

  it.each(["off", "false", "0", "yes", "  "])(
    "stays OFF for non-enabling env value %j",
    (value: string): void => {
      vi.stubEnv("NEXT_PUBLIC_DATA_LOSS_NOTICE", value)
      expect(resolveFeatureFlag()).toEqual({
        enabled: false,
        source: "default-off",
      })
    },
  )

  it("runtime override wins over the env value (both directions)", (): void => {
    vi.stubEnv("NEXT_PUBLIC_DATA_LOSS_NOTICE", "on")
    setRuntimeOverride(false)
    expect(resolveFeatureFlag()).toEqual({ enabled: false, source: "provider" })

    setRuntimeOverride(true)
    expect(resolveFeatureFlag()).toEqual({ enabled: true, source: "provider" })
  })

  it("falls back to env after the override is cleared", (): void => {
    vi.stubEnv("NEXT_PUBLIC_DATA_LOSS_NOTICE", "on")
    setRuntimeOverride(true)
    setRuntimeOverride(null)
    expect(resolveFeatureFlag()).toEqual({ enabled: true, source: "env" })
  })
})
