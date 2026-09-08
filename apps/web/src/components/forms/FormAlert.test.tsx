/**
 * FormAlert smoke tests (NFM-4456).
 *
 * FormAlert is the visual treatment that every useFormSubmit consumer renders
 * its status through. This guards the public surface so consumers don't drift.
 *
 * NFM-4456 follow-up (NFM-4477 §1.1): assert token-driven surface classes
 * (no raw `bg-red-900/40` etc.) and `focus-visible` on the retry button.
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

  // NFM-4477 §1.1 — token-driven surface, not hardcoded dark-only Tailwind.
  it("uses token-driven error surface (no hardcoded bg-red-900/40)", () => {
    render(<FormAlert status="error" error="e" />)
    const node = screen.getByTestId("form-alert")
    expect(node.className).toContain("bg-[var(--alert-error-bg)]")
    expect(node.className).toContain("border-[var(--alert-error-border)]")
    expect(node.className).toContain("text-[var(--alert-error-text)]")
    expect(node.className).not.toContain("bg-red-900/40")
    expect(node.className).not.toContain("text-red-300")
  })

  it("uses token-driven success surface (no hardcoded bg-green-900/40)", () => {
    render(<FormAlert status="success" error={null} />)
    const node = screen.getByTestId("form-alert")
    expect(node.className).toContain("bg-[var(--alert-success-bg)]")
    expect(node.className).toContain("border-[var(--alert-success-border)]")
    expect(node.className).toContain("text-[var(--alert-success-text)]")
    expect(node.className).not.toContain("bg-green-900/40")
    expect(node.className).not.toContain("text-green-300")
  })

  // NFM-4477 §1.3 — focus-visible ring on retry.
  it("retry button has focus-visible ring", () => {
    render(<FormAlert status="error" error="x" onRetry={() => {}} />)
    const retry = screen.getByRole("button", { name: "重试" })
    expect(retry.className).toContain("focus-visible:ring-2")
    expect(retry.className).toContain("var(--alert-error-ring)")
  })
})
