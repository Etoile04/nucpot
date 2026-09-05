import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, act } from "@testing-library/react"
import { App } from "antd"
import { FeedbackModal } from "./FeedbackModal"
import { submitFeedback } from "@/lib/feedback-api"

vi.mock("@/lib/feedback-api", () => ({
  FEEDBACK_TYPES: [
    { value: "bug_report", label: "Bug 报告" },
    { value: "feature_request", label: "功能建议" },
  ],
  submitFeedback: vi.fn(),
}))

const mockedSubmit = vi.mocked(submitFeedback)

function renderModal() {
  return render(
    <App>
      <FeedbackModal open onClose={() => {}} />
    </App>,
  )
}

async function fillAndSubmit(title: string, description: string) {
  fireEvent.change(screen.getByPlaceholderText("一句话概括您的问题或建议"), {
    target: { value: title },
  })
  fireEvent.change(screen.getByPlaceholderText(/请详细描述/), {
    target: { value: description },
  })
  await act(async () => {
    fireEvent.click(screen.getByText("提交反馈"))
  })
}

describe("FeedbackModal", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("blocks empty submit with field-level errors and does not call the API", async () => {
    renderModal()

    await act(async () => {
      fireEvent.click(screen.getByText("提交反馈"))
    })

    expect(mockedSubmit).not.toHaveBeenCalled()
    expect(
      (await screen.findAllByText(/请(选择问题类型|输入简要描述|输入详细描述)/))
        .length,
    ).toBeGreaterThan(0)
  })

  it("submits successfully with valid values", async () => {
    mockedSubmit.mockResolvedValueOnce({
      id: "fb-1",
      feedback_type: "bug_report",
      priority: "medium",
      status: "open",
      created_at: "2026-09-05T00:00:00Z",
    })
    renderModal()

    await fillAndSubmit("页面报错", "点击提交后页面报错")

    expect(mockedSubmit).toHaveBeenCalledTimes(1)
    expect(mockedSubmit.mock.calls[0]?.[0].title).toBe("页面报错")
  })

  it("shows an inline error with retry when submit fails", async () => {
    mockedSubmit.mockRejectedValueOnce(new Error("服务器错误"))
    renderModal()

    await fillAndSubmit("页面报错", "点击提交后页面报错")

    expect(await screen.findByText("提交失败")).toBeInTheDocument()
    expect(screen.getAllByText("服务器错误").length).toBeGreaterThan(0)
    expect(await screen.findByText(/重\s?试/)).toBeInTheDocument()

    mockedSubmit.mockResolvedValueOnce({
      id: "fb-2",
      feedback_type: "bug_report",
      priority: "medium",
      status: "open",
      created_at: "2026-09-05T00:00:00Z",
    })
    await act(async () => {
      fireEvent.click(screen.getByText(/重\s?试/))
    })

    expect(mockedSubmit).toHaveBeenCalledTimes(2)
  })
})
