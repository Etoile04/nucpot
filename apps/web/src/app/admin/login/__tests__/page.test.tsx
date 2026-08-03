/**
 * Unit tests for the login page — returnTo redirect behaviour.
 *
 * NFM-2331 — validates that the login page reads the `returnTo` query
 * parameter after a successful login and redirects accordingly.
 * Also validates open-redirect protection.
 */

import {
  describe,
  it,
  expect,
  vi,
  beforeEach,
} from "vitest"
import {
  render,
  screen,
  fireEvent,
  waitFor,
} from "@testing-library/react"
const pushMock = vi.fn()
const refreshMock = vi.fn()
let mockSearchParamsString = ""

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, refresh: refreshMock }),
  useSearchParams: () => new URLSearchParams(mockSearchParamsString),
}))

const loginMock = vi.fn()
vi.mock("@/lib/api-client", () => ({
  authApi: {
    login: (...args: unknown[]) => loginMock(...args),
  },
  request: vi.fn(),
}))

import LoginPage from "../page"

describe("LoginPage — returnTo redirect", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockSearchParamsString = ""
    loginMock.mockResolvedValue(undefined)
  })

  function fillAndSubmit(email: string, password: string) {
    const emailInput = screen.getByLabelText("邮箱")
    const passwordInput = screen.getByLabelText("密码")
    fireEvent.change(emailInput, { target: { value: email } })
    fireEvent.change(passwordInput, { target: { value: password } })
    const submitButton = screen.getByRole("button", { name: /登录/ })
    fireEvent.click(submitButton)
  }

  it("redirects to /admin/blog when no returnTo is present (default)", async () => {
    mockSearchParamsString = ""
    render(<LoginPage />)
    fillAndSubmit("admin@example.com", "pass123")

    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/admin/blog")
    })
  })

  it("redirects to returnTo path when provided as query param", async () => {
    mockSearchParamsString =
      "returnTo=%2Fdashboard%2Fliterature%3Fpage%3D3%26status%3Dpending"
    render(<LoginPage />)
    fillAndSubmit("admin@example.com", "pass123")

    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith(
        "/dashboard/literature?page=3&status=pending",
      )
    })
  })

  it("redirects to returnTo with hash preserved", async () => {
    mockSearchParamsString = "returnTo=%2Fadmin%2Flightrag%23tab%3Dreview"
    render(<LoginPage />)
    fillAndSubmit("admin@example.com", "pass123")

    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/admin/lightrag#tab=review")
    })
  })

  it("blocks open redirect — absolute URL starting with http://", async () => {
    mockSearchParamsString = "returnTo=http%3A%2F%2Fevil.com"
    render(<LoginPage />)
    fillAndSubmit("admin@example.com", "pass123")

    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/admin/blog")
    })
  })

  it("blocks open redirect — protocol-relative URL //evil.com", async () => {
    mockSearchParamsString = "returnTo=%2F%2Fevil.com%2Fsteal"
    render(<LoginPage />)
    fillAndSubmit("admin@example.com", "pass123")

    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/admin/blog")
    })
  })

  it("blocks open redirect — javascript: scheme", async () => {
    mockSearchParamsString = "returnTo=javascript%3Aalert(1)"
    render(<LoginPage />)
    fillAndSubmit("admin@example.com", "pass123")

    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/admin/blog")
    })
  })

  it("blocks open redirect — returnTo not starting with /", async () => {
    mockSearchParamsString = "returnTo=relative-path-trick"
    render(<LoginPage />)
    fillAndSubmit("admin@example.com", "pass123")

    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/admin/blog")
    })
  })

  it("shows error message when login fails", async () => {
    loginMock.mockRejectedValue(new Error("Invalid credentials"))
    render(<LoginPage />)
    fillAndSubmit("admin@example.com", "wrong")

    await waitFor(() => {
      expect(screen.getByText("Invalid credentials")).toBeVisible()
    })
    expect(pushMock).not.toHaveBeenCalled()
  })
})
