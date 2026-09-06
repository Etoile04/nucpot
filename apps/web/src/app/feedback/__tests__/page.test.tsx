import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"

vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => ({ user: null, loading: false }),
}))

/**
 * Client-side length caps on the /feedback form (NFM-4384, extracted from
 * PR #1209).
 *
 * The backend contract (`FeedbackCreate` in apps/api/src/nfm_db/schemas/
 * feedback.py) enforces title ≤ 100 and description ≤ 2000; the feedback
 * modal already caps its inputs at the same limits. Without matching caps
 * here, an over-limit submission round-trips to a 422 instead of being
 * prevented client-side.
 */
import FeedbackPage from "../page"

describe("feedback page length caps (NFM-4384)", () => {
  it("caps the title input at 100 characters", () => {
    render(<FeedbackPage />)
    const title = screen.getByPlaceholderText("简要描述您的反馈") as HTMLInputElement
    expect(title.maxLength).toBe(100)
  })

  it("caps the description textarea at 2000 characters", () => {
    render(<FeedbackPage />)
    const description = screen.getByPlaceholderText("请提供更多细节…") as HTMLTextAreaElement
    expect(description.maxLength).toBe(2000)
  })
})
