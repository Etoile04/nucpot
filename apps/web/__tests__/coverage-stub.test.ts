/**
 * Tests for apps/web/scripts/emit-coverage-stub.mjs (NFM-2045, KR-5.2).
 *
 * apps/web contributes ZERO core-module coverage under the KR-5 spec
 * (NFM-2035 §2 ADR-KR5-1). The core set is:
 *
 *   apps/api/src/, apps/api/src/models/, apps/api/alembic_migrations/, packages/
 *
 * No apps/web path is in that set — the only apps/web mention is an
 * *exclusion* (`apps/web/src/components/ui/`, which does not even exist in
 * this tree). The one TypeScript core surface, `packages/shared/src/index.ts`,
 * declares nothing but interfaces, so it has no executable lines to cover.
 *
 * So apps/web emits a documented n/a stub rather than real coverage. These
 * tests pin the two properties the KR-5 aggregator (NFM-2046) depends on:
 *
 *   1. The stub is recognisable, so the aggregator can skip it deliberately.
 *   2. It reports zero *total* lines, so line-weighted aggregation adds
 *      nothing to either numerator or denominator — no false-zero drag on
 *      the KR-5 percentage.
 */

import { describe, it, expect } from "vitest"

import {
  STUB_MARKER,
  buildStubCoverageXml,
} from "../scripts/emit-coverage-stub.mjs"

/** Parse with jsdom's DOMParser and fail loudly on malformed XML. */
function parseXml(xml: string): Document {
  const doc = new DOMParser().parseFromString(xml, "text/xml")
  const error = doc.querySelector("parsererror")
  expect(error, `XML failed to parse: ${error?.textContent}`).toBeNull()
  return doc
}

describe("emit-coverage-stub", () => {
  it("emits well-formed Cobertura XML", () => {
    const doc = parseXml(buildStubCoverageXml())

    expect(doc.documentElement.tagName).toBe("coverage")
    expect(buildStubCoverageXml().startsWith("<?xml")).toBe(true)
  })

  it("carries a recognisable n/a marker citing the spec ADR", () => {
    const xml = buildStubCoverageXml()

    // The marker must name the ADR so a reader of the raw file understands
    // why coverage is absent without needing the issue tracker.
    expect(STUB_MARKER).toContain("ADR-KR5-1")
    expect(xml).toContain(STUB_MARKER)
  })

  it("declares the marker on a single package so the aggregator can skip it", () => {
    const doc = parseXml(buildStubCoverageXml())
    const packages = doc.querySelectorAll("package")

    expect(packages).toHaveLength(1)
    expect(packages[0]?.getAttribute("name")).toBe(STUB_MARKER)
  })

  it("reports zero total lines so it cannot contribute a false zero", () => {
    const doc = parseXml(buildStubCoverageXml())
    const root = doc.documentElement

    // Line-weighted aggregation is covered/total. Both zero => contributes
    // nothing. A stub with lines-valid > 0 and lines-covered = 0 would drag
    // the KR-5 number down, which is exactly what we must avoid.
    expect(root.getAttribute("lines-valid")).toBe("0")
    expect(root.getAttribute("lines-covered")).toBe("0")

    // No classes either, so a path-filtering aggregator finds nothing to include.
    expect(doc.querySelectorAll("class")).toHaveLength(0)
  })

  it("is byte-for-byte deterministic so running tests never dirties the tree", () => {
    // The stub is committed. If it embedded a wall-clock timestamp, every
    // `pnpm test` would leave an unstaged diff and CI cleanliness checks
    // would fail.
    expect(buildStubCoverageXml()).toBe(buildStubCoverageXml())
  })
})
