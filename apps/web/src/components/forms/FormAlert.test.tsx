/**
 * FormAlert smoke tests (NFM-4456).
 *
 * FormAlert is the visual treatment that every useFormSubmit consumer renders
 * its status through. This guards the public surface so consumers don't drift.
 */
import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { FormAlert } from "./FormAlert"

describe("FormAlert", () => {
  it("renders nothing for idle", () => {
    const { container } = render(<FormAlert status="idle" error={null} />)
    expect(container.firstChild).toBeNull()
  })

  it("renders nothing for submitting", () => {
    const { container } = render(<FormAlert status="submitting" error={null} />)
    expect(container.firstChild).toBeNull()
  })

  it("renders error message in plain theme", () => {
    render(<FormAlert status="error" error="出错了" />)
    expect(screen.getByTestId("form-alert")).toHaveAttribute("data-status", "error")
    expect(screen.getByText(/出错了/)).toBeTruthy()
  })

  it("renders success message in plain theme", () => {
    render(<FormAlert status="success" error={null} successMessage="完成" />)
    expect(screen.getByTestId("form-alert")).toHaveAttribute("data-status", "success")
    expect(screen.getByText(/完成/)).toBeTruthy()
  })

  it("renders retry button when onRetry is provided", () => {
    render(<FormAlert status="error" error="x" onRetry={() => {}} retryLabel="重新尝试" />)
    expect(screen.getByText("重新尝试")).toBeTruthy()
  })
})
