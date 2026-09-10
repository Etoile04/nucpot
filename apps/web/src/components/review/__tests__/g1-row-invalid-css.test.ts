// @vitest-environment node
/**
 * globals.css `.g1-row-invalid` contract tests (NFM-4576 W1 / AC-10).
 *
 * Spec: docs/specs/G1-extraction-value-presentation.md §4.3
 *
 * NFM-4553 E2E QA W1 (P2): every red-row rule was scoped to
 * `.g1-row-invalid > td[:first-child]`, which never matches Layout B's
 * `<div>` sidebar rows — the ⚠ glyph, red wash, 4px bar and 500ms shake
 * were dead selectors on the Literature detail page. These tests pin the
 * stylesheet so both row flavours stay covered:
 *
 *   • div rows (Layout B / NFM-4553) — direct rules on the row element
 *   • td rows (Layout A / NFM-4554) — the `> td` variants must remain
 *
 * Node environment (not jsdom): the file layer reads globals.css from
 * disk; jsdom externalises node builtins and applies no stylesheets
 * anyway. Real-cascade proof lives in
 * e2e/nfm4576-w1-invalid-row-treatment.spec.ts (Playwright).
 */
import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

const css = readFileSync(path.join(process.cwd(), "src/styles/globals.css"), "utf8")

describe("globals.css g1-row-invalid rules (NFM-4576 W1)", () => {
  it("paints the red-row treatment directly on the row element (no <td> dependency)", () => {
    // :not(tr) targets Layout B's <div> rows while staying inert on the
    // Layout A <tr> rows served by the `> td` variants below.
    expect(css).toMatch(
      /\.g1-row-invalid:not\(tr\)\s*\{[^}]*background-color:\s*var\(--alert-error-bg\)/,
    )
    expect(css).toMatch(
      /\.g1-row-invalid:not\(tr\)\s*\{[^}]*border-left:\s*4px solid var\(--alert-error-border\)/,
    )
    expect(css).toMatch(/\.g1-row-invalid:not\(tr\)\s*\{[^}]*animation:\s*g1-row-invalid-shake/)
  })

  it("carries the redundant ⚠ glyph on the row head (::before)", () => {
    // WCAG 1.4.1 — colour is never the sole carrier of the invalid state.
    expect(css).toMatch(/\.g1-row-invalid:not\(tr\)::before\s*\{[^}]*content:\s*"⚠/)
  })

  it("keeps the > td variants for Layout A table rows (NFM-4554)", () => {
    expect(css).toMatch(
      /\.g1-row-invalid > td\s*\{[^}]*background-color:\s*var\(--alert-error-bg\)/,
    )
    expect(css).toMatch(
      /\.g1-row-invalid > td:first-child\s*\{[^}]*border-left:\s*4px solid var\(--alert-error-border\)/,
    )
  })

  it("suppresses the shake under prefers-reduced-motion", () => {
    const reducedBlocks = css.split(/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{/).slice(1)
    const divVariantSuppressed = reducedBlocks.some((block) => {
      const head = block.slice(0, 400)
      return /\.g1-row-invalid:not\(tr\)/.test(head) && /animation:\s*none/.test(head)
    })
    expect(divVariantSuppressed).toBe(true)
  })
})
