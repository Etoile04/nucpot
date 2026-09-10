import { describe, it, expect } from "vitest"
import { adaptToG1Row, adaptToG1Rows } from "@/lib/g1-extraction/adapter"
import type { LiteratureExtractionResultItem } from "@/lib/api-client"

const ROW: LiteratureExtractionResultItem = {
  id: "x1",
  source_type: "kg_node",
  property_name: "Thermal conductivity",
  item_type: "thermal_conductivity",
  item_data: { formula: "k = \\alpha + \\beta T" },
  value: 0.34,
  confidence: 0.92,
  review_status: "pending",
  unit: "W/(m·K)",
  source_page: 3,
  source_paragraph: "The thermal conductivity is 0.34 W/(m·K) at 300 K.",
  provenance: ["llm"],
}

describe("adaptToG1Row (NFM-4553 G1-E wire-in)", () => {
  it("maps a legacy row to a G1 row with all key fields", () => {
    const out = adaptToG1Row(ROW)
    expect(out.id).toBe("x1")
    expect(out.property_name).toBe("Thermal conductivity")
    expect(out.value_numeric).toBe(0.34)
    expect(out.value_expression).toBe("k = \\alpha + \\beta T")
    expect(out.unit).toBe("W/(m·K)")
    expect(out.confidence).toBe(0.92)
    expect(out.review_status).toBe("pending")
    expect(out.validity_check).toBeNull()
    expect(out.source_span?.page).toBe(3)
    expect(out.dedupe_key).toContain("x1")
  })

  it("flips validity_check to fail when legacy review_status='invalid'", () => {
    const out = adaptToG1Row({ ...ROW, id: "bad", review_status: "invalid" })
    expect(out.validity_check?.status).toBe("fail")
    expect(out.review_status).toBe("invalid")
  })

  it("coerces unknown review_status to pending", () => {
    const out = adaptToG1Row({ ...ROW, id: "weird", review_status: "frobnicated" })
    expect(out.review_status).toBe("pending")
  })

  it("handles null/undefined inputs safely", () => {
    const out = adaptToG1Row({
      ...ROW,
      id: "nullish",
      value: undefined,
      confidence: null,
      review_status: null,
      unit: null,
      source_paragraph: null,
      item_data: {}, // override so extractValueExpression returns null
    })
    expect(out.value_numeric).toBeNull()
    expect(out.value_text).toBeNull()
    expect(out.value_expression).toBeNull()
    expect(out.confidence).toBe(0)
    expect(out.review_status).toBe("pending")
    expect(out.unit).toBeNull()
    expect(out.source_span).toBeNull()
  })
})

describe("adaptToG1Rows", () => {
  it("returns [] for undefined input", () => {
    expect(adaptToG1Rows(undefined)).toEqual([])
  })

  it("skips kg_edge source_type", () => {
    const rows = adaptToG1Rows([
      { ...ROW, id: "keep" },
      { ...ROW, id: "skip", source_type: "kg_edge" },
    ])
    expect(rows).toHaveLength(1)
    expect(rows[0]?.id).toBe("keep")
  })
})
