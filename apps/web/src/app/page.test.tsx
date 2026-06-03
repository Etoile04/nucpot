import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import HomePage from "@/app/page"

describe("HomePage", () => {
  it("renders the main heading", () => {
    render(<HomePage />)
    expect(
      screen.getByRole("heading", { level: 1 })
    ).toHaveTextContent("核燃料与材料物性数据库")
  })

  it("renders the description paragraph", () => {
    render(<HomePage />)
    expect(
      screen.getByText("可持续共享的核燃料与材料物性数据库平台")
    ).toBeInTheDocument()
  })
})
