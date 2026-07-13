import { describe, it, expect, vi, beforeEach } from "vitest"
import { GET } from "./route"

/**
 * NFM-1367: Text search must not return 500 for valid queries like "Zr".
 *
 * Root cause: route.ts called dbQuery.textSearch("search_vector", query)
 * but the `potentials` table has no `search_vector` tsvector column.
 * Fix: replace textSearch with ILIKE filter on existing text columns.
 */

const MOCK_POTENTIALS = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    name: "Zr_eam.alloy",
    display_name: "Zr EAM Alloy Potential",
    type: "EAM",
    elements: ["Zr"],
    description: "Embedded atom method potential for Zr alloys",
    tags: [],
    version: "1.0",
    status: "published",
    extra: {},
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    name: "Cu_meam",
    display_name: "Cu MEAM Potential",
    type: "MEAM",
    elements: ["Cu"],
    description: "Modified embedded atom method for copper",
    tags: [],
    version: "1.0",
    status: "published",
    extra: {},
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
]

function createMockSupabaseQueryBuilder() {
  const chain: string[] = []
  let returnData: Record<string, unknown>[] = MOCK_POTENTIALS
  let returnError: { message: string } | null = null

  const builder: Record<string, unknown> = {
    select: vi.fn(() => {
      chain.push("select")
      return builder
    }),
    eq: vi.fn((_col: string, _val: unknown) => {
      chain.push(`eq:${_col}`)
      return builder
    }),
    order: vi.fn((_col: string, _opts: unknown) => {
      chain.push(`order:${_col}`)
      return builder
    }),
    range: vi.fn((_from: number, _to: number) => {
      chain.push("range")
      return builder
    }),
    overlaps: vi.fn((_col: string, _arr: unknown) => {
      chain.push(`overlaps:${_col}`)
      return builder
    }),
    ilike: vi.fn((_col: string, _pattern: string) => {
      chain.push(`ilike:${_col}:${_pattern}`)
      return builder
    }),
    or: vi.fn((_filter: string) => {
      chain.push(`or:${_filter}`)
      return builder
    }),
    filter: vi.fn((_col: string, _op: string, _val: string) => {
      chain.push(`filter:${_col}:${_op}`)
      return builder
    }),
    contains: vi.fn((_col: string, _val: unknown) => {
      chain.push(`contains:${_col}`)
      return builder
    }),
    textSearch: vi.fn((_col: string, _query: string) => {
      chain.push(`textSearch:${_col}`)
      return builder
    }),
  }

  // Make the chain spy accessible
  builder.__chain = chain

  // Make builder directly awaitable (chain methods return builder, not a proxy)
  builder.then = (resolve: (v: unknown) => void) => {
    resolve({ data: returnData, count: returnData.length, error: returnError })
  }

  return {
    builder,
    getChain: () => chain,
    setData: (data: Record<string, unknown>[]) => { returnData = data },
    setError: (msg: string) => { returnError = { message: msg } },
  }
}

