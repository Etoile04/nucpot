/**
 * Tests for SkeletonTable component.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SkeletonTable } from './skeleton-table'

describe('SkeletonTable', () => {
  it('renders with default 8 rows', () => {
    const { container } = render(<SkeletonTable />)
    // Header row + 8 data rows = 9 row divs with border-b
    const rows = container.querySelectorAll('[class*="border-b"]')
    expect(rows.length).toBeGreaterThanOrEqual(8)
  })

  it('renders custom row count', () => {
    const { container } = render(<SkeletonTable rows={3} />)
    // Header + 3 data rows
    const rows = container.querySelectorAll('[class*="border-b"]')
    expect(rows.length).toBe(4)
  })

  it('has role=status for accessibility', () => {
    render(<SkeletonTable />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('has sr-only loading text', () => {
    render(<SkeletonTable />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('has aria-label for the container', () => {
    render(<SkeletonTable />)
    expect(screen.getByRole('status')).toHaveAttribute('aria-label', 'Loading ontology list')
  })
})
