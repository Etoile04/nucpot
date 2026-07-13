/**
 * WCAG 2.1 contrast ratio calculation utilities.
 * @module a11y/contrast
 */

/** Convert a hex color string (#RGB or #RRGGBB) to relative luminance. */
export function hexToLuminance(hex: string): number {
  const cleaned = hex.replace("#", "")
  const full =
    cleaned.length === 3
      ? cleaned
          .split("")
          .map((c) => c + c)
          .join("")
      : cleaned

  const r = parseInt(full.slice(0, 2), 16) / 255
  const g = parseInt(full.slice(2, 4), 16) / 255
  const b = parseInt(full.slice(4, 6), 16) / 255

  const toLinear = (c: number): number =>
    c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4

  return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b)
}

/**
 * Calculate the WCAG contrast ratio between two hex colors.
 * Returns a value between 1:1 and 21:1.
 */
export function contrastRatio(foreground: string, background: string): number {
  const fgLum = hexToLuminance(foreground)
  const bgLum = hexToLuminance(background)
  const lighter = Math.max(fgLum, bgLum)
  const darker = Math.min(fgLum, bgLum)
  return (lighter + 0.05) / (darker + 0.05)
}

/** WCAG AA minimum for normal text (< 18pt, or < 14pt bold). */
export const WCAG_AA_NORMAL = 4.5

/** WCAG AA minimum for large text (>= 18pt, or >= 14pt bold). */
export const WCAG_AA_LARGE = 3.0

/** WCAG AA minimum for non-text UI components and graphical objects. */
export const WCAG_AA_NON_TEXT = 3.0

export interface ContrastViolation {
  foreground: string
  background: string
  ratio: number
  required: number
  label: string
}

/**
 * Check a set of color pairs against WCAG AA thresholds.
 * @returns Violations (pairs that don't meet the required ratio).
 */
export function auditContrastPairs(
  pairs: ReadonlyArray<{
    foreground: string
    background: string
    required: number
    label: string
  }>
): ContrastViolation[] {
  return pairs
    .map((p) => ({
      ...p,
      ratio: contrastRatio(p.foreground, p.background),
    }))
    .filter((p) => p.ratio < p.required)
}

/**
 * Audit the nucpot design tokens defined in globals.css.
 * Returns violations for text-on-background pairs.
 */
export function auditDesignTokens(): ContrastViolation[] {
  const pairs = [
    // Core text tokens (fixed values)
    { foreground: "#f9fafb", background: "#1f2937", required: WCAG_AA_NORMAL, label: "primary-text on surface" },
    { foreground: "#d1d5db", background: "#1f2937", required: WCAG_AA_NORMAL, label: "text-secondary on surface" },
    { foreground: "#93c5fd", background: "#1f2937", required: WCAG_AA_NORMAL, label: "accent on surface" },
    { foreground: "#d1d5db", background: "#374151", required: WCAG_AA_NORMAL, label: "text-secondary on surface-elevated" },
    { foreground: "#93c5fd", background: "#374151", required: WCAG_AA_NORMAL, label: "accent on surface-elevated" },
    // Button white text (fixed: blue-600/700)
    { foreground: "#ffffff", background: "#2563eb", required: WCAG_AA_NORMAL, label: "white on blue-600 button" },
    { foreground: "#ffffff", background: "#1d4ed8", required: WCAG_AA_NORMAL, label: "white on blue-700 button-hover" },
    // Homepage-specific Tailwind combos (fixed: gray-300 on cards)
    { foreground: "#d1d5db", background: "#374151", required: WCAG_AA_NORMAL, label: "gray-300 text on gray-700 card" },
    { foreground: "#d1d5db", background: "#1f2937", required: WCAG_AA_NORMAL, label: "gray-300 text on gray-800 bg" },
  ]

  return auditContrastPairs(pairs)
}
