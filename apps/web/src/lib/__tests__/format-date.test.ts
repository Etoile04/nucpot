import { describe, it, expect } from "vitest"
import { formatDateTime } from "../format-date"

describe("formatDateTime", () => {
  it("renders local YYYY-MM-DD HH:mm without ISO artifacts", () => {
    const out = formatDateTime("2026-09-01T01:29:54.036093Z")
    expect(out).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)
    expect(out).not.toContain("T")
    expect(out).not.toContain("Z")
  })

  it("converts to the viewer's local timezone", () => {
    // The rendered value must equal the instant re-derived through local
    // Date getters — pinning both format and localization behaviour.
    const iso = "2026-09-01T00:00:00Z"
    const d = new Date(iso)
    const pad = (n: number) => String(n).padStart(2, "0")
    const expected = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
    expect(formatDateTime(iso)).toBe(expected)
  })

  it("drops sub-minute precision (issue example: 2026-09-01 01:29)", () => {
    const out = formatDateTime("2026-09-01T01:29:54.036093Z")
    expect(out.endsWith(":29")).toBe(true)
  })

  it("returns '-' for null, undefined, empty, and invalid input", () => {
    expect(formatDateTime(null)).toBe("-")
    expect(formatDateTime(undefined)).toBe("-")
    expect(formatDateTime("")).toBe("-")
    expect(formatDateTime("not-a-date")).toBe("-")
  })
})
