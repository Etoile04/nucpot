/**
 * Material Properties fixtures for the Phase 2 E2E suite.
 *
 * Decoupled from backend availability — Playwright boots only Next.js
 * (port 3000); the API endpoints under /api/v1/... are fulfilled via
 * page.route in each spec.
 *
 * Spec: NFM-1401
 */

import type {
  MaterialProperty,
  MaterialPropertyListResponse,
  MaterialPropertyMeta,
  MaterialSummary,
} from "@/lib/materials-api"

// ── Material summary (GET /api/v1/materials/{id}) ─────────────────────

export const MATERIAL_SUMMARY_001: MaterialSummary = {
  id: "m_001",
  name: "氧化锆 (ZrO₂)",
  formula: "ZrO2",
}

// ── Property list (GET /api/v1/materials/{id}/properties?...) ─────────

interface RawProperty {
  id: string
  name: string
  value: string
  unit: string | null
  source: string
  confidence: number
}

/**
 * 8 deterministic properties spread across:
 *   - name       asc / desc
 *   - value      asc / desc
 *   - confidence asc / desc
 *
 * Sorted by `name asc` it reads top-to-bottom as: Density, Fracture
 * Toughness, Melting Point, Poisson Ratio, Specific Heat Capacity,
 * Thermal Conductivity, Vickers Hardness, Young's Modulus.
 *
 * Filter `heat` matches "Specific Heat Capacity" alone.
 * Filter `mpa` matches only "Young's Modulus" (case-insensitive).
 */
const RAW_PROPERTIES: ReadonlyArray<RawProperty> = [
  {
    id: "p_001",
    name: "Density",
    value: "6.05",
    unit: "g/cm³",
    source: "Smith 2019, p.42",
    confidence: 0.92,
  },
  {
    id: "p_002",
    name: "Melting Point",
    value: "2715",
    unit: "K",
    source: "Smith 2019, p.43",
    confidence: 0.95,
  },
  {
    id: "p_003",
    name: "Young's Modulus",
    value: "200",
    unit: "GPa",
    source: "ASM Handbook v2",
    confidence: 0.88,
  },
  {
    id: "p_004",
    name: "Poisson Ratio",
    value: "0.31",
    unit: null,
    source: "ASM Handbook v2",
    confidence: 0.72,
  },
  {
    id: "p_005",
    name: "Specific Heat Capacity",
    value: "0.456",
    unit: "J/(g·K)",
    source: "NIST WebBook",
    confidence: 0.65,
  },
  {
    id: "p_006",
    name: "Thermal Conductivity",
    value: "2.5",
    unit: "W/(m·K)",
    source: "NIST WebBook",
    confidence: 0.81,
  },
  {
    id: "p_007",
    name: "Vickers Hardness",
    value: "1200",
    unit: "HV",
    source: "Smith 2019, p.58",
    confidence: 0.59,
  },
  {
    id: "p_008",
    name: "Fracture Toughness",
    value: "7.5",
    unit: "MPa·m^0.5",
    source: "Smith 2019, p.62",
    confidence: 0.74,
  },
]

const PROPERTIES: ReadonlyArray<MaterialProperty> = RAW_PROPERTIES

const META: MaterialPropertyMeta = {
  total: PROPERTIES.length,
  page: 1,
  limit: 50,
}

export const MATERIAL_PROPERTY_LIST: MaterialPropertyListResponse = {
  data: PROPERTIES,
  meta: META,
}

/**
 * Build a property list response sorted by the given column / order.
 * Used by spec-level mocks so we can exercise the table's `sorter`
 * without driving the Ant Design click-then-wait interaction in two
 * regions.
 */
export function buildMaterialPropertyList(
  sortBy: "name" | "value" | "confidence" = "name",
  order: "asc" | "desc" = "asc",
): MaterialPropertyListResponse {
  const sorted = [...PROPERTIES].sort((a, b) => {
    let cmp = 0
    if (sortBy === "confidence") {
      cmp = a.confidence - b.confidence
    } else {
      cmp = String(a[sortBy]).localeCompare(String(b[sortBy]))
    }
    return order === "asc" ? cmp : -cmp
  })

  return { data: sorted, meta: META }
}
