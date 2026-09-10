/**
 * MeasurementRowItem — invalid-row treatment tests (NFM-4576 W1 / AC-10).
 *
 * Spec: docs/specs/G1-extraction-value-presentation.md §4.3
 *       (validity_check.status='fail' → 红行 + 悬停原因)
 *
 * Background: NFM-4553 E2E QA report W1 (P2, comment 61e862a4) found the
 * AC-10 red-row treatment never fired on Layout B. Two independent layers
 * hid it:
 *
 *   1. globals.css scoped every rule to `.g1-row-invalid > td[:first-child]`,
 *      but the Layout B sidebar row is a `<div role="button">` with no
 *      `<td>` children — the ⚠ glyph, red wash, 4px bar and 500ms shake
 *      had no matching element.
 *   2. The row's inline `borderLeft` / `backgroundColor` styles win the
 *      cascade over any class rule, so even a correct selector would have
 *      been painted over.
 *
 * These tests pin both layers at unit level:
 *
 *   1. The component applies `.g1-row-invalid` on validity fail (AC-10).
 *   2. When invalid, the component omits the inline border/background
 *      declarations so the globals.css class rules own the treatment.
 *   3. Non-invalid rows keep the per-review-status 4px bar + transparent
 *      background (unchanged Layout B behaviour).
 *   4. globals.css carries direct `.g1-row-invalid:not(tr)` rules
 *      (background + border + ⚠ glyph + shake + reduced-motion guard) so
 *      the class paints on `<div>` rows, while the `> td` variants remain
 *      for Layout A table rows (NFM-4554 regression guard).
 *
 * Real-cascade verification (does the selector actually paint?) lives in
 * e2e/nfm4576-w1-invalid-row-treatment.spec.ts under Playwright — jsdom
 * does not apply stylesheets, so computed-style assertions cannot live
 * here. The stylesheet-contract assertions live in
 * g1-row-invalid-css.test.ts (node environment).
 */
import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { MeasurementRowItem, escapeHtml } from "../MeasurementRowItem"
import type { LiteratureExtractionResultItem } from "@/lib/api-client"

/* ------------------------------------------------------------------ */
/*  Fixtures                                                           */
/* ------------------------------------------------------------------ */

function makeRow(
  id: string,
  validityStatus: "ok" | "warn" | "fail" | "unknown",
  validityReason: string | null,
): LiteratureExtractionResultItem {
  return {
    id,
    source_type: "manual",
    property_name: "lattice_constant",
    item_type: "measurement",
    item_data: {
      validity_check: { status: validityStatus, reason: validityReason },
    },
    value: 0.3,
    confidence: 0.6,
    unit: "Å",
    review_status: "pending",
    source_page: 3,
    source_paragraph: "sample paragraph",
    provenance: ["manual"],
  }
}

/* ------------------------------------------------------------------ */
/*  Component layer                                                    */
/* ------------------------------------------------------------------ */

describe("MeasurementRowItem invalid-row treatment (NFM-4576 W1)", () => {
  it("applies the g1-row-invalid class on validity_check.status='fail'", () => {
    const row = makeRow("pm-fail-1", "fail", "lattice 0.3Å outside valid_range")
    render(<MeasurementRowItem row={row} isSelected={false} onClick={() => {}} />)

    const el = screen.getByTestId("measurement-row-pm-fail-1")
    expect(el).toHaveClass("g1-row-invalid")
    // Hover reason (AC-10 — 悬停原因)
    expect(el).toHaveAttribute("title", expect.stringContaining("outside valid_range"))
  })

  it("defers border + background to the CSS class on invalid rows (no inline cascade override)", () => {
    // W1 layer 2: inline borderLeft/backgroundColor beat any class rule.
    // On invalid rows they must be omitted so globals.css owns the
    // red wash + 4px error bar. Selection feedback stays available via
    // aria-pressed + the open drawer — the louder invalid signal wins.
    const row = makeRow("pm-fail-2", "fail", "lattice 0.3Å outside valid_range")
    render(<MeasurementRowItem row={row} isSelected={false} onClick={() => {}} />)

    const el = screen.getByTestId("measurement-row-pm-fail-2")
    expect(el.style.borderLeft).toBe("")
    expect(el.style.backgroundColor).toBe("")
  })

  it("keeps the review-status bar + transparent background on non-invalid rows", () => {
    const row = makeRow("pm-ok-1", "ok", null)
    render(<MeasurementRowItem row={row} isSelected={false} onClick={() => {}} />)

    const el = screen.getByTestId("measurement-row-pm-ok-1")
    expect(el).not.toHaveClass("g1-row-invalid")
    // Unchanged Layout B behaviour: 4px per-review-status bar, no tint.
    expect(el.style.borderLeft).toContain("4px solid")
    expect(el.style.backgroundColor).toBe("transparent")
  })
})

describe("escapeHtml — KaTeX fallback hardening (NFM-4576 gate round-1)", () => {
  it("escapes every HTML-significant character, & first so entities are not double-escaped", () => {
    // renderFormula's belt-and-braces catch returns the raw expression
    // into dangerouslySetInnerHTML — escaping here means a non-parse
    // exception can never smuggle markup through the fallback path.
    expect(escapeHtml(`<img src=x onerror="alert('a')">&`)).toBe(
      "&lt;img src=x onerror=&quot;alert(&#39;a&#39;)&quot;&gt;&amp;",
    )
    // Order matters: & must go first or the introduced entities would
    // themselves be re-escaped.
    expect(escapeHtml("&amp;")).toBe("&amp;amp;")
  })
})