describe("GET /api/potentials", () => {
  beforeEach(() => {
    vi.resetModules()
  })

  it("returns 200 with results for text search query 'Zr' (NFM-1367)", async () => {
    const mock = createMockSupabaseQueryBuilder()
    vi.doMock("@/lib/supabase", () => ({
      supabase: {
        from: vi.fn(() => mock.builder),
      },
    }))

    const url = new URL("http://localhost/api/potentials?q=Zr&page=1&limit=20&sort=updated")
    const { GET } = await import("./route")
    const response = await GET({ url } as Request)

    expect(response.status).toBe(200)
    const body = await response.json()
    expect(body.potentials).toBeDefined()
    expect(body.total).toBeGreaterThanOrEqual(0)

    // Must NOT call textSearch (the broken path)
    expect(mock.getChain()).not.toContain("textSearch:search_vector")

    // Must use ILIKE filter instead
    const hasIlike = mock.getChain().some((c: string) => c.startsWith("or:"))
    expect(hasIlike).toBe(true)
  })

  it("returns 200 with empty results for query matching nothing", async () => {
    const mock = createMockSupabaseQueryBuilder()
    mock.setData([])
    vi.doMock("@/lib/supabase", () => ({
      supabase: {
        from: vi.fn(() => mock.builder),
      },
    }))

    const url = new URL("http://localhost/api/potentials?q=ZZZZNONEXISTENT&page=1&limit=20")
    const { GET } = await import("./route")
    const response = await GET({ url } as Request)

    expect(response.status).toBe(200)
    const body = await response.json()
    expect(body.potentials).toEqual([])
    expect(body.total).toBe(0)
  })

  it("returns 200 without search filter when no query param", async () => {
    const mock = createMockSupabaseQueryBuilder()
    vi.doMock("@/lib/supabase", () => ({
      supabase: {
        from: vi.fn(() => mock.builder),
      },
    }))

    const url = new URL("http://localhost/api/potentials?page=1&limit=20")
    const { GET } = await import("./route")
    const response = await GET({ url } as Request)

    expect(response.status).toBe(200)
    // Should not apply any text filter when no query
    const hasIlike = mock.getChain().some((c: string) => c.startsWith("or:"))
    expect(hasIlike).toBe(false)
  })

  it("returns 200 for empty string query (edge case)", async () => {
    const mock = createMockSupabaseQueryBuilder()
    vi.doMock("@/lib/supabase", () => ({
      supabase: {
        from: vi.fn(() => mock.builder),
      },
    }))

    const url = new URL("http://localhost/api/potentials?q=&page=1&limit=20")
    const { GET } = await import("./route")
    const response = await GET({ url } as Request)

    expect(response.status).toBe(200)
    const body = await response.json()
    expect(body.total).toBeGreaterThanOrEqual(0)
  })

  it("returns 500 when Supabase query fails (preserves error logging)", async () => {
    const mock = createMockSupabaseQueryBuilder()
    mock.setError("relation \"potentials\" does not exist")
    vi.doMock("@/lib/supabase", () => ({
      supabase: {
        from: vi.fn(() => mock.builder),
      },
    }))

    const url = new URL("http://localhost/api/potentials?q=Zr&page=1&limit=20")
    const { GET } = await import("./route")
    const response = await GET({ url } as Request)

    expect(response.status).toBe(500)
    const body = await response.json()
    expect(body.error).toBeDefined()
  })

  it("sanitizes dots from queries to prevent PostgREST .or() filter breakage", async () => {
    const mock = createMockSupabaseQueryBuilder()
    vi.doMock("@/lib/supabase", () => ({
      supabase: {
        from: vi.fn(() => mock.builder),
      },
    }))

    // "0.5" contains dots that break PostgREST column.operator.value parsing
    const url = new URL("http://localhost/api/potentials?q=0.5&page=1&limit=20")
    const { GET } = await import("./route")
    const response = await GET({ url } as Request)

    expect(response.status).toBe(200)

    const orCall = mock.getChain().find((c: string) => c.startsWith("or:"))
    expect(orCall).toBeDefined()
    // Dots must be stripped — "0.5" should become "05" in the pattern
    expect(orCall).toContain("%05%")
    // Must NOT contain the unstripped dot inside filter values
    expect(orCall).not.toContain("%0.5%")
  })

  it("searches name, description, and display_name with ILIKE", async () => {
    const mock = createMockSupabaseQueryBuilder()
    vi.doMock("@/lib/supabase", () => ({
      supabase: {
        from: vi.fn(() => mock.builder),
      },
    }))

    const url = new URL("http://localhost/api/potentials?q=Zr&page=1&limit=20")
    const { GET } = await import("./route")
    await GET({ url } as Request)

    const orCall = mock.getChain().find((c: string) => c.startsWith("or:"))
    expect(orCall).toBeDefined()
    // Should search across name, description, and display_name
    expect(orCall).toContain("name.ilike")
    expect(orCall).toContain("description.ilike")
    expect(orCall).toContain("display_name.ilike")
  })
})
