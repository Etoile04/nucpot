import { describe, it, expect } from "vitest"
import { extractHeadings, slugifyHeadingText } from "./headings"

describe("slugifyHeadingText", () => {
  it("lower-cases ASCII letters", () => {
    expect(slugifyHeadingText("Hello World")).toBe("hello-world")
  })

  it("collapses runs of whitespace and punctuation into a single hyphen", () => {
    expect(slugifyHeadingText("API  Reference —  Overview!!")).toBe(
      "api-reference-overview"
    )
  })

  it("preserves CJK characters (Unicode letters)", () => {
    expect(slugifyHeadingText("技术选型")).toBe("技术选型")
  })

  it("preserves mixed CJK + ASCII + digits", () => {
    expect(slugifyHeadingText("后端技术栈 v2")).toBe("后端技术栈-v2")
  })

  it("trims leading and trailing hyphens", () => {
    expect(slugifyHeadingText("!!! punctuation-only !!!")).toBe(
      "punctuation-only"
    )
  })

  it("returns an empty string when the input has no letters or digits", () => {
    expect(slugifyHeadingText("!!! ???")).toBe("")
  })
})

describe("extractHeadings", () => {
  it("returns headings with level, text, and slugified id", () => {
    const md = ["## 技术选型", "", "Some body.", "", "### 后端技术栈"].join("\n")

    expect(extractHeadings(md)).toEqual([
      { id: "技术选型", text: "技术选型", level: 2 },
      { id: "后端技术栈", text: "后端技术栈", level: 3 },
    ])
  })

  it("keeps headings in source order and respects level 1..6", () => {
    const md = [
      "# 一级标题",
      "## 二级 A",
      "### 三级 A",
      "#### 四级 A",
      "##### 五级 A",
      "###### 六级 A",
      "####### invalid",
    ].join("\n")

    const headings = extractHeadings(md)
    expect(headings.map((h) => h.level)).toEqual([1, 2, 3, 4, 5, 6])
    expect(headings.map((h) => h.text)).toEqual([
      "一级标题",
      "二级 A",
      "三级 A",
      "四级 A",
      "五级 A",
      "六级 A",
    ])
    // Depth-7 markers aren't valid headings and should be skipped.
    expect(headings.find((h) => h.text.includes("invalid"))).toBeUndefined()
  })

  it("ignores markdown inside fenced code blocks", () => {
    const md = [
      "## Real heading",
      "",
      "```md",
      "## Not a heading",
      "```",
      "",
      "## Another real",
    ].join("\n")

    const headings = extractHeadings(md)
    expect(headings.map((h) => h.text)).toEqual([
      "Real heading",
      "Another real",
    ])
  })

  it("ignores markdown inside tilde-fenced code blocks", () => {
    const md = [
      "## Real heading",
      "",
      "~~~",
      "## Not a heading",
      "~~~",
      "",
      "## After fence",
    ].join("\n")

    const headings = extractHeadings(md)
    expect(headings.map((h) => h.text)).toEqual(["Real heading", "After fence"])
  })

  it("returns an empty list for markdown with no headings", () => {
    expect(extractHeadings("just a paragraph\n\nwith two lines")).toEqual([])
  })

  it("strips trailing closing hashes (ATX-style # heading #)", () => {
    const md = ["## Quick start ##", ""].join("\n")
    expect(extractHeadings(md)).toEqual([
      { id: "quick-start", text: "Quick start", level: 2 },
    ])
  })

  it("reuses identical headings with the same id (deterministic)", () => {
    const md = ["## 概述", "## 概述"].join("\n")
    expect(extractHeadings(md)).toEqual([
      { id: "概述", text: "概述", level: 2 },
      { id: "概述", text: "概述", level: 2 },
    ])
  })
})
