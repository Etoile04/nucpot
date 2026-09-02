/**
 * Mock API fixtures for the DataLossNotice e2e backstop (NFM-4204).
 *
 * The original spec (shipped in 2cec252 / NFM-4146) claimed "the test
 * harness seeds a fixture via the standard test-setup helpers" — but no
 * fixture existed, and prod has zero `lost` rows, so every run timed out
 * waiting for a DOM state that could never occur. These fixtures make the
 * spec self-sufficient: one lost row (the notice cohort) alongside intact
 * and unadjudicated rows (the negative cohorts), served via Playwright
 * route interception (see data-loss-notice-mock-server.ts).
 *
 * Types are structural copies of `MaterialProperty` /
 * `MaterialPropertyListResponse` / the backend's `ApiResponse<T>` envelope
 * (apps/web/src/lib/materials-api.ts, api-client.ts). They are declared
 * locally because no other e2e fixture imports from `src/` — keeping the
 * Playwright transform free of tsconfig-alias resolution — so drift is
 * guarded by the spec's assertions rather than the type checker.
 */

// ── Structural API shapes (mirror src/lib/materials-api.ts) ────────────

interface MockSourceRef {
  readonly id: string
  readonly title: string
  readonly doi: string | null
  readonly journal: string | null
  readonly year: number | null
  readonly authors: ReadonlyArray<string>
  readonly url: string | null
}

interface MockAttribution {
  readonly status: "lost" | "intact"
  readonly lostAt?: string
  readonly siblingPlaceholderCount?: number
}

interface MockMaterialProperty {
  readonly id: string
  readonly name: string
  readonly value: string
  readonly unit: string | null
  readonly source: MockSourceRef | null
  readonly confidence: number
  readonly conditions: ReadonlyArray<Record<string, unknown>>
  readonly attribution?: MockAttribution
  readonly createdAt?: string
}

// ── Builders ────────────────────────────────────────────────────────────

const LOST_MEASUREMENT_ID = "0199-loss-feca1-aaaaaaaaaaaa"

function makeSource(overrides: Partial<MockSourceRef> = {}): MockSourceRef {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    title: "Owen, R. et al.",
    doi: "10.0000/nucmat.2023.001",
    journal: "J. Nucl. Mater.",
    year: 2023,
    authors: ["Owen, R.", "Patel, S."],
    url: "https://doi.org/10.0000/nucmat.2023.001",
    ...overrides,
  }
}

function makeProperty(
  overrides: Partial<MockMaterialProperty> = {},
): MockMaterialProperty {
  return {
    id: "0199-prop-feca1-000000000001",
    name: "密度",
    value: "5.1",
    unit: "g/cm³",
    source: makeSource(),
    confidence: 0.92,
    conditions: [],
    attribution: { status: "intact" },
    createdAt: "2026-08-15T00:00:00Z",
    ...overrides,
  }
}

// ── Fixture rows ────────────────────────────────────────────────────────

/**
 * The property page fixture: 3 intact rows, 1 unadjudicated row (no
 * attribution envelope — outside the canonical cohort), and exactly ONE
 * lost row. The lost measurement's source is NULL, matching migration
 * 070's placeholder collapse (the event that makes the notice apply).
 */
export const MOCK_FECRAL_PROPERTIES: ReadonlyArray<MockMaterialProperty> = [
  makeProperty({
    id: "0199-prop-feca1-000000000001",
    name: "密度",
    value: "5.1",
  }),
  makeProperty({
    id: "0199-prop-feca1-000000000002",
    name: "熔点",
    value: "1733",
    unit: "K",
    source: makeSource({
      id: "00000000-0000-0000-0000-000000000002",
      title: "Yamamoto, A. et al.",
      year: 2021,
    }),
    confidence: 0.88,
  }),
  makeProperty({
    id: "0199-prop-feca1-000000000003",
    name: "热导率",
    value: "13.9",
    unit: "W/(m·K)",
    source: makeSource({
      id: "00000000-0000-0000-0000-000000000003",
      title: "Fielding, J. et al.",
      year: 2019,
    }),
    confidence: 0.75,
  }),
  // Unadjudicated: no attribution envelope → row must NOT carry
  // data-attribution-status nor the --data-loss class.
  makeProperty({
    id: "0199-prop-feca1-000000000004",
    name: "电阻率",
    value: "1.38",
    unit: "µΩ·m",
    source: null,
    confidence: 0.4,
    attribution: undefined,
  }),
  // The lost row — the ONLY member of the notice cohort.
  makeProperty({
    id: LOST_MEASUREMENT_ID,
    name: "弹性模量",
    value: "193",
    unit: "GPa",
    source: null,
    confidence: 0.5,
    attribution: {
      status: "lost",
      lostAt: "2026-08-01",
      siblingPlaceholderCount: 3,
    },
  }),
]

export const MOCK_LOST_MEASUREMENT_ID = LOST_MEASUREMENT_ID

/** Full HTTP body for `GET /api/v1/materials/FeCrAl/properties`. */
export const MOCK_PROPERTIES_RESPONSE = {
  success: true,
  data: {
    data: MOCK_FECRAL_PROPERTIES,
    meta: {
      total: MOCK_FECRAL_PROPERTIES.length,
      page: 1,
      limit: 50,
    },
  },
}
