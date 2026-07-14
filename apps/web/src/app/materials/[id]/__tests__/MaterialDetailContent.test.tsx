import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MaterialDetailContent } from "../MaterialDetailContent"

// ── API mock (vi.hoisted avoids TDZ in hoisted vi.mock factory) ──────

const { mockRequest } = vi.hoisted(() => ({
  mockRequest: vi.fn(),
}))

vi.mock("@/lib/api-client", () => ({
  request: (...args: unknown[]) => mockRequest(...args),
}))

// ── Test data ──────────────────────────────────────────────────────────

const MATERIAL_DETAIL = {
  id: "mat-001",
  name: "二氧化锆",
  formula: "ZrO2",
  crystal_structure: "萤石结构",
  description: "核燃料包壳材料",
  is_active: true,
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-06-15T00:00:00Z",
  aliases: [
    { alias_name: "Zirconia", alias_type: "英文名", source: "IUPAC" },
    { alias_name: "ZrO2", alias_type: "化学式", source: "CAS" },
  ],
  composition: [
    { element: "Zr", fraction: 0.67 },
    { element: "O", fraction: 0.33 },
  ],
}

function mockSuccessResponse() {
  mockRequest.mockResolvedValue({ success: true, data: MATERIAL_DETAIL })
}

// ── Tests ─────────────────────────────────────────────────────────────

describe("MaterialDetailContent", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockSuccessResponse()
  })

  it("shows loading spinner initially", () => {
    mockRequest.mockReturnValue(new Promise(() => {}))

    const { container } = render(<MaterialDetailContent materialId="mat-001" />)

    expect(container.querySelector("[aria-busy]")).toBeTruthy()
  })

  it("renders material name and formula after load", async () => {
    render(<MaterialDetailContent materialId="mat-001" />)

    await waitFor(() => {
      // Title renders as h2 with the material name
      const headings = screen.getAllByText("二氧化锆")
      expect(headings.length).toBeGreaterThanOrEqual(1)
    })
    expect(screen.getByText("化学式：ZrO2")).toBeInTheDocument()
  })

  it("renders basic info descriptions", async () => {
    render(<MaterialDetailContent materialId="mat-001" />)

    // Wait for the descriptions table to appear (proves data loaded)
    await waitFor(() => {
      expect(screen.getByText("萤石结构")).toBeInTheDocument()
    })

    expect(screen.getByText("活跃")).toBeInTheDocument()
    expect(screen.getByText("核燃料包壳材料")).toBeInTheDocument()
  })

  it("renders aliases table", async () => {
    render(<MaterialDetailContent materialId="mat-001" />)

    // Wait for an alias row to appear (proves table loaded)
    await waitFor(() => {
      expect(screen.getByText("Zirconia")).toBeInTheDocument()
    })

    expect(screen.getByText("英文名")).toBeInTheDocument()
    expect(screen.getByText("IUPAC")).toBeInTheDocument()
  })

  it("renders composition table with percentage formatting", async () => {
    render(<MaterialDetailContent materialId="mat-001" />)

    await waitFor(() => {
      expect(screen.getByText("组成")).toBeInTheDocument()
    })

    expect(screen.getByText("Zr")).toBeInTheDocument()
    expect(screen.getByText("67.00%")).toBeInTheDocument()
    expect(screen.getByText("O")).toBeInTheDocument()
    expect(screen.getByText("33.00%")).toBeInTheDocument()
  })

  it("renders navigation buttons to graph and properties pages", async () => {
    render(<MaterialDetailContent materialId="mat-001" />)

    // Wait for data to load (unique field in Descriptions)
    await waitFor(() => {
      expect(screen.getByText("萤石结构")).toBeInTheDocument()
    })

    const graphLink = screen.getByText("查看知识图谱").closest("a")
    expect(graphLink).toHaveAttribute("href", "/materials/mat-001/graph")

    const propsLink = screen.getByText("查看属性").closest("a")
    expect(propsLink).toHaveAttribute("href", "/materials/mat-001/properties")
  })

  it("renders back link", async () => {
    render(<MaterialDetailContent materialId="mat-001" />)

    await waitFor(() => {
      const link = screen.getByText("返回浏览").closest("a")
      expect(link).toHaveAttribute("href", "/browse")
    })
  })

  it("renders error state with retry button on failure", async () => {
    mockRequest.mockRejectedValue(new Error("网络超时"))

    const { container } = render(<MaterialDetailContent materialId="mat-001" />)

    await waitFor(() => {
      expect(screen.getByText("加载失败")).toBeInTheDocument()
    })
    expect(screen.getByText("网络超时")).toBeInTheDocument()

    // AntD Alert action button may not be accessible in jsdom;
    // query the DOM directly for the button element.
    const retryBtn = container.querySelector(".ant-alert-action button")
    expect(retryBtn).toBeTruthy()
    expect(retryBtn?.textContent?.replace(/\s/g, "")).toContain("重试")
  })

  it("renders dashes for null optional fields", async () => {
    mockRequest.mockResolvedValue({
      success: true,
      data: { ...MATERIAL_DETAIL, formula: null, crystal_structure: null, description: null },
    })

    render(<MaterialDetailContent materialId="mat-001" />)

    // Wait for Descriptions to render — "名称" label appears in table
    await waitFor(() => {
      expect(screen.getByText("名称")).toBeInTheDocument()
    })

    const dashes = screen.getAllByText("-")
    expect(dashes.length).toBeGreaterThanOrEqual(2)
  })

  it("shows inactive status for is_active=false", async () => {
    mockRequest.mockResolvedValue({
      success: true,
      data: { ...MATERIAL_DETAIL, is_active: false },
    })

    render(<MaterialDetailContent materialId="mat-001" />)

    await waitFor(() => {
      expect(screen.getByText("停用")).toBeInTheDocument()
    })
  })

  it("retries fetch when retry button is clicked", async () => {
    mockRequest
      .mockRejectedValueOnce(new Error("首次失败"))
      .mockResolvedValueOnce({ success: true, data: MATERIAL_DETAIL })

    const { container } = render(<MaterialDetailContent materialId="mat-001" />)

    await waitFor(() => {
      expect(screen.getByText("加载失败")).toBeInTheDocument()
    })

    const retryBtn = container.querySelector(".ant-alert-action button")
    expect(retryBtn).toBeTruthy()
    fireEvent.click(retryBtn!)

    await waitFor(() => {
      expect(screen.getByText("萤石结构")).toBeInTheDocument()
    })
    expect(mockRequest).toHaveBeenCalledTimes(2)
  })
})
