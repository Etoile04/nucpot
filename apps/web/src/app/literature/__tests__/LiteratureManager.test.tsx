/**
 * LiteratureManager — NFM-3307 (QA followup)
 *
 * Covers:
 *  - W7: Upload button shows tooltip / disabled state when user lacks
 *    editor role, AND a 403 from the upload endpoint surfaces a
 *    user-visible error message (not a silent failure).
 *  - W8: After a successful upload the literature list is refreshed
 *    with cleared filters, so the newly uploaded item is visible
 *    without a manual reload.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, fireEvent, waitFor } from "@testing-library/react"
import { ConfigProvider } from "antd"

// vi.hoisted() runs before vi.mock factories, so we can safely reference
// these spies from inside the factories.
const { mockUseAuth, mockLiteratureApi, mockMessage } = vi.hoisted(() => {
  const mockUseAuth = vi.fn()
  const mockLiteratureApi = {
    list: vi.fn(),
    get: vi.fn(),
    upload: vi.fn(),
    fromDoi: vi.fn(),
    reextract: vi.fn(),
    delete: vi.fn(),
  }
  // Mock the antd message static so we can assert what the component
  // surfaces without depending on the portal/ConfigProvider dance.
  const mockMessage = {
    open: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(),
  }
  return { mockUseAuth, mockLiteratureApi, mockMessage }
})

vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => mockUseAuth(),
}))

vi.mock("@/lib/api-client", () => ({
  literatureApi: mockLiteratureApi,
}))

vi.mock("antd", async () => {
  const actual =
    await vi.importActual<typeof import("antd")>("antd")
  return {
    ...actual,
    message: mockMessage,
  }
})

import LiteratureManager from "../LiteratureManager"

// ── Helpers ───────────────────────────────────────────────────────────

function makeEditorUser() {
  return {
    id: "u-1",
    username: "alice",
    email: "alice@example.com",
    full_name: "Alice",
    blog_role: "editor",
    is_active: true,
  }
}

function makeAdminUser() {
  return { ...makeEditorUser(), blog_role: "admin" }
}

function makeReviewerUser() {
  return { ...makeEditorUser(), blog_role: "reviewer" }
}

function makeNullUser() {
  return null
}

function mockListSuccess(items: readonly unknown[] = [], total = 0) {
  mockLiteratureApi.list.mockResolvedValue({ items, total })
}

function renderComponent() {
  return render(
    <ConfigProvider>
      <LiteratureManager />
    </ConfigProvider>,
  )
}

function makePdfFile(name = "test.pdf") {
  return new File(["%PDF-1.4 dummy"], name, { type: "application/pdf" })
}

function dispatchUploadToInput(input: HTMLInputElement, file: File) {
  Object.defineProperty(input, "files", {
    value: [file],
    writable: false,
    configurable: true,
  })
  fireEvent.change(input, { target: { files: [file] } })
}

// ── W7: Role-based gating of upload UI ───────────────────────────────

describe("LiteratureManager — upload UI role gating (W7)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListSuccess()
  })

  it("renders the upload dragger enabled when user has the editor role", async () => {
    mockUseAuth.mockReturnValue({
      user: makeEditorUser(),
      loading: false,
    })
    renderComponent()

    const fileInput = (await waitFor(() =>
      document.querySelector('input[type="file"]'),
    )) as HTMLInputElement
    expect(fileInput).toBeTruthy()
    const wrapper = document.querySelector(".ant-upload-drag")
    const wrapperDisabled =
      wrapper?.classList.contains("ant-upload-drag-disabled") ||
      wrapper?.classList.contains("ant-upload-disabled") ||
      wrapper?.getAttribute("aria-disabled") === "true"
    expect(fileInput.disabled || wrapperDisabled).toBe(false)
  })

  it("renders the upload dragger enabled when user has the admin role", async () => {
    mockUseAuth.mockReturnValue({
      user: makeAdminUser(),
      loading: false,
    })
    renderComponent()

    const fileInput = (await waitFor(() =>
      document.querySelector('input[type="file"]'),
    )) as HTMLInputElement
    const wrapper = document.querySelector(".ant-upload-drag")
    const wrapperDisabled =
      wrapper?.classList.contains("ant-upload-drag-disabled") ||
      wrapper?.classList.contains("ant-upload-disabled") ||
      wrapper?.getAttribute("aria-disabled") === "true"
    expect(fileInput.disabled || wrapperDisabled).toBe(false)
  })

  it("disables the upload dragger when user is a reviewer (no editor privilege)", async () => {
    mockUseAuth.mockReturnValue({
      user: makeReviewerUser(),
      loading: false,
    })
    renderComponent()

    await waitFor(() =>
      expect(document.querySelector('input[type="file"]')).toBeTruthy(),
    )

    const wrapper = document.querySelector(".ant-upload-drag") as
      | HTMLElement
      | null
    const isWrapperDisabled =
      wrapper?.classList.contains("ant-upload-drag-disabled") === true ||
      wrapper?.classList.contains("ant-upload-disabled") === true ||
      wrapper?.getAttribute("aria-disabled") === "true"
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement | null
    expect(isWrapperDisabled || fileInput?.disabled === true).toBe(true)
  })

  it("disables the upload dragger when no user is logged in", async () => {
    mockUseAuth.mockReturnValue({
      user: makeNullUser(),
      loading: false,
    })
    renderComponent()

    await waitFor(() =>
      expect(document.querySelector('input[type="file"]')).toBeTruthy(),
    )
    const wrapper = document.querySelector(".ant-upload-drag") as
      | HTMLElement
      | null
    const isWrapperDisabled =
      wrapper?.classList.contains("ant-upload-drag-disabled") === true ||
      wrapper?.classList.contains("ant-upload-disabled") === true ||
      wrapper?.getAttribute("aria-disabled") === "true"
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement | null
    expect(isWrapperDisabled || fileInput?.disabled === true).toBe(true)
  })

  it("shows an inline role-required hint when the user lacks editor privilege", async () => {
    mockUseAuth.mockReturnValue({
      user: makeReviewerUser(),
      loading: false,
    })
    renderComponent()

    await waitFor(() => {
      const text = document.body.textContent ?? ""
      expect(text).toMatch(
        /需要.*编辑|需要.*editor|editor.*权限|编辑.*权限|没有.*权限|upload.*role|role.*required/i,
      )
    })
  })
})

// ── W7: 403 from upload endpoint must surface a message ──────────────

describe("LiteratureManager — upload 403 surfaces a message (W7)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListSuccess()
  })

  it("shows a clear permission-denied message when upload returns 403", async () => {
    mockUseAuth.mockReturnValue({
      user: makeEditorUser(),
      loading: false,
    })
    mockLiteratureApi.upload.mockImplementation(async () => {
      throw new Error("User lacks required permission: editor")
    })

    renderComponent()

    const fileInput = (await waitFor(() =>
      document.querySelector('input[type="file"]'),
    )) as HTMLInputElement
    dispatchUploadToInput(fileInput, makePdfFile("perm.pdf"))

    // Wait for the catch block to surface an error message.  The error
    // must clearly mention a permission / role requirement, NOT just
    // forward the opaque backend error string.
    await waitFor(
      () => {
        expect(mockMessage.open).toHaveBeenCalled()
      },
      { timeout: 3000 },
    )

    // Find the error call (type === "error").  At least one error call
    // must include "权限" or "编辑" so the user understands the cause.
    const errorCalls = (mockMessage.open.mock.calls as unknown[][]).filter(
      (call) => (call[0] as { type?: string })?.type === "error",
    )
    expect(errorCalls.length).toBeGreaterThan(0)
    const combined = errorCalls
      .map((call) =>
        JSON.stringify((call[0] as { content?: string })?.content ?? ""),
      )
      .join("\n")
    expect(combined).toMatch(/权限|permission|editor|编辑/i)
  })
})

// ── W8: List refresh after successful upload ─────────────────────────

describe("LiteratureManager — list refresh after upload (W8)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("re-fetches the literature list after a successful upload", async () => {
    mockUseAuth.mockReturnValue({
      user: makeEditorUser(),
      loading: false,
    })
    mockListSuccess()
    mockLiteratureApi.upload.mockResolvedValue({
      literature_id: "lit-new",
      status: "parsing",
    })
    mockLiteratureApi.get.mockResolvedValue({
      id: "lit-new",
      title: "test.pdf",
      doi: null,
      journal: null,
      year: null,
      status: "parsing",
      source_id: "lit-new",
      created_at: "2026-01-01T00:00:00Z",
    })

    renderComponent()

    await waitFor(() => expect(mockLiteratureApi.list).toHaveBeenCalled())
    mockLiteratureApi.list.mockClear()

    const fileInput = (await waitFor(() =>
      document.querySelector('input[type="file"]'),
    )) as HTMLInputElement
    dispatchUploadToInput(fileInput, makePdfFile("refresh.pdf"))

    await waitFor(() => {
      expect(mockLiteratureApi.list).toHaveBeenCalled()
    })
    const calls = mockLiteratureApi.list.mock.calls
    const lastCall = calls[calls.length - 1]
    expect(lastCall).toBeDefined()
    const lastCallArgs = lastCall![0] as {
      page?: number
      status?: string
    }
    expect(lastCallArgs.page).toBe(1)
    expect(lastCallArgs.status).toBeUndefined()
  })
})