/**
 * Tests for New Ontology Page.
 * Tests OntologyEditForm directly (not the page wrapper that uses React.use()).
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import React from "react"
import type { ReactNode } from "react"

const mockRequest = vi.fn()
vi.mock("@/lib/api-client", () => ({ request: (...args: unknown[]) => mockRequest(...args) }))

vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => ({ user: { role: "admin" }, loading: false }),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/admin/ontology/new",
}))

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe("NewOntologyPage (via OntologyEditForm)", () => {
  beforeEach(() => { vi.clearAllMocks() })

  it("renders new version heading", async () => {
    const { OntologyEditForm } = await import("../[typeId]/edit/page")
    render(<OntologyEditForm versionId="" />, { wrapper })
    expect(screen.getByText("New ontology version")).toBeInTheDocument()
  })

  it("does not call API in new mode", async () => {
    const { OntologyEditForm } = await import("../[typeId]/edit/page")
    render(<OntologyEditForm versionId="" />, { wrapper })
    await new Promise(r => setTimeout(r, 200))
    expect(mockRequest).not.toHaveBeenCalled()
  })

  it("renders Save draft button", async () => {
    const { OntologyEditForm } = await import("../[typeId]/edit/page")
    render(<OntologyEditForm versionId="" />, { wrapper })
    expect(screen.getByText("Save draft")).toBeInTheDocument()
  })
})
