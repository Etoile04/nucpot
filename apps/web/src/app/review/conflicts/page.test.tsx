/**
 * Tests for ConflictsReviewPage — ReviewQueueTable + ConflictDetailPanel.
 *
 * Spec: NFM-1006
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// ── Mock data ──────────────────────────────────────────────────────────

const MOCK_CONFLICTS = [
  {
    id: 'c1',
    entityName: 'UO2',
    property: '密度',
    sourceA: {
      id: 'sa1',
      sourceTitle: '文献A',
      value: '10.95',
      unit: 'g/cm³',
      confidence: 0.92,
    },
    sourceB: {
      id: 'sb1',
      sourceTitle: '文献B',
      value: '10.50',
      unit: 'g/cm³',
      confidence: 0.85,
    },
    conflictNumber: 1,
  },
  {
    id: 'c2',
    entityName: 'UO2',
    property: '熔点',
    sourceA: {
      id: 'sa2',
      sourceTitle: '文献C',
      value: '1850',
      unit: '°C',
      confidence: 0.88,
    },
    sourceB: {
      id: 'sb2',
      sourceTitle: '文献D',
      value: '1820',
      unit: '°C',
      confidence: 0.78,
    },
    conflictNumber: 2,
  },
] as const

const MOCK_RESPONSE = {
  items: [...MOCK_CONFLICTS],
  total: 2,
  page: 1,
  pageSize: 20,
}

// ── Mock API module ──────────────────────────────────────────────────

vi.mock('@/lib/kg-review-api', () => ({
  fetchConflicts: vi.fn(),
  resolveConflict: vi.fn().mockResolvedValue({ resolved: true }),
}))

// Import after mock setup
import { fetchConflicts, resolveConflict } from '@/lib/kg-review-api'
import ConflictsReviewPage from '@/app/review/conflicts/page'

const mockedFetchConflicts = vi.mocked(fetchConflicts)
const mockedResolveConflict = vi.mocked(resolveConflict)

// ── Tests ──────────────────────────────────────────────────────────────

describe('ConflictsReviewPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedFetchConflicts.mockResolvedValue(MOCK_RESPONSE)
  })

  it('renders page title 冲突解决', async () => {
    render(<ConflictsReviewPage />)
    expect(screen.getByText('冲突解决')).toBeDefined()
  })

  it('renders refresh button', async () => {
    render(<ConflictsReviewPage />)
    const btn = screen.getByLabelText('刷新')
    expect(btn).toBeDefined()
  })

  it('displays conflict items in the table after loading', async () => {
    render(<ConflictsReviewPage />)

    await waitFor(() => {
      expect(mockedFetchConflicts).toHaveBeenCalledWith('pending', 1, 20)
    })

    // Check mapped titles appear
    expect(screen.getByText('UO2 — 密度')).toBeDefined()
    expect(screen.getByText('UO2 — 熔点')).toBeDefined()
  })

  it('shows empty state when no conflicts', async () => {
    mockedFetchConflicts.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      pageSize: 20,
    })

    render(<ConflictsReviewPage />)

    await waitFor(() => {
      expect(screen.getByText('暂无待审项目')).toBeDefined()
    })
  })

  it('opens detail panel when clicking a row action button', async () => {
    render(<ConflictsReviewPage />)

    await waitFor(() => {
      expect(screen.getByText('UO2 — 密度')).toBeDefined()
    })

    // Click the first "通过" button (approve action)
    const approveButtons = screen.getAllByLabelText(/通过.*/)
    fireEvent.click(approveButtons[0])

    // Detail panel should appear with conflict info
    await waitFor(() => {
      expect(screen.getByText('冲突详情')).toBeDefined()
    })
    expect(screen.getByText('UO2')).toBeDefined()
    expect(screen.getByText('属性: 密度 · 冲突 #1')).toBeDefined()
  })

  it('shows side-by-side sources in detail panel', async () => {
    render(<ConflictsReviewPage />)

    await waitFor(() => {
      expect(screen.getByText('UO2 — 密度')).toBeDefined()
    })

    const approveButtons = screen.getAllByLabelText(/通过.*/)
    fireEvent.click(approveButtons[0])

    await waitFor(() => {
      expect(screen.getByText('版本 A')).toBeDefined()
      expect(screen.getByText('版本 B')).toBeDefined()
    })
    expect(screen.getAllByText('文献A').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('文献B').length).toBeGreaterThanOrEqual(1)
  })

  it('calls resolveConflict with keep_a when clicking 保留版本 A', async () => {
    render(<ConflictsReviewPage />)

    await waitFor(() => {
      expect(screen.getByText('UO2 — 密度')).toBeDefined()
    })

    const approveButtons = screen.getAllByLabelText(/通过.*/)
    fireEvent.click(approveButtons[0])

    await waitFor(() => {
      expect(screen.getByText('保留版本 A')).toBeDefined()
    })

    fireEvent.click(screen.getByText('保留版本 A'))

    await waitFor(() => {
      expect(mockedResolveConflict).toHaveBeenCalledWith('c1', 'keep_a')
    })
  })

  it('calls resolveConflict with skip when clicking 跳过', async () => {
    render(<ConflictsReviewPage />)

    await waitFor(() => {
      expect(screen.getByText('UO2 — 密度')).toBeDefined()
    })

    const approveButtons = screen.getAllByLabelText(/通过.*/)
    fireEvent.click(approveButtons[0])

    await waitFor(() => {
      expect(screen.getByText('跳过')).toBeDefined()
    })

    fireEvent.click(screen.getByText('跳过'))

    await waitFor(() => {
      expect(mockedResolveConflict).toHaveBeenCalledWith('c1', 'skip')
    })
  })

  it('shows error message when API fails', async () => {
    mockedFetchConflicts.mockRejectedValue(
      new Error('网络错误'),
    )

    render(<ConflictsReviewPage />)

    await waitFor(() => {
      expect(screen.getByText('网络错误')).toBeDefined()
    })
  })
})
