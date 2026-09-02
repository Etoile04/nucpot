import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { usePathname } from "next/navigation";
import Footer from "./Footer";

// Mock next/navigation. Default to a non-hidden admin path so the email link
// renders; individual tests override with mockReturnValueOnce for the hide test.
vi.mock("next/navigation", () => ({
  usePathname: vi.fn(() => "/admin/ontology/v-draft/edit"),
}))

/**
 * Tailwind v4 color tokens -> foreground RGB. Mirrors the values emitted by
 * the Tailwind compiler so the test stays meaningful in CI (jsdom does not
 * load the generated CSS, so we reason about the *expected* computed color
 * from the className rather than the runtime computed style).
 *
 * Sources: tailwindcss.com/docs/colors and the local `--color-accent`
 * token in apps/web/src/styles/globals.css (documented as WCAG AA on dark).
 */
const TAILWIND_COLORS: Record<string, [number, number, number]> = {
  "text-blue-200": [0xbf, 0xdb, 0xfe], // ~13.8:1 on bg-gray-900 (#101828)
  "text-blue-300": [0x93, 0xc5, 0xfd], // ~9.4:1  on bg-gray-900  AA
  "text-blue-400": [0x60, 0xa5, 0xfa], // ~6.8:1  on bg-gray-900  AA (only when actually rendered)
  "text-blue-500": [0x3b, 0x82, 0xf6], // ~4.5:1  on bg-gray-900  borderline
  "text-blue-600": [0x25, 0x6e, 0xeb], // ~3.4:1  on bg-gray-900  fails AA
}

// Body background in dark mode is `bg-gray-900` = #101828. Ant Design's reset
// overrides `a { color }` at runtime (see apps/web/src/components/antd-provider.tsx),
// which is what produced the 3.42:1 failure in NFM-3803: Tailwind's
// `text-blue-400` was being shadowed by `--ant-color-link: #1668dc`. The fix
// must use Tailwind v4's `!` important prefix so the link wins the cascade.
const PAGE_BG: [number, number, number] = [0x10, 0x18, 0x28] // #101828 = gray-900

// WCAG 2.x relative luminance -- https://www.w3.org/TR/WCAG21/#dfn-relative-luminance
function relativeLuminance([r, g, b]: [number, number, number]): number {
  const channel = (c: number) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

function contrastRatio(
  fg: [number, number, number],
  bg: [number, number, number],
): number {
  const l1 = relativeLuminance(fg)
  const l2 = relativeLuminance(bg)
  const lighter = Math.max(l1, l2)
  const darker = Math.min(l1, l2)
  return (lighter + 0.05) / (darker + 0.05)
}

function extractColorClass(className: string, prefix: string): string | null {
  // Tailwind v4 bang syntax escapes as "\!text-blue-300" in HTML className,
  // but jsdom typically returns the raw "!text-blue-300". Strip an optional
  // leading bang (with or without backslash escape) so the result can be
  // looked up in TAILWIND_COLORS.
  const tokens = className
    .split(/\s+/)
    .map((t) => t.replace(/^\\?!/, ""))
  return tokens.find((t) => t.startsWith(prefix) && !t.includes("/")) ?? null
}

describe("Footer -- color-contrast (NFM-3803)", () => {
  it("renders the feedback email link on non-hidden paths", () => {
    render(<Footer />)
    const link = screen.getByRole("link", { name: /nucpot@agentmail\.to/i })
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute("href", "mailto:nucpot@agentmail.to")
  })

  it("hides itself on /ontology to maximize viewer space", () => {
    vi.mocked(usePathname).mockReturnValueOnce("/ontology/smirnov2014")
    const { container } = render(<Footer />)
    expect(container.querySelector("footer")).toBeNull()
  })

  it("uses a text-* utility with WCAG AA (>=4.5:1) contrast against bg-gray-900", () => {
    render(<Footer />)
    const link = screen.getByRole("link", { name: /nucpot@agentmail\.to/i })
    const cls = link.getAttribute("class") ?? ""

    const colorClass =
      extractColorClass(cls, "text-blue-") ?? extractColorClass(cls, "text-[")

    expect(
      colorClass,
      `Footer link missing a text-* color class: "${cls}"`,
    ).not.toBeNull()

    const fg = TAILWIND_COLORS[colorClass as string]
    expect(
      fg,
      `Unrecognised color class "${colorClass}" -- add to TAILWIND_COLORS`,
    ).toBeDefined()

    const ratio = contrastRatio(fg as [number, number, number], PAGE_BG)
    expect(
      ratio,
      `Footer link "${colorClass}" contrast ${ratio.toFixed(2)}:1 fails WCAG AA (>=4.5:1) on bg-gray-900`,
    ).toBeGreaterThanOrEqual(4.5)
  })

  it("uses !important so Tailwind wins over Ant Design's `a` reset (NFM-3803 root cause)", () => {
    // Ant Design v5 dark theme injects `a { color: var(--ant-color-link); }`
    // via CSS-in-JS, which renders as #1668dc in the smoke lighthouse audit.
    // Without `!important`, the Tailwind class is overridden and contrast
    // collapses to 3.42:1. The bang-prefix in Tailwind v4 produces
    // `color: ... !important`, which must appear on the link.
    render(<Footer />)
    const link = screen.getByRole("link", { name: /nucpot@agentmail\.to/i })
    const cls = link.getAttribute("class") ?? ""

    const hasImportantClass = cls
      .split(/\s+/)
      .some((t) => t.startsWith("!text-") || t.startsWith("\\!text-"))
    expect(
      hasImportantClass,
      `Footer link classes "${cls}" must include a !important text-* class to override Antd's a-reset`,
    ).toBe(true)
  })
})