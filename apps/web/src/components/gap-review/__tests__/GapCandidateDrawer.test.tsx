/** @jest-environment jsdom */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { GapCandidateDrawer } from '../GapCandidateDrawer'
import type { GapCandidate, TextSpan } from '@/lib/reference-gaps/types'

// Mock the API module
vi.mock('@/lib/reference-gaps/api', () => ({
  getCandidateHistory: vi.fn(),
  postDecision: vi.fn(),
}))

import { getCandidateHistory, postDecision } from '@/lib/reference-gaps/api'
const mockGetHistory = vi.mocked(getCandidateHistory)
const mockPostDecision = vi.mocked(postDecision)

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        {children}
      </QueryClientProvider>
    )
  }
}

const MATCH_SPANS: readonly TextSpan[] = [{ start: 0, end: 12 }]

const CANDIDATE: GapCandidate = {
  id: 'cand-1',
  entity_name: 'Phase Transition',
  entity_type: 'process',
  confidence: 0.92,
  source_passage: 'Phase transition occurs when a material changes state.',
  match_spans: MATCH_SPANS,
  suggested_properties: [{ temperature: '100°C' }],
  source_document: 'thermo-textbook-v2.pdf',
  created_at: '2026-08-25T00:00:00Z',
}

describe('GapCandidateDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetHistory.mockResolvedValue({ decisions: [] })
    mockPostDecision.mockResolvedValue({
      id: 'dec-1',
      candidate_id: 'cand-1',
      decision: 'accepted',
      decided_at: '2026-08-25T01:00:00Z',
      reviewer_id: 'user-1',
    })
  })

  it('does not render when candidate is null', () => {
    const onClose = vi.fn()
    render(
      <GapCandidateDrawer candidate={null} open={false} onClose={onClose} />,
      { wrapper: createWrapper() },
    )
    expect(screen.queryByText('Phase Transition')).not.toBeInTheDocument()
  })

  it('renders candidate details when open', () => {
    const onClose = vi.fn()
    render(
      <GapCandidateDrawer candidate={CANDIDATE} open={true} onClose={onClose} />,
      { wrapper: createWrapper() },
    )
    expect(screen.getAllByText('Phase Transition').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('process')).toBeInTheDocument()
    expect(screen.getByText('92%')).toBeInTheDocument()
  })

  it('renders the highlighted source passage', () => {
    const onClose = vi.fn()
    render(
      <GapCandidateDrawer candidate={CANDIDATE} open={true} onClose={onClose} />,
      { wrapper: createWrapper() },
    )
    const mark = screen.getByRole('mark')
    expect(mark).toHaveTextContent('Phase transi')
  })

  it('shows action buttons', () => {
    const onClose = vi.fn()
    render(
      <GapCandidateDrawer candidate={CANDIDATE} open={true} onClose={onClose} />,
      { wrapper: createWrapper() },
    )
    expect(screen.getByRole('button', { name: /采.纳/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /拒.绝/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /暂.缓/ })).toBeInTheDocument()
  })

  it('calls postDecision and onDecision on accept click', async () => {
    const onClose = vi.fn()
    const onDecision = vi.fn()
    render(
      <GapCandidateDrawer candidate={CANDIDATE} open={true} onClose={onClose} onDecision={onDecision} />, { wrapper: createWrapper() },
    )
    fireEvent.click(screen.getByRole('button', { name: /采.纳/ }))
    await waitFor(() => {
      expect(mockPostDecision).toHaveBeenCalledTimes(1)
      expect(mockPostDecision.mock.calls[0]?.[0]).toEqual({
        candidate_id: 'cand-1',
        decision: 'accepted',
      })
    })
    await waitFor(() => {
      expect(onDecision).toHaveBeenCalledWith('cand-1', 'accepted')
    })
  })

  it('shows error toast when postDecision fails', async () => {
    mockPostDecision.mockRejectedValueOnce(new Error('Network error'))
    const onClose = vi.fn()
    const fakeMessageApi = { error: vi.fn() } as unknown as import('antd/es/message/interface').MessageInstance
    render(
      <GapCandidateDrawer candidate={CANDIDATE} open={true} onClose={onClose} messageApi={fakeMessageApi} />,
      { wrapper: createWrapper() },
    )
    fireEvent.click(screen.getByRole('button', { name: /拒.绝/ }))
    await waitFor(() => {
      expect(fakeMessageApi.error).toHaveBeenCalledWith('操作失败，请重试')
    })
  })

  it('disables all action buttons while a decision is pending', async () => {
    // Make the mutation hang
    mockPostDecision.mockImplementationOnce(
      () => new Promise(() => {}),
    )
    const onClose = vi.fn()
    render(
      <GapCandidateDrawer candidate={CANDIDATE} open={true} onClose={onClose} />, { wrapper: createWrapper() },
    )
    fireEvent.click(screen.getByRole('button', { name: /暂.缓/ }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /采.纳/ })).toBeDisabled()
      expect(screen.getByRole('button', { name: /拒.绝/ })).toBeDisabled()
    })
  })
})
