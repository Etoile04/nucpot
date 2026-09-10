/**
 * ReviewDrawer — 5-action proof-reading drawer.
 *
 * Spec §4.3: 确认通过 / 需修改 / 标记无效 / 来源存疑 / 跳过.
 * Spec §3.4: dispute + modify REQUIRE a reviewer note.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"

import {
  submitReviewDecision,
  NOTE_REQUIRED_ACTIONS,
  type ReviewQueueItem,
} from "@/lib/admin/review-queue-api"
import { ReviewDrawer } from "./ReviewDrawer"

vi.mock("@/lib/admin/review-queue-api", () => ({
  submitReviewDecision: vi.fn(),
  NOTE_REQUIRED_ACTIONS: new Set(["dispute", "modify"]),
}))

const item: ReviewQueueItem = {
  id: "row-123",
  itemType: "measurement",
  confidence: 0.42,
  reviewStatus: "pending",
  source: { paragraph: "The thermal conductivity is 0.34 W/m·K", page: 3, doi: "10.1234/abcd" },
  createdAt: "2026-09-10T12:00:00Z",
  valueScalar: 0.34,
  unitId: "unit-abc-123",
  notes: null,
  propertyTypeId: "pt-thermal-conductivity",
  propertyTypeName: "thermal conductivity",
  datasetId: null,
  dedupeKey: null,
  validityCheck: { status: "unknown", reason: null },
}

const noop = () => {}

describe("ReviewDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders nothing visible when no item is open", () => {
    const { container } = render(
      <ReviewDrawer item={null} open={false} onClose={noop} onDecided={noop} />,
    )
    // Antd Drawer still mounts the wrapper div but hides the panel when
    // closed — only the message context holder is in the live tree, and
    // no action buttons are present.
    expect(container.querySelector('[data-testid="review-action-confirm"]')).toBeNull()
    expect(container.querySelector('[data-testid="review-action-dispute"]')).toBeNull()
  })

  it("shows value + unit + confidence + status when item is open", () => {
    render(<ReviewDrawer item={item} open onClose={noop} onDecided={noop} />)
    expect(screen.getByText("0.34")).toBeInTheDocument()
    expect(screen.getByText("42%")).toBeInTheDocument()
    expect(screen.getByText(/pending/)).toBeInTheDocument()
  })

  it("exposes all five spec §4.3 action buttons", () => {
    render(<ReviewDrawer item={item} open onClose={noop} onDecided={noop} />)
    expect(screen.getByTestId("review-action-confirm")).toBeInTheDocument()
    expect(screen.getByTestId("review-action-modify")).toBeInTheDocument()
    expect(screen.getByTestId("review-action-invalid")).toBeInTheDocument()
    expect(screen.getByTestId("review-action-dispute")).toBeInTheDocument()
    expect(screen.getByTestId("review-action-skip")).toBeInTheDocument()
  })

  it("renders the source paragraph when present", () => {
    render(<ReviewDrawer item={item} open onClose={noop} onDecided={noop} />)
    expect(screen.getByText(/thermal conductivity is 0.34/)).toBeInTheDocument()
  })

  it("dispatches 'confirm' as backend status 'approved'", async () => {
    const onDecided = vi.fn()
    const onClose = vi.fn()
    vi.mocked(submitReviewDecision).mockResolvedValueOnce()
    render(<ReviewDrawer item={item} open onClose={onClose} onDecided={onDecided} />)
    fireEvent.click(screen.getByTestId("review-action-confirm"))
    await waitFor(() => {
      expect(submitReviewDecision).toHaveBeenCalledWith("row-123", {
        action: "confirm",
        note: undefined,
      })
      expect(onDecided).toHaveBeenCalledWith("row-123", "confirm")
      expect(onClose).toHaveBeenCalled()
    })
  })

  it("dispatches 'invalid' as backend status 'rejected'", async () => {
    const onDecided = vi.fn()
    vi.mocked(submitReviewDecision).mockResolvedValueOnce()
    render(<ReviewDrawer item={item} open onClose={noop} onDecided={onDecided} />)
    fireEvent.click(screen.getByTestId("review-action-invalid"))
    await waitFor(() => {
      expect(submitReviewDecision).toHaveBeenCalledWith("row-123", {
        action: "invalid",
        note: undefined,
      })
    })
  })

  it("blocks 'dispute' without a note (spec §3.4 mandatory note)", async () => {
    render(<ReviewDrawer item={item} open onClose={noop} onDecided={noop} />)
    fireEvent.click(screen.getByTestId("review-action-dispute"))
    await waitFor(() => {
      expect(submitReviewDecision).not.toHaveBeenCalled()
    })
  })

  it("submits 'dispute' with the entered note when provided", async () => {
    vi.mocked(submitReviewDecision).mockResolvedValueOnce()
    render(<ReviewDrawer item={item} open onClose={noop} onDecided={noop} />)
    const textarea = screen.getByRole("textbox")
    fireEvent.change(textarea, { target: { value: "段落摘要与数值不符" } })
    fireEvent.click(screen.getByTestId("review-action-dispute"))
    await waitFor(() => {
      expect(submitReviewDecision).toHaveBeenCalledWith("row-123", {
        action: "dispute",
        note: "段落摘要与数值不符",
      })
    })
  })

  it("submits 'modify' with the entered note when provided", async () => {
    vi.mocked(submitReviewDecision).mockResolvedValueOnce()
    render(<ReviewDrawer item={item} open onClose={noop} onDecided={noop} />)
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "value should be 0.32" },
    })
    fireEvent.click(screen.getByTestId("review-action-modify"))
    await waitFor(() => {
      expect(submitReviewDecision).toHaveBeenCalledWith("row-123", {
        action: "modify",
        note: "value should be 0.32",
      })
    })
  })

  it("renders validity_check fail reason when validityCheck.status='fail'", () => {
    render(
      <ReviewDrawer
        item={item}
        open
        onClose={noop}
        onDecided={noop}
        validityCheck={{
          status: "fail",
          reason: "键长 0.3Å 低于有效域下限 0.5Å",
        }}
      />,
    )
    expect(screen.getByText(/键长 0\.3Å 低于有效域下限/)).toBeInTheDocument()
  })
})

describe("NOTE_REQUIRED_ACTIONS (transitional 6-state mapping)", () => {
  it("flags dispute + modify per spec §3.4", () => {
    expect(NOTE_REQUIRED_ACTIONS.has("dispute")).toBe(true)
    expect(NOTE_REQUIRED_ACTIONS.has("modify")).toBe(true)
  })

  it("does not require a note for confirm / invalid / skip", () => {
    expect(NOTE_REQUIRED_ACTIONS.has("confirm")).toBe(false)
    expect(NOTE_REQUIRED_ACTIONS.has("invalid")).toBe(false)
    expect(NOTE_REQUIRED_ACTIONS.has("skip")).toBe(false)
  })
})
