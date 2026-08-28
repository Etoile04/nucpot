/**
 * Tests for OntologyDetailContent component.
 * Tests the inner component directly (not the page wrapper that uses React.use()).
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import React from "react"
import type { ReactNode } from "react"

const mockRequest = vi.fn()
vi.mock("@/lib/api-client", () => ({ request: (...args: unknown[]) => mockRequest(...args) }))

vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => ({ user: { role: "admin" }, loading: false }),
}))

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))

const DETAIL_RESPONSE = {
  success: true,
  data: {
    id: "v1", version: "1.0.0", status: "draft", description: "Test version",
    created_at: "2026-01-15T10:00:00Z", updated_at: "2026-01-15T12:00:00Z",
    changelog: "init", created_by: "admin",
    ontology_data: {
      entity_types: [{ name: "mat.alloy", chinese_name: "合金", english_name: "alloy", domain: "materials", description: "An alloy" }],
      relation_types: [{ name: "has_comp", source_types: ["mat.alloy"], target_types: ["mat.element"], description: "composition relation" }],
    },
  },
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe("OntologyDetailContent", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockRequest.mockResolvedValue(DETAIL_RESPONSE)
  })

  it("renders version heading", async () => {
    const { OntologyDetailContent } = await import("../[typeId]/page")
    render(<OntologyDetailContent typeId="v1" />, { wrapper })
    await waitFor(() => expect(screen.getByText("Version 1.0.0")).toBeInTheDocument(), { timeout: 5000 })
  })

  it("renders entity types table", async () => {
    const { OntologyDetailContent } = await import("../[typeId]/page")
    render(<OntologyDetailContent typeId="v1" />, { wrapper })
    await waitFor(() => expect(screen.getByText("mat.alloy")).toBeInTheDocument(), { timeout: 5000 })
    expect(screen.getByText("合金")).toBeInTheDocument()
  })

  it("shows edit link for draft status", async () => {
    const { OntologyDetailContent } = await import("../[typeId]/page")
    render(<OntologyDetailContent typeId="v1" />, { wrapper })
    await waitFor(() => expect(screen.getByText("Edit draft")).toBeInTheDocument(), { timeout: 5000 })
  })

  it("shows error on fetch failure", async () => {
    mockRequest.mockRejectedValue(new Error("not found"))
    const { OntologyDetailContent } = await import("../[typeId]/page")
    render(<OntologyDetailContent typeId="bad" />, { wrapper })
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument(), { timeout: 5000 })
  })
})
