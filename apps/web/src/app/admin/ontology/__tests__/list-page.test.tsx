/**
 * Tests for Ontology List Page.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const mockRequest = vi.fn()
vi.mock('@/lib/api-client', () => ({
  request: (...args: unknown[]) => mockRequest(...args),
}))

const mockUseAuth = vi.fn()
vi.mock('@/components/AuthProvider', () => ({
  useAuth: () => mockUseAuth(),
}))

const LIST_RESPONSE = {
  items: [
    { id: 'v1', version: '1.0.0', status: 'draft', description: 'First', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', changelog: null, created_by: 'admin' },
    { id: 'v2', version: '2.0.0', status: 'published', description: 'Second', created_at: '2026-01-02T00:00:00Z', updated_at: '2026-01-02T00:00:00Z', changelog: 'promote', created_by: 'admin' },
  ],
  total: 2,
  page: 1,
  limit: 10,
  pages: 1,
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('OntologyListPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockRequest.mockResolvedValue(LIST_RESPONSE)
    mockUseAuth.mockReturnValue({ user: null, loading: false })
  })

  it('renders heading', async () => {
    const { default: Page } = await import('../page')
    render(<Page />, { wrapper })
    expect(screen.getByText('Ontology Versions')).toBeInTheDocument()
  })

  it('renders version entries after loading', async () => {
    const { default: Page } = await import('../page')
    render(<Page />, { wrapper })
    await waitFor(() => expect(screen.getByText('v1.0.0')).toBeInTheDocument())
    expect(screen.getByText('v2.0.0')).toBeInTheDocument()
  })

  it('renders new version link when authorized', async () => {
    mockUseAuth.mockReturnValue({ user: { role: 'admin' }, loading: false })
    const { default: Page } = await import('../page')
    render(<Page />, { wrapper })
    await waitFor(() => expect(screen.getByText(/New version/i)).toBeInTheDocument())
  })

  it('shows empty state when no versions', async () => {
    mockRequest.mockResolvedValue({ items: [], total: 0, page: 1, limit: 10, pages: 0 })
    const { default: Page } = await import('../page')
    render(<Page />, { wrapper })
    await waitFor(() => expect(screen.getByText('No ontology versions found')).toBeInTheDocument())
  })

  it('shows error state on fetch failure', async () => {
    mockRequest.mockRejectedValue(new Error('Server error'))
    const { default: Page } = await import('../page')
    render(<Page />, { wrapper })
    await waitFor(() => expect(screen.getByText('Server error')).toBeInTheDocument())
  })
})
