import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ConfigProvider } from 'antd'
import { BulkActionToolbar } from './BulkActionToolbar'
import type { GapCandidate } from '@/lib/gap-decisions/types'
import * as api from '@/lib/gap-decisions/bulk-decisions-api'

// ── Fixtures ──────────────────────────────────────────────────────────

const HIGH: GapCandidate = { candidate_id: 'c-1', confidence: 0.92 }
const LOW: GapCandidate = { candidate_id: 'c-2', confidence: 0.55 }
const MED: GapCandidate = { candidate_id: 'c-3', confidence: 0.71 }

function renderWithAntd(ui: React.ReactElement) {
  return render(<ConfigProvider>{ui}</ConfigProvider>)
}

function defaultProps(overrides?: Record<string, unknown>) {
  return {
    selectedItems: [HIGH, LOW],
    confidenceThreshold: 0.7,
    onSuccess: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  }
}

// ── Tests ──────────────────────────────────────────────────────────────

describe('BulkActionToolbar', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('returns null when no items selected', () => {
    const { container } = renderWithAntd(
      <BulkActionToolbar {...defaultProps({ selectedItems: [] })} />,
    )
    expect(container.innerHTML).toBe('')
  })

  it('renders toolbar when items are selected', () => {
    renderWithAntd(<BulkActionToolbar {...defaultProps()} />)
    expect(screen.getByRole('toolbar')).toBeDefined()
  })

  it('shows correct selected count', () => {
    renderWithAntd(<BulkActionToolbar {...defaultProps()} />)
    expect(screen.getByText(/2 selected/)).toBeDefined()
  })

  it('shows accept count filtered by confidence threshold', () => {
    renderWithAntd(
      <BulkActionToolbar
        {...defaultProps({ selectedItems: [HIGH, MED, LOW], confidenceThreshold: 0.7 })}
      />,
    )
    expect(screen.getByText(/2/)).toBeDefined()
  })

  it('disables accept button when no items meet threshold', () => {
    renderWithAntd(
      <BulkActionToolbar
        {...defaultProps({ selectedItems: [LOW], confidenceThreshold: 0.9 })}
      />,
    )
    const acceptBtn = screen.getByTestId('bulk-accept')
    expect((acceptBtn as HTMLButtonElement).disabled).toBe(true)
  })

  it('calls API with only above-threshold items on accept', async () => {
    const onSuccess = vi.fn()
    vi.spyOn(api, 'submitBulkDecisions').mockResolvedValueOnce({
      results: [
        { candidate_id: 'c-1', decision: 'accepted', decided_at: '2024-01-01T00:00:00Z', reviewer_id: 'u-1' },
      ],
    })

    renderWithAntd(
      <BulkActionToolbar {...defaultProps({ selectedItems: [HIGH, LOW], onSuccess })} />,
    )

    fireEvent.click(screen.getByTestId('bulk-accept'))

    await waitFor(() => {
      expect(api.submitBulkDecisions).toHaveBeenCalledWith({
        decisions: [{ candidate_id: 'c-1', decision: 'accepted' }],
      })
      expect(onSuccess).toHaveBeenCalled()
    })
  })

  it('calls API with all selected items on reject', async () => {
    const onSuccess = vi.fn()
    vi.spyOn(api, 'submitBulkDecisions').mockResolvedValueOnce({
      results: [
        { candidate_id: 'c-1', decision: 'rejected', decided_at: '2024-01-01T00:00:00Z', reviewer_id: 'u-1' },
        { candidate_id: 'c-2', decision: 'rejected', decided_at: '2024-01-01T00:00:00Z', reviewer_id: 'u-1' },
      ],
    })

    renderWithAntd(<BulkActionToolbar {...defaultProps({ onSuccess })} />)

    fireEvent.click(screen.getByTestId('bulk-reject'))

    await waitFor(() => {
      expect(api.submitBulkDecisions).toHaveBeenCalledWith({
        decisions: [
          { candidate_id: 'c-1', decision: 'rejected' },
          { candidate_id: 'c-2', decision: 'rejected' },
        ],
      })
    })
  })

  it('calls API with all selected items on defer', async () => {
    const onSuccess = vi.fn()
    vi.spyOn(api, 'submitBulkDecisions').mockResolvedValueOnce({
      results: [
        { candidate_id: 'c-1', decision: 'deferred', decided_at: '2024-01-01T00:00:00Z', reviewer_id: 'u-1' },
        { candidate_id: 'c-2', decision: 'deferred', decided_at: '2024-01-01T00:00:00Z', reviewer_id: 'u-1' },
      ],
    })

    renderWithAntd(<BulkActionToolbar {...defaultProps({ onSuccess })} />)

    fireEvent.click(screen.getByTestId('bulk-defer'))

    await waitFor(() => {
      expect(api.submitBulkDecisions).toHaveBeenCalledWith({
        decisions: [
          { candidate_id: 'c-1', decision: 'deferred' },
          { candidate_id: 'c-2', decision: 'deferred' },
        ],
      })
    })
  })

  it('calls onError when bulk operation fails', async () => {
    const onError = vi.fn()
    vi.spyOn(api, 'submitBulkDecisions').mockRejectedValueOnce(new Error('Network error'))

    renderWithAntd(<BulkActionToolbar {...defaultProps({ onError })} />)

    fireEvent.click(screen.getByTestId('bulk-reject'))

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith('Network error')
    })
  })

  it('has role=toolbar', () => {
    renderWithAntd(<BulkActionToolbar {...defaultProps()} />)
    expect(screen.getByRole('toolbar').getAttribute('aria-label')).toBe('Bulk actions')
  })
})
