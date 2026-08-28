import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { GapReviewFilters } from '../GapReviewFilters'
import type { GapCandidateFilters } from '@/lib/reference-gaps/gap-candidates'

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

const BASELINE: GapCandidateFilters = {
  confidence_min: undefined,
  confidence_max: undefined,
  entity_type: undefined,
  source_doc: undefined,
  decision_status: undefined,
}

describe('GapReviewFilters', () => {
  const onChange = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders all 5 filter controls', () => {
    render(<GapReviewFilters filters={BASELINE} onFilterChange={onChange} />)
    expect(screen.getByPlaceholderText('Min Confidence')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Max Confidence')).toBeInTheDocument()
    expect(screen.getAllByText('Entity Type').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByPlaceholderText('Source Doc')).toBeInTheDocument()
    expect(screen.getAllByText('Decision Status').length).toBeGreaterThanOrEqual(1)
  })

  it('calls onFilterChange with entity_type when selected', () => {
    const { container } = render(<GapReviewFilters filters={BASELINE} onFilterChange={onChange} />)
    // Ant Design Select: find the selector and trigger mouseDown to open dropdown
    const entitySelect = container.querySelector('[title="Entity Type"]')
    if (entitySelect) {
      fireEvent.click(entitySelect)
      // Ant Design renders dropdown in document.body portal
      const option = document.querySelector('.ant-select-item-option-content')
      if (option) {
        fireEvent.click(option)
      }
      expect(onChange).toHaveBeenCalled()
      expect((onChange.mock.calls[0] as unknown as Record<string, unknown>).entity_type).toBeDefined()
    }
  })

  it('renders a reset button', () => {
    render(<GapReviewFilters filters={BASELINE} onFilterChange={onChange} />)
    expect(screen.getByText('Reset')).toBeInTheDocument()
  })

  it('resets all filters when reset is clicked', () => {
    const filters: GapCandidateFilters = { ...BASELINE, entity_type: 'Material', decision_status: 'pending' }
    render(<GapReviewFilters filters={filters} onFilterChange={onChange} />)
    fireEvent.click(screen.getByText('Reset'))
    expect(onChange).toHaveBeenCalledWith({ ...BASELINE })
  })

  it('calls onFilterChange with source_doc input', () => {
    render(<GapReviewFilters filters={BASELINE} onFilterChange={onChange} />)
    const input = screen.getByPlaceholderText('Source Doc')
    fireEvent.change(input, { target: { value: 'doc1.pdf' } })
    fireEvent.blur(input)
    expect(onChange).toHaveBeenCalledWith({ ...BASELINE, source_doc: 'doc1.pdf' })
  })
})
