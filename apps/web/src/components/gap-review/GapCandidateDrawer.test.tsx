/**
 * GapCandidateDrawer — optimistic update with rollback tests.
 * Spec: NFM-3745
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ConfigProvider, message } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { GapCandidateDrawer } from './GapCandidateDrawer'
import * as api from '@/lib/reference-gaps/api'
import type { GapCandidate } from '@/lib/reference-gaps/types'

// ── Fixtures ──────────────────────────────

const CANDIDATE: GapCandidate = {
  id: 'gc-1',
  entity_name: 'U-235',
  entity_type: 'Material',
  confidence: 0.87,
  source_passage: 'Uranium-235 has a thermal neutron capture cross section of 98.3 barns.',
  match_spans: [{ start: 0, end: 8 }],
  suggested_properties: [{ symbol: 'U-235', category: 'actinide' }],
  source_document: 'NDS-2024-001.pdf',
  created_at: '2026-01-15T10:30:00Z',
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
}

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = createQueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <ConfigProvider>{ui}</ConfigProvider>
    </QueryClientProvider>,
  )
}

// ── Tests ───────────────────────────────

describe('GapCandidateDrawer — optimistic update with rollback (NFM-3745)', () => {
  const messageErrorSpy = vi.fn()

  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(message, 'error').mockImplementation(messageErrorSpy)
    vi.spyOn(api, 'getCandidateHistory').mockResolvedValue({
      decisions: [],
    })
  })

  it('shows optimistic state (checkmark + primary style) when accept is clicked', async () => {
    vi.spyOn(api, 'postDecision').mockImplementation(
      () => new Promise(() => {}),
    )

    renderWithProviders(
      <GapCandidateDrawer
        candidate={CANDIDATE}
        open={true}
        onClose={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByTestId('drawer-accepted'))

    // Optimistic: button text should show “...” suffix
    await waitFor(() => {
      expect(screen.getByTestId('drawer-accepted').textContent).toContain('...')
    })

    // Other buttons should be disabled during pending mutation
    await waitFor(() => {
      expect((screen.getByTestId('drawer-rejected') as HTMLButtonElement).disabled).toBe(true)
    })
  })

  it('rolls back optimistic state and shows error toast when API fails', async () => {
    vi.spyOn(api, 'postDecision').mockRejectedValueOnce(
      new Error('Network error'),
    )
    const fakeMessageApi = { error: vi.fn() } as unknown as import('antd/es/message/interface').MessageInstance

    renderWithProviders(
      <GapCandidateDrawer
        candidate={CANDIDATE}
        open={true}
        onClose={vi.fn()}
        messageApi={fakeMessageApi}
      />,
    )

    fireEvent.click(screen.getByTestId('drawer-accepted'))

    // Optimistic state should appear
    await waitFor(() => {
      expect(screen.getByTestId('drawer-accepted').textContent).toContain('...')
    })

    // After failure, optimistic state should roll back
    await waitFor(() => {
      const acceptBtn = screen.getByTestId('drawer-accepted')
      const rejectBtn = screen.getByTestId('drawer-rejected')
      // Button text should no longer have “...”
      expect(acceptBtn.textContent).not.toContain('...')
      // Other buttons should be re-enabled
      expect((rejectBtn as HTMLButtonElement).disabled).toBe(false)
      expect((screen.getByTestId('drawer-deferred') as HTMLButtonElement).disabled).toBe(false)
    })

    // Error toast should have been shown via messageApi prop
    await waitFor(() => {
      expect(fakeMessageApi.error).toHaveBeenCalledWith('操作失败，请重试')
    })
  })

  it('closes drawer and calls onDecision on success', async () => {
    const onDecision = vi.fn()
    const onClose = vi.fn()
    vi.spyOn(api, 'postDecision').mockResolvedValueOnce({
      id: 'd-1',
      candidate_id: 'gc-1',
      decision: 'accepted',
      decided_at: '2026-01-15T10:35:00Z',
      reviewer_id: 'u-1',
    })

    renderWithProviders(
      <GapCandidateDrawer
        candidate={CANDIDATE}
        open={true}
        onClose={onClose}
        onDecision={onDecision}
      />,
    )

    fireEvent.click(screen.getByTestId('drawer-accepted'))

    await waitFor(() => {
      expect(onDecision).toHaveBeenCalledWith('gc-1', 'accepted')
      expect(onClose).toHaveBeenCalled()
    })

    expect(messageErrorSpy).not.toHaveBeenCalled()
  })

  it('rolls back when reject fails and keeps all buttons enabled', async () => {
    vi.spyOn(api, 'postDecision').mockRejectedValueOnce(
      new Error('Server error'),
    )

    renderWithProviders(
      <GapCandidateDrawer
        candidate={CANDIDATE}
        open={true}
        onClose={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByTestId('drawer-rejected'))

    // Optimistic label
    await waitFor(() => {
      expect(screen.getByTestId('drawer-rejected').textContent).toContain('...')
    })

    // All buttons should be back to normal after rollback
    await waitFor(() => {
      expect((screen.getByTestId('drawer-accepted') as HTMLButtonElement).disabled).toBe(false)
      expect((screen.getByTestId('drawer-rejected') as HTMLButtonElement).disabled).toBe(false)
      expect((screen.getByTestId('drawer-deferred') as HTMLButtonElement).disabled).toBe(false)
    })
  })
})
