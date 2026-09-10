/**
 * NFM-4539 / RAG-C: anonymous-open frontend surface + §3.2 fallback badge
 * + UAT-6 honest empty-state copy.
 *
 *   - /search no longer shows the "需登录" copy.
 *   - SemanticSearchResults renders the §3.2 fallback badge when
 *     ``fallback.used=true``.
 *   - An empty answer after a real query renders the UAT-6 honest
 *     "知识库暂未覆盖该问题" copy, not a generic Empty.
 *   - The RAG_LOGIN_REQUIRED_MESSAGE flow is no longer triggered by an
 *     open /query call (NFM-4539 RAG-C de-wall).
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, act } from "@testing-library/react"
import { RagSearchView } from "./RagSearchView"

// Mock ragApi module
vi.mock("@/lib/rag-api", () => ({
  ragApi: {
    query: vi.fn(),
  },
  RAG_LOGIN_REQUIRED_MESSAGE: "语义检索需要登录，请先登录后再试。",
  isAuthExpiredError: (err: unknown) =>
    typeof err === "object" &&
    err !== null &&
    typeof (err as { message?: unknown }).message === "string" &&
    (err as { message: string }).message.includes("认证已过期"),
}))

import { ragApi } from "@/lib/rag-api"
import type { RagQueryResponse } from "@/lib/rag-api"
const mockedQuery = vi.mocked(ragApi.query)

describe("RagSearchView — RAG-C anonymous-open", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("does NOT advertise a login requirement in the hint text (RAG-C)", () => {
    render(<RagSearchView />)
    expect(screen.queryByText(/需登录/)).not.toBeInTheDocument()
  })

  it("renders the §3.2 fallback badge when fallback.used=true", async () => {
    mockedQuery.mockResolvedValueOnce({
      answer: "",
      citations: [],
      conversationId: "conv-fallback",
      fallback: { used: true, kind: "iliKE", originalError: "sidecar timeout" },
    })

    render(<RagSearchView />)
    const input = screen.getByPlaceholderText("描述您想了解的核材料属性或关系...")
    await act(async () => {
      fireEvent.change(input, { target: { value: "UO2 conductivity" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByText("语义检索"))
    })

    // AC-4 / §3.2: badge must be visible
    expect(screen.getByTestId("rag-fallback-badge")).toBeInTheDocument()
    expect(screen.getByText("语义检索超时,已回退文本检索")).toBeInTheDocument()
  })

  it("renders UAT-6 honest empty-state copy when answer is empty (RAG-C)", async () => {
    mockedQuery.mockResolvedValueOnce({
      answer: "",
      citations: [],
      conversationId: "conv-empty",
      fallback: { used: false, kind: null, originalError: null },
    })

    render(<RagSearchView />)
    const input = screen.getByPlaceholderText("描述您想了解的核材料属性或关系...")
    await act(async () => {
      fireEvent.change(input, { target: { value: "完全未收录的提问" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByText("语义检索"))
    })

    // UAT-6: honest neutral copy, not a generic Empty placeholder.
    expect(screen.getByTestId("rag-empty-coverage")).toBeInTheDocument()
    expect(screen.getByText("知识库暂未覆盖该问题")).toBeInTheDocument()
    // The generic "请输入查询内容" copy is only shown pre-search.
    expect(screen.queryByText("请输入查询内容进行语义检索")).not.toBeInTheDocument()
  })

  it("renders normal answer when fallback.used=false and answer is non-empty", async () => {
    mockedQuery.mockResolvedValueOnce({
      answer: "UO2 密度 10.97 g/cm³",
      citations: [],
      conversationId: "conv-ok",
      fallback: { used: false, kind: null, originalError: null },
    } satisfies RagQueryResponse)

    render(<RagSearchView />)
    const input = screen.getByPlaceholderText("描述您想了解的核材料属性或关系...")
    await act(async () => {
      fireEvent.change(input, { target: { value: "UO2 密度" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByText("语义检索"))
    })

    expect(screen.getByText("UO2 密度 10.97 g/cm³")).toBeInTheDocument()
    // No fallback badge on the success path.
    expect(screen.queryByTestId("rag-fallback-badge")).not.toBeInTheDocument()
    expect(screen.queryByTestId("rag-empty-coverage")).not.toBeInTheDocument()
  })
})