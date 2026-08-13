/**
 * Component tests for the Hub Admin UI (NFM-2023).
 *
 * AC-2: node list renders registered nodes with live-status badges.
 * AC-4: register form validates input and calls the Hub API.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import type { ResourceNode } from "@/lib/admin/hub-types"

const listHubNodes = vi.fn()
const registerHubNode = vi.fn()
const getHubNode = vi.fn()

vi.mock("@/lib/admin/hub-api", () => ({
  listHubNodes: (...args: unknown[]) => listHubNodes(...args),
  registerHubNode: (...args: unknown[]) => registerHubNode(...args),
  getHubNode: (...args: unknown[]) => getHubNode(...args),
  updateHubNodeStatus: vi.fn(),
  deregisterHubNode: vi.fn(),
}))

function makeNode(overrides: Partial<ResourceNode> = {}): ResourceNode {
  return {
    id: "0b7c9b1e-1111-4222-8333-444455556666",
    hub_node_id: "aaaa1111-2222-4333-8444-555566667777",
    name: "西南所-计算节点",
    node_type: "computing",
    api_endpoint: "https://node.example.org",
    public_key: null,
    status: "active",
    last_heartbeat: new Date(Date.now() - 10_000).toISOString(),
    offline_since: null,
    sync_watermark: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-30T00:00:00Z",
    ...overrides,
  }
}

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  )
}

/** Click the modal's confirm button (antd spaces 2-char CJK labels). */
function clickRegisterSubmit() {
  const button = screen
    .getAllByRole("button")
    .find((el) => el.textContent?.replace(/\s/g, "") === "注册")
  expect(button).toBeTruthy()
  fireEvent.click(button as HTMLElement)
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("HubAdminContent (AC-2)", () => {
  it("renders registered nodes with live status badges", async () => {
    listHubNodes.mockResolvedValue({
      items: [
        makeNode(),
        makeNode({
          id: "1c8d0c2f-2222-4333-9444-555566667777",
          name: "东北所-存储节点",
          node_type: "storage",
          last_heartbeat: null,
        }),
      ],
      total: 2,
      page: 1,
      per_page: 20,
      pages: 1,
    })

    const { default: HubAdminContent } = await import(
      "@/components/admin/hub/HubAdminContent"
    )
    renderWithQuery(<HubAdminContent />)

    expect(await screen.findByText("西南所-计算节点")).toBeInTheDocument()
    expect(screen.getByText("东北所-存储节点")).toBeInTheDocument()
    // Fresh heartbeat → 在线; never heartbeated → 注册中.
    expect(screen.getByText("在线")).toBeInTheDocument()
    expect(screen.getByText("注册中")).toBeInTheDocument()
  })
})

describe("RegisterNodeModal (AC-4)", () => {
  it("shows validation errors and does not call the API on empty submit", async () => {
    const { default: RegisterNodeModal } = await import(
      "@/components/admin/hub/RegisterNodeModal"
    )
    renderWithQuery(
      <RegisterNodeModal open onClose={() => {}} onRegistered={() => {}} />,
    )

    clickRegisterSubmit()

    expect(await screen.findByText("请输入中心节点 ID")).toBeInTheDocument()
    expect(screen.getByText("请输入节点名称")).toBeInTheDocument()
    expect(screen.getByText("请选择节点类型")).toBeInTheDocument()
    expect(screen.getByText("请输入节点 API 地址")).toBeInTheDocument()
    expect(registerHubNode).not.toHaveBeenCalled()
  })

  it("rejects a malformed hub_node_id and URL", async () => {
    const { default: RegisterNodeModal } = await import(
      "@/components/admin/hub/RegisterNodeModal"
    )
    renderWithQuery(
      <RegisterNodeModal open onClose={() => {}} onRegistered={() => {}} />,
    )

    fireEvent.change(screen.getByLabelText("所属中心节点 ID"), {
      target: { value: "not-a-uuid" },
    })
    fireEvent.change(screen.getByLabelText("API 地址"), {
      target: { value: "not-a-url" },
    })
    clickRegisterSubmit()

    expect(await screen.findByText("必须是合法的 UUID")).toBeInTheDocument()
    expect(screen.getByText("必须是合法的 URL")).toBeInTheDocument()
    expect(registerHubNode).not.toHaveBeenCalled()
  })

  it("submits valid input to the Hub API and reports success", async () => {
    const created = makeNode()
    registerHubNode.mockResolvedValue(created)
    const onRegistered = vi.fn()

    const { default: RegisterNodeModal } = await import(
      "@/components/admin/hub/RegisterNodeModal"
    )
    renderWithQuery(
      <RegisterNodeModal open onClose={() => {}} onRegistered={onRegistered} />,
    )

    fireEvent.change(screen.getByLabelText("所属中心节点 ID"), {
      target: { value: created.hub_node_id },
    })
    fireEvent.change(screen.getByLabelText("节点名称"), {
      target: { value: created.name },
    })
    // Open the antd Select dropdown and pick the computing option.
    fireEvent.mouseDown(screen.getByLabelText("节点类型"))
    fireEvent.click(await screen.findByText("计算节点 (computing)"))
    fireEvent.change(screen.getByLabelText("API 地址"), {
      target: { value: created.api_endpoint },
    })
    clickRegisterSubmit()

    await waitFor(() => {
      expect(registerHubNode).toHaveBeenCalledWith(
        expect.objectContaining({
          hub_node_id: created.hub_node_id,
          name: created.name,
          node_type: "computing",
          api_endpoint: created.api_endpoint,
        }),
      )
    })
    await waitFor(() => expect(onRegistered).toHaveBeenCalledWith(created))
  })
})
