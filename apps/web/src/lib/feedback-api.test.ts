import { describe, it, expect, vi, beforeEach } from "vitest"

describe("submitFeedback (NFM-4380)", () => {
  beforeEach(() => {
    global.fetch = vi.fn()
  })

  const payload = {
    feedback_type: "bug_report",
    title: "标题",
    description: "描述",
    page_url: "https://nucpot.example.com/",
  }

  it("posts to the /api/feedback alias (nginx proxies verbatim to the backend)", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, data: { id: "fb-1" } }),
    })
    const { submitFeedback } = await import("./feedback-api")
    await submitFeedback(payload)

    expect(global.fetch).toHaveBeenCalledWith(
      "/api/feedback",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
    )
  })

  it("unwraps the ApiResponse envelope and returns data", async () => {
    const result = {
      id: "fb-1",
      feedback_type: "bug_report",
      priority: "medium",
      status: "open",
      created_at: "2026-09-07T00:00:00Z",
    }
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, data: result, error: null }),
    })
    const { submitFeedback } = await import("./feedback-api")

    await expect(submitFeedback(payload)).resolves.toEqual(result)
  })

  it("throws the envelope error when success is false", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({ success: false, data: null, error: "服务暂时不可用" }),
    })
    const { submitFeedback } = await import("./feedback-api")

    await expect(submitFeedback(payload)).rejects.toThrow("服务暂时不可用")
  })

  it("surfaces the HTTP status on non-OK responses", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 429,
      json: async () => ({ detail: "rate limited" }),
    })
    const { submitFeedback } = await import("./feedback-api")

    await expect(submitFeedback(payload)).rejects.toThrow("提交失败 (429)")
  })
})
