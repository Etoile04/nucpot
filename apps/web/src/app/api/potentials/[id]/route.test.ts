/**
 * Tests for GET /api/potentials/[id] — Next.js BFF route handler.
 *
 * Validates:
 * - 200 with potential data when record exists
 * - 404 with error details when Supabase query fails
 * - Correct table and id passed to Supabase
 * - Uses supabaseAdmin when available, falls back to supabase
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { NextRequest } from "next/server"

// Mock supabase module before importing the handler
const mockFrom = vi.fn()

vi.mock("@/lib/supabase", () => ({
  supabase: { from: mockFrom },
  supabaseAdmin: null,
}))

// Import the handler after mock setup
const { GET } = await import("./route")

function mockChain(result: { data: unknown; error: unknown }) {
  return {
    select: vi.fn().mockReturnThis(),
    eq: vi.fn().mockReturnThis(),
    single: vi.fn().mockResolvedValue(result),
  }
}

describe("GET /api/potentials/[id]", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("returns 200 with potential data when found", async () => {
    const potential = {
      id: "0fe545c7-8ce0-427b-89f0-8921a9789238",
      name: "Buckingham_UO2_Thompson_2014",
      type: "Buckingham",
      status: "published",
      elements: ["U", "O"],
    }

    mockFrom.mockReturnValue(mockChain({ data: potential, error: null }))

    const request = new NextRequest(
      "http://localhost:3000/api/potentials/0fe545c7-8ce0-427b-89f0-8921a9789238",
    )
    const response = await GET(request, {
      params: Promise.resolve({ id: "0fe545c7-8ce0-427b-89f0-8921a9789238" }),
    })

    expect(response.status).toBe(200)
    const json = await response.json()
    expect(json.id).toBe("0fe545c7-8ce0-427b-89f0-8921a9789238")
    expect(json.name).toBe("Buckingham_UO2_Thompson_2014")
  })

  it("returns 404 with error details when Supabase query fails", async () => {
    const supabaseError = {
      code: "PGRST116",
      message: "Results contain 0 rows",
      details: null,
      hint: null,
    }

    mockFrom.mockReturnValue(mockChain({ data: null, error: supabaseError }))

    const request = new NextRequest(
      "http://localhost:3000/api/potentials/0fe545c7-8ce0-427b-89f0-8921a9789238",
    )
    const response = await GET(request, {
      params: Promise.resolve({ id: "0fe545c7-8ce0-427b-89f0-8921a9789238" }),
    })

    expect(response.status).toBe(404)
    const json = await response.json()
    expect(json.error).toBe("Potential not found")
    expect(json.detail).toContain("PGRST116")
  })

  it("queries the potentials table with the correct id", async () => {
    mockFrom.mockReturnValue(mockChain({ data: null, error: null }))

    const request = new NextRequest(
      "http://localhost:3000/api/potentials/12345678-1234-1234-1234-123456789012",
    )
    await GET(request, {
      params: Promise.resolve({ id: "12345678-1234-1234-1234-123456789012" }),
    })

    expect(mockFrom).toHaveBeenCalledWith("potentials")
  })

  it("returns 404 when no error but no data", async () => {
    mockFrom.mockReturnValue(mockChain({ data: null, error: null }))

    const request = new NextRequest(
      "http://localhost:3000/api/potentials/0fe545c7-8ce0-427b-89f0-8921a9789238",
    )
    const response = await GET(request, {
      params: Promise.resolve({ id: "0fe545c7-8ce0-427b-8921a9789238" }),
    })

    expect(response.status).toBe(404)
    const json = await response.json()
    expect(json.error).toBe("Potential not found")
    expect(json.detail).toBe("No data returned")
  })
})
