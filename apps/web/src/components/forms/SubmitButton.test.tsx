/**
 * SubmitButton tests (NFM-4456 follow-up to NFM-4477 §1.2 / §1.3).
 *
 * Asserts the visible loading + success states and the focus ring so the
 * component never regresses to a browser-default grey button.
 */
import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { SubmitButton } from "./SubmitButton"

describe("SubmitButton", () => {
  it("renders idle children with default skin", () => {
    render(<SubmitButton status="idle">保存</SubmitButton>)
    const btn = screen.getByRole("button", { name: "保存" })
    expect(btn).not.toBeDisabled()
    expect(btn.getAttribute("aria-busy")).toBe("false")
    expect(btn.className).toContain("bg-[var(--btn-primary-bg)]")
    expect(btn.className).toContain("focus-visible:ring-2")
    expect(screen.queryByTestId("submit-spinner")).toBeNull()
  })

  it("disables + swaps label + shows spinner while submitting", () => {
    render(
      <SubmitButton status="submitting" loadingLabel="保存中…">
        保存
      </SubmitButton>,
    )
    const btn = screen.getByRole("button", { name: "保存中…" })
    expect(btn).toBeDisabled()
    expect(btn.getAttribute("aria-busy")).toBe("true")
    expect(screen.getByTestId("submit-spinner")).toBeTruthy()
  })

  it("uses children as label when loadingLabel is omitted", () => {
    render(<SubmitButton status="submitting">保存</SubmitButton>)
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled()
    expect(screen.getByTestId("submit-spinner")).toBeTruthy()
  })

  it("locks the button and renders success skin when disableOnSuccess", () => {
    render(
      <SubmitButton status="success" disableOnSuccess>
        保存
      </SubmitButton>,
    )
    const btn = screen.getByRole("button", { name: "保存" })
    expect(btn).toBeDisabled()
    expect(btn.className).toContain("bg-[var(--onto-accent-success)]")
    expect(btn.textContent).toContain("✓")
    expect(screen.queryByTestId("submit-spinner")).toBeNull()
  })

  it("does not lock on success when disableOnSuccess is false", () => {
    render(<SubmitButton status="success">保存</SubmitButton>)
    expect(screen.getByRole("button", { name: "保存" })).not.toBeDisabled()
  })

  it("respects caller-provided disabled", () => {
    render(
      <SubmitButton status="idle" disabled>
        保存
      </SubmitButton>,
    )
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled()
  })

  it("merges caller-provided className on top of the default skin", () => {
    render(
      <SubmitButton status="idle" className="bg-[#1890ff]">
        保存
      </SubmitButton>,
    )
    const btn = screen.getByRole("button", { name: "保存" })
    expect(btn.className).toContain("bg-[var(--btn-primary-bg)]")
    expect(btn.className).toContain("bg-[#1890ff]")
  })
})
