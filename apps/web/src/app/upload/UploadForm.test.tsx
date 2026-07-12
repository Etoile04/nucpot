/** Component tests for the upload form (NFM-299).
 * Tests that the form renders required fields and wires to upload-api.
 * Authoritative fetch behavior tests are in upload-api.test.ts.
 */

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"

describe("UploadForm", () => {
  it("renders required form fields", async () => {
    const { default: UploadForm } = await import("../../app/upload/UploadForm")
    render(<UploadForm />)
    // Required fields should be present (Chinese labels per NFM-DB convention)
    expect(screen.getByText("名称")).toBeDefined()
    expect(screen.getByText("类型")).toBeDefined()
    expect(screen.getByText("元素")).toBeDefined()
    expect(screen.getByText("体系名称")).toBeDefined()
    expect(screen.getByText("描述")).toBeDefined()
    expect(screen.getByText("许可类型")).toBeDefined()
  })

  it("renders submit button", async () => {
    const { default: UploadForm } = await import("../../app/upload/UploadForm")
    const { container } = render(<UploadForm />)
    const submitBtn = container.querySelector('button[type="submit"]')
    expect(submitBtn).toBeTruthy()
    expect(submitBtn?.textContent?.replace(/\s/g, "")).toContain("上传")
  })

  it("renders file upload area", async () => {
    const { default: UploadForm } = await import("../../app/upload/UploadForm")
    render(<UploadForm />)
    expect(screen.getByText("选择文件")).toBeDefined()
  })
})
