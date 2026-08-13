/** Component tests for the upload form (NFM-299).
 * Tests that the form renders required fields and wires to upload-api.
 * Authoritative fetch behavior tests are in upload-api.test.ts.
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { renderTrustedHtmlPreview } from "../../app/upload/UploadForm"

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

describe("renderTrustedHtmlPreview (NFM-1344 XSS mitigation)", () => {
  function makeFakeWindow() {
    const doc = document.implementation.createHTMLDocument("preview")
    const writeSpy = vi.fn()
    // Guard: the whole point of this fix is that the legacy doc-write sink is never used.
    doc.write = writeSpy as unknown as typeof doc.write
    return { win: { document: doc } as unknown as Window, doc, writeSpy }
  }

  it("renders html via a sandboxed srcdoc iframe, never the doc-write sink", () => {
    const { win, doc, writeSpy } = makeFakeWindow()

    renderTrustedHtmlPreview(win, "<h1>授权书</h1>")

    expect(writeSpy).not.toHaveBeenCalled()
    const iframe = doc.querySelector("iframe")
    expect(iframe).not.toBeNull()
    expect(iframe?.getAttribute("srcdoc")).toBe("<h1>授权书</h1>")
  })

  it("applies a sandbox attribute of at least 'allow-same-origin'", () => {
    const { win, doc } = makeFakeWindow()

    renderTrustedHtmlPreview(win, "<p>x</p>")

    const sandbox = doc.querySelector("iframe")?.getAttribute("sandbox") ?? ""
    const tokens = sandbox.split(/\s+/).filter(Boolean)
    expect(tokens).toContain("allow-same-origin")
    // Scripts must NOT be allowed — that would reopen the XSS surface.
    expect(tokens).not.toContain("allow-scripts")
  })
})
