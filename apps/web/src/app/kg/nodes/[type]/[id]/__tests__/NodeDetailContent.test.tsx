/**
 * Tests for NodeDetailContent — KG Node Detail page.
 *
 * Spec: NFM-1099
 *
 * Mocking strategy:
 *  - vi.hoisted() exposes fetchMock for stubbing global fetch
 *  - vi.mock("next/navigation") supplies useRouter + useParams
 *  - Vitest setup provides matchMedia polyfill (AntD responsive)
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  render,
  screen,
  fireEvent,
  waitFor,
} from '@testing-library/react'

// ── Hoisted fetch mock (must come before the component import) ──────

const { fetchMock } = vi.hoisted(() => ({
  fetchMock: vi.fn(),
}))

vi.stubGlobal('fetch', fetchMock)

// ── Mock next/navigation ─────────────────────────────────────────────

const { pushMock, replaceMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: pushMock,
    replace: replaceMock,
    back: vi.fn(),
  }),
  useParams: () => ({ type: 'Material', id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee' }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/kg/nodes/Material/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
}))

// ── Component import (must come after the mocks) ────────────────────

import { NodeDetailContent } from '../NodeDetailContent'

// ── Fixture data ────────────────────────────────────────────────────

const NODE_DETAIL = {
  success: true,
  data: {
    id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    node_type: 'Material',
    label: 'UO2',
    aliases: ['uranium dioxide', 'UOX'],
    properties: {
      density: '10.97 g/cm³',
      melting_point: '3123 K',
      structure: 'fluorite',
    },
    confidence: 0.92,
    status: 'active',
    source_id: 'src-001',
  },
}

const NODE_DETAIL_NO_PROPS = {
  success: true,
  data: {
    ...NODE_DETAIL.data,
    properties: {},
  },
}

const RELATIONS_RESPONSE = {
  success: true,
  data: {
    items: [
      {
        id: 'edge-1',
        relation_type: 'contains',
        confidence: 0.85,
        properties: {},
        source_node: {
          ...NODE_DETAIL.data,
          id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        },
        target_node: {
          id: 'fffffff1-aaaa-bbbb-cccc-111111111111',
          node_type: 'Property',
          label: 'Density',
          aliases: [],
          properties: {},
          confidence: 0.9,
          status: 'active',
          source_id: null,
        },
      },
      {
        id: 'edge-2',
        relation_type: 'measured_in',
        confidence: 0.7,
        properties: {},
        source_node: {
          id: 'fffffff2-aaaa-bbbb-cccc-222222222222',
          node_type: 'Experiment',
          label: 'High-T Furnace Trial',
          aliases: [],
          properties: {},
          confidence: 0.75,
          status: 'active',
          source_id: null,
        },
        target_node: {
          ...NODE_DETAIL.data,
        },
      },
    ],
    total: 2,
    limit: 50,
    offset: 0,
  },
}

// ── Helpers ─────────────────────────────────────────────────────────

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function queueResponses(responses: Array<{ body: unknown; status?: number }>) {
  for (const r of responses) {
    fetchMock.mockResolvedValueOnce(jsonResponse(r.body, r.status ?? 200))
  }
}

// ── Tests ────────────────────────────────────────────────────────────

describe('NodeDetailContent', () => {
  beforeEach(() => {
    fetchMock.mockReset()
    pushMock.mockReset()
    replaceMock.mockReset()
    queueResponses([
      { body: NODE_DETAIL },
      { body: RELATIONS_RESPONSE },
    ])
  })

  afterEach(() => {
    fetchMock.mockReset()
  })

  it('1. shows loading spinner before fetch resolves', () => {
    fetchMock.mockReset()
    fetchMock.mockImplementation(() => new Promise(() => {})) // never resolves

    render(<NodeDetailContent />)
    // Use a dedicated testid on the loading wrapper (AntD Spin splits
    // its tip text into per-character spans, so a text matcher is brittle).
    expect(screen.getByTestId('node-loading')).toBeInTheDocument()
  })

  it('2. renders the node label and type badge after data loads', async () => {
    render(<NodeDetailContent />)

    expect(await screen.findByText('UO2')).toBeInTheDocument()
    expect(screen.getByText('Material')).toBeInTheDocument()
  })

  it('3. renders property entries from the node detail', async () => {
    render(<NodeDetailContent />)

    expect(await screen.findByText('density')).toBeInTheDocument()
    expect(screen.getByText('10.97 g/cm³')).toBeInTheDocument()
    expect(screen.getByText('melting_point')).toBeInTheDocument()
  })

  it('4. shows the ConfidenceBadge with role=status', async () => {
    render(<NodeDetailContent />)

    await screen.findByText('UO2')
    // Header badge + one per sidebar edge (>= 1 total)
    const badges = screen.getAllByRole('status')
    expect(badges.length).toBeGreaterThanOrEqual(1)
  })

  it('5. displays the source ID when present', async () => {
    render(<NodeDetailContent />)

    await screen.findByText('UO2')
    expect(screen.getByText('src-001')).toBeInTheDocument()
  })

  it('6. renders the 404 Result page when the node is missing', async () => {
    fetchMock.mockReset()
    queueResponses([
      { body: NODE_DETAIL, status: 404 },
    ])

    render(<NodeDetailContent />)

    expect(await screen.findByText('Node not found')).toBeInTheDocument()
  })

  it('7. renders the error Result page on non-404 failure', async () => {
    fetchMock.mockReset()
    queueResponses([
      { body: { detail: 'Internal server error' }, status: 500 },
    ])

    render(<NodeDetailContent />)

    expect(await screen.findByText('Failed to load node')).toBeInTheDocument()
  })

  it('8. renders relations in the sidebar after they load', async () => {
    render(<NodeDetailContent />)

    // Wait for relations useEffect to resolve
    await waitFor(() => {
      expect(screen.getByText('Density')).toBeInTheDocument()
    })
    expect(screen.getByText('High-T Furnace Trial')).toBeInTheDocument()
    expect(screen.getByText(/Relations \(2\)/)).toBeInTheDocument()
  })

  it('9. shows empty-relations message when none exist', async () => {
    fetchMock.mockReset()
    queueResponses([
      { body: NODE_DETAIL },
      {
        body: {
          success: true,
          data: { items: [], total: 0, limit: 50, offset: 0 },
        },
      },
    ])

    render(<NodeDetailContent />)

    expect(await screen.findByText('No relations recorded.')).toBeInTheDocument()
  })

  it('10. clicking a relation navigates to the other node', async () => {
    render(<NodeDetailContent />)

    // Wait for relations to render, then narrow by the other-node label
    const densityRow = await waitFor(() => {
      const row = screen.getByText('Density').closest('button')
      if (!row) throw new Error('Density relation button not rendered')
      return row
    })

    fireEvent.click(densityRow)

    expect(pushMock).toHaveBeenCalledWith(
      '/kg/nodes/Property/fffffff1-aaaa-bbbb-cccc-111111111111',
    )
  })

  it('11. back button navigates to search', async () => {
    render(<NodeDetailContent />)

    await screen.findByText('UO2')
    const backBtn = screen.getByRole('button', { name: /back to search/i })
    fireEvent.click(backBtn)

    expect(pushMock).toHaveBeenCalledWith('/kg/search')
  })

  it('12. gracefully handles nodes with no properties', async () => {
    fetchMock.mockReset()
    queueResponses([
      { body: NODE_DETAIL_NO_PROPS },
      {
        body: {
          success: true,
          data: { items: [], total: 0, limit: 50, offset: 0 },
        },
      },
    ])

    render(<NodeDetailContent />)

    expect(await screen.findByText('No properties recorded.')).toBeInTheDocument()
  })

  it('13. displays the truncated ID for the current node', async () => {
    render(<NodeDetailContent />)

    const idEl = await screen.findByTestId('node-id')
    // First 8 chars of node.id + "…"
    expect(idEl).toHaveTextContent(/aaaaaaaa…/)
  })

  it('14. clicking Retry re-fetches the node and renders success', async () => {
    // First request fails with 500
    fetchMock.mockReset()
    queueResponses([{ body: { detail: 'Internal server error' }, status: 500 }])

    render(<NodeDetailContent />)

    // Confirm error state
    expect(await screen.findByText('Failed to load node')).toBeInTheDocument()

    // Queue a successful response for the retry
    queueResponses([
      { body: NODE_DETAIL },
      { body: RELATIONS_RESPONSE },
    ])

    // Click Retry
    const retryBtn = screen.getByRole('button', { name: /retry/i })
    fireEvent.click(retryBtn)

    // The retry should trigger a new fetch and render the node
    expect(await screen.findByText('UO2')).toBeInTheDocument()
    expect(screen.getByText('Material')).toBeInTheDocument()
    // 3 calls: initial node (500) → retry node (success) → relations
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })
})
