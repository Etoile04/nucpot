/**
 * NFM-4311 (BUG-30) — search view failure state.
 *
 * Same contract as the browse view: failure renders an alert + manual
 * 重试 button; empty results render the neutral 未找到 empty state.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
// @vitest-environment jsdom
import type React from "react"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"

const mockListPotentials = vi.fn()

vi.mock("next/link", () => {
  const MockLink = ({
    href,
    children,
  }: {
    href: string
    children: React.ReactNode
  }) => <a href={href}>{children}</a>
  return { default: MockLink, Link: MockLink }
})


vi.mock("@/lib/potentials-api", () => ({
  listPotentials: (...args: unknown[]) => mockListPotentials(...args),
}))

import { SearchView } from "../SearchView"

const ONE_ROW = {
  potentials: [
    {
      id: "id-1",
      name: "EAM_U_Zhou_2004",
      type: "EAM",
      elements: ["U"],
      description: "",
      version: "1.0",
      tags: [],
    },
  ],
  total: 1,
  page: 1,
  limit: 24,
  total_pages: 1,
}

describe("SearchView failure vs empty state (NFM-4311)", () => {
  beforeEach(() => {
    mockListPotentials.mockReset()
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      // /api/stats — envelope contract consumed by useElementOptions (NFM-4310)
      json: async () => ({ success: true, data: { elements: ["U", "Mo"] } }),
    })
  })

  it("renders an alert with a retry button when the search fails", async () => {
    mockListPotentials.mockRejectedValue(new Error("Failed to fetch"))
    render(<SearchView />)
    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("搜索失败")
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument()
    expect(screen.queryByText("未找到匹配的势函数")).not.toBeInTheDocument()
  })

  it("manual retry re-runs the search and recovers", async () => {
    mockListPotentials.mockRejectedValueOnce(new Error("Failed to fetch"))
    mockListPotentials.mockResolvedValue(ONE_ROW)
    render(<SearchView />)
    await screen.findByRole("alert")
    fireEvent.click(screen.getByRole("button", { name: "重试" }))
    await waitFor(() => {
      expect(screen.getByText("EAM_U_Zhou_2004")).toBeInTheDocument()
    })
  })

  it("renders the neutral empty state (no alert) for zero hits", async () => {
    mockListPotentials.mockResolvedValue({
      potentials: [],
      total: 0,
      page: 1,
      limit: 24,
      total_pages: 1,
    })
    render(<SearchView />)
    await screen.findByText("未找到匹配的势函数")
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })
})
