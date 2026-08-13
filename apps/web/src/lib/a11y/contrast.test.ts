import { describe, it, expect } from "vitest"
import {
  hexToLuminance,
  contrastRatio,
  WCAG_AA_NORMAL,
  auditContrastPairs,
  auditDesignTokens,
} from "./contrast"

describe("hexToLuminance", () => {
  it("returns 0 for pure black", () => {
    expect(hexToLuminance("#000000")).toBe(0)
  })

  it("returns 1 for pure white", () => {
    expect(hexToLuminance("#ffffff")).toBe(1)
  })

  it("handles 3-char shorthand", () => {
    expect(hexToLuminance("#abc")).toBeCloseTo(hexToLuminance("#aabbcc"), 4)
  })

  it("returns consistent results for mid-gray", () => {
    const lum = hexToLuminance("#808080")
    expect(lum).toBeCloseTo(0.216, 2)
  })
})

describe("contrastRatio", () => {
  it("returns 1 for identical colors", () => {
    expect(contrastRatio("#000000", "#000000")).toBe(1)
  })

  it("returns 21 for black on white", () => {
    expect(contrastRatio("#ffffff", "#000000")).toBeCloseTo(21, 0)
  })

  it("computes white on blue-600 (#2563eb) above WCAG AA", () => {
    const ratio = contrastRatio("#ffffff", "#2563eb")
    expect(ratio).toBeGreaterThanOrEqual(WCAG_AA_NORMAL)
  })

  it("computes white on blue-400 (#60a5fa) below WCAG AA", () => {
    const ratio = contrastRatio("#ffffff", "#60a5fa")
    expect(ratio).toBeLessThan(WCAG_AA_NORMAL)
  })
})

describe("auditContrastPairs", () => {
  it("returns no violations for compliant pairs", () => {
    const violations = auditContrastPairs([
      { foreground: "#ffffff", background: "#000000", required: WCAG_AA_NORMAL, label: "test" },
    ])
    expect(violations).toHaveLength(0)
  })

  it("returns violations for non-compliant pairs", () => {
    const violations = auditContrastPairs([
      { foreground: "#808080", background: "#808080", required: WCAG_AA_NORMAL, label: "same-color" },
    ])
    expect(violations).toHaveLength(1)
    expect(violations[0]?.label).toBe("same-color")
  })
})

describe("auditDesignTokens — WCAG AA compliance", () => {
  it("has zero violations for all design token text-on-background pairs", () => {
    const violations = auditDesignTokens()

    // If any violations, report them for debugging
    if (violations.length > 0) {
      const details = violations
        .map((v) => `  ${v.label}: ${v.ratio.toFixed(2)}:1 (need ${v.required}:1) [${v.foreground} on ${v.background}]`)
        .join("\n")
      throw new Error(`WCAG AA contrast violations found:\n${details}`)
    }

    expect(violations).toHaveLength(0)
  })
})
