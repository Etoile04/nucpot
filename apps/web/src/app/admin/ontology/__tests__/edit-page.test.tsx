/**
 * Tests for OntologyEditForm component.
 * Tests the form directly (not the EditPage wrapper that uses React.use()).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'

const mockRequest = vi.fn()
vi.mock('@/lib/api-client', () => ({
  request: (...args: unknown[]) => mockRequest(...args),
}))

vi.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({ user: { blog_role: 'admin' } }),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => '/admin/ontology/new',
}))

vi.mock('next/link', () => ({ default: ({ children, href, ...rest }: { children: React.ReactNode; href: string; [key: string]: unknown }) => (
    <a href={href}>{children}</a>
),
}))

const MOCK_DETAIL = {
  data: {
    id: 'v1',
    version: '1.0',
    status: 'draft',
    changelog: 'Initial',
    created_by: 'admin',
    created_at: '2026-01-15T10:00:00Z',
    updated_at: '2026-01-15T10:00:00Z',
    ontology_data: {
      entity_types: [
        { name: 'mat.alloy', chinese_name: '合金', english_name: 'Alloy', domain: 'Materials', description: 'An alloy', label_template: null, required_properties: null },
      ],
      relation_types: [],
    },
  },
}

function createWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client }, children)
  }
}

import { OntologyEditForm } from '../[typeId]/edit/page'

describe('OntologyEditForm (new mode)', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders new version heading', () => {
    render(<OntologyEditForm versionId='' />, { wrapper: createWrapper() })
    expect(screen.getByText('New ontology version')).toBeInTheDocument()
  })

  it('has one empty entity row by default', () => {
    render(<OntologyEditForm versionId='' />, { wrapper: createWrapper() })
    expect(screen.getByText('Entity #1')).toBeInTheDocument()
  })

  it('does not render promote button', () => {
    render(<OntologyEditForm versionId='' />, { wrapper: createWrapper() })
    expect(screen.queryByText('Promote and publish')).not.toBeInTheDocument()
  })

  it('renders Save draft button', () => {
    render(<OntologyEditForm versionId='' />, { wrapper: createWrapper() })
    expect(screen.getByText('Save draft')).toBeInTheDocument()
  })

  it('entity name input is enabled for new', () => {
    render(<OntologyEditForm versionId='' />, { wrapper: createWrapper() })
    const nameInput = screen.getByPlaceholderText('e.g. mat.zr_alloy_phase')
    expect(nameInput).not.toBeDisabled()
  })
})

describe('OntologyEditForm (edit mode)', () => {
  beforeEach(() => { vi.clearAllMocks(); mockRequest.mockResolvedValue(MOCK_DETAIL) })

  it('renders edit heading with version number', async () => {
    render(<OntologyEditForm versionId='v1' />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByText(/Edit v1.0/)).toBeInTheDocument())
  })

  it('populates entity fields from API', async () => {
    render(<OntologyEditForm versionId='v1' />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByDisplayValue('mat.alloy')).toBeInTheDocument())
    expect(screen.getByDisplayValue('合金')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Materials')).toBeInTheDocument()
  })

  it('entity name input is disabled for existing', async () => {
    render(<OntologyEditForm versionId='v1' />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByText(/Edit v1.0/)).toBeInTheDocument())
    const nameInput = screen.getByPlaceholderText('e.g. mat.zr_alloy_phase')
    expect(nameInput).toBeDisabled()
  })

  it('renders Save draft and Promote buttons', async () => {
    render(<OntologyEditForm versionId='v1' />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByText(/Edit v1.0/)).toBeInTheDocument())
    expect(screen.getByText('Save draft')).toBeInTheDocument()
    expect(screen.getByText('Promote and publish')).toBeInTheDocument()
  })

  it('renders Entity Types and Relation Types sections', async () => {
    render(<OntologyEditForm versionId='v1' />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByText(/Edit v1.0/)).toBeInTheDocument())
    expect(screen.getByText('Entity Types')).toBeInTheDocument()
    expect(screen.getByText('Relation Types')).toBeInTheDocument()
 })

  it('shows error on fetch failure', async () => {
    mockRequest.mockRejectedValue(new Error('Not found'))
    render(<OntologyEditForm versionId='bad' />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByText(/Not found/)).toBeInTheDocument())
  })

  it('renders Add entity type and Add relation type buttons', async () => {
    render(<OntologyEditForm versionId='v1' />, { wrapper: createWrapper() })
    await waitFor(() => expect(screen.getByText(/Edit v1.0/)).toBeInTheDocument())
    expect(screen.getByText('+ Add entity type')).toBeInTheDocument()
    expect(screen.getByText('+ Add relation type')).toBeInTheDocument()
  })
})
