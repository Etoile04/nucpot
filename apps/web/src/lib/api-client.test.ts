import { describe, it, expect } from "vitest"

describe("smoke test", () => {
  it("runs vitest successfully", () => {
    expect(1 + 1).toBe(2)
  })

  it("supports Chinese text in assertions", () => {
    const title = "核燃料与材料物性数据库"
    expect(title).toContain("核燃料")
  })
})
