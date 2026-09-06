/**
 * NFM-4311 (BUG-30) — browse list failure state.
 *
 * Failure and empty are separate semantics: a failed load renders an
 * alert with a manual 重试 button (auto-retry already happens once in
 * the data layer); an empty result renders the neutral 暂无数据 empty
 * state. The two presentations must be visually distinct.
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


vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

vi.mock("@/lib/potentials-api", () => ({
  listPotentials: (...args: unknown[]) => mockListPotentials(...args),
}))

import { BrowseView } from "../BrowseView"

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
  limit: 12,
  total_pages: 1,
}

function renderBrowse() {
  return render(<BrowseView />)
}

describe("BrowseView failure vs empty state (NFM-4311)", () => {
  beforeEach(() => {
    mockListPotentials.mockReset()
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      // /api/stats — envelope contract consumed by useElementOptions (NFM-4310)
      json: async () => ({ success: true, data: { elements: ["U", "Mo"] } }),
    })
  })

  it("renders an alert with a retry button when the list load fails", async () => {
    mockListPotentials.mockRejectedValue(new Error("Failed to fetch"))
    renderBrowse()
    const alert = await screen.findByRole("alert")
    expect(alert).toBeInTheDocument()
    expect(alert.textContent).toContain("加载失败")
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument()
    // Failure state must NOT be presented as the neutral empty state.
    expect(screen.queryByText("暂无势函数数据")).not.toBeInTheDocument()
  })

  it("manual retry re-invokes the load and recovers", async () => {
    mockListPotentials.mockRejectedValueOnce(new Error("Failed to fetch"))
    mockListPotentials.mockResolvedValue(ONE_ROW)
    renderBrowse()
    await screen.findByRole("alert")
    fireEvent.click(screen.getByRole("button", { name: "重试" }))
    await waitFor(() => {
      expect(screen.getByText("EAM_U_Zhou_2004")).toBeInTheDocument()
    })
    expect(mockListPotentials).toHaveBeenCalledTimes(2)
  })

  it("renders the neutral empty state (no alert) for zero results", async () => {
    mockListPotentials.mockResolvedValue({
      potentials: [],
      total: 0,
      page: 1,
      limit: 12,
      total_pages: 1,
    })
    renderBrowse()
    await screen.findByText("暂无势函数数据")
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument()
  })
})
