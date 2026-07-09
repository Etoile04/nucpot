/**
 * Tests for KgReviewPage — migrated to canonical review-api.
 *
 * Spec: NFM-1096
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── Mock data ──────────────────────────────────────────────────────────

const MOCK_ITEMS = [
  {
    id: 'kg1',
    title: 'UO2 密度属性',
    type: '实体',
    source: '文献A',
    confidence: 0.92,
    status: 'pending' as const,
    createdAt: '2025-01-15T00:00:00Z',
  },
  {
    id: 'kg2',
    title: 'UO2 熔点属性',
    type: '实体',
    source: '文献B',
    confidence: 0.85,
    status: 'pending' as const,
    createdAt: '2025-01-16T00:00:00Z',
  },
] as const

const MOCK_QUEUE_RESPONSE = {
  items: [...MOCK_ITEMS],
  total: 25,
  page: 1,
  pageSize: 20,
}

const EMPTY_RESPONSE = {
  items: [],
  total: 0,
  page: 1,
  pageSize: 20,
}

// ── Mock API module ──────────────────────────────────────────────────

vi.mock('@/lib/review-api', () => ({
  getKgReviewQueue: vi.fn(),
  batchKgAction: vi.fn().mockResolvedValue(undefined),
}))

import { getKgReviewQueue, batchKgAction } from '@/lib/review-api'
import KgReviewPage from '@/app/review/kg/page'

const mockedGetKgReviewQueue = vi.mocked(getKgReviewQueue)
const mockedBatchKgAction = vi.mocked(batchKgAction)

// ── Tests ──────────────────────────────────────────────────────────────

describe('KgReviewPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedGetKgReviewQueue.mockResolvedValue(MOCK_QUEUE_RESPONSE)
  })

  it('1. renders page title 知识图谱审核', async () => {
    render(<KgReviewPage />)
    expect(screen.getByText('知识图谱审核')).toBeDefined()
  })

  it('2. fetches queue on mount with correct params (status=pending, page=1, limit=20)', async () => {
    render(<KgReviewPage />)

    await waitFor(() => {
      expect(mockedGetKgReviewQueue).toHaveBeenCalledWith('pending', 1, 20)
    })
  })

  it('3. renders ReviewQueueTable with fetched items', async () => {
    render(<KgReviewPage />)

    await waitFor(() => {
      expect(screen.getByText('UO2 密度属性')).toBeDefined()
    })
    expect(screen.getByText('UO2 熔点属性')).toBeDefined()
  })

  it('4. shows loading state while fetching', async () => {
    // Use never-resolving promises to keep the component in loading state
    mockedGetKgReviewQueue.mockImplementation(() => new Promise(() => {}))

    render(<KgReviewPage />)

    // Items should NOT be rendered while loading
    expect(screen.queryByText('UO2 密度属性')).toBeNull()
  })

  it('5. shows error state when fetch fails', async () => {
    mockedGetKgReviewQueue.mockRejectedValue(new Error('网络错误'))

    render(<KgReviewPage />)

    await waitFor(() => {
      expect(screen.getByText('网络错误')).toBeDefined()
    })
  })

  it('6. calls batchKgAction on batch approve via confirmation modal', async () => {
    render(<KgReviewPage />)

    await waitFor(() => {
      expect(screen.getByText('UO2 密度属性')).toBeDefined()
    })

    // Select all items via the select-all checkbox
    const selectAllCheckbox = screen.getByLabelText('选择全部')
    fireEvent.click(selectAllCheckbox)

    // Click batch approve button (appears when items are selected)
    const batchApproveBtn = screen.getByLabelText(/批量通过/)
    fireEvent.click(batchApproveBtn)

    // Confirm in the modal
    const confirmBtn = screen.getByText('确认通过')
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(mockedBatchKgAction).toHaveBeenCalledWith('approve', ['kg1', 'kg2'])
    })
  })

  it('7. calls batchKgAction on individual reject', async () => {
    render(<KgReviewPage />)

    await waitFor(() => {
      expect(screen.getByText('UO2 密度属性')).toBeDefined()
    })

    // Click individual reject button (拒绝) for the first item
    const rejectButtons = screen.getAllByLabelText(/拒绝.*/)
    fireEvent.click(rejectButtons[0])

    await waitFor(() => {
      expect(mockedBatchKgAction).toHaveBeenCalledWith('reject', ['kg1'])
    })
  })

  it('8. calls getKgReviewQueue with new page on pagination change', async () => {
    render(<KgReviewPage />)

    await waitFor(() => {
      expect(screen.getByText('UO2 密度属性')).toBeDefined()
    })

    // Clear previous calls to isolate pagination trigger
    vi.clearAllMocks()
    mockedGetKgReviewQueue.mockResolvedValue({
      ...MOCK_QUEUE_RESPONSE,
      page: 2,
    })

    // Click page 2 button rendered by ReviewQueueTable
    const page2Button = screen.getByText('2')
    fireEvent.click(page2Button)

    await waitFor(() => {
      expect(mockedGetKgReviewQueue).toHaveBeenCalledWith('pending', 2, 20)
    })
  })

  it('9. resets page to 1 on filter change', async () => {
    render(<KgReviewPage />)

    await waitFor(() => {
      expect(mockedGetKgReviewQueue).toHaveBeenCalledWith('pending', 1, 20)
    })

    vi.clearAllMocks()
    mockedGetKgReviewQueue.mockResolvedValue(EMPTY_RESPONSE)

    // Change the status filter to "已通过" (approved)
    const select = screen.getByLabelText('筛选状态')
    fireEvent.change(select, { target: { value: 'approved' } })

    await waitFor(() => {
      expect(mockedGetKgReviewQueue).toHaveBeenCalledWith('approved', 1, 20)
    })
  })

  it('10. clears selection after successful batch action', async () => {
    render(<KgReviewPage />)

    await waitFor(() => {
      expect(screen.getByText('UO2 密度属性')).toBeDefined()
    })

    // Select all items
    const selectAllCheckbox = screen.getByLabelText('选择全部')
    fireEvent.click(selectAllCheckbox)

    // Open confirmation modal and confirm
    const batchApproveBtn = screen.getByLabelText(/批量通过/)
    fireEvent.click(batchApproveBtn)
    const confirmBtn = screen.getByText('确认通过')
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(mockedBatchKgAction).toHaveBeenCalled()
    })

    // After batch action, the batch bar disappears (selection cleared)
    expect(screen.queryByLabelText(/批量通过/)).toBeNull()
  })
})
