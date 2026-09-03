/**
 * Unit + contract coverage for the DATA_LOSS_NOTICE feature flag
 * resolution (NFM-4180).
 *
 * NFM-4146/NFM-4207 resolved the flag from a build-time env var
 * (`NEXT_PUBLIC_DATA_LOSS_NOTICE`), and a source-contract test pinned
 * the free `process.env.*` member expression so Next.js inlined it.
 * NFM-4180 replaces that mechanism wholesale: the source of truth is
 * the backend feature-flag service (`lib/flag-service.ts`), so the
 * build contract inverts — the env var must be read NOWHERE, and the
 * module must resolve from the flag-service cache with a fail-closed
 * default.
 */

import { readFileSync } from "node:fs"
import { join } from "node:path"

import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  FEATURE_FLAG_NAME,
  refreshFeatureFlag,
  resolveFeatureFlag,
  setRuntimeOverride,
} from "../feature-flag"

vi.mock("@/lib/flag-service", () => ({
  evaluateFlag: vi.fn(),
  getCachedEvaluation: vi.fn().mockReturnValue(undefined),
}))

import { evaluateFlag, getCachedEvaluation } from "@/lib/flag-service"

const mockedEvaluate = vi.mocked(evaluateFlag)
const mockedGetCached = vi.mocked(getCachedEvaluation)

const MODULE_SOURCE = readFileSync(
  join(import.meta.dirname, "..", "feature-flag.ts"),
  "utf8",
)

describe("feature-flag source contract (NFM-4180)", (): void => {
  it("never reads the NEXT_PUBLIC_DATA_LOSS_NOTICE env var", (): void => {
    // The env var was baked into the client bundle at build time —
    // flipping it required a redeploy (the exact defect NFM-4180
    // removes). Any reintroduction of this read is a regression.
    expect(MODULE_SOURCE).not.toMatch(/NEXT_PUBLIC_DATA_LOSS_NOTICE/)
  })

  it("resolves through the flag-service cache", (): void => {
    expect(MODULE_SOURCE).toMatch(/getCachedEvaluation/)
  })
})

describe("resolveFeatureFlag", (): void => {
  beforeEach((): void => {
    setRuntimeOverride(null)
    mockedGetCached.mockReturnValue(undefined)
    mockedEvaluate.mockReset()
  })

  it("defaults to OFF (fail closed) with no cache and no override", (): void => {
    expect(resolveFeatureFlag()).toEqual({
      enabled: false,
      source: "default-off",
    })
  })

  it("resolves the cached flag-service evaluation", (): void => {
    mockedGetCached.mockReturnValue({
      key: FEATURE_FLAG_NAME,
      enabled: true,
      rollout_percentage: 100,
      value: true,
      bucket: 7,
    })
    expect(resolveFeatureFlag()).toEqual({
      enabled: true,
      source: "flag-service",
    })
  })

  it("a cached value of false stays OFF (fail closed on backend kill switch)", (): void => {
    mockedGetCached.mockReturnValue({
      key: FEATURE_FLAG_NAME,
      enabled: false,
      rollout_percentage: 0,
      value: false,
      bucket: 7,
    })
    expect(resolveFeatureFlag()).toEqual({
      enabled: false,
      source: "flag-service",
    })
  })

  it("runtime override wins over the cached value (both directions)", (): void => {
    mockedGetCached.mockReturnValue({
      key: FEATURE_FLAG_NAME,
      enabled: true,
      rollout_percentage: 100,
      value: true,
      bucket: 7,
    })
    setRuntimeOverride(false)
    expect(resolveFeatureFlag()).toEqual({ enabled: false, source: "provider" })

    setRuntimeOverride(true)
    expect(resolveFeatureFlag()).toEqual({ enabled: true, source: "provider" })
  })

  it("falls back to the cache after the override is cleared", (): void => {
    mockedGetCached.mockReturnValue({
      key: FEATURE_FLAG_NAME,
      enabled: true,
      rollout_percentage: 100,
      value: true,
      bucket: 7,
    })
    setRuntimeOverride(false)
    setRuntimeOverride(null)
    expect(resolveFeatureFlag()).toEqual({
      enabled: true,
      source: "flag-service",
    })
  })
})

describe("refreshFeatureFlag", (): void => {
  beforeEach((): void => {
    mockedEvaluate.mockReset()
  })

  it("returns the evaluation value from the flag service", async (): Promise<void> => {
    mockedEvaluate.mockResolvedValue({
      key: FEATURE_FLAG_NAME,
      enabled: true,
      rollout_percentage: 10,
      value: true,
      bucket: 3,
    })
    await expect(refreshFeatureFlag()).resolves.toBe(true)
    expect(mockedEvaluate).toHaveBeenCalledWith(FEATURE_FLAG_NAME)
  })

  it("propagates the fail-closed false on evaluation failure", async (): Promise<void> => {
    mockedEvaluate.mockResolvedValue({
      key: FEATURE_FLAG_NAME,
      enabled: false,
      rollout_percentage: 0,
      value: false,
      bucket: 0,
    })
    await expect(refreshFeatureFlag()).resolves.toBe(false)
  })
})
